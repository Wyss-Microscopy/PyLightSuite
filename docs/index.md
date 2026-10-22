# PyLightSuite

**PyLightSuite** (the `lightsuite` Python package) registers large lightsheet microscopy volumes to standard anatomical atlases, then exports atlas-space volumes, per-region intensity tables, and imported cell coordinates. It runs from the command line (`lightsuite`), and its interactive stages open in [Napari](https://napari.org).

It is a Python port of the registration core of the [original MATLAB LightSuite](https://github.com/dimokaramanlis/LightSuite).

## Workflows

| Workflow | Stages | Guide |
|----------|--------|-------|
| **Brain lightsheet** | preprocess → check-orientation → init-registration → align-slices\* → match-points\* → register → export → view-registration\* | [Brain lightsheet](usage_lightsheet_brain.md) |
| **Spinal cord** | check-orientation → preprocess → straighten → align-longitudinal → init-registration → match-points\* → register → export → region-stats | [Spinal cord](usage_spinal_cord.md) |
| **External annotations** | convert-annotations → import-annotations (after register) | [Annotation import](annotation_import.md) |

\* optional stage.

## Start here

1. **[Install](installation.md)** PyLightSuite, Elastix, BCPD and an atlas.
2. Copy one of config.yaml template from [`examples/`](https://github.com/Wyss-Microscopy/PyLightSuite/tree/main/examples) and edit its paths. Or create it using the GUI by filling the paths and selecting the options. 
3. Check the setup: `uv run lightsuite doctor -c my_sample.yaml`.
4. Follow the workflow guide, or run all stages in order with `uv run lightsuite brain run -c my_sample.yaml` (or `spinal run`).

```bash
uv run lightsuite brain stages -c my_sample.yaml     # checklist of done / pending stages
uv run lightsuite gui -c my_sample.yaml              # run stages from one Napari window
```

## Supported data

| | Supported |
|---|-----------|
| **Input** | Stitched TIFF: one multi-page TIFF per channel (`channelperfile`, eg. mesoSPIM) or one TIFF per Z plane (`planeperfile`, e.g. Terastitcher output, SmartSPIM) |
| **Brain atlases** | Allen CCF v3 (10 µm), Perens LSFM (20 µm), BrainGlobe atlases (`atlas` extra) |
| **Spinal atlas** | Fiederling et al. 2021 |
| **Output** | Multi-page TIFF volumes, CSV/JSON region tables, NPZ/CSV coordinates |

CZI and Imaris `.ims` volumes are not read directly, export them to TIFF first.

## Not in PyLightSuite

Built-in cell detection, the widefield coronal slice module, probe tracing, the CZI reader and spinal cohort NNMF analysis remain in the [original MATLAB LightSuite](https://github.com/dimokaramanlis/LightSuite). For cell counts, run any detection tool and bring its output in with [annotation import](annotation_import.md).

## All guides

1. **[Installation](installation.md)**: Python environment, Elastix, BCPD, atlases, tests, troubleshooting
2. **[Brain lightsheet](usage_lightsheet_brain.md)**: brain config, every stage, outputs, tuning
3. **[Spinal cord](usage_spinal_cord.md)**: spinal config, every stage, region statistics
4. **[Annotation import](annotation_import.md)**: external cells and masks into atlas space
5. **[Registration spaces](registration_spaces.md)**: atlas space vs sample space outputs
6. **[Configuration reference](config_reference.md)**: every YAML key and its default

## Getting help

Run `uv run lightsuite doctor` first. If the problem remains, open an [issue on GitHub](https://github.com/Wyss-Microscopy/PyLightSuite/issues) with your OS, the `doctor` output, the config file (paths can be redacted) and the full error message.
