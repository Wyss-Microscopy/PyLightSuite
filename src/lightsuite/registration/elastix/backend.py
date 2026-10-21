"""Run elastix and transformix through the itk-elastix Python package.

These functions replace calls to the ``elastix`` / ``transformix`` command-line programs.
They take the same inputs (MetaImage files, parameter files, landmark files) and write
the same files to the output directory (``TransformParameters.N.txt``, ``elastix.log``,
``IterationInfo.*``, ``result.mhd``, ``outputpoints.txt``, ``deformationField.mhd``), so
callers and saved runs stay compatible with transforms made by the binaries.

Elastix runs in one long-lived helper process rather than in the calling process:

- itk-elastix holds the GIL while it works, which would freeze the GUI;
- loading the ITK modules takes ~15 s, so the helper loads them once per session;
- a cancelled stage can stop a running registration by ending the helper;
- an elastix crash cannot take down the GUI.

Set ``LIGHTSUITE_ELASTIX_INPROCESS=1`` to run in the calling process instead
(debugging, profiling). If the helper cannot start, elastix also runs in-process.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

INPROCESS_ENV = "LIGHTSUITE_ELASTIX_INPROCESS"
CALL_RECORD_NAME = "elastix_call.json"
_INSTALL_HINT = "Install it with: uv sync (itk-elastix is a core dependency of lightsuite)."


class ElastixError(RuntimeError):
    """elastix or transformix failed."""


# ---------------------------------------------------------------------------
# Availability


def elastix_available() -> bool:
    """True when itk-elastix is installed (checked without importing ITK)."""
    try:
        return importlib.util.find_spec("itk") is not None and (
            importlib.util.find_spec("itk.ElastixPython") is not None
            or _distribution_version("itk-elastix") is not None
        )
    except (ImportError, ValueError):
        return False


def elastix_version() -> str | None:
    """Installed itk-elastix version, or None."""
    return _distribution_version("itk-elastix")


def require_elastix(stage: str) -> None:
    """Raise a clear error when itk-elastix is missing."""
    if not elastix_available():
        msg = f"itk-elastix is required for {stage} but is not installed. {_INSTALL_HINT}"
        raise RuntimeError(msg)


def _distribution_version(name: str) -> str | None:
    from importlib import metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Public API


def elastix(
    *,
    fixed: Sequence[Path] | Path,
    moving: Sequence[Path] | Path,
    parameter_files: Sequence[Path] | Path,
    output_dir: Path,
    fixed_points: Path | None = None,
    moving_points: Path | None = None,
    initial_transform: Path | None = None,
) -> list[Path]:
    """Register images; same as ``elastix -f0 .. -m0 .. [-fp -mp] [-t0] -p .. -out``.

    Returns the ``TransformParameters.N.txt`` files written to *output_dir*.
    """
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "fixed": [str(Path(p)) for p in _as_list(fixed)],
        "moving": [str(Path(p)) for p in _as_list(moving)],
        "parameter_files": [str(Path(p)) for p in _as_list(parameter_files)],
        "output_dir": str(output_dir),
        "fixed_points": str(fixed_points) if fixed_points is not None else None,
        "moving_points": str(moving_points) if moving_points is not None else None,
        "initial_transform": str(initial_transform) if initial_transform is not None else None,
    }
    # Lets inversion find the inputs of this run (the binary's log held the command line).
    (output_dir / CALL_RECORD_NAME).write_text(json.dumps(spec, indent=2), encoding="utf-8")
    result = _call("elastix", spec)
    return [Path(p) for p in result["transforms"]]


def transformix(
    *,
    transform_path: Path,
    output_dir: Path,
    input_image: Path | None = None,
    points_file: Path | None = None,
    deformation_field: bool = False,
    pixel_type: str = "float",
) -> dict[str, Path]:
    """Apply a transform; same as ``transformix -tp .. [-in ..] [-def ..] -out ..``.

    Writes ``result.mhd`` (with *input_image*), ``outputpoints.txt`` (with
    *points_file*) and/or ``deformationField.mhd`` (with *deformation_field*), and
    returns the written paths under the keys ``result``, ``points`` and ``deformation``.
    """
    if input_image is None and points_file is None and not deformation_field:
        msg = "transformix needs an input image, a points file or deformation_field=True."
        raise ValueError(msg)
    if pixel_type not in ("float", "double"):
        msg = f"pixel_type must be 'float' or 'double', got {pixel_type!r}"
        raise ValueError(msg)
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "transform_path": str(Path(transform_path).expanduser()),
        "output_dir": str(output_dir),
        "input_image": str(input_image) if input_image is not None else None,
        "points_file": str(points_file) if points_file is not None else None,
        "deformation_field": bool(deformation_field),
        "pixel_type": pixel_type,
    }
    result = _call("transformix", spec)
    return {key: Path(value) for key, value in result.items()}


def shutdown_helper() -> None:
    """Stop the helper process (it restarts on the next call)."""
    with _LOCK:
        _stop_worker()


# ---------------------------------------------------------------------------
# Jobs (run in the helper process, or in-process)


def _job_elastix(spec: dict[str, Any]) -> dict[str, Any]:
    import itk

    output_dir = Path(spec["output_dir"])
    image_type = itk.Image[itk.F, 3]
    method = itk.ElastixRegistrationMethod[image_type, image_type].New()
    for index, path in enumerate(spec["fixed"]):
        image = itk.imread(path, itk.F)
        method.SetFixedImage(image) if index == 0 else method.AddFixedImage(image)
    for index, path in enumerate(spec["moving"]):
        image = itk.imread(path, itk.F)
        method.SetMovingImage(image) if index == 0 else method.AddMovingImage(image)

    parameter_object = itk.ParameterObject.New()
    for path in spec["parameter_files"]:
        parameter_object.AddParameterFile(path)
    method.SetParameterObject(parameter_object)

    if spec["fixed_points"] is not None:
        method.SetFixedPointSetFileName(spec["fixed_points"])
    if spec["moving_points"] is not None:
        method.SetMovingPointSetFileName(spec["moving_points"])
    if spec["initial_transform"] is not None:
        method.SetInitialTransformParameterFileName(spec["initial_transform"])

    method.SetOutputDirectory(str(output_dir))
    method.SetLogToFile(True)
    method.SetLogToConsole(False)
    try:
        method.UpdateLargestPossibleRegion()
    except RuntimeError as exc:
        raise ElastixError(_failure_text("elastix", exc, output_dir / "elastix.log")) from exc

    transforms = sorted(output_dir.glob("TransformParameters.*.txt"), key=_transform_index)
    if not transforms:
        msg = f"elastix wrote no TransformParameters.*.txt to {output_dir}"
        raise ElastixError(msg)
    return {"transforms": [str(p) for p in transforms]}


def _job_transformix(spec: dict[str, Any]) -> dict[str, Any]:
    import itk
    import numpy as np

    output_dir = Path(spec["output_dir"])
    pixel = itk.D if spec["pixel_type"] == "double" else itk.F
    image_type = itk.Image[pixel, 3]

    maps = _load_transform_chain(Path(spec["transform_path"]))
    only_points = spec["points_file"] is not None and spec["input_image"] is None
    only_points = only_points and not spec["deformation_field"]
    parameter_object = itk.ParameterObject.New()
    for params in maps:
        if only_points:
            # Points do not depend on the output grid; a 1-voxel grid avoids resampling a
            # full volume that nobody reads.
            params["Size"] = ["1"] * len(params.get("Size", ["1", "1", "1"]))
        parameter_object.AddParameterMap(params)

    filt = itk.TransformixFilter[image_type].New()
    if spec["input_image"] is not None:
        moving = itk.imread(spec["input_image"], pixel)
    else:
        # The filter always needs an input image; the transform defines the output grid.
        dtype = np.float32 if pixel == itk.F else np.float64
        moving = itk.image_from_array(np.zeros((1, 1, 1), dtype=dtype))
    filt.SetMovingImage(moving)
    filt.SetTransformParameterObject(parameter_object)
    if spec["points_file"] is not None:
        filt.SetFixedPointSetFileName(spec["points_file"])
    if spec["deformation_field"]:
        filt.SetComputeDeformationField(True)
    filt.SetOutputDirectory(str(output_dir))
    filt.SetLogToFile(True)
    filt.SetLogToConsole(False)
    try:
        filt.UpdateLargestPossibleRegion()
    except RuntimeError as exc:
        log_path = output_dir / "transformix.log"
        raise ElastixError(_failure_text("transformix", exc, log_path)) from exc

    written: dict[str, str] = {}
    if spec["input_image"] is not None:
        result_path = output_dir / "result.mhd"
        itk.imwrite(filt.GetOutput(), str(result_path))
        written["result"] = str(result_path)
    if spec["points_file"] is not None:
        points_path = output_dir / "outputpoints.txt"
        if not points_path.is_file():
            msg = f"transformix wrote no outputpoints.txt to {output_dir}"
            raise ElastixError(msg)
        written["points"] = str(points_path)
    if spec["deformation_field"]:
        field_path = output_dir / "deformationField.mhd"
        if not field_path.is_file():
            itk.imwrite(filt.GetOutputDeformationField(), str(field_path))
        written["deformation"] = str(field_path)
    return written


_JOBS = {"elastix": _job_elastix, "transformix": _job_transformix}


# elastix 5.1 writes the first spelling, 5.2+ the second.
_INITIAL_TRANSFORM_KEYS = (
    "InitialTransformParametersFileName",
    "InitialTransformParameterFileName",
)


def _load_transform_chain(path: Path) -> list[dict[str, list[str]]]:
    """Read a transform file and the files it chains to, initial transform first.

    The command-line transformix follows ``InitialTransformParameter(s)FileName`` itself;
    here each file becomes one map of the parameter object, applied in order.
    """
    import itk

    chain: list[dict[str, list[str]]] = []
    seen: set[str] = set()
    current: Path | None = path
    while current is not None:
        key = str(current.resolve())
        if key in seen:
            msg = f"Circular InitialTransformParametersFileName chain at {current}"
            raise ElastixError(msg)
        seen.add(key)
        if not current.is_file():
            msg = f"Transform parameter file not found: {current}"
            raise ElastixError(msg)
        reader = itk.ParameterObject.New()
        reader.ReadParameterFile(str(current))
        params = {k: list(v) for k, v in dict(reader.GetParameterMap(0)).items()}
        initial = "NoInitialTransform"
        for key in _INITIAL_TRANSFORM_KEYS:
            value = (params.get(key) or ["NoInitialTransform"])[0]
            if value and value != "NoInitialTransform":
                initial = value
            if key in params:
                params[key] = ["NoInitialTransform"]
        chain.append(params)
        if initial and initial != "NoInitialTransform":
            nxt = Path(initial)
            current = nxt if nxt.is_absolute() else current.parent / nxt
        else:
            current = None
    chain.reverse()
    return chain


def _transform_index(path: Path) -> int:
    match = re.search(r"TransformParameters\.(\d+)", path.name)
    return int(match.group(1)) if match else 0


def _failure_text(program: str, exc: BaseException, log_path: Path) -> str:
    text = f"{program} failed: {exc}"
    if log_path.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace").strip()[-3000:]
        text += f"\n--- {log_path.name} (tail) ---\n{tail}"
    return text


# ---------------------------------------------------------------------------
# Helper process
#
# A plain ``python -m lightsuite.registration.elastix._helper`` subprocess speaking JSON
# lines over stdin/stdout. Unlike multiprocessing's spawn mode it never re-imports the
# caller's script, so scripts and notebooks need no ``if __name__ == "__main__":`` guard.

_LOCK = threading.Lock()
_WORKER: _Worker | None = None
_INPROCESS_FALLBACK = False
_READY_TIMEOUT_S = 600.0


class _WorkerDied(ElastixError):
    pass


class _Worker:
    def __init__(self) -> None:
        env = dict(os.environ)
        # Let the helper import lightsuite from wherever this process found it.
        paths = [p for p in sys.path if p and Path(p).exists()]
        if env.get("PYTHONPATH"):
            paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env["PYTHONIOENCODING"] = "utf-8"
        self.log_path = Path(tempfile.gettempdir()) / f"lightsuite_elastix_helper_{os.getpid()}.log"
        self._log = open(self.log_path, "w", encoding="utf-8")  # noqa: SIM115 - lives with the helper
        self.process = subprocess.Popen(
            [sys.executable, "-m", "lightsuite.registration.elastix._helper"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            env=env,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._replies: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_replies, daemon=True)
        self._reader.start()
        try:
            reply = self._wait(timeout=_READY_TIMEOUT_S)
        except BaseException:
            self.stop()
            raise
        if reply.get("status") != "ready":
            self.stop()
            raise ElastixError(f"elastix helper process failed to start: {reply.get('message')}")

    def _read_replies(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._replies.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self._replies.put(None)

    def call(self, job: str, spec: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps({"job": job, "spec": spec}) + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            raise _WorkerDied(f"elastix helper process is not running: {exc}") from exc
        return self._wait()

    def _wait(self, timeout: float | None = None) -> dict[str, Any]:
        from lightsuite.reporter import check_stage_cancelled

        waited = 0.0
        while True:
            try:
                reply = self._replies.get(timeout=0.2)
            except queue.Empty:
                waited += 0.2
                check_stage_cancelled()
                if timeout is not None and waited > timeout:
                    msg = f"elastix helper process did not start within {timeout:.0f} s."
                    raise _WorkerDied(msg) from None
                continue
            if reply is None:
                try:
                    code = self.process.wait(5)
                except subprocess.TimeoutExpired:
                    code = None
                msg = (
                    f"elastix helper process exited unexpectedly (exit code {code}); "
                    f"see {self.log_path}"
                )
                raise _WorkerDied(msg)
            return reply

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                assert self.process.stdin is not None
                self.process.stdin.close()
                self.process.wait(2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                pass
        self._log.close()


def _helper_main() -> None:  # pragma: no cover - runs in the helper process
    """Serve jobs from stdin; replies go to the original stdout, everything else to stderr."""
    reply_fd = os.dup(1)
    os.dup2(2, 1)  # ITK and elastix may print to stdout; keep that out of the reply channel
    replies = os.fdopen(reply_fd, "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr

    def send(message: dict[str, Any]) -> None:
        replies.write(json.dumps(message) + "\n")
        replies.flush()

    try:
        import itk

        image_type = itk.Image[itk.F, 3]
        itk.ElastixRegistrationMethod[image_type, image_type]  # load ITK modules once
        itk.TransformixFilter[image_type]
    except BaseException as exc:  # noqa: BLE001 - report any import failure
        send({"status": "error", "message": f"{type(exc).__name__}: {exc}"})
        return
    send({"status": "ready"})

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        try:
            send({"status": "ok", "result": _JOBS[request["job"]](request["spec"])})
        except BaseException as exc:  # noqa: BLE001 - send every failure back
            if isinstance(exc, ElastixError):
                message = str(exc)
            else:
                message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            send({"status": "error", "message": message})


def _stop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.stop()
        _WORKER = None


atexit.register(shutdown_helper)


def _call(job: str, spec: dict[str, Any]) -> dict[str, Any]:
    global _WORKER, _INPROCESS_FALLBACK
    require_elastix(job)
    if os.environ.get(INPROCESS_ENV, "").strip() in ("1", "true", "yes") or _INPROCESS_FALLBACK:
        return _JOBS[job](spec)

    from lightsuite.exceptions import StageCancelledError
    from lightsuite.reporter import emit_pipeline_message

    with _LOCK:
        if _WORKER is None:
            try:
                _WORKER = _Worker()
            except StageCancelledError:
                raise
            except (ElastixError, OSError) as exc:
                _INPROCESS_FALLBACK = True
                emit_pipeline_message(f"{exc} Running elastix in this process instead.")
                return _JOBS[job](spec)
        try:
            reply = _WORKER.call(job, spec)
        except (StageCancelledError, _WorkerDied):
            # Ending the helper is the only way to stop a running registration.
            _stop_worker()
            raise
    if reply.get("status") != "ok":
        raise ElastixError(reply.get("message", "elastix helper returned an error"))
    return reply["result"]


def _as_list(value: Sequence[Path] | Path) -> list[Path]:
    if isinstance(value, (str, Path)):
        return [Path(value)]
    return [Path(v) for v in value]
