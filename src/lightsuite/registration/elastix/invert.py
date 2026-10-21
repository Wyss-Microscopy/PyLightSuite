"""Invert elastix transforms (invertElastixTransformCP.m port)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from lightsuite.registration.elastix import backend
from lightsuite.registration.elastix.mhd import read_mhd_spacing

GENTLE_PARAMETER_FILE = "inversion_parameters_gentle.txt"


def invert_elastix_transform(transform_dir: Path, output_dir: Path | None = None) -> Path:
    """Invert forward elastix B-spline transform; return inverted TransformParameters path."""
    transform_dir = transform_dir.expanduser()
    if output_dir is None:
        output_dir = transform_dir.parent / "elastix_inverse_temp"
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    # A failed earlier run leaves these behind; callers read the gentle file as
    # "this run fell back to the gentle schedule".
    _clear_inversion_workspace(output_dir)
    (output_dir / GENTLE_PARAMETER_FILE).unlink(missing_ok=True)

    fixed_mhd, param_paths = _forward_run_inputs(transform_dir)

    coef_files = sorted(transform_dir.glob("TransformParameters.*.txt"), reverse=True)
    if not coef_files:
        msg = f"No TransformParameters.*.txt in {transform_dir}"
        raise FileNotFoundError(msg)

    fixpath = transform_dir / "fixed.txt"
    movpath = transform_dir / "moving.txt"
    landmark_paths = (
        (fixpath, movpath)
        if fixpath.is_file() and movpath.is_file()
        else None
    )

    forward_param = param_paths[0]
    # Multithreaded elastix sums partial results in a load-dependent order, so a run that
    # diverges once (seen on a busy machine) usually converges when repeated.
    for attempt in range(2):
        if attempt:
            _clear_inversion_workspace(output_dir)
        inverted = _run_inversion_attempt(
            fixed_mhd=fixed_mhd,
            output_dir=output_dir,
            coef_path=coef_files[0],
            param_path=forward_param,
            landmark_paths=landmark_paths,
        )
        if inverted is not None:
            return _finalize_inverted_transform(inverted)

    gentle_param = _build_gentle_inversion_parameter_file(
        forward_param,
        output_dir / GENTLE_PARAMETER_FILE,
    )
    _clear_inversion_workspace(output_dir)
    inverted = _run_inversion_attempt(
        fixed_mhd=fixed_mhd,
        output_dir=output_dir,
        coef_path=coef_files[0],
        param_path=gentle_param,
        landmark_paths=landmark_paths,
    )
    if inverted is None:
        msg = (
            f"elastix inversion failed with the forward (twice) and gentle schedules in "
            f"{output_dir}. "
            "See elastix.log in that directory."
        )
        raise RuntimeError(msg)
    return _finalize_inverted_transform(inverted)


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
    landmark_paths: tuple[Path, Path] | None,
) -> Path | None:
    """Run one inversion attempt; return TransformParameters path or None on failure.

    MATLAB invertElastixTransformCP registers the fixed image to itself (one image for
    all metrics), starting from the forward transform.
    """
    _ = read_mhd_spacing(fixed_mhd)
    try:
        inverted = backend.elastix(
            fixed=fixed_mhd,
            moving=fixed_mhd,
            parameter_files=param_path,
            output_dir=output_dir,
            initial_transform=coef_path,
            fixed_points=landmark_paths[0] if landmark_paths is not None else None,
            moving_points=landmark_paths[1] if landmark_paths is not None else None,
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


def _build_gentle_inversion_parameter_file(forward_param: Path, destination: Path) -> Path:
    """Single-resolution schedule for inversion when the forward multistep run diverges."""
    text = forward_param.read_text(encoding="utf-8", errors="replace")
    n_metrics = _count_forward_metrics(text)
    sample_triple = _last_sample_region_triple(text)
    pyramid = " ".join(["1"] * 3 * n_metrics)
    sample_region = " ".join(sample_triple for _ in range(n_metrics))

    text = _replace_elastix_param_line(text, "NumberOfResolutions", "1")
    text = _replace_elastix_param_line(text, "ImagePyramidSchedule", pyramid)
    text = _replace_elastix_param_line(text, "MaximumNumberOfIterations", "500")
    text = _replace_elastix_param_line(text, "SampleRegionSize", sample_region)
    text = _replace_elastix_param_line(text, "SP_a", "200")
    text = _replace_elastix_param_line(text, "SP_alpha", "0.6")
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


def _count_forward_metrics(text: str) -> int:
    n_mi = text.count("AdvancedMattesMutualInformation")
    n_landmark = text.count("CorrespondingPointsEuclideanDistanceMetric")
    if n_mi == 0 and n_landmark == 0:
        return 1
    return max(n_mi + n_landmark, 1)


def _replace_elastix_param_line(text: str, key: str, value: str) -> str:
    """Replace one elastix parameter with a numeric / space-separated value list."""
    pat = re.compile(rf"^\s*\(\s*{re.escape(key)}\s+[^\)]*\)\s*$", re.MULTILINE | re.IGNORECASE)
    text = pat.sub("", text)
    return text.rstrip() + f"\n({key} {value})\n"


def _last_sample_region_triple(text: str) -> str:
    match = re.search(r"\(\s*SampleRegionSize\s+([^\)]+)\)", text, flags=re.IGNORECASE)
    if not match:
        return "2 2 2"
    values = [float(v) for v in match.group(1).split()]
    if len(values) >= 3:
        return " ".join(f"{v:g}" for v in values[-3:])
    return "2 2 2"


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
