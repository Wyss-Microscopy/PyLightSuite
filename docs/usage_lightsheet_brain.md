# Brain lightsheet

This guide takes a stitched whole-brain lightsheet volume to atlas-space registered volumes and per-region intensity tables.

Before you start, finish the [installation](installation.md): PyLightSuite with the `gui` extra, Elastix 5.1.0, BCPD, and an Allen, Perens or BrainGlobe atlas. You also need:

- the stitched TIFF data (Terastitcher, BigStitcher, SmartSPIM or similar),
- the native **voxel size** in µm, as `[x, y, z]`,
- a fast local scratch folder with enough free space ([hardware](installation.md#hardware-and-storage)).

---

## Pipeline overview

| # | Stage | Kind | Writes |
|:-:|-------|------|--------|
| 1 | `preprocess` | Automatic | `chan_N_sample_register_20um.tif`, `regopts.json`, `sample_reference.json` |
| 2 | `check-orientation` | GUI | `brain_orientation.txt` |
| 3 | `init-registration` | Automatic | `regopts.json` (coarse transform), `qc/initial/` previews |
| 4 | `align-slices` | GUI, optional | `slice_correspondence.json` |
| 5 | `match-points` | GUI, optional | `atlas2histology_tform.json` |
| 6 | `register` | Automatic | `transform_params.json`, B-spline files, `qc/affine/` and `qc/bspline/` previews |
| 7 | `export` | Automatic | `volume_registered/`, `stats/` region tables |
| 8 | `import-annotations` | Automatic, optional | `imports/` atlas coordinates for external cells and masks |
| 9 | `view-registration` | GUI, optional | (review only) |

Every command takes the config with `-c`. Run them from the repository folder as `uv run lightsuite brain <stage> -c my_mouse.yaml`.

---

## Configuration

Copy the template and edit it:

```bash
cp examples/brain_lightsheet.yaml my_mouse.yaml
```

The keys you must set:

```yaml
sample:
  name: M001                          # used in output file names
  source:
    format: tiff_stack
    path: /data/M001/stitched         # folder with the TIFFs
    tiff_type: channelperfile         # or planeperfile (one TIFF per Z plane)
  scratch: /fastssd/scratch/M001      # fast local disk for temporary files
  save_path: /data/M001/lightsuite    # all results and checkpoints
  voxel_um: [5.26, 5.26, 5.0]         # native voxel size [x, y, z] in µm

atlas:
  provider: allen                     # or perens (resolution_um: 20)
  resolution_um: 10
  atlas_dir: /atlases/allen_ccf_10um

registration:
  resolution_um: 20                   # working resolution for registration
  channel_primary: 1                  # structural / autofluorescence channel
  channel_secondary: 2                # optional second channel (dual MI); remove if unused

detection:
  enabled: false                      # built-in detection is not available; use import-annotations
```

Check the config before running:

```bash
uv run lightsuite brain validate-config -c my_mouse.yaml
uv run lightsuite doctor -c my_mouse.yaml
uv run lightsuite config explain -c my_mouse.yaml     # print the resolved paths and settings
```

Every key and its default is listed in the [configuration reference](config_reference.md). You can also create a starter config interactively with `uv run lightsuite config init brain`.

### Several channels in separate folders

When each channel is its own folder of Z-plane TIFFs, list the folders in channel order:

```yaml
sample:
  source:
    tiff_type: planeperfile
    channels:
      - /data/M001/channel_488
      - /data/M001/channel_561
```

All folders must have the same image size and plane count.

### SmartSPIM `All_Channels` folders

Some SmartSPIM exports put every channel in one folder (`Z000000_Ch0.tif`, `Z000000_Ch1.tif`, …). Split it first (this creates symlinks, not copies):

```bash
uv run lightsuite brain split-smartspim-channels -s /data/M001/All_Channels
```

Then list the new `All_Channels/Ch0`, `Ch1`, … folders under `source.channels`.

---

## Running the whole pipeline

```bash
uv run lightsuite brain run -c my_mouse.yaml
```

`run` executes the stages in order and opens each GUI stage when it is reached. Useful options:

| Option | Effect |
|--------|--------|
| `--through register` | Stop after a stage |
| `--from register` | Start at a stage |
| `--resume` | Skip stages whose checkpoint already exists |
| `--include-optional` | Also run the optional stages (align-slices, match-points, view-registration, segmentation import) that are not done yet |
| `--force-preprocess` | Redo preprocess even if cached volumes match the config |

See where a sample stands at any time:

```bash
uv run lightsuite brain stages -c my_mouse.yaml
```

Or run every stage from one window with `uv run lightsuite gui -c my_mouse.yaml`.

---

## Stage by stage

### 1. Preprocess

Reads the raw TIFFs once and downsamples each channel to the registration resolution.

```bash
uv run lightsuite brain preprocess -c my_mouse.yaml
```

Writes to `save_path`:

- `chan_N_sample_register_20um.tif`: one downsampled volume per channel,
- `regopts.json`: volume metadata used by every later stage,
- `sample_reference.json`: the native voxel grid, needed for [annotation import](annotation_import.md).

### 2. Check orientation (GUI)

Tells PyLightSuite how the sample axes map onto the atlas axes.

```bash
uv run lightsuite brain check-orientation -c my_mouse.yaml
```

The atlas is on the left and the sample on the right.

1. Use the three drop-downs to map each sample axis to an atlas axis, with flips if needed.
2. Click **Update preview** after each change.
3. Scroll through slices, and press **Ctrl+E** to cycle the viewed axes.
4. When anterior–posterior, dorsal–ventral and left–right all agree, click **Save orientation && close**.

This writes `brain_orientation.txt`. If you already know the mapping, set `registration.orientation` (e.g. `[1, -3, 2]`, negative = flipped) in the YAML and skip this stage.

### 3. Init registration

Coarse similarity alignment of the sample to the atlas, using BCPD on point clouds extracted from both volumes.

```bash
uv run lightsuite brain init-registration -c my_mouse.yaml
```

The log says which method ran: `Init-registration: coarse similarity alignment (BCPD)…`, or `(ICP fallback)` if BCPD was not found.

Check the previews in `qc/initial/` (`dim1_initial_registration.png`, `dim2_…`, `dim3_…`): eight slices per axis with the atlas region outlines on top. The outlines should roughly follow the brain. If the brain is rotated or mirrored, fix the orientation (stage 2) and rerun this stage.

If the log warns about a **sparse sample cloud** and the previews look poor, lower `registration.cloud_threshold` (default `5.0`, try `3.0`).

### 4. Align slices (GUI, optional, recommended)

Matches sample slices to the corresponding atlas planes along each axis. This corrects stretch or compression along an axis before control points are placed.

```bash
uv run lightsuite brain align-slices -c my_mouse.yaml
```

1. Pick the axis with **Volume axis** (1 = Y, 2 = X, 3 = Z).
2. The sample slice is fixed on the left. Scroll the atlas on the right with **PgUp/PgDn**, the plane spinbox, or the mouse wheel over the atlas.
3. When the anatomy matches, press **Enter** (or **Confirm slice**) to move to the next of about 20 anchors. Use **←/→** to revisit anchors.
4. Repeat for all three axes, then click **Save && Close**.

This writes `slice_correspondence.json`. `match-points` uses it to pre-select atlas planes, and `register` uses it to improve the affine step and add landmarks.

### 5. Match control points (GUI, optional)

Places matching landmarks on sample (left) and atlas (right) slices. You can skip it: `register` then uses only the automatic point pairs from init-registration.

```bash
uv run lightsuite brain match-points -c my_mouse.yaml
```

1. Move between slices with **←/→** or the **Navigation** panel. Scroll the atlas plane with **PgUp/PgDn**, the **Atlas plane** spinbox or the mouse wheel over the atlas. (Napari's own bottom slider does not change slices here.)
2. Click a landmark on the sample, then the same landmark on the atlas. Points are numbered in pairs.
3. Once a slice has matching counts, the atlas outline on the sample refits. Toggle it with **O**.
4. **Backspace** removes the last point; **Clear slice points** resets the slice.
5. Click **Save && Close**.

Tips: place at least 16 pairs spread along the anterior–posterior axis. Ventricle edges, the corpus callosum and large nuclei make good landmarks. Avoid damaged tissue.

This writes `atlas2histology_tform.json`.

### 6. Register

Runs Elastix: an affine step, then a B-spline (deformable) step, using the control points and, when present, the slice correspondence.

```bash
uv run lightsuite brain register -c my_mouse.yaml
uv run lightsuite brain register -c my_mouse.yaml --single-step     # faster, lower quality
```

Outputs:

- `transform_params.json`: the complete transform,
- `bspline_samp_to_atlas_20um.txt` and `bspline_atlas_to_samp_20um.txt`: B-spline parameter files for both directions,
- `qc/affine/{name}_dim{1,2,3}_affine_registration.png` and `qc/bspline/{name}_dim{1,2,3}_bspline_registration.png`: preview slices with atlas outlines after each step.

Compare the affine and B-spline previews: the B-spline outlines should follow the tissue more closely without becoming wavy. Warnings about large landmark residuals or poor overlap are printed in the log. Elastix works in `_work/`, which is deleted when the stage succeeds.

The slice-correspondence steps can be turned off with `registration.use_slice_correspondence_affine: false` and `registration.use_slice_correspondence_landmarks: false`.

#### Wavy or over-warped B-spline outlines

If the affine preview looks right but the B-spline preview shows tangled region outlines, the deformation has more freedom than the image data can constrain. This is common on wide camera frames where the brain fills only part of the field.

- Raise `registration.bspline_bending_weight` (default `2.0`). This is a smoothness penalty; `0` turns it off.
- Raise `registration.bspline_spatial_scale_mm` from `0.64` to about `1.0`. This coarsens the B-spline grid about threefold and often *improves* landmark agreement on wide frames.
- Set `registration.sample_content_crop: auto` to crop the registration volume to the brain.

### 7. Export

Applies the transform: warps the channels into atlas space and computes per-region statistics.

```bash
uv run lightsuite brain export -c my_mouse.yaml --save-volume --write-csv
uv run lightsuite brain export -c my_mouse.yaml --space both --save-volume --write-csv
```

| Option | Effect |
|--------|--------|
| `--save-volume` / `--no-save-volume` | Write the registered channel TIFFs |
| `--write-csv` / `--no-write-csv` | Write region tables |
| `--space atlas\|sample\|both` | Output space; `sample` warps the atlas labels onto the sample instead ([registration spaces](registration_spaces.md)) |

Outputs:

- `volume_registered/chan_NN_registered_atlas.tif`: each channel in atlas space (with `--save-volume`),
- `volume_registered/sample_space/`: atlas template, labels and divisions warped onto the sample grid (with `--space sample` or `both`),
- `stats/region_stats.csv`: one tidy table with every channel, region, hemisphere and metric,
- `stats/region_stats_top10.csv`: the top regions per channel, one column per metric,
- `stats/chanNN_region_stats.csv`, `stats/chanNN_intensities.csv` / `.json`: per-channel tables,
- `stats/*_sample.csv`: the same tables computed in sample space (with `--space sample` or `both`).

`region_stats.csv` columns:

| Column | Meaning |
|--------|---------|
| `sample`, `channel`, `atlas` | Where the row comes from |
| `parcellation_index` | Atlas label value |
| `acronym`, `name`, `structure`, `division` | Region names |
| `hemisphere` | `left` or `right` |
| `metric`, `value` | e.g. `median_intensity`, `std`, `volume_mm3`; `cell_count`, `cell_density` after an import |

Choose metrics with `analysis.intensity_metrics` and the top-N size with `analysis.top_n_regions`. Allen region names need `parcellation_to_parcellation_term_membership.csv` ([installation](installation.md#allen-brain-atlas-default)).

### 8. Import annotations (optional)

Maps cells or masks from an external detection tool into atlas space (written to `imports/`) and adds `cell_count` / `cell_density` rows to `stats/region_stats.csv`.

```bash
uv run lightsuite brain convert-annotations -c my_mouse.yaml     # vendor export → points.csv
uv run lightsuite brain import-annotations -c my_mouse.yaml
```

The file formats and supported tools are described in [annotation import](annotation_import.md).

### 9. View registration (GUI, optional)

Opens a Napari review of the result: registered channels, atlas labels, division check boxes, and imported points or masks.

```bash
uv run lightsuite brain view-registration -c my_mouse.yaml                 # sample space (default)
uv run lightsuite brain view-registration -c my_mouse.yaml --space atlas
```

Sample space needs `export --space sample` (or `both`) first for the warped atlas labels.

---

## Output layout

```
<save_path>/
├── regopts.json                      # preprocess + init-registration state
├── sample_reference.json             # native grid for annotation import
├── chan_1_sample_register_20um.tif   # one per channel (preprocess)
├── brain_orientation.txt             # check-orientation
├── slice_correspondence.json         # align-slices
├── atlas2histology_tform.json        # match-points
├── transform_params.json             # register
├── bspline_samp_to_atlas_20um.txt
├── bspline_atlas_to_samp_20um.txt
├── qc/
│   ├── initial/dim{1,2,3}_initial_registration.png
│   ├── affine/{name}_dim{1,2,3}_affine_registration.png
│   └── bspline/{name}_dim{1,2,3}_bspline_registration.png
├── stats/                            # export: region_stats.csv, region_stats_top10.csv, chanNN_*.csv
├── imports/                          # import-annotations: *_atlas_coords.npz, *_sample_coords.npz
├── volume_registered/
│   ├── chan_01_registered_atlas.tif  # export --save-volume
│   └── sample_space/                 # export --space sample|both
└── _work/                            # temporary Elastix folders, removed on success
```

## Rerunning stages

Each stage reads the checkpoints of the stages before it from `save_path`. Rerunning a stage overwrites its outputs, so rerun everything after it too. For example, after changing control points rerun `register` and `export`. `uv run lightsuite brain run -c my_mouse.yaml --from register` does that.

---

## Dual-channel registration

With two structural contrasts (e.g. autofluorescence and a label), register on both with mutual information:

```yaml
registration:
  channel_primary: 1
  channel_secondary: 2
  dual_channel_mi_weight_primary: 0.4
  dual_channel_mi_weight_secondary: 0.4
```

---

## Migrating from MATLAB

| MATLAB LightSuite | PyLightSuite |
|-------------------|--------------|
| `opts` struct in `ls_analyze_lightsheet_volume.m` | YAML config |
| `check_lightsuite_installation.m` | `lightsuite doctor` |
| `preprocessLightSheetVolume.m` | `brain preprocess` |
| `getBrainOrientation.m` | `brain check-orientation` |
| `initializeRegistration.m` | `brain init-registration` |
| `matchControlPoints_unified.m` | `brain match-points` |
| `multiobjRegistration.m` | `brain register` |
| `generateRegisteredBrainVolumes.m` | `brain export` |
| `transformPointsToAtlas.m` | `brain import-annotations` |
| `regopts.mat`, `atlas2histology_tform.mat`, `transform_params.mat` | the same names with `.json` |

The same TIFF layouts, atlas files and Elastix version work in both. MATLAB control-point sessions can be brought over with `uv run lightsuite brain import-matlab-control-points --help`. MATLAB `.mat` checkpoints are otherwise not read: rerun the Python stages.

The B-spline step matches MATLAB's metrics, optimizer, pyramid and iteration counts, with three deliberate differences:

- **Bending energy:** `registration.bspline_bending_weight` adds a smoothness term MATLAB does not have. Set it to `0` to match MATLAB.
- **Histogram bins:** written explicitly as 32 (MATLAB inherits 32 from its defaults file).
- **Step size estimation:** `ASGDParameterEstimationMethod` is `DisplacementDistribution`; MATLAB's default `Original` takes larger steps that can fold the grid.

## Limitations

- No built-in cell detection: use [annotation import](annotation_import.md). Preprocess warns if `detection.enabled: true`.
- Registered volumes are written as TIFF only.
- With the Perens atlas, division names are filled in only where Perens ids match the Allen ontology.
