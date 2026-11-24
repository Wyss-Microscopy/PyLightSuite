"""Tests for the itk-elastix backend (elastix / transformix without the binaries)."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter
from scipy.ndimage import shift as nd_shift

from lightsuite.registration.elastix import backend
from lightsuite.registration.elastix.mhd import read_mhd_volume, write_mhd
from lightsuite.registration.elastix.params import write_parameter_file
from lightsuite.registration.elastix.points import write_landmark_file

pytestmark = pytest.mark.skipif(not backend.elastix_available(), reason="itk-elastix missing")

SPACING = 0.02
# Volume (Y, X, Z) shift of the moving image relative to the fixed image, in voxels.
SHIFT_YXZ = (2.0, -3.0, 1.0)


def _translation_params(**overrides) -> dict:
    params = {
        "Registration": "MultiResolutionRegistration",
        "Metric": "AdvancedMeanSquares",
        "Transform": "TranslationTransform",
        "Optimizer": "AdaptiveStochasticGradientDescent",
        "ImageSampler": "Full",
        "Interpolator": "LinearInterpolator",
        "ResampleInterpolator": "FinalLinearInterpolator",
        "Resampler": "DefaultResampler",
        "FixedImagePyramid": "FixedRecursiveImagePyramid",
        "MovingImagePyramid": "MovingRecursiveImagePyramid",
        "NumberOfResolutions": 2,
        "MaximumNumberOfIterations": 400,
        "NumberOfHistogramBins": 32,
        "AutomaticParameterEstimation": "true",
        "UseDirectionCosines": "false",
        "WriteResultImage": "false",
        "DefaultPixelValue": 0,
    }
    params.update(overrides)
    return params


@pytest.fixture(scope="module")
def registered(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One translation registration shared by the tests below."""
    work = tmp_path_factory.mktemp("elastix_backend")
    rng = np.random.default_rng(0)
    fixed = gaussian_filter(rng.random((40, 36, 32)).astype(np.float32), 3) * 1000
    moving = nd_shift(fixed, SHIFT_YXZ, order=1)
    write_mhd(fixed, work / "fixed", [SPACING] * 3)
    write_mhd(moving, work / "moving", [SPACING] * 3)
    param_path = work / "params.txt"
    write_parameter_file(param_path, _translation_params())
    transforms = backend.elastix(
        fixed=work / "fixed.mhd",
        moving=work / "moving.mhd",
        parameter_files=param_path,
        output_dir=work / "out",
    )
    return {"work": work, "transforms": transforms, "moving": moving, "fixed": fixed}


def _translation_mm(transform_path: Path) -> np.ndarray:
    text = transform_path.read_text(encoding="utf-8")
    match = re.search(r"\(TransformParameters ([^)]*)\)", text)
    assert match is not None
    return np.array(match.group(1).split(), dtype=float)


def test_elastix_writes_cli_outputs(registered: dict) -> None:
    out = registered["work"] / "out"
    assert [p.name for p in registered["transforms"]] == ["TransformParameters.0.txt"]
    assert (out / "elastix.log").is_file()
    assert list(out.glob("IterationInfo.0.R*.txt"))
    record = json.loads((out / backend.CALL_RECORD_NAME).read_text(encoding="utf-8"))
    assert record["fixed"] == [str(registered["work"] / "fixed.mhd")]

    # Elastix maps fixed -> moving: physical (x, y, z) = volume (X, Y, Z) shift.
    expected = np.array([SHIFT_YXZ[1], SHIFT_YXZ[0], SHIFT_YXZ[2]]) * SPACING
    np.testing.assert_allclose(_translation_mm(registered["transforms"][0]), expected, atol=0.004)


def test_transformix_image_points_and_field(registered: dict) -> None:
    work = registered["work"]
    transform = registered["transforms"][0]
    shift_mm = _translation_mm(transform)

    written = backend.transformix(
        transform_path=transform,
        output_dir=work / "tfx",
        input_image=work / "moving.mhd",
        deformation_field=True,
    )
    warped = read_mhd_volume(written["result"])
    fixed = registered["fixed"]
    inner = (slice(6, -6),) * 3
    corr = np.corrcoef(warped[inner].ravel(), fixed[inner].ravel())[0, 1]
    assert corr > 0.98

    from lightsuite.registration.elastix.runner import _read_mhd_vector_field

    field = _read_mhd_vector_field(written["deformation"])
    assert field.shape == (*fixed.shape, 3)
    np.testing.assert_allclose(field[10, 10, 10], shift_mm, atol=1e-5)

    points = np.array([[0.2, 0.3, 0.1], [0.5, 0.4, 0.3]])
    write_landmark_file(work / "pts.txt", points)
    written = backend.transformix(
        transform_path=transform,
        output_dir=work / "tfx_pts",
        points_file=work / "pts.txt",
    )
    out = [
        [float(v) for v in m.group(1).split()]
        for m in re.finditer(r"OutputPoint\s*=\s*\[([^\]]+)\]", written["points"].read_text())
    ]
    np.testing.assert_allclose(np.asarray(out), points + shift_mm, atol=1e-5)


def test_transformix_follows_initial_transform_chain(registered: dict, tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    base = registered["transforms"][0].read_text(encoding="utf-8")
    first.write_text(
        re.sub(r"\(TransformParameters [^)]*\)", "(TransformParameters 0.1 0 0)", base),
        encoding="utf-8",
    )
    text = re.sub(r"\(TransformParameters [^)]*\)", "(TransformParameters 0 0.2 0)", base)
    text = re.sub(
        r'\(InitialTransformParameters?FileName "[^"]*"\)',
        f'(InitialTransformParameterFileName "{first.as_posix()}")',
        text,
    )
    assert first.as_posix() in text
    second.write_text(text, encoding="utf-8")

    write_landmark_file(tmp_path / "pts.txt", np.array([[0.3, 0.3, 0.3]]))
    written = backend.transformix(
        transform_path=second,
        output_dir=tmp_path / "out",
        points_file=tmp_path / "pts.txt",
    )
    match = re.search(r"OutputPoint\s*=\s*\[([^\]]+)\]", written["points"].read_text())
    assert match is not None
    mapped = [float(v) for v in match.group(1).split()]
    np.testing.assert_allclose(mapped, [0.4, 0.5, 0.3], atol=1e-6)


def test_elastix_failure_raises_elastix_error(tmp_path: Path) -> None:
    write_mhd(np.ones((8, 8, 8), np.float32), tmp_path / "flat", [SPACING] * 3)
    param_path = tmp_path / "params.txt"
    write_parameter_file(param_path, _translation_params(Transform="NoSuchTransform"))
    with pytest.raises(backend.ElastixError):
        backend.elastix(
            fixed=tmp_path / "flat.mhd",
            moving=tmp_path / "flat.mhd",
            parameter_files=param_path,
            output_dir=tmp_path / "out",
        )
    # The helper survives a failed job.
    assert backend._WORKER is None or backend._WORKER.process.poll() is None


def test_cancel_stops_running_registration(registered: dict, tmp_path: Path) -> None:
    from lightsuite.exceptions import StageCancelledError
    from lightsuite.reporter import stage_cancellation

    work = registered["work"]
    param_path = tmp_path / "slow.txt"
    write_parameter_file(
        param_path,
        _translation_params(NumberOfResolutions=1, MaximumNumberOfIterations=10_000_000),
    )
    cancel = threading.Event()
    threading.Timer(2.0, cancel.set).start()
    started = time.perf_counter()
    with stage_cancellation(cancel), pytest.raises(StageCancelledError):
        backend.elastix(
            fixed=work / "fixed.mhd",
            moving=work / "moving.mhd",
            parameter_files=param_path,
            output_dir=tmp_path / "out",
        )
    assert time.perf_counter() - started < 30
    assert backend._WORKER is None


def test_require_elastix_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "elastix_available", lambda: False)
    with pytest.raises(RuntimeError, match="itk-elastix is required for brain register"):
        backend.require_elastix("brain register")


def test_unguarded_script_runs_once_and_exits(registered: dict, tmp_path: Path) -> None:
    """The helper must not re-run the caller's script, and must not block interpreter exit."""
    import subprocess
    import sys

    marker = tmp_path / "runs.txt"
    script = tmp_path / "user_script.py"
    transform = registered["transforms"][0]
    script.write_text(
        "from pathlib import Path\n"
        "from lightsuite.registration.elastix import backend\n"
        f"marker = Path({str(marker)!r})\n"
        "marker.write_text(marker.read_text() + 'x' if marker.exists() else 'x')\n"
        "backend.transformix(\n"
        f"    transform_path=Path({str(transform)!r}),\n"
        f"    output_dir=Path({str(tmp_path / 'out')!r}),\n"
        "    deformation_field=True,\n"
        ")\n"
        "print('done')\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "done" in proc.stdout
    assert "in this process instead" not in proc.stdout + proc.stderr
    assert marker.read_text() == "x"


def test_caller_threads_keep_running_during_registration(registered: dict, tmp_path: Path) -> None:
    """Elastix runs out of process, so it cannot hold this process's GIL (GUI stays live)."""
    work = registered["work"]
    param_path = tmp_path / "params.txt"
    write_parameter_file(
        param_path,
        _translation_params(NumberOfResolutions=1, MaximumNumberOfIterations=3000),
    )
    ticks = 0
    done = threading.Event()

    def tick() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            time.sleep(0.01)

    ticker = threading.Thread(target=tick)
    ticker.start()
    started = time.perf_counter()
    try:
        backend.elastix(
            fixed=work / "fixed.mhd",
            moving=work / "moving.mhd",
            parameter_files=param_path,
            output_dir=tmp_path / "out",
        )
    finally:
        done.set()
        ticker.join()
    elapsed = time.perf_counter() - started
    assert ticks > 0.3 * elapsed / 0.01


def test_forward_bspline_points_follow_transform_axes(tmp_path: Path) -> None:
    """Points move by the transform's physical (x, y, z) shift, in volume (y, x, z) order."""
    from lightsuite.import_.transform import apply_forward_bspline_to_points

    transform = tmp_path / "bspline_atlas_to_samp_20um.txt"
    transform.write_text(
        '(Transform "TranslationTransform")\n(NumberOfParameters 3)\n'
        "(TransformParameters 0.1 -0.04 0.02)\n"
        '(InitialTransformParameterFileName "NoInitialTransform")\n'
        '(HowToCombineTransforms "Compose")\n(FixedImageDimension 3)\n(MovingImageDimension 3)\n'
        '(FixedInternalImagePixelType "float")\n(MovingInternalImagePixelType "float")\n'
        "(Size 50 40 30)\n(Index 0 0 0)\n(Spacing 0.02 0.02 0.02)\n(Origin 0 0 0)\n"
        "(Direction 1 0 0 0 1 0 0 0 1)\n(UseDirectionCosines \"false\")\n",
        encoding="utf-8",
    )
    points_yxz = np.array([[10.0, 12.0, 5.0], [0.0, 0.0, 0.0]])
    out = apply_forward_bspline_to_points(points_yxz, transform, temp_dir=tmp_path / "pts")
    # x += 0.1 mm = 5 voxels, y -= 0.04 mm = 2 voxels, z += 0.02 mm = 1 voxel
    np.testing.assert_allclose(out, points_yxz + np.array([-2.0, 5.0, 1.0]), atol=1e-5)
