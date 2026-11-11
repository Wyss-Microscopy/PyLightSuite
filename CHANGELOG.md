# Changelog

All notable changes to PyLightSuite (the `lightsuite` Python package) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`info_results.md`**: after every stage, brain and spinal workflows write a guide to the
  files in `save_path`: what each file is, which stage wrote it, the metrics and channels in
  the statistics tables, and what their columns mean. Rebuild it with
  `lightsuite brain info` / `lightsuite spinal info`.
- **Docs**: new [output files](docs/outputs.md) page, generated from the same list of outputs.

### Fixed

- Docs: brain QC previews are written directly in `qc/`, not in `qc/initial/`, `qc/affine/`
  and `qc/bspline/`.

## [0.1.0] - 2026-10-19

Initial public release: a Python port of the registration core of the
[original MATLAB LightSuite](https://github.com/dimokaramanlis/LightSuite).

### Added

- **Brain lightsheet workflow** (`lightsuite brain`): preprocess, orientation check, slice
  alignment, coarse BCPD initialisation, control-point matching, Elastix B-spline registration,
  export to atlas and sample space, and regional intensity tables. Allen CCF v3 and Perens LSFM
  atlases, plus BrainGlobe atlases via the `atlas` extra.
- **Spinal cord workflow** (`lightsuite spinal`): preprocess, straightening, longitudinal
  alignment, registration to the Fiederling spinal cord atlas, export, per-sample region
  statistics and cord plots.
- **Annotation import**: bring external cell detections (Imaris, FIJI, Arivis, SmartSPIM, or
  custom converters) and masks into atlas space.
- **Pipeline orchestration**: `run` with `--from`, `--through` and `--resume`; `stages`
  checkpoint tables; resumable JSON checkpoints.
- **Napari GUI** (`lightsuite gui`): unified shell with a config editor and interactive stages.
- **Helpers**: `lightsuite doctor`, `lightsuite config init|explain|schema`,
  `lightsuite workflow list`, and import of MATLAB `regopts.mat` / control-point sessions.

### Known gaps vs MATLAB

Built-in cell detection, the widefield slice module, the CZI reader, spinal cohort NNMF analysis
and GPU-accelerated detection remain available only in the original MATLAB LightSuite.
