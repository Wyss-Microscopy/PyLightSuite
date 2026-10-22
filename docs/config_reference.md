# Configuration reference

LightSuite configs are YAML files validated by Pydantic models in `src/lightsuite/config/models.py`. The Napari **Config** dock (`lightsuite gui`) edits a subset of keys; everything else can still be set in YAML and is **preserved on Save** when the GUI does not touch that key.

## GUI vs YAML legend

| Symbol | Meaning |
|--------|---------|
| **GUI** | Shown and editable in the Napari Config dock for that workflow |
| **YAML** | YAML only (not in the Config form; unchanged on Save unless you edit the file) |
| **GUI†** | GUI always writes this value on Save (no control in the form) |

**Workflows:** *brain* = whole-brain lightsheet, *spinal* = spinal cord.

**Templates:** `examples/brain_lightsheet.yaml`, `examples/spinal_cord.yaml`.

---

## `sample` — paths and imaging grid

| Key | Brain | Spinal | Default | Description |
|-----|:-----:|:------:|---------|-------------|
| `sample.name` | GUI | GUI | *(required)* | Sample id (logs, exports, filenames) |
| `sample.scratch` | GUI | GUI | *(required)* | Fast disk for intermediates |
| `sample.save_path` | GUI | GUI | *(required)* | Results / checkpoints directory |
| `sample.voxel_um` | GUI | GUI | brain: optional; spinal: required | Native voxel size µm `[x, y, z]` |
| `sample.source.format` | YAML | YAML | `auto` | `auto` or `tiff_stack` (`czi` and `imaris` are reserved and not implemented: export to TIFF) |
| `sample.source.path` | GUI | GUI | — | Stitched TIFF folder (`channelperfile`) or first channel folder |
| `sample.source.tiff_type` | GUI | GUI | `channelperfile` | `channelperfile` (one TIFF per channel) or `planeperfile` (one TIFF per Z plane); spinal also accepts `auto` and `multichannel_single` |
| `sample.source.channels` | GUI | GUI | — | List of per-channel folders, in channel order (`planeperfile` only) |
| `sample.source.skip_corrupt_slices` | — | YAML | `false` | Skip bad slice TIFFs instead of failing (spinal) |

---

## `atlas` — reference anatomy

### Brain (`atlas`)

| Key | GUI | Default | Description |
|-----|:---:|---------|-------------|
| `atlas.provider` | GUI | `allen` | `allen` or `perens` with local files; also `princeton` or `rat` with `source: brainglobe` |
| `atlas.source` | GUI | `files` | `files` (local NIfTI) or `brainglobe` (needs the `atlas` extra) |
| `atlas.brainglobe_name` | GUI | inferred | Registry name when `source: brainglobe` |
| `atlas.resolution_um` | GUI | `10` | Native atlas voxel size µm (local files; auto from BrainGlobe catalog) |
| `atlas.atlas_dir` | GUI | — | Folder with template + annotation NIfTIs (local files); falls back to `LIGHTSUITE_ATLAS_PATH` |
| `atlas.content_trim` | YAML | `off` | Trim atlas padding for registration: `off`, `auto`, `manual` |
| `atlas.content_margin_vox` | YAML | `8` | Margin around auto-detected atlas foreground |
| `atlas.content_box` | YAML | — | Manual atlas crop `[y0,y1,x0,x1,z0,z1]` when `content_trim: manual` |

### Spinal (`atlas`)

| Key | GUI | Default | Description |
|-----|:---:|---------|-------------|
| `atlas.atlas_dir` | GUI | *(required)* | Fiederling atlas folder (`Template.tif`, `Annotation.tif`, CSVs) |

---

## `registration` — alignment parameters

### Brain

| Key | GUI | Default | Description |
|-----|:---:|---------|-------------|
| `registration.resolution_um` | GUI | `20` | Isotropic working resolution µm |
| `registration.channel_primary` | GUI | `1` | 1-based registration channel |
| `registration.channel_secondary` | GUI | — | Optional second channel for dual MI (`none` in GUI = unset) |
| `registration.bspline_spatial_scale_mm` | GUI | `0.64` | B-spline grid spacing mm |
| `registration.control_point_weight` | GUI | `0.2` | Landmark weight in Elastix (0–1) |
| `registration.augment_points` | GUI | `false` | Add auto-landmarks to control points |
| `registration.dual_channel_mi_weight_primary` | GUI | `0.4` | Dual MI weight for primary channel (alias: `dual_channel_mi_weight_autofluor`) |
| `registration.dual_channel_mi_weight_secondary` | GUI | `0.4` | Dual MI weight for secondary channel (alias: `dual_channel_mi_weight_signal`) |
| `registration.orientation` | GUI | from `brain_orientation.txt` | Axis permutation, e.g. `[1, -3, 2]`; empty in GUI = omit (use check-orientation) |
| `registration.canvas_mode` | GUI | `off` | Working grid: `off`, `pad`, `crop`, `union` |
| `registration.bspline_bending_weight` | YAML | `2.0` | B-spline smoothness penalty; raise if outlines look wavy (0 = off, as in MATLAB) |
| `registration.cloud_threshold` | YAML | `5.0` | Init-registration point-cloud threshold; lower (e.g. `3.0`) for sparse samples |
| `registration.sample_cloud_subsample` | YAML | `0.1` | Fraction of gradient points kept |
| `registration.outlier_ratio` | YAML | `0.01` | Outlier rejection ratio |
| `registration.bcpd_path` | YAML | — | Path to the BCPD binary (else `LIGHTSUITE_BCPD_PATH`, then `PATH`) |
| `registration.use_slice_correspondence_affine` | YAML | `true` | Correspondence-informed affine before B-spline |
| `registration.use_slice_correspondence_landmarks` | YAML | `true` | Add align-slices anchors as B-spline landmarks |
| `registration.correspondence_landmark_weight` | YAML | `0.2` | Min landmark weight for correspondence pairs |
| `registration.correspondence_landmark_max_count` | YAML | `96` | Max merged correspondence landmark pairs |
| `registration.sample_content_crop` | YAML | `off` | Crop registration TIFFs to sample foreground: `off`, `auto`, `manual` |
| `registration.sample_content_margin_vox` | YAML | `8` | Margin for auto sample crop |
| `registration.sample_content_box` | YAML | — | Manual sample crop box |
| `registration.sample_content_trim_z` | YAML | `true` | Drop sparse Z slices in auto crop |

### Spinal

| Key | GUI | Default | Description |
|-----|:---:|---------|-------------|
| `registration.resolution_um` | GUI | `20` | Working resolution µm after preprocess |
| `registration.channel_primary` | GUI | `1` | Registration channel |
| `registration.control_point_weight` | GUI | `0.2` | Landmark weight (0–1) |
| `registration.straightening_lambda_pos` | YAML | `5000` | Straightening position penalty |
| `registration.straightening_lambda_ang` | YAML | `5000` | Straightening angle penalty |
| `registration.target_orientation_deg` | YAML | `90` | Target orientation after straightening |
| `registration.longitudinal_direction` | YAML | from `cord_orientation.txt` | `rostrocaudal` or `caudorostral` override |

---

## `detection` — reserved (brain)

| Key | GUI | Default | Description |
|-----|:---:|---------|-------------|
| `detection.enabled` | GUI | `true` | Built-in detection is not implemented: set `false` and use [annotation import](annotation_import.md) |

The other `detection.*` keys (`backend`, `cell_diameter_um`, `thresholds`, `channel`, `debug`, `save_cell_images`, `write_to_csv`) are accepted for compatibility with MATLAB-era configs; no detection runs.

---

## `compute` — parallelism and memory

| Key | Brain | Spinal | Default | Description |
|-----|:-----:|:------:|---------|-------------|
| `compute.workers` | GUI | GUI | `4` | Parallel workers |
| `compute.use_gpu` | YAML | YAML | `false` | Reserved (not used in Python) |
| `compute.max_in_memory_scratch_gb` | YAML | YAML | `24` | RAM threshold for in-memory scratch vs memmap |

---

## `export` — registered volumes and spaces

| Key | Brain | Spinal | Default | Description |
|-----|:-----:|:------:|---------|-------------|
| `export.registered_volume_format` | YAML | YAML | `tiff` | Registered volume format |
| `export.write_pyramid` | YAML | YAML | `true` | Write image pyramids |
| `export.write_cells_csv` | YAML | YAML | `true` | Legacy parcellation CSV flag |
| `export.save_registered_volume` | YAML | YAML | `false` | Write full registered channel TIFFs |
| `export.spaces` | YAML | YAML | `[atlas]` | `atlas` and/or `sample` export spaces |
| `export.save_sample_space_volume` | YAML | YAML | `true` | Warp atlas template/labels to sample grid |

---

## `analysis` — region stats and parcellation

| Key | Brain | Spinal | Default | Description |
|-----|:-----:|:------:|---------|-------------|
| `analysis.intensity_metrics` | GUI | GUI | `median_intensity`, `std`, `volume_mm3` | Metrics in `region_stats*.csv`: also `mean_intensity`, `variance` |
| `analysis.parcellate_intensities` | YAML† | GUI | `true` | Compute intensity stats from registered channels (brain: tied to export) |
| `analysis.count_points` | GUI† | GUI† | `true` | Bin imported points into `cell_count` / `cell_density` (GUI always saves `true`) |
| `analysis.write_tidy_csv` | YAML | YAML | `true` | Emit long-form `chanXX_region_stats.csv` (brain export) |
| `analysis.intensity_channels` | YAML | YAML | all | Subset of channel indices to parcellate |
| `analysis.relative_intensity_to` | YAML | YAML | `none` | Spinal: `none` or `background` → adds `relative_median_intensity` |
| `analysis.point_labels` | YAML | YAML | all | Filter import labels for counting (stem of `*_atlas_coords.npz`) |
| `analysis.stats_spaces` | GUI | GUI | `[atlas]` | Write stats in `atlas` and/or `sample` space |
| `analysis.hemisphere_flip` | YAML | YAML | `false` | Swap L/R for Fiederling hemisphere mask |
| `analysis.hemisphere_keep_whole` | YAML | YAML | `true` | Also emit whole-cord rows alongside left/right |
| `analysis.top_n_regions` | YAML | YAML | `10` | Write `region_stats_top{N}.csv` (0 disables) |
| `analysis.top_n_rank_by` | YAML | YAML | per channel: `cell_count` if present else first intensity metric | Preferred ranking metric for the top-N CSV |

**GUI intensity metric checkboxes:** Median, Mean, Std, Variance, Volume mm³.

**GUI stats space checkboxes:** Atlas and/or Sample (`analysis.stats_spaces`).

---

## `import` — external annotations

Each entry in `import.annotations[]`:

| Key | Brain | Spinal | Description |
|-----|:-----:|:------:|-------------|
| `format` | GUI | GUI | `points_csv` or `mask_tiff` |
| `path` | GUI | GUI | Path to annotation file |
| `label` | GUI | GUI | Output filename stem (optional) |

| Key | Brain | Spinal | Default | Description |
|-----|:-----:|:------:|---------|-------------|
| `import.write_csv` | YAML | YAML | `true` | Write coordinate CSV alongside NPZ |

**Notes:**

- Brain: `points_csv` and `mask_tiff` supported; point counts merged into `region_stats.csv` after export/import.
- Spinal: `points_csv` only (`mask_tiff` must be converted to points).

---

## GUI behavior notes

1. **Round-trip:** Keys marked **YAML** are left unchanged when you Save from the Config dock, as long as they already exist in the file.
2. **Templates:** **From template** loads an example YAML; edit paths, then **Save as…**.
3. **Validation:** Save runs config validation; invalid paths or missing files are reported in the dock status line.
4. **BrainGlobe:** When `atlas.source` is BrainGlobe, the GUI hides atlas directory and resolution (taken from the catalog).
5. **TIFF layout:** `planeperfile` shows channel folder rows; `channelperfile` shows a single source path.
6. **Orientation:** Brain orientation can be set in the GUI or left empty to use `brain_orientation.txt` from `check-orientation`.
7. **Stage-specific GUIs:** Match-points, align-slices, check-orientation, and view-registration use separate Napari panels; their outputs are checkpoint files, not YAML fields.

---

## Related docs

- [Brain lightsheet usage](usage_lightsheet_brain.md)
- [Spinal cord usage](usage_spinal_cord.md)
- [Annotation import](annotation_import.md)
- [Registration spaces](registration_spaces.md)
