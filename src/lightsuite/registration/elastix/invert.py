"""Invert elastix transforms (matlab_elastix invertElastixTransformCP.m port)."""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

import numpy as np

from lightsuite.registration.elastix import backend
from lightsuite.registration.elastix.mhd import read_mhd_spacing, read_mhd_volume
from lightsuite.registration.elastix.runner import run_transformix_physical_points

INVERSION_PARAMETER_FILE = "inversion_parameters.txt"
# invertElastixTransformCP iterfac: iterations = forward final level x this factor.
# 1.5 is the matlab_elastix default (cord, slices); multiobjRegistration.m passes 2 for brains.
INVERSION_ITERATION_FACTOR = 1.5
BRAIN_INVERSION_ITERATION_FACTOR = 2.0
# Gain-schedule offset A of the optimizer step size a / (k + 1 + A)^alpha. The inversion
# keeps the forward run's SP_A (20), as matlab_elastix does; if that inverse fails the
# round-trip check, it is rerun with SP_A 1.
INVERSION_FALLBACK_SP_A = 1
# Round-trip check: tissue points x of the fixed image go through the forward transform and
# then the inverse. A converged inverse brings them back within a few µm (median 2-7 µm on
# three test brains); a median above this limit (in mm, the physical unit of LightSuite
# elastix runs) means the inversion did not converge.
ROUNDTRIP_MEDIAN_LIMIT_MM = 0.025
ROUNDTRIP_POINTS = 3000
ROUNDTRIP_MARGIN_VOXELS = 25
INVERSION_REPORT_FILE = "inversion_report.json"
logger = logging.getLogger(__name__)
# Components elastix creates once per metric; the inversion has a single metric.
_PER_METRIC_KEYS = ("FixedImagePyramid", "MovingImagePyramid", "ImageSampler", "Interpolator")


def invert_elastix_transform(
    transform_dir: Path,
    output_dir: Path | None = None,
    *,
    iteration_factor: float = INVERSION_ITERATION_FACTOR,
) -> Path:
    """Invert a forward elastix B-spline transform; return the inverse TransformParameters.

    Follows matlab_elastix ``invertElastixTransformCP`` (2026-09): register the forward
    fixed image to itself, starting from the forward transform, with
    ``DisplacementMagnitudePenalty`` (which drives T(x) towards x) at the finest pyramid
    level only. No image metric and no landmarks are used. The optimizer keeps the forward
    ``SP_A``; if the inverse fails a round-trip check (forward then inverse should return
    tissue points to where they started), it is rerun with ``SP_A`` 1 and the better of the
    two is kept. ``inversion_report.json`` in *output_dir* records which was used.
    """
    transform_dir = transform_dir.expanduser()
    if output_dir is None:
        output_dir = transform_dir.parent / "elastix_inverse_temp"
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_inversion_workspace(output_dir)
    # Left over from an earlier run: the fallback attempt and its report.
    shutil.rmtree(output_dir / f"sp_a_{INVERSION_FALLBACK_SP_A}", ignore_errors=True)
    (output_dir / INVERSION_REPORT_FILE).unlink(missing_ok=True)

    fixed_mhd, param_paths = _forward_run_inputs(transform_dir)
    coef_files = sorted(transform_dir.glob("TransformParameters.*.txt"), reverse=True)
    if not coef_files:
        msg = f"No TransformParameters.*.txt in {transform_dir}"
        raise FileNotFoundError(msg)

    forward_text = param_paths[0].read_text(encoding="utf-8", errors="replace")
    try:
        probe = _roundtrip_probe(fixed_mhd, coef_files[0], output_dir / "roundtrip_forward")
    except (OSError, ValueError, backend.ElastixError) as exc:
        logger.warning("B-spline inversion: skipping the round-trip check (%s).", exc)
        probe = None

    # The forward SP_A (20) gives the most accurate inverse, but a run can still diverge
    # (e.g. load-dependent summation order). A diverged inverse maps the probe points back
    # far from where they started, so check the round trip and, if it is off, rerun with
    # SP_A 1 (a different step-size schedule) and keep the better inverse.
    candidates: list[tuple[float, Path, int | None]] = []
    for sp_a in (None, INVERSION_FALLBACK_SP_A):
        attempt_dir = output_dir if sp_a is None else output_dir / f"sp_a_{sp_a}"
        inverted = _invert_with_retry(
            fixed_mhd=fixed_mhd,
            output_dir=attempt_dir,
            coef_path=coef_files[0],
            parameter_text=build_inversion_parameters(
                forward_text, iteration_factor=iteration_factor, sp_a=sp_a
            ),
        )
        if inverted is None:
            continue
        error_mm = _roundtrip_median_mm(probe, inverted, attempt_dir / "roundtrip_inverse")
        candidates.append((error_mm, inverted, sp_a))
        if error_mm <= ROUNDTRIP_MEDIAN_LIMIT_MM:
            break
        logger.warning(
            "B-spline inversion (SP_A %s): median round-trip error %.1f µm exceeds %.0f µm.",
            "forward" if sp_a is None else sp_a,
            error_mm * 1000.0,
            ROUNDTRIP_MEDIAN_LIMIT_MM * 1000.0,
        )
    if not candidates:
        msg = f"elastix inversion failed in {output_dir}. See elastix.log in that directory."
        raise RuntimeError(msg)

    error_mm, inverted, sp_a = min(candidates, key=lambda c: c[0])
    report = {
        "sp_a": "forward" if sp_a is None else sp_a,
        "iteration_factor": iteration_factor,
        "roundtrip_median_um": None if probe is None else round(error_mm * 1000.0, 2),
        "attempts": [
            {"sp_a": "forward" if s is None else s, "roundtrip_median_um": round(e * 1000.0, 2)}
            for e, _, s in candidates
        ],
    }
    (output_dir / INVERSION_REPORT_FILE).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return inverted


def _invert_with_retry(
    *, fixed_mhd: Path, output_dir: Path, coef_path: Path, parameter_text: str
) -> Path | None:
    """Run the inversion; repeat once if elastix fails. Returns the pure inverse or None."""
    output_dir.mkdir(parents=True, exist_ok=True)
    param_path = output_dir / INVERSION_PARAMETER_FILE
    param_path.write_text(parameter_text, encoding="utf-8")
    # Multithreaded elastix sums partial results in a load-dependent order; repeat once
    # before giving up.
    for attempt in range(2):
        if attempt:
            _clear_inversion_workspace(output_dir)
        inverted = _run_inversion_attempt(
            fixed_mhd=fixed_mhd,
            output_dir=output_dir,
            coef_path=coef_path,
            param_path=param_path,
        )
        if inverted is not None:
            return _finalize_inverted_transform(inverted)
    return None


def _roundtrip_probe(
    fixed_mhd: Path, forward_coef: Path, work_dir: Path
) -> tuple[np.ndarray, np.ndarray] | None:
    """Tissue points of the fixed image (physical x, y, z) and their forward images."""
    volume = read_mhd_volume(fixed_mhd)  # (Y, X, Z)
    positive = volume[volume > 0]
    if positive.size == 0:
        return None
    # Stay away from the image border, where the inverse is poorly constrained.
    margin = np.minimum(ROUNDTRIP_MARGIN_VOXELS, np.array(volume.shape) // 4)
    inner = np.zeros(volume.shape, dtype=bool)
    inner[tuple(slice(m, n - m) for m, n in zip(margin, volume.shape, strict=True))] = True
    idx = np.argwhere(inner & (volume > np.median(positive)))
    if len(idx) == 0:
        return None
    rng = np.random.default_rng(0)
    idx = idx[rng.choice(len(idx), size=min(ROUNDTRIP_POINTS, len(idx)), replace=False)]
    spacing_xyz = read_mhd_spacing(fixed_mhd)
    points = idx[:, [1, 0, 2]].astype(float) * spacing_xyz
    mapped = run_transformix_physical_points(
        points_xyz=points, transform_path=forward_coef, output_dir=work_dir
    )
    return points, mapped


def _roundtrip_median_mm(
    probe: tuple[np.ndarray, np.ndarray] | None, inverse: Path, work_dir: Path
) -> float:
    """Median |inverse(forward(x)) - x| over the probe points (0 when there is no probe)."""
    if probe is None:
        return 0.0
    points, mapped = probe
    back = run_transformix_physical_points(
        points_xyz=mapped, transform_path=inverse, output_dir=work_dir
    )
    error = np.linalg.norm(back - points, axis=1)
    error[~np.isfinite(error)] = np.inf
    return float(np.median(error))


def build_inversion_parameters(
    forward_text: str,
    *,
    iteration_factor: float = INVERSION_ITERATION_FACTOR,
    sp_a: int | None = None,
) -> str:
    """Inversion parameter file from the forward one (invertElastixTransformCP edits).

    MATLAB edits: ``Registration`` -> MultiResolutionRegistration, ``Metric`` ->
    DisplacementMagnitudePenalty, ``NumberOfResolutions`` -> 1,
    ``ImagePyramidSchedule`` -> its last (finest) level, ``MaximumNumberOfIterations`` ->
    last level x *iteration_factor*; ``SP_A`` -> *sp_a* when given. Everything else (grid
    spacing, sampler, sample region, other optimizer settings) is kept from the forward run,
    as in MATLAB. Python additionally keeps only the first entry of per-metric component
    lists (MATLAB's forward runs never have them) and skips writing the result image, which
    only costs time.
    """
    params = read_parameter_text(forward_text)
    dim = int(_first_number(params.get("FixedImageDimension"), default=3))

    params["Registration"] = ['"MultiResolutionRegistration"']
    params["Metric"] = ['"DisplacementMagnitudePenalty"']
    for key in list(params):
        if re.fullmatch(r"Metric\d+Weight", key):
            del params[key]
    for key in _PER_METRIC_KEYS:
        if key in params and len(params[key]) > 1:
            params[key] = params[key][:1]

    params["NumberOfResolutions"] = ["1"]
    schedule = params.get("ImagePyramidSchedule")
    params["ImagePyramidSchedule"] = schedule[-dim:] if schedule else ["1"] * dim
    iterations = params.get("MaximumNumberOfIterations") or ["2000"]
    params["MaximumNumberOfIterations"] = [str(round(float(iterations[-1]) * iteration_factor))]
    if sp_a is not None:
        params["SP_A"] = [str(sp_a)]
    params["WriteResultImage"] = ['"false"']
    return format_parameter_text(params)


def read_parameter_text(text: str) -> dict[str, list[str]]:
    """Parse an elastix parameter file into ``{key: [raw tokens]}`` (quotes kept)."""
    params: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.split("//", 1)[0].strip()
        match = re.fullmatch(r"\(\s*(\w+)\s*(.*?)\s*\)", line)
        if match is None:
            continue
        params[match.group(1)] = re.findall(r'"[^"]*"|[^\s"]+', match.group(2))
    return params


def format_parameter_text(params: dict[str, list[str]]) -> str:
    return "\n".join(f"({key} {' '.join(values)})" for key, values in params.items()) + "\n"


def _first_number(values: list[str] | None, *, default: float) -> float:
    if not values:
        return default
    try:
        return float(values[0].strip('"'))
    except ValueError:
        return default


def _finalize_inverted_transform(inverted_path: Path) -> Path:
    text = _force_no_initial_transform(inverted_path.read_text(encoding="utf-8"))
    inverted_path.write_text(text, encoding="utf-8")
    return inverted_path


def _run_inversion_attempt(
    *,
    fixed_mhd: Path,
    output_dir: Path,
    coef_path: Path,
    param_path: Path,
) -> Path | None:
    """Run one inversion attempt; return TransformParameters path or None on failure."""
    _ = read_mhd_spacing(fixed_mhd)
    try:
        inverted = backend.elastix(
            fixed=fixed_mhd,
            moving=fixed_mhd,
            parameter_files=param_path,
            output_dir=output_dir,
            initial_transform=coef_path,
        )
    except backend.ElastixError:
        return None
    return inverted[0] if inverted else None


def _forward_run_inputs(transform_dir: Path) -> tuple[Path, list[Path]]:
    """Fixed image and parameter files of the forward run in *transform_dir*."""
    record_path = transform_dir / backend.CALL_RECORD_NAME
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        fixed_mhd = Path(record["fixed"][0])
        param_paths = [Path(p) for p in record["parameter_files"]]
        if fixed_mhd.is_file() and param_paths and all(p.is_file() for p in param_paths):
            return fixed_mhd, param_paths

    # Runs made with the elastix binary: its log holds the command line.
    log_path = transform_dir / "elastix.log"
    if not log_path.is_file():
        msg = f"Missing elastix.log in {transform_dir}"
        raise FileNotFoundError(msg)
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    fixed_mhd = _parse_fixed_image_path(log_text, transform_dir)
    param_paths = _parse_parameter_file_paths(log_text, transform_dir)
    if not param_paths:
        msg = f"Could not parse parameter file paths from {log_path}"
        raise RuntimeError(msg)
    return fixed_mhd, param_paths


def _clear_inversion_workspace(output_dir: Path) -> None:
    for pattern in (
        "TransformParameters.*.txt",
        "elastix.log",
        "IterationInfo.*",
        "result.*",
        backend.CALL_RECORD_NAME,
    ):
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def _force_no_initial_transform(text: str) -> str:
    """Strip any chained initial transform so the saved file is the pure inverse."""
    # elastix 5.1 writes InitialTransformParametersFileName, 5.2+ InitialTransformParameterFileName.
    pattern = re.compile(r'\(\s*(InitialTransformParameters?FileName)\s+"[^"]*"\s*\)')
    if pattern.search(text):
        return pattern.sub(r'(\1 "NoInitialTransform")', text)
    replacement = '(InitialTransformParametersFileName "NoInitialTransform")'
    return text.rstrip("\n") + "\n" + replacement + "\n"


def write_inverted_transform_copy(source: Path, destination: Path) -> Path:
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def _guess_target_mhd(transform_dir: Path) -> Path | None:
    """Single-fixed elastix runs write ``*_target.mhd`` (Windows logs omit ``-f``)."""
    candidates = sorted(
        transform_dir.glob("*_target.mhd"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    candidates.extend(
        sorted(
            transform_dir.glob("*_target.MHD"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    )
    seen: set[str] = set()
    for path in candidates:
        key = path.name.lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def _parse_fixed_image_path(log_text: str, transform_dir: Path) -> Path:
    patterns = [
        r"-f0\s+(.+?)\s+-m0\b",
        r"-f\s+(.+?)(?=\s+(?<![fm])-m\b)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, log_text, flags=re.DOTALL)
        if matches:
            candidate = _strip_quotes(matches[-1].strip())
            path = Path(candidate)
            if path.is_file():
                return path
    guesses = list(transform_dir.glob("*_dual_f0.mhd")) + list(transform_dir.glob("fixed.mhd"))
    if guesses:
        return guesses[0]
    target_mhd = _guess_target_mhd(transform_dir)
    if target_mhd is not None:
        return target_mhd
    msg = f"Could not determine fixed image path from elastix log in {transform_dir}"
    raise RuntimeError(msg)


def _parse_parameter_file_paths(log_text: str, transform_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in (
        r"end of ParameterFile:\s*([^\r\n]+)",
        r'(?<![fm])-p\s+"([^"]+)"',
        r"(?<![fm])-p\s+(\S+)",
    ):
        for match in re.findall(pattern, log_text):
            cleaned = _strip_quotes(str(match).strip().split("=")[0])
            if cleaned:
                paths.append(Path(cleaned))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen and path.is_file():
            unique.append(path)
            seen.add(key)
    if not unique:
        for path in transform_dir.glob("*parameters*.txt"):
            if path.is_file():
                unique.append(path)
    return unique


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value
