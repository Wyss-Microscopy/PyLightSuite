# Examples

## Config templates

| Workflow | Template | Guide |
|----------|----------|-------|
| Brain lightsheet | [`brain_lightsheet.yaml`](brain_lightsheet.yaml) | [Brain lightsheet](../docs/usage_lightsheet_brain.md) |
| Spinal cord | [`spinal_cord.yaml`](spinal_cord.yaml) | [Spinal cord](../docs/usage_spinal_cord.md) |

Copy a template next to your data or into the repository folder, replace every `/path/to/...`, then check it:

```bash
cp examples/brain_lightsheet.yaml my_mouse.yaml
uv run lightsuite brain validate-config -c my_mouse.yaml
uv run lightsuite doctor -c my_mouse.yaml
```

`validate-config` reports every placeholder path that does not exist yet. You can also build a config interactively with `uv run lightsuite config init brain` (or `spinal`), or with the **From template** button in the Config panel of `uv run lightsuite gui`.

Every key is described in the [configuration reference](../docs/config_reference.md).

## Annotation import

- [`annotation_sample/points.csv`](annotation_sample/points.csv): a minimal points file in LightSuite Sample Space.
- [`annotation_sample/custom_converter_example.py`](annotation_sample/custom_converter_example.py): a template for `import.converter.suite: custom`.

See the [annotation import guide](../docs/annotation_import.md).

## Notebooks

[`notebooks/`](notebooks/) holds Jupyter notebooks that convert vendor cell-detection exports to `points.csv`:

| Notebook | Source |
|----------|--------|
| [`convert_arivis_to_lightsuite.ipynb`](notebooks/convert_arivis_to_lightsuite.ipynb) | Arivis |
| [`convert_imaris_to_lightsuite.ipynb`](notebooks/convert_imaris_to_lightsuite.ipynb) | Imaris |
| [`convert_smartspim_detection_to_lightsuite.ipynb`](notebooks/convert_smartspim_detection_to_lightsuite.ipynb) | SmartSPIM |

The same conversions are available from the command line: `lightsuite brain convert-annotations`, `convert-smartspim-points`, `convert-fiji-points`, and `lightsuite spinal convert-imaris-spots`.

To run the notebooks: `uv run --with jupyter jupyter lab examples/notebooks`.
