# Output files

<!-- Generated from src/lightsuite/export/results_info.py. Do not edit by hand:
     uv run python -m lightsuite.export.results_info --write-docs docs/outputs.md -->

Every stage writes into `sample.save_path`. After each stage PyLightSuite also writes **`info_results.md`** there: a short guide listing the files that actually exist in that folder, what each one is, which metrics and channels the statistics tables contain, and what the table columns mean.

Rebuild it at any time, for example for a sample processed with an older version:

```bash
uv run lightsuite brain info -c my_mouse.yaml
uv run lightsuite spinal info -c my_spinal.yaml
```

Files that PyLightSuite does not recognise are listed under *Other files*. Temporary folders (`_work/`) are not listed.

The tables below list every output each workflow can produce. `{label}` is the annotation label, `NN` / `N` the channel number and `{name}` the sample name.

## Brain lightsheet

### Statistics tables

| File | Written by | What it is |
|------|------------|------------|
| `stats/region_stats.csv` | export, import-annotations | Main results table in atlas space: one row per channel × region × hemisphere × metric. Use this file to compare animals. |
| `stats/region_stats_sample_topN.csv` | export | Top regions per channel in sample space, one column per metric. |
| `stats/region_stats_topN.csv` | export | Top N regions per channel (N = `analysis.top_n_regions`), one column per metric. |
| `stats/region_stats_sample.csv` | export | The same table computed in sample space (atlas labels warped onto the 20 µm registration grid). Not directly comparable with `region_stats.csv`. |
| `stats/chanNN_region_stats_sample.csv` | export | Sample-space region statistics for one channel. |
| `stats/chanNN_region_stats.csv` | export | Region statistics for one channel (same columns as `region_stats.csv`). |
| `stats/chanNN_intensities.csv` | export | Intensity per region for one channel, one row per region (MATLAB-style table). |
| `stats/chanNN_intensities.json` | export | The same per-channel intensities as JSON. |
| `stats/{label}_region_counts.csv` | import-annotations, region-stats | Cell counts and densities per region for one imported annotation. |

### Registered volumes (atlas space)

| File | Written by | What it is |
|------|------------|------------|
| `volume_registered/chan_NN_registered_atlas.tif` | export | Each channel warped into atlas space at the atlas resolution (written with `--save-volume`). |

### Atlas warped onto the sample (sample space)

| File | Written by | What it is |
|------|------------|------------|
| `volume_registered/sample_space/annotation_in_sample_20um.tif` | export --space sample | Atlas region labels warped onto the sample (20 µm registration grid). |
| `volume_registered/sample_space/annotation_boundary_in_sample_20um.tif` | export --space sample | Atlas region outlines warped onto the sample. |
| `volume_registered/sample_space/template_in_sample_20um.tif` | export --space sample | Atlas template warped onto the sample. |
| `volume_registered/sample_space/division_labels_in_sample_20um.tif` | export --space sample | Atlas division labels warped onto the sample. |
| `volume_registered/sample_space/sample_space_manifest.json` | export --space sample | List of the sample-space files and how they were written (for example whether they are stored on the permuted grid). |

### Imported annotations

| File | Written by | What it is |
|------|------------|------------|
| `imports/{label}_atlas_coords.npz` | import-annotations | Imported points in atlas space (array `atlasptcoords`). |
| `imports/{label}_sample_coords.npz` | import-annotations | Imported points on the 20 µm registration grid (array `regptcoords`). |
| `imports/{label}_atlas_coords.csv` | import-annotations | Atlas-space points as CSV (with `--write-csv`). |
| `imports/{label}_registered_atlas.tif` | import-annotations | Imported mask warped into atlas space. |
| `imports/{label}_in_sample_20um.tif` | import-annotations | Imported mask on the 20 µm registration grid. |
| `imports/import_annotations_summary.json` | import-annotations | Summary of what was imported: labels, kinds and feature counts. |
| `converted/{label}_points.csv` | convert-annotations | Detections converted from a vendor export into `points.csv` format (native sample space). Input to import-annotations. |
| `converted/convert_annotations_summary.json` | convert-annotations | Summary of the conversion: source tool, files and point counts. |

### Registration quality control

| File | Written by | What it is |
|------|------------|------------|
| `qc/dim{1,2,3}_initial_registration.png` | init-registration | Coarse alignment preview with atlas outlines, one image per axis. |
| `qc/{name}_dim{1,2,3}_affine_registration.png` | register | Preview slices with atlas outlines after the affine step. |
| `qc/{name}_dim{1,2,3}_bspline_registration.png` | register | Preview slices with atlas outlines after the B-spline step. The outlines should follow the tissue more closely than the affine ones without going wavy. |
| `qc/init_registration_diagnostics.json` | init-registration | Numbers describing the coarse alignment. |
| `qc/registration_diagnostics.json` | register | Registration quality summary (overlap, landmark residuals, warnings). |
| `qc/affine_fit_stats.json` | register | Fit statistics of the affine step. |
| `qc/correspondence_affine_stats.json` | register | How the slice correspondence was used in the affine step. |
| `qc/correspondence_landmark_stats.json` | register | How the slice correspondence was used as landmarks. |

### Stage checkpoints

| File | Written by | What it is |
|------|------------|------------|
| `regopts.json` | preprocess, init-registration | Volume metadata (sizes, voxel sizes, channel files) and the coarse starting transform. Every later stage reads it. |
| `sample_reference.json` | preprocess | The native (full-resolution) voxel grid of the stitched data. Annotation import uses it to place detections. |
| `chan_N_sample_register_20um.tif` | preprocess | Each channel downsampled to the registration resolution (unpermuted sample grid). |
| `brain_orientation.txt` | check-orientation | The sample orientation chosen in the orientation check. |
| `slice_correspondence.json` | align-slices | Matches between sample slices and atlas planes. Pre-selects planes in match-points and guides register. |
| `atlas2histology_tform.json` | match-points | Manual control points (sample and atlas landmarks) and the affine fitted to them. |
| `atlas_content_manifest.json` | init-registration, register | Crop box applied to the atlas when `atlas.content_trim` is on. |
| `transform_params.json` | register | The complete sample-to-atlas transform. Export and annotation import read it. |
| `bspline_samp_to_atlas_20um.txt` | register | Elastix B-spline parameter file, sample to atlas. |
| `bspline_atlas_to_samp_20um.txt` | register | Elastix B-spline parameter file, atlas to sample. Needed for sample-space export. |

## Spinal cord

### Statistics tables

| File | Written by | What it is |
|------|------------|------------|
| `stats/region_stats.csv` | export, import-annotations, region-stats | Main results table in atlas space: one row per channel × region × segment × hemisphere × metric, plus rollups (see `rollup_level`). Use this file to compare animals. |
| `stats/region_stats_structure.csv` | region-stats | Rollup to structures (laminae and funiculi combined). |
| `stats/region_stats_division.csv` | region-stats | Rollup to grey matter vs white matter. |
| `stats/region_stats_horn.csv` | region-stats | Rollup to dorsal horn, ventral horn and central region. |
| `stats/region_stats_sample_topN.csv` | export | Top regions per channel in sample space, one column per metric. |
| `stats/region_stats_topN.csv` | export | Top N regions per channel (N = `analysis.top_n_regions`), one column per metric. |
| `stats/region_stats_sample.csv` | export | The same table computed in sample space (atlas labels warped onto the 20 µm registration grid). Not directly comparable with `region_stats.csv`. |
| `stats/chanNN_region_stats.csv` | export | Region statistics for one channel (same columns as `region_stats.csv`). |
| `stats/{label}_region_counts.csv` | import-annotations, region-stats | Cell counts and densities per region for one imported annotation. |

### Registered volumes (atlas space)

| File | Written by | What it is |
|------|------------|------------|
| `volume_registered/chanNN_channelN.tiff` | export | Each channel warped onto the atlas grid (10 × 10 × 20 µm). |
| `volume_registered/template_registered.tiff` | export | Atlas template on the same grid, for visual comparison. |
| `volume_registered/annotation_registered.tiff` | export | Atlas region labels on the same grid. |
| `volume_registered/hemisphere_registered.tiff` | export | Left/right hemisphere mask on the same grid. |

### Atlas warped onto the sample (sample space)

| File | Written by | What it is |
|------|------------|------------|
| `volume_registered/sample_space/annotation_in_sample_20um.tif` | export --space sample | Atlas region labels warped onto the sample (20 µm registration grid). |
| `volume_registered/sample_space/template_in_sample_20um.tif` | export --space sample | Atlas template warped onto the sample. |
| `volume_registered/sample_space/segments_in_sample_20um.tif` | export --space sample | Rostrocaudal segment labels (C1 … Co2) warped onto the straightened sample. |
| `volume_registered/sample_space/hemisphere_in_sample_20um.tif` | export --space sample | Left/right hemisphere mask warped onto the straightened sample. |
| `volume_registered/sample_space/chan_NN_sample_straight_20um.tif` | export --space sample | Each straightened sample channel on the same grid, to overlay with the warped labels. |
| `volume_registered/sample_space/registration_export_sample.png` | export --space sample | Preview of the warped atlas labels over the sample. |
| `volume_registered/sample_space/sample_space_manifest.json` | export --space sample | List of the sample-space files and how they were written (for example whether they are stored on the permuted grid). |

### Imported annotations

| File | Written by | What it is |
|------|------------|------------|
| `volume_registered/{label}_atlas_coords.npz` | import-annotations | Imported points in atlas space (array `atlasptcoords`). |
| `volume_registered/{label}_sample_coords.npz` | import-annotations | Imported points on the 20 µm registration grid (array `regptcoords`). |
| `volume_registered/{label}_atlas_coords.csv` | import-annotations | Atlas-space points as CSV (with `--write-csv`). |
| `volume_registered/import_annotations_summary.json` | import-annotations | Summary of what was imported: labels, kinds and feature counts. |
| `converted/{label}_points.csv` | convert-annotations | Detections converted from a vendor export into `points.csv` format (native sample space). Input to import-annotations. |
| `converted/convert_annotations_summary.json` | convert-annotations | Summary of the conversion: source tool, files and point counts. |

### Plots

| File | Written by | What it is |
|------|------------|------------|
| `plots/cord_heatmap_{metric}_{channel}.png` | plot-heatmap | Segment × region heatmap of one metric and channel. |
| `plots/*.csv` | plot-heatmap, view | The matrix behind a heatmap, saved from the plot command or the viewer. |

### Registration quality control

| File | Written by | What it is |
|------|------------|------------|
| `qc/registration_initial_similarity.png` | init-registration | Preview after the coarse similarity alignment. |
| `qc/registration_initial_affine.png` | init-registration | Preview after the affine alignment. |
| `qc/registration_bspline.png` | register | Preview after the B-spline registration. |

### Stage checkpoints

| File | Written by | What it is |
|------|------------|------------|
| `regopts.json` | preprocess, init-registration | Volume metadata (sizes, voxel sizes, channel files) and the coarse starting transform. Every later stage reads it. |
| `sample_reference.json` | preprocess | The native (full-resolution) voxel grid of the stitched data. Annotation import uses it to place detections. |
| `cord_orientation.txt` | check-orientation | Whether the cord runs rostrocaudal or caudorostral in the stack. |
| `spinal_alignment_opt.json` | straighten | Cord centerline and straightening settings. |
| `longitudinal_correspondence.json` | align-longitudinal | Anchors matching positions along the sample to positions along the atlas. |
| `corresponding_points.json` | match-points | Manual control points, in the layout of MATLAB's `corresponding_points.mat` (1-based). |
| `transform_params.json` | register | The complete sample-to-atlas transform. Export and annotation import read it. |

### Cache and transform files

| File | Written by | What it is |
|------|------------|------------|
| `cache/chan_N_sample_register_20um.tif` | preprocess | Each channel downsampled to the registration resolution. |
| `cache/regvol_cord.tif` | preprocess | The cord volume used for registration. |
| `cache/straightvol.tif` | init-registration | The straightened cord volume. |
| `cache/register_cache.json` | preprocess | Fingerprint of the cached volumes, so preprocess can skip reading the raw stack again. |
| `cache/orientation_preview.npz` | check-orientation | Cached preview for the orientation GUI. |
| `cache/match_points_atlas.npz` | match-points | Cached atlas slices for the control-point GUI. |
| `transforms/affine_atlas_to_samp_20um.txt` | init-registration | Elastix affine parameter file. |
| `transforms/bspline_samp_to_atlas_20um.txt` | register | Elastix B-spline parameter file, sample to atlas. |
| `transforms/bspline_atlas_to_samp_20um.txt` | register | Elastix B-spline parameter file, atlas to sample. Needed for sample-space export. |

## Statistics table columns

The long-format tables (`region_stats.csv`, `region_stats_sample.csv` and the spinal rollups) have one measurement per row. `segment` and `rollup_level` appear only in spinal cord tables.

| Column | Meaning |
|--------|---------|
| `sample` | Sample name (`sample.name`). |
| `channel` | Channel number, or the import label for cell counts. |
| `atlas` | Atlas used for registration. |
| `parcellation_index` | Atlas label value of the region. |
| `acronym` | Region acronym. |
| `name` | Region name. |
| `structure` | Parent structure of the region. |
| `division` | Coarse division (e.g. grey or white matter). |
| `segment` | Rostrocaudal segment (C1 … Co2). |
| `rollup_level` | `region`, `structure`, `division` or `horn`. |
| `hemisphere` | `left`, `right` or `whole`. |
| `metric` | What `value` measures (see the metrics list). |
| `value` | The number. |

## Metrics

| Metric | Meaning |
|--------|---------|
| `median_intensity` | Median voxel intensity in the region. |
| `relative_median_intensity` | Median intensity relative to background (`analysis.relative_intensity_to: background`). |
| `mean_intensity` | Mean voxel intensity in the region. |
| `std` | Standard deviation of voxel intensity in the region. |
| `variance` | Variance of voxel intensity in the region. |
| `volume_mm3` | Region volume in mm³. |
| `cell_count` | Number of imported points in the region. |
| `cell_density` | Imported points per mm³. |

Choose intensity metrics with `analysis.intensity_metrics`. `cell_count` and `cell_density` appear after an annotation import. See [registration spaces](registration_spaces.md) for why atlas-space and sample-space tables should not be compared directly.
