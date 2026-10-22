# PyLightSuite

[![Python](https://img.shields.io/badge/Python-3.11--3.12-blue.svg)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Upstream: LightSuite (MATLAB)](https://img.shields.io/badge/upstream-LightSuite%20(MATLAB)-orange.svg)](https://github.com/dimokaramanlis/LightSuite)

**PyLightSuite is a Python port of [LightSuite](https://github.com/dimokaramanlis/LightSuite)**, the MATLAB framework by Karamanlis et al. for scalable atlas registration and cell detection across the central nervous system.

It registers large microscopy volumes to standard anatomical atlases and exports atlas-space intensities, parcellation tables, and cell coordinates. The Python pipeline (installed as the `lightsuite` package and CLI) targets 100 GB+ lightsheet datasets and runs **without a MATLAB license**.

| | Link |
|---|---|
| **This repository (Python)** | [`docs/`](docs/index.md): installation, usage guides, configuration reference |
| **Original MATLAB LightSuite** | [github.com/dimokaramanlis/LightSuite](https://github.com/dimokaramanlis/LightSuite) · [lightsuite.readthedocs.io](https://lightsuite.readthedocs.io/en/latest/) |

> **Note:** `lightsuite.readthedocs.io` documents the **MATLAB** toolbox. Python usage is documented in this repository's [`docs/`](docs/index.md) folder.

---

## What the Python port does

PyLightSuite is a command-line pipeline with optional **Napari** GUIs for manual refinement. Configuration is YAML-based, and stages can be resumed from JSON checkpoints.

| Workflow | CLI | Summary |
|----------|-----|---------|
| **Brain lightsheet** | `lightsuite brain` | Preprocess → orientation check → coarse alignment → control-point matching → B-spline registration (Elastix) → export registered volumes and regional intensity tables. Supports the Allen CCF v3 and Perens LSFM atlases. |
| **Spinal cord** | `lightsuite spinal` | Preprocess → straighten (central canal / A–P axis) → register to the Fiederling spinal atlas → export → per-sample region statistics and cord-specific plots. |
| **External annotations** | `lightsuite brain import-annotations` | Import spot lists (`points.csv`) or masks (TIFF) after registration and map them to atlas regions. |

**Inputs:** multi-page TIFF stacks (channel-per-file or plane-per-file layouts).
**Outputs:** atlas-space registered volumes (multi-page TIFF), parcellation intensity CSVs, optional annotation transforms.

Interactive stages (orientation, match-points, registration review) are available through `lightsuite gui -c config.yaml` or the per-stage Napari tools.

---

## Quick start
After full installation: 

```bash
cd PyLightSuite
uv sync --extra dev --extra gui --extra atlas

cp examples/brain_lightsheet.yaml my_sample.yaml
# edit paths and voxel size

uv run lightsuite doctor -c my_sample.yaml
uv run lightsuite brain run -c my_sample.yaml --through match-points
```

Example configs are indexed in [`examples/README.md`](examples/README.md) (brain, spinal cord).

**Requirements:** Python 3.11–3.12, [Elastix 5.1.0](https://github.com/SuperElastix/elastix/releases/tag/5.1.0) on `PATH`, and atlas NIfTI files. See the [installation guide](docs/installation.md) and [Hardware requirements](#hardware-requirements) below.

---

## Hardware requirements

The Python pipeline is bound by **CPU, RAM, and fast local disk**. **The GPU is not used** (`compute.use_gpu` is reserved and ignored). Built-in cell detection is not implemented in Python.

Run `uv run lightsuite doctor -c <config.yaml>` before long jobs. It checks Elastix, scratch free space (≥50 GB), atlas paths, and common config mistakes.

Use a **fast local SSD/NVMe** for `sample.scratch`, not a network mount. Elastix and transformix temporary files also go to scratch (spinal: `sample.scratch/<sample.name>/`).

| Profile | CPU | RAM | Scratch disk | Notes |
|---------|-----|-----|--------------|-------|
| Spinal cord, 1–2 channels | 4+ cores | 16 GB | 50 GB SSD | Register/export usually fit in 16 GB. |
| Whole brain, 20 µm, 1–2 channels | 8+ cores | 32–64 GB | 100–200 GB SSD | `register` is the RAM peak. |
| Large brain / wide field / dual-channel MI | 8–16 cores | 64–128 GB | 200–500 GB SSD | Content crop and B-spline tuning are often required. |

Per-step bottlenecks, sizing guidance, and remediation options are in the [installation guide](docs/installation.md#hardware-and-storage) and the workflow guides ([brain](docs/usage_lightsheet_brain.md), [spinal cord](docs/usage_spinal_cord.md)).

---

## Python port vs original MATLAB LightSuite

The [original MATLAB toolbox](https://github.com/dimokaramanlis/LightSuite) covers whole-brain lightsheet, spinal cord, widefield coronal slices, functional ultrasound (fUS), built-in cell detection, and probe/implant tracing. This repository is a **focused Python port of the registration core**. The MATLAB code is not included here.

| | **PyLightSuite (this repo)** | **LightSuite (MATLAB)** |
|---|---------------------------|---------------------|
| **Runtime** | Python 3.11–3.12, CLI + Napari GUIs | MATLAB R2022b+, MATLAB GUIs |
| **Configuration** | YAML files | Script-based `opts` structs |
| **Brain registration** | Yes | Yes |
| **Spinal cord registration** | Yes (per-sample stats) | Yes (+ cohort NNMF analysis) |
| **Built-in 3D cell detection** | No (import external spots) | Yes |
| **Widefield coronal slice module** | No | Yes |
| **Functional ultrasound (fUS)** | No | Yes |
| **Probe & implant tracing** | No | Yes |
| **GPU-accelerated detection** | No | Yes |
| **CZI reader** | No (export to TIFF first) | Yes |

See [Brain lightsheet → Migrating from MATLAB](docs/usage_lightsheet_brain.md#migrating-from-matlab) for the command mapping and MATLAB interoperability helpers.

---

## Documentation

| Guide | Contents |
|-------|----------|
| [Home](docs/index.md) | Overview, quick start, documentation map |
| [Installation](docs/installation.md) | Python, `uv`, Elastix, BCPD, atlas files |
| [Brain lightsheet](docs/usage_lightsheet_brain.md) | YAML config, CLI stages, outputs, GUI |
| [Spinal cord](docs/usage_spinal_cord.md) | Straightening, registration, region statistics |
| [Annotation import](docs/annotation_import.md) | Bringing external spots and masks into atlas space |
| [Registration spaces](docs/registration_spaces.md) | Atlas-space vs sample-space outputs |
| [Configuration reference](docs/config_reference.md) | Every YAML key, with GUI/YAML availability |

The `docs/` folder is also an [MkDocs](https://www.mkdocs.org/) site: `uv run --with-requirements docs/requirements.txt mkdocs serve`.

---

## Credits, citation & license

PyLightSuite was created by Louis Pivron (louis.pivron@epfl.ch), derived from **LightSuite** by Karamanlis D, Xie Y, Foucher CG, Alappat M, Masood A, Dimitropoulou I, Pivron LAF, Martínez de Paz JM, Macé E, Gaspari S, and El-Boustani S. Algorithms, default parameters, and atlas conventions follow the MATLAB implementation unless noted otherwise in the docs.

If you use PyLightSuite in your research, please cite the original work:

> Karamanlis D, *et al.* (2026) **LightSuite: scalable atlas registration and cell detection across the central nervous system.** doi: [10.64898/2026.10.13.757638](https://www.biorxiv.org/content/10.64898/2026.10.13.757638v1)

Until the preprint is available, please also link to this repository when you use the Python port.

- **Python port issues:** [PyLightSuite issues](https://github.com/Wyss-Microscopy/PyLightSuite/issues)
- **MATLAB toolbox issues:** [LightSuite issues](https://github.com/dimokaramanlis/LightSuite/issues)
- **License:** [GPL-3.0](LICENSE), the same license as the original LightSuite
