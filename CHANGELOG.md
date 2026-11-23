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

### Changed

- Elastix and transformix now run through the `itk-elastix` Python package (a core
  dependency) instead of the Elastix 5.1.0 binaries, so there is no separate Elastix install
  or `PATH` setup. Elastix runs in a helper process, so the GUI stays responsive during
  registration and Cancel stops a running registration. The bundled elastix is 5.3:
  applying existing transforms gives identical results; new registrations differ from 5.1
  by a few µm (more for inverse B-splines in poorly constrained regions).
  `lightsuite doctor` checks for `itk-elastix` instead of the binaries.
- B-spline inversion now matches matlab_elastix `invertElastixTransformCP` (2026-09):
  `DisplacementMagnitudePenalty` only, finest pyramid level only, no landmarks, the forward
  run's `SP_A`, and the forward run's final iterations x 2 for brains (as in
  `multiobjRegistration.m`) or x 1.5 for the spinal cord. Previously Python re-ran the
  forward metrics (mutual information, landmarks, bending energy) over all levels, which
  could diverge and took ~2.5x longer. On seven brains the inversion takes 45-55 s and
  maps tissue points back within a median of 2-7 µm (forward then inverse). After
  inverting, the pipeline checks this round trip on 3000 tissue points; if the median is
  above 25 µm (a diverged run), it reruns with `SP_A` 1 and keeps the better inverse
  (`inversion_report.json` in the work folder records which). The "gentle" fallback
  schedule is gone; a failed elastix run is retried once.
- B-spline registration has no bending-energy penalty and uses 48 histogram bins, as in
  MATLAB `performMultObjBsplineRegistration.m` (Python used a penalty of weight 2.0 and 32
  bins). `registration.bspline_bending_weight` is no longer a setting: older configs still
  load, and a non-zero value warns that it is ignored.

### Fixed

- Brain and spinal cord point import (`import-annotations`) mapped points through the
  inverse B-spline with the x and y displacement components swapped, misplacing cells by up
  to hundreds of µm (on one brain, a third of the cells landed outside the brain). Points
  are now pushed through the forward B-spline with transformix, which is exact; MATLAB's
  `transformPointsToAtlas` uses a first-order approximation instead (median error ~19 µm
  on the deposited TRAP2 brains). Registered volumes and intensity tables were not affected.
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
