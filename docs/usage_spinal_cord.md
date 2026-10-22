# Spinal cord

This guide registers a stitched spinal cord lightsheet volume to the [Fiederling et al. (2021)](https://www.sciencedirect.com/science/article/pii/S2667237521001260) spinal cord atlas and computes per-region, per-segment statistics.

Before you start, finish the [installation](installation.md) with the `gui` extra and Elastix 5.1.0, and download the [Fiederling atlas](installation.md#fiederling-spinal-cord-atlas). BCPD is not used by the spinal workflow.

For whole brains, see [Brain lightsheet](usage_lightsheet_brain.md).

---

## Pipeline overview

| # | Stage | Kind | Writes |
|:-:|-------|------|--------|
| 1 | `check-orientation` | GUI | `cord_orientation.txt` |
| 2 | `preprocess` | Automatic | `regopts.json`, `sample_reference.json`, `cache/` |
| 3 | `straighten` | GUI | `spinal_alignment_opt.json` |
| 4 | `align-longitudinal` | GUI | `longitudinal_correspondence.json` |
| 5 | `init-registration` | Automatic | straightened volume, coarse affine |
| 6 | `match-points` | GUI, optional | `corresponding_points.json` |
| 7 | `register` | Automatic | `transform_params.json`, `transforms/`, `qc/` PNGs |
| 8 | `export` | Automatic | `volume_registered/`, `stats/region_stats.csv` |
| 9 | `import-annotations` | Automatic, optional | atlas coordinates for external cells |
| 10 | `region-stats` | Automatic | `stats/` tables (also run by export and import) |
| 11 | `view` | GUI, optional | (review only) |

Every command takes the config with `-c`: `uv run lightsuite spinal <stage> -c my_spinal.yaml`.

---

## Configuration

```bash
cp examples/spinal_cord.yaml my_spinal.yaml
```

The keys you must set:

```yaml
sample:
  name: SC001
  source:
    format: tiff_stack
    tiff_type: planeperfile           # one TIFF per Z plane; or channelperfile
    channels:                         # one folder per channel, in channel order
      - /data/SC001/Ch1_stitched
      - /data/SC001/Ch2_stitched
  scratch: /fastssd/scratch/SC001
  save_path: /data/SC001/lightsuite
  voxel_um: [1.8, 1.8, 4.0]           # native microscope voxel size [x, y, z], required

atlas:
  atlas_dir: /atlases/SC_P56_Atlas_10x10x20_v5_2020

registration:
  resolution_um: 20
  channel_primary: 2                  # structural / autofluorescence channel
```

`sample.voxel_um` is the **native** microscope voxel size, not the 20 µm registration grid. Plane-per-file stacks are downsampled while loading, so a wrong value here wastes memory and time. `lightsuite doctor -c` warns when `voxel_um` equals `registration.resolution_um`.

For a single channel, use `source.path` with `tiff_type: channelperfile` instead of `channels`. Other `tiff_type` values: `auto` (detect) and `multichannel_single` (one Bio-Formats-style stack). When channel folders have different plane counts, only planes present in every channel (matched by the number in the file name) are loaded. Set `sample.source.skip_corrupt_slices: true` to skip a few unreadable planes instead of failing.

Check the config:

```bash
uv run lightsuite spinal validate-config -c my_spinal.yaml
uv run lightsuite doctor -c my_spinal.yaml
```

Every key is listed in the [configuration reference](config_reference.md).

---

## Running the whole pipeline

`check-orientation` must be done once first (or set `registration.longitudinal_direction` in the YAML). Then:

```bash
uv run lightsuite spinal check-orientation -c my_spinal.yaml
uv run lightsuite spinal run -c my_spinal.yaml
uv run lightsuite spinal stages -c my_spinal.yaml       # checklist of done / pending stages
```

`run` accepts `--from`, `--through` and `--resume` like the brain workflow. Or run every stage from one window with `uv run lightsuite gui -c my_spinal.yaml`.

---

## Stage by stage

### 1. Check orientation (GUI)

```bash
uv run lightsuite spinal check-orientation -c my_spinal.yaml
```

A window shows longitudinal maximum projections of the atlas (left, rostral at the top) and the sample (right). Click **Rostrocaudal** or **Caudorostral** until the sample's rostral end matches the atlas, then **Save**.

This writes `cord_orientation.txt`. Instead, you can set `registration.longitudinal_direction: rostrocaudal` (or `caudorostral`) in the YAML. **Preprocess fails if neither is set.**

The first run also caches downsampled channels under `cache/`, so preprocess does not read the raw stack a second time.

### 2. Preprocess

```bash
uv run lightsuite spinal preprocess -c my_spinal.yaml
```

Caches every channel as `cache/chan_N_sample_register_20um.tif` and writes `regopts.json` and `sample_reference.json`.

### 3. Straighten (GUI)

Trace the cord so it can be straightened along its length.

```bash
uv run lightsuite spinal straighten -c my_spinal.yaml
```

1. Select the **center**, **anterior** or **posterior** layer in Napari's layer list, then click on the slice to place that point: the central canal, then the anterior and posterior edges.
2. Move through slices with the slider, **←/→**, **PgUp/PgDn** or the mouse wheel. Annotate slices spread along the whole cord.
3. From two annotated slices on, a fit preview appears (yellow axis, cyan centre, green/red predicted edges). Toggle it with **P**.
4. **Clear last** undoes the last point, **Clear all** removes everything.
5. Save with **S** (or **Save (s)**).

### 4. Align longitudinal (GUI)

Matches straightened sample slices to atlas planes along the cord. This matters when the imaged cord is shorter than the atlas.

```bash
uv run lightsuite spinal align-longitudinal -c my_spinal.yaml
```

The sample slice is fixed on the left. Scroll the atlas on the right with **PgUp/PgDn**, the spinbox or the mouse wheel. Confirm about 20 anchors with **Enter** (or **Confirm anchor**), then **Save && Close**.

### 5. Init registration

```bash
uv run lightsuite spinal init-registration -c my_spinal.yaml
```

Applies the straightening and a coarse affine alignment to the atlas.

### 6. Match control points (GUI, optional)

```bash
uv run lightsuite spinal match-points -c my_spinal.yaml
```

Same controls as the [brain match-points stage](usage_lightsheet_brain.md#5-match-control-points-gui-optional).

### 7. Register

```bash
uv run lightsuite spinal register -c my_spinal.yaml
```

Runs the Elastix B-spline registration. Check the overlay PNGs in `qc/`. Elastix temporary files go to `<sample.scratch>/<sample.name>/`.

### 8. Export

```bash
uv run lightsuite spinal export -c my_spinal.yaml
uv run lightsuite spinal export -c my_spinal.yaml --space both
```

Writes the registered channels (`chanNN_channelN.tiff`), `template_registered.tiff` and `annotation_registered.tiff` under `volume_registered/`, on the atlas grid (10 × 10 × 20 µm). `--space sample` or `both` also writes the atlas labels warped onto the straightened sample under `volume_registered/sample_space/` ([registration spaces](registration_spaces.md)). Region statistics are computed at the end of export.

### 9. Import annotations (optional)

Maps cells from an external detection tool into atlas space.

```bash
uv run lightsuite spinal import-annotations -c my_spinal.yaml
```

Imaris spot exports need converting first:

```bash
uv run lightsuite spinal convert-imaris-spots \
  -s /path/to/Spot_OnePageMultiComponent_Detailed.csv \
  -o /path/to/converted \
  --voxel-um 1,1,1.8 \
  --shape-yxz 10234,2006,837
```

`--voxel-um` is the voxel size **as Imaris knows it** (Image Properties → Geometry → Voxel Size), which is not necessarily `sample.voxel_um`:

| Imaris voxel size | Use `--voxel-um` |
|-------------------|------------------|
| Same as the microscope, e.g. 1.8³ µm | `1.8,1.8,1.8` |
| 1 µm³ and the same image size as PyLightSuite | `1,1,1` |
| 1 µm³ in XY, but more Z planes than PyLightSuite (Imaris kept the full acquisition) | `1,1,1.8` (Z scaled to the PyLightSuite grid) |

A wrong value shrinks the coordinates, and most spots then land outside the cord. Pass `--shape-yxz` (from `sample_reference.json`) to get a warning when the units look wrong, and check the result with `spinal view`. Other formats are described in [annotation import](annotation_import.md).

### 10. Region statistics

```bash
uv run lightsuite spinal region-stats -c my_spinal.yaml
uv run lightsuite spinal region-stats -c my_spinal.yaml --no-count-points
uv run lightsuite spinal region-stats -c my_spinal.yaml --no-parcellate-intensities
```

Combines intensities from the exported channels and counts of imported points per Fiederling region and rostrocaudal segment (C1 … Co2). Export and import-annotations already run it; run it again after changing the `analysis` settings.

```yaml
analysis:
  parcellate_intensities: true
  intensity_channels: [1, 2]      # omit for all channels
  relative_intensity_to: none     # or background: adds relative_median_intensity
  count_points: true
  point_labels: [imaris_TAyellow] # omit for all imports
  hemisphere_flip: false          # set true if left/right look swapped
```

Outputs in `stats/`:

| File | Contents |
|------|----------|
| `region_stats.csv` | Long table: every region × segment × hemisphere × metric, plus rollups |
| `region_stats_structure.csv` | Rollup to structures (combined laminae and funiculi) |
| `region_stats_division.csv` | Rollup to grey / white matter |
| `region_stats_horn.csv` | Rollup to dorsal horn / ventral horn / central |
| `region_stats_top10.csv` | Top regions per channel |
| `chanNN_region_stats.csv` | Per-channel intensities |
| `{label}_region_counts.csv` | Per-import cell counts |

Rows carry `segment` (e.g. `C5`, `L3`), the region name and acronym, `hemisphere` (`left`, `right`, `whole`), `rollup_level` (`region`, `structure`, `division`, `horn`), `metric` and `value`. Metrics are `median_intensity`, `std`, `volume_mm3`, `cell_count`, `cell_density` and optionally `relative_median_intensity`.

Plot a segment × region heatmap:

```bash
uv run lightsuite spinal plot-heatmap -c my_spinal.yaml --metric cell_density --channel imaris_TAyellow
```

### 11. View (GUI, optional)

```bash
uv run lightsuite spinal view -c my_spinal.yaml                  # atlas space
uv run lightsuite spinal view -c my_spinal.yaml --space sample   # needs export --space sample
```

Shows the registered channels, the warped atlas template and labels, and imported spots. The **stats / plots** panel reads `stats/region_stats.csv`: choose a segment range, compare left and right, and save the displayed matrix as CSV. `inspect-imports` is the same viewer; add `--headless` to only validate the inputs.

---

## Output layout

```
<save_path>/
├── cord_orientation.txt                  # check-orientation
├── regopts.json                          # preprocess, init-registration
├── sample_reference.json                 # native grid for annotation import
├── spinal_alignment_opt.json             # straighten
├── longitudinal_correspondence.json      # align-longitudinal
├── transform_params.json                 # register
├── cache/                                # downsampled and straightened volumes
├── transforms/                           # Elastix parameter files
├── qc/                                   # registration overlay PNGs
├── corresponding_points.json             # match-points
├── volume_registered/                    # export (+ sample_space/, imported coordinates)
├── stats/                                # region statistics
└── plots/                                # plot-heatmap figures
```

---

## Differences from the brain workflow

- Orientation is only rostrocaudal vs caudorostral, and it is set **before** preprocess.
- The cord is straightened before registration.
- The atlas is the Fiederling TIFF + CSV package, not Allen or Perens NIfTI files.
- Statistics are per segment, with left/right from the atlas hemisphere mask.

Spinal cohort NNMF analysis is only in the [original MATLAB LightSuite](https://github.com/dimokaramanlis/LightSuite) (`example_analysis_spinal_cord.m`).

## Developer check

`uv run lightsuite spinal validate-parity` compares the automated stages with the MATLAB reference fixtures in `tests/fixtures/spinal_cord`.
