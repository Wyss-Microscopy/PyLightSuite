"""Invert elastix transforms (matlab_elastix invertElastixTransformCP.m port)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from lightsuite.registration.elastix import backend
from lightsuite.registration.elastix.mhd import read_mhd_spacing

INVERSION_PARAMETER_FILE = "inversion_parameters.txt"
# invertElastixTransformCP iterfac: iterations = forward final level x this factor.
INVERSION_ITERATION_FACTOR = 1.5
# Components elastix creates once per metric; the inversion has a single metric.
_PER_METRIC_KEYS = ("FixedImagePyramid", "MovingImagePyramid", "ImageSampler", "Interpolator")


def invert_elastix_transform(transform_dir: Path, output_dir: Path | None = None) -> Path:
    """Invert a forward elastix B-spline transform; return the inverse TransformParameters.

    Follows matlab_elastix ``invertElastixTransformCP`` (2026-09): register the forward
    fixed image to itself, starting from the forward transform, with
    ``DisplacementMagnitudePenalty`` (which drives T(x) towards x) at the finest pyramid
    level only. No image metric and no landmarks are used.
    """
    transform_dir = transform_dir.expanduser()
    if output_dir is None:
        output_dir = transform_dir.parent / "elastix_inverse_temp"
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_inversion_workspace(output_dir)

    fixed_mhd, param_paths = _forward_run_inputs(transform_dir)
    coef_files = sorted(transform_dir.glob("TransformParameters.*.txt"), reverse=True)
    if not coef_files:
        msg = f"No TransformParameters.*.txt in {transform_dir}"
        raise FileNotFoundError(msg)

    param_path = output_dir / INVERSION_PARAMETER_FILE
    param_path.write_text(
        build_inversion_parameters(param_paths[0].read_text(encoding="utf-8", errors="replace")),
        encoding="utf-8",
    )

    # Multithreaded elastix sums partial results in a load-dependent order; repeat once
    # before giving up.
    for attempt in range(2):
        if attempt:
            _clear_inversion_workspace(output_dir)
        inverted = _run_inversion_attempt(
            fixed_mhd=fixed_mhd,
            output_dir=output_dir,
            coef_path=coef_files[0],
            param_path=param_path,
        )
        if inverted is not None:
            return _finalize_inverted_transform(inverted)
    msg = f"elastix inversion failed twice in {output_dir}. See elastix.log in that directory."
    raise RuntimeError(msg)


def build_inversion_parameters(
    forward_text: str,
    *,
    iteration_factor: float = INVERSION_ITERATION_FACTOR,
) -> str:
    """Inversion parameter file from the forward one (invertElastixTransformCP edits).

    MATLAB edits: ``Registration`` -> MultiResolutionRegistration, ``Metric`` ->
    DisplacementMagnitudePenalty, ``NumberOfResolutions`` -> 1,
    ``ImagePyramidSchedule`` -> its last (finest) level, ``MaximumNumberOfIterations`` ->
    last level x 1.5. Everything else (grid spacing, sampler, sample region, optimizer
    settings) is kept from the forward run, as in MATLAB. Python additionally keeps only
    the first entry of per-metric component lists (MATLAB's forward runs never have them)
    and skips writing the result image, which only costs time.
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
