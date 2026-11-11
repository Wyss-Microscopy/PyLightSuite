"""Write ``info_results.md``: a plain-language guide to the files in ``save_path``.

The guide is rebuilt from what is actually on disk, so it only describes outputs that
exist. Every known output is listed once in :data:`OUTPUT_ENTRIES`; the same list
renders ``docs/outputs.md`` (see :func:`render_outputs_reference`), and a test keeps
the committed page in sync with the code.

Regenerate the docs page after editing the entries::

    uv run python -m lightsuite.export.results_info --write-docs docs/outputs.md
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

RESULTS_INFO_FILENAME = "info_results.md"

BRAIN = frozenset({"brain"})
SPINAL = frozenset({"spinal"})
BOTH = BRAIN | SPINAL

#: Section id → heading, in display order.
SECTIONS: dict[str, str] = {
    "stats": "Statistics tables",
    "volumes": "Registered volumes (atlas space)",
    "sample_space": "Atlas warped onto the sample (sample space)",
    "imports": "Imported annotations",
    "plots": "Plots",
    "qc": "Registration quality control",
    "checkpoints": "Stage checkpoints",
    "cache": "Cache and transform files",
}

#: Directories under ``save_path`` that are never listed (temporary workspaces).
_SKIP_DIRS = frozenset({"_work"})
#: Stop scanning after this many files (guards against a mis-set ``save_path``).
_MAX_SCAN_FILES = 20_000
#: CSVs larger than this only have their header read.
_MAX_CSV_SCAN_BYTES = 500 * 1024 * 1024
#: Files listed per multi-file entry before collapsing to a count.
_MAX_FILES_PER_ENTRY = 10
_MAX_OTHER_FILES = 30
_MAX_VALUES_SHOWN = 12


@dataclass(frozen=True)
class OutputEntry:
    """One known output file (or family of files) under ``save_path``."""

    pattern: str
    """Glob relative to ``save_path``; ``*`` never crosses a ``/``."""
    name: str
    """Human-readable name shown in the guide (e.g. ``chan_NN_registered_atlas.tif``)."""
    section: str
    stage: str
    description: str
    workflows: frozenset[str] = BOTH

    def matches(self, rel_path: str) -> bool:
        return rel_path.count("/") == self.pattern.count("/") and fnmatch.fnmatchcase(
            rel_path, self.pattern
        )


def _e(
    pattern: str,
    section: str,
    stage: str,
    description: str,
    workflows: frozenset[str] = BOTH,
    *,
    name: str | None = None,
) -> OutputEntry:
    return OutputEntry(
        pattern=pattern,
        name=name or pattern,
        section=section,
        stage=stage,
        description=description,
        workflows=workflows,
    )


# First match wins, so list more specific patterns before general ones.
OUTPUT_ENTRIES: tuple[OutputEntry, ...] = (
    # ------------------------------------------------------------------ statistics
    _e(
        "stats/region_stats.csv",
        "stats",
        "export, import-annotations",
        "Main results table in atlas space: one row per channel × region × hemisphere × "
        "metric. Use this file to compare animals.",
        BRAIN,
    ),
    _e(
        "stats/region_stats.csv",
        "stats",
        "export, import-annotations, region-stats",
        "Main results table in atlas space: one row per channel × region × segment × "
        "hemisphere × metric, plus rollups (see `rollup_level`). Use this file to compare "
        "animals.",
        SPINAL,
    ),
    _e(
        "stats/region_stats_structure.csv",
        "stats",
        "region-stats",
        "Rollup to structures (laminae and funiculi combined).",
        SPINAL,
    ),
    _e(
        "stats/region_stats_division.csv",
        "stats",
        "region-stats",
        "Rollup to grey matter vs white matter.",
        SPINAL,
    ),
    _e(
        "stats/region_stats_horn.csv",
        "stats",
        "region-stats",
        "Rollup to dorsal horn, ventral horn and central region.",
        SPINAL,
    ),
    _e(
        "stats/region_stats_sample_top*.csv",
        "stats",
        "export",
        "Top regions per channel in sample space, one column per metric.",
        name="stats/region_stats_sample_topN.csv",
    ),
    _e(
        "stats/region_stats_top*.csv",
        "stats",
        "export",
        "Top N regions per channel (N = `analysis.top_n_regions`), one column per metric.",
        name="stats/region_stats_topN.csv",
    ),
    _e(
        "stats/region_stats_sample.csv",
        "stats",
        "export",
        "The same table computed in sample space (atlas labels warped onto the 20 µm "
        "registration grid). Not directly comparable with `region_stats.csv`.",
    ),
    _e(
        "stats/chan*_region_stats_sample.csv",
        "stats",
        "export",
        "Sample-space region statistics for one channel.",
        BRAIN,
        name="stats/chanNN_region_stats_sample.csv",
    ),
    _e(
        "stats/chan*_region_stats.csv",
        "stats",
        "export",
        "Region statistics for one channel (same columns as `region_stats.csv`).",
        name="stats/chanNN_region_stats.csv",
    ),
    _e(
        "stats/chan*_intensities.csv",
        "stats",
        "export",
        "Intensity per region for one channel, one row per region (MATLAB-style table).",
        BRAIN,
        name="stats/chanNN_intensities.csv",
    ),
    _e(
        "stats/chan*_intensities.json",
        "stats",
        "export",
        "The same per-channel intensities as JSON.",
        BRAIN,
        name="stats/chanNN_intensities.json",
    ),
    _e(
        "stats/*_region_counts.csv",
        "stats",
        "import-annotations, region-stats",
        "Cell counts and densities per region for one imported annotation.",
        name="stats/{label}_region_counts.csv",
    ),
    # ------------------------------------------------------------- atlas-space volumes
    _e(
        "volume_registered/chan_*_registered_atlas.tif",
        "volumes",
        "export",
        "Each channel warped into atlas space at the atlas resolution "
        "(written with `--save-volume`).",
        BRAIN,
        name="volume_registered/chan_NN_registered_atlas.tif",
    ),
    _e(
        "volume_registered/chan*_channel*.tiff",
        "volumes",
        "export",
        "Each channel warped onto the atlas grid (10 × 10 × 20 µm).",
        SPINAL,
        name="volume_registered/chanNN_channelN.tiff",
    ),
    _e(
        "volume_registered/template_registered.tiff",
        "volumes",
        "export",
        "Atlas template on the same grid, for visual comparison.",
        SPINAL,
    ),
    _e(
        "volume_registered/annotation_registered.tiff",
        "volumes",
        "export",
        "Atlas region labels on the same grid.",
        SPINAL,
    ),
    _e(
        "volume_registered/hemisphere_registered.tiff",
        "volumes",
        "export",
        "Left/right hemisphere mask on the same grid.",
        SPINAL,
    ),
    # ---------------------------------------------------------------- sample space
    _e(
        "volume_registered/sample_space/annotation_in_sample_20um.tif",
        "sample_space",
        "export --space sample",
        "Atlas region labels warped onto the sample (20 µm registration grid).",
    ),
    _e(
        "volume_registered/sample_space/annotation_boundary_in_sample_20um.tif",
        "sample_space",
        "export --space sample",
        "Atlas region outlines warped onto the sample.",
        BRAIN,
    ),
    _e(
        "volume_registered/sample_space/template_in_sample_20um.tif",
        "sample_space",
        "export --space sample",
        "Atlas template warped onto the sample.",
    ),
    _e(
        "volume_registered/sample_space/division_labels_in_sample_20um.tif",
        "sample_space",
        "export --space sample",
        "Atlas division labels warped onto the sample.",
        BRAIN,
    ),
    _e(
        "volume_registered/sample_space/segments_in_sample_20um.tif",
        "sample_space",
        "export --space sample",
        "Rostrocaudal segment labels (C1 … Co2) warped onto the straightened sample.",
        SPINAL,
    ),
    _e(
        "volume_registered/sample_space/hemisphere_in_sample_20um.tif",
        "sample_space",
        "export --space sample",
        "Left/right hemisphere mask warped onto the straightened sample.",
        SPINAL,
    ),
    _e(
        "volume_registered/sample_space/chan_*_sample_straight_20um.tif",
        "sample_space",
        "export --space sample",
        "Each straightened sample channel on the same grid, to overlay with the warped labels.",
        SPINAL,
        name="volume_registered/sample_space/chan_NN_sample_straight_20um.tif",
    ),
    _e(
        "volume_registered/sample_space/registration_export_sample.png",
        "sample_space",
        "export --space sample",
        "Preview of the warped atlas labels over the sample.",
        SPINAL,
    ),
    _e(
        "volume_registered/sample_space/sample_space_manifest.json",
        "sample_space",
        "export --space sample",
        "List of the sample-space files and how they were written (for example whether "
        "they are stored on the permuted grid).",
    ),
    # --------------------------------------------------------------------- imports
    _e(
        "imports/*_atlas_coords.npz",
        "imports",
        "import-annotations",
        "Imported points in atlas space (array `atlasptcoords`).",
        BRAIN,
        name="imports/{label}_atlas_coords.npz",
    ),
    _e(
        "imports/*_sample_coords.npz",
        "imports",
        "import-annotations",
        "Imported points on the 20 µm registration grid (array `regptcoords`).",
        BRAIN,
        name="imports/{label}_sample_coords.npz",
    ),
    _e(
        "imports/*_atlas_coords.csv",
        "imports",
        "import-annotations",
        "Atlas-space points as CSV (with `--write-csv`).",
        BRAIN,
        name="imports/{label}_atlas_coords.csv",
    ),
    _e(
        "imports/*_registered_atlas.tif",
        "imports",
        "import-annotations",
        "Imported mask warped into atlas space.",
        BRAIN,
        name="imports/{label}_registered_atlas.tif",
    ),
    _e(
        "imports/*_in_sample_20um.tif",
        "imports",
        "import-annotations",
        "Imported mask on the 20 µm registration grid.",
        BRAIN,
        name="imports/{label}_in_sample_20um.tif",
    ),
    _e(
        "imports/import_annotations_summary.json",
        "imports",
        "import-annotations",
        "Summary of what was imported: labels, kinds and feature counts.",
        BRAIN,
    ),
    _e(
        "volume_registered/*_atlas_coords.npz",
        "imports",
        "import-annotations",
        "Imported points in atlas space (array `atlasptcoords`).",
        SPINAL,
        name="volume_registered/{label}_atlas_coords.npz",
    ),
    _e(
        "volume_registered/*_sample_coords.npz",
        "imports",
        "import-annotations",
        "Imported points on the 20 µm registration grid (array `regptcoords`).",
        SPINAL,
        name="volume_registered/{label}_sample_coords.npz",
    ),
    _e(
        "volume_registered/*_atlas_coords.csv",
        "imports",
        "import-annotations",
        "Atlas-space points as CSV (with `--write-csv`).",
        SPINAL,
        name="volume_registered/{label}_atlas_coords.csv",
    ),
    _e(
        "volume_registered/import_annotations_summary.json",
        "imports",
        "import-annotations",
        "Summary of what was imported: labels, kinds and feature counts.",
        SPINAL,
    ),
    _e(
        "converted/*_points.csv",
        "imports",
        "convert-annotations",
        "Detections converted from a vendor export into `points.csv` format "
        "(native sample space). Input to import-annotations.",
        name="converted/{label}_points.csv",
    ),
    _e(
        "converted/convert_annotations_summary.json",
        "imports",
        "convert-annotations",
        "Summary of the conversion: source tool, files and point counts.",
    ),
    # ----------------------------------------------------------------------- plots
    _e(
        "plots/cord_heatmap_*.png",
        "plots",
        "plot-heatmap",
        "Segment × region heatmap of one metric and channel.",
        SPINAL,
        name="plots/cord_heatmap_{metric}_{channel}.png",
    ),
    _e(
        "plots/*.csv",
        "plots",
        "plot-heatmap, view",
        "The matrix behind a heatmap, saved from the plot command or the viewer.",
        SPINAL,
        name="plots/*.csv",
    ),
    # -------------------------------------------------------------------------- qc
    _e(
        "qc/dim*_initial_registration.png",
        "qc",
        "init-registration",
        "Coarse alignment preview with atlas outlines, one image per axis.",
        BRAIN,
        name="qc/dim{1,2,3}_initial_registration.png",
    ),
    _e(
        "qc/*_dim*_affine_registration.png",
        "qc",
        "register",
        "Preview slices with atlas outlines after the affine step.",
        BRAIN,
        name="qc/{name}_dim{1,2,3}_affine_registration.png",
    ),
    _e(
        "qc/*_dim*_bspline_registration.png",
        "qc",
        "register",
        "Preview slices with atlas outlines after the B-spline step. The outlines should "
        "follow the tissue more closely than the affine ones without going wavy.",
        BRAIN,
        name="qc/{name}_dim{1,2,3}_bspline_registration.png",
    ),
    _e(
        "qc/init_registration_diagnostics.json",
        "qc",
        "init-registration",
        "Numbers describing the coarse alignment.",
        BRAIN,
    ),
    _e(
        "qc/registration_diagnostics.json",
        "qc",
        "register",
        "Registration quality summary (overlap, landmark residuals, warnings).",
        BRAIN,
    ),
    _e(
        "qc/affine_fit_stats.json",
        "qc",
        "register",
        "Fit statistics of the affine step.",
        BRAIN,
    ),
    _e(
        "qc/correspondence_affine_stats.json",
        "qc",
        "register",
        "How the slice correspondence was used in the affine step.",
        BRAIN,
    ),
    _e(
        "qc/correspondence_landmark_stats.json",
        "qc",
        "register",
        "How the slice correspondence was used as landmarks.",
        BRAIN,
    ),
    _e(
        "qc/registration_initial_similarity.png",
        "qc",
        "init-registration",
        "Preview after the coarse similarity alignment.",
        SPINAL,
    ),
    _e(
        "qc/registration_initial_affine.png",
        "qc",
        "init-registration",
        "Preview after the affine alignment.",
        SPINAL,
    ),
    _e(
        "qc/registration_bspline.png",
        "qc",
        "register",
        "Preview after the B-spline registration.",
        SPINAL,
    ),
    # ----------------------------------------------------------------- checkpoints
    _e(
        "regopts.json",
        "checkpoints",
        "preprocess, init-registration",
        "Volume metadata (sizes, voxel sizes, channel files) and the coarse starting "
        "transform. Every later stage reads it.",
    ),
    _e(
        "sample_reference.json",
        "checkpoints",
        "preprocess",
        "The native (full-resolution) voxel grid of the stitched data. Annotation import "
        "uses it to place detections.",
    ),
    _e(
        "chan_*_sample_register_*um.tif",
        "checkpoints",
        "preprocess",
        "Each channel downsampled to the registration resolution (unpermuted sample grid).",
        BRAIN,
        name="chan_N_sample_register_20um.tif",
    ),
    _e(
        "brain_orientation.txt",
        "checkpoints",
        "check-orientation",
        "The sample orientation chosen in the orientation check.",
        BRAIN,
    ),
    _e(
        "cord_orientation.txt",
        "checkpoints",
        "check-orientation",
        "Whether the cord runs rostrocaudal or caudorostral in the stack.",
        SPINAL,
    ),
    _e(
        "spinal_alignment_opt.json",
        "checkpoints",
        "straighten",
        "Cord centerline and straightening settings.",
        SPINAL,
    ),
    _e(
        "longitudinal_correspondence.json",
        "checkpoints",
        "align-longitudinal",
        "Anchors matching positions along the sample to positions along the atlas.",
        SPINAL,
    ),
    _e(
        "slice_correspondence.json",
        "checkpoints",
        "align-slices",
        "Matches between sample slices and atlas planes. Pre-selects planes in "
        "match-points and guides register.",
        BRAIN,
    ),
    _e(
        "atlas2histology_tform.json",
        "checkpoints",
        "match-points",
        "Manual control points (sample and atlas landmarks) and the affine fitted to them.",
        BRAIN,
    ),
    _e(
        "corresponding_points.json",
        "checkpoints",
        "match-points",
        "Manual control points, in the layout of MATLAB's `corresponding_points.mat` "
        "(1-based).",
        SPINAL,
    ),
    _e(
        "atlas_content_manifest.json",
        "checkpoints",
        "init-registration, register",
        "Crop box applied to the atlas when `atlas.content_trim` is on.",
        BRAIN,
    ),
    _e(
        "transform_params.json",
        "checkpoints",
        "register",
        "The complete sample-to-atlas transform. Export and annotation import read it.",
    ),
    _e(
        "bspline_samp_to_atlas_20um.txt",
        "checkpoints",
        "register",
        "Elastix B-spline parameter file, sample to atlas.",
        BRAIN,
    ),
    _e(
        "bspline_atlas_to_samp_20um.txt",
        "checkpoints",
        "register",
        "Elastix B-spline parameter file, atlas to sample. Needed for sample-space export.",
        BRAIN,
    ),
    # ---------------------------------------------------------------------- cache
    _e(
        "cache/chan_*_sample_register_*um.tif",
        "cache",
        "preprocess",
        "Each channel downsampled to the registration resolution.",
        SPINAL,
        name="cache/chan_N_sample_register_20um.tif",
    ),
    _e(
        "cache/regvol_cord.tif",
        "cache",
        "preprocess",
        "The cord volume used for registration.",
        SPINAL,
    ),
    _e(
        "cache/straightvol.tif",
        "cache",
        "init-registration",
        "The straightened cord volume.",
        SPINAL,
    ),
    _e(
        "cache/register_cache.json",
        "cache",
        "preprocess",
        "Fingerprint of the cached volumes, so preprocess can skip reading the raw stack "
        "again.",
        SPINAL,
    ),
    _e(
        "cache/orientation_preview.npz",
        "cache",
        "check-orientation",
        "Cached preview for the orientation GUI.",
        SPINAL,
    ),
    _e(
        "cache/match_points_atlas.npz",
        "cache",
        "match-points",
        "Cached atlas slices for the control-point GUI.",
        SPINAL,
    ),
    _e(
        "transforms/affine_atlas_to_samp_20um.txt",
        "cache",
        "init-registration",
        "Elastix affine parameter file.",
        SPINAL,
    ),
    _e(
        "transforms/bspline_samp_to_atlas_20um.txt",
        "cache",
        "register",
        "Elastix B-spline parameter file, sample to atlas.",
        SPINAL,
    ),
    _e(
        "transforms/bspline_atlas_to_samp_20um.txt",
        "cache",
        "register",
        "Elastix B-spline parameter file, atlas to sample. Needed for sample-space export.",
        SPINAL,
    ),
)

#: Tidy (long-format) statistics tables that get a metric/channel summary.
_TIDY_STATS = frozenset(
    {
        "stats/region_stats.csv",
        "stats/region_stats_sample.csv",
        "stats/region_stats_structure.csv",
        "stats/region_stats_division.csv",
        "stats/region_stats_horn.csv",
    }
)

#: Column → meaning, for the tidy statistics tables.
COLUMN_DESCRIPTIONS: dict[str, str] = {
    "sample": "Sample name (`sample.name`).",
    "channel": "Channel number, or the import label for cell counts.",
    "atlas": "Atlas used for registration.",
    "parcellation_index": "Atlas label value of the region.",
    "acronym": "Region acronym.",
    "name": "Region name.",
    "structure": "Parent structure of the region.",
    "division": "Coarse division (e.g. grey or white matter).",
    "segment": "Rostrocaudal segment (C1 … Co2).",
    "rollup_level": "`region`, `structure`, `division` or `horn`.",
    "hemisphere": "`left`, `right` or `whole`.",
    "metric": "What `value` measures (see the metrics list).",
    "value": "The number.",
}

#: Metric → meaning.
METRIC_DESCRIPTIONS: dict[str, str] = {
    "median_intensity": "Median voxel intensity in the region.",
    "relative_median_intensity": "Median intensity relative to background "
    "(`analysis.relative_intensity_to: background`).",
    "mean_intensity": "Mean voxel intensity in the region.",
    "std": "Standard deviation of voxel intensity in the region.",
    "variance": "Variance of voxel intensity in the region.",
    "volume_mm3": "Region volume in mm³.",
    "cell_count": "Number of imported points in the region.",
    "cell_density": "Imported points per mm³.",
}


# ----------------------------------------------------------------------------- scanning
@dataclass
class _Scan:
    matched: dict[OutputEntry, list[str]] = field(default_factory=dict)
    other: list[str] = field(default_factory=list)
    truncated: bool = False
    has_work_dir: bool = False


def entries_for(workflow: str) -> list[OutputEntry]:
    """Known outputs of one workflow, in display order."""
    return [entry for entry in OUTPUT_ENTRIES if workflow in entry.workflows]


def _iter_files(save_path: Path, scan: _Scan) -> Iterable[str]:
    count = 0
    for root, dirs, files in os.walk(save_path):
        rel_root = Path(root).relative_to(save_path)
        if rel_root == Path("."):
            if any(d in _SKIP_DIRS for d in dirs):
                scan.has_work_dir = True
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or name.endswith(".tmp"):
                continue
            rel = (rel_root / name).as_posix()
            if rel == RESULTS_INFO_FILENAME:
                continue
            count += 1
            if count > _MAX_SCAN_FILES:
                scan.truncated = True
                return
            yield rel


def scan_save_path(save_path: Path, workflow: str) -> _Scan:
    scan = _Scan()
    entries = entries_for(workflow)
    for rel in _iter_files(save_path, scan):
        entry = next((e for e in entries if e.matches(rel)), None)
        if entry is None:
            scan.other.append(rel)
        else:
            scan.matched.setdefault(entry, []).append(rel)
    return scan


@dataclass(frozen=True)
class _CsvSummary:
    columns: list[str]
    values: dict[str, list[str]]


def _summarize_csv(path: Path) -> _CsvSummary | None:
    import pandas as pd

    try:
        columns = [str(c) for c in pd.read_csv(path, nrows=0).columns]
        values: dict[str, list[str]] = {}
        wanted = [c for c in ("metric", "channel", "hemisphere", "segment") if c in columns]
        if wanted and path.stat().st_size <= _MAX_CSV_SCAN_BYTES:
            frame = pd.read_csv(path, usecols=wanted, dtype=str)
            for col in wanted:
                values[col] = [str(v) for v in pd.unique(frame[col].dropna())]
        return _CsvSummary(columns=columns, values=values)
    except Exception:  # noqa: BLE001 - a malformed CSV must not break the guide
        return None


# ---------------------------------------------------------------------------- rendering
def _format_values(values: list[str]) -> str:
    shown = ", ".join(f"`{v}`" for v in values[:_MAX_VALUES_SHOWN])
    if len(values) > _MAX_VALUES_SHOWN:
        shown += f", … ({len(values)} in total)"
    return shown


def _render_entry(entry: OutputEntry, files: list[str], save_path: Path) -> list[str]:
    is_family = "*" in entry.pattern
    head = f"- **`{entry.name if is_family else files[0]}`**"
    if is_family:
        head += f" ({len(files)} file{'s' if len(files) != 1 else ''})"
    lines = [f"{head}: {entry.description} *Written by: {entry.stage}.*"]
    if is_family:
        for rel in files[:_MAX_FILES_PER_ENTRY]:
            lines.append(f"  - `{rel}`")
        if len(files) > _MAX_FILES_PER_ENTRY:
            lines.append(f"  - … and {len(files) - _MAX_FILES_PER_ENTRY} more")
    for rel in files:
        if rel not in _TIDY_STATS:
            continue
        summary = _summarize_csv(save_path / rel)
        if summary is None:
            continue
        for col, label in (
            ("metric", "Metrics"),
            ("channel", "Channels"),
            ("hemisphere", "Hemispheres"),
            ("segment", "Segments"),
        ):
            if summary.values.get(col):
                lines.append(f"  - {label}: {_format_values(summary.values[col])}")
    return lines


def _render_column_guide(save_path: Path, scan: _Scan) -> list[str]:
    present = {rel for files in scan.matched.values() for rel in files}
    candidates = ("stats/region_stats.csv", "stats/region_stats_sample.csv")
    source = next((rel for rel in candidates if rel in present), None)
    if source is None:
        return []
    summary = _summarize_csv(save_path / source)
    if summary is None:
        return []
    lines = [
        "## Reading the statistics tables",
        "",
        f"`{source}` and the other long-format tables in `stats/` have one measurement per "
        "row. Filter by `metric` (and `channel`, `hemisphere`, …) before plotting or "
        "averaging.",
        "",
        "| Column | Meaning |",
        "|--------|---------|",
    ]
    for col in summary.columns:
        lines.append(f"| `{col}` | {COLUMN_DESCRIPTIONS.get(col, '')} |")
    metrics = summary.values.get("metric", [])
    if metrics:
        lines += ["", "Metrics in this file:", ""]
        for metric in metrics:
            meaning = METRIC_DESCRIPTIONS.get(metric, "")
            lines.append(f"- `{metric}`" + (f": {meaning}" if meaning else ""))
    lines.append("")
    return lines


def _render_start_here(present: set[str], workflow: str) -> list[str]:
    tips: list[str] = []
    if "stats/region_stats.csv" in present:
        tips.append(
            "- **Numbers per region:** `stats/region_stats.csv` (atlas space). "
            "This is the table to compare animals with."
        )
    if any(rel.startswith("volume_registered/") and "/" not in rel[18:] for rel in present):
        tips.append("- **Images in atlas space:** the volumes in `volume_registered/`.")
    if any(rel.startswith("volume_registered/sample_space/") for rel in present):
        tips.append(
            "- **Atlas drawn on your sample:** `volume_registered/sample_space/`."
        )
    if any(rel.startswith("qc/") and rel.endswith(".png") for rel in present):
        tips.append(
            "- **Check the registration first:** the PNG previews in `qc/` "
            "(atlas outlines should follow the tissue)."
        )
    if not tips:
        return []
    view = f"lightsuite {workflow} " + ("view-registration" if workflow == "brain" else "view")
    tips.append(f"- **Browse everything in Napari:** `{view} -c <config>`.")
    return ["## Start here", "", *tips, ""]


def _render_notes(present: set[str], scan: _Scan) -> list[str]:
    notes = [
        "- Rerunning a stage overwrites its outputs, so rerun the stages after it too. "
        "This file is rewritten after every stage.",
    ]
    if "stats/region_stats.csv" in present and "stats/region_stats_sample.csv" in present:
        notes.append(
            "- Do not compare `region_stats.csv` and `region_stats_sample.csv` directly: "
            "voxel sizes, region volumes and density denominators differ. Use atlas space "
            "across animals and sample space for quality checks."
        )
    if scan.has_work_dir:
        notes.append(
            "- `_work/` holds temporary Elastix files. It is normally removed when a stage "
            "succeeds; if it is still here, a stage may have stopped early."
        )
    if scan.truncated:
        notes.append(
            f"- Only the first {_MAX_SCAN_FILES} files were scanned; this folder holds more."
        )
    return ["## Notes", "", *notes, ""]


def build_results_info(
    save_path: Path,
    workflow: str,
    *,
    sample_name: str,
    atlas_label: str,
    stage: str | None = None,
    config_name: str | None = None,
    now: datetime | None = None,
) -> str:
    """Return the Markdown text of ``info_results.md`` for ``save_path``."""
    from lightsuite import __version__

    save_path = Path(save_path).expanduser()
    scan = scan_save_path(save_path, workflow)
    present = {rel for files in scan.matched.values() for rel in files}
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    workflow_label = "brain lightsheet" if workflow == "brain" else "spinal cord"

    spaces = []
    if "stats/region_stats.csv" in present or any(
        e.section == "volumes" for e in scan.matched
    ):
        spaces.append("atlas")
    if any(e.section == "sample_space" for e in scan.matched) or (
        "stats/region_stats_sample.csv" in present
    ):
        spaces.append("sample")

    lines = [
        f"# PyLightSuite results: {sample_name}",
        "",
        "This file lists what PyLightSuite has written to this folder and what each file "
        "is. Paths are relative to this folder. It is regenerated automatically; edits "
        "will be overwritten.",
        "",
        f"- **Workflow:** {workflow_label}",
        f"- **Atlas:** {atlas_label}",
        f"- **Output spaces present:** {', '.join(spaces) if spaces else 'none yet'}",
    ]
    if config_name:
        lines.append(f"- **Config:** `{config_name}`")
    updated = f"{timestamp}" + (f", after stage `{stage}`" if stage else "")
    lines += [
        f"- **Updated:** {updated}",
        f"- **PyLightSuite version:** {__version__}",
        "",
        "The full list of outputs is in the PyLightSuite documentation, `docs/outputs.md`.",
        "",
    ]
    lines += _render_start_here(present, workflow)

    for section, title in SECTIONS.items():
        section_entries = [e for e in entries_for(workflow) if e.section == section]
        rendered: list[str] = []
        for entry in section_entries:
            files = scan.matched.get(entry)
            if files:
                rendered += _render_entry(entry, files, save_path)
        if rendered:
            lines += [f"## {title}", "", *rendered, ""]
        if section == "stats" and rendered:
            lines += _render_column_guide(save_path, scan)

    if scan.other:
        lines += [
            "## Other files",
            "",
            "Files this version of PyLightSuite does not describe (older layouts, files "
            "from other tools, or outputs added after this guide was written):",
            "",
        ]
        lines += [f"- `{rel}`" for rel in scan.other[:_MAX_OTHER_FILES]]
        if len(scan.other) > _MAX_OTHER_FILES:
            lines.append(f"- … and {len(scan.other) - _MAX_OTHER_FILES} more")
        lines.append("")

    lines += _render_notes(present, scan)
    return "\n".join(lines).rstrip() + "\n"


def _atlas_label(config: Any, workflow: str) -> str:
    if workflow == "spinal":
        return "Fiederling spinal cord atlas"
    atlas = config.atlas
    if getattr(atlas, "brainglobe_name", None):
        return f"{atlas.brainglobe_name} (BrainGlobe)"
    provider = getattr(atlas.provider, "value", atlas.provider)
    return f"{provider} {atlas.resolution_um:g} µm"


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_results_info(
    config: Any,
    workflow: str,
    *,
    stage: str | None = None,
    config_path: str | Path | None = None,
) -> Path | None:
    """Write ``<save_path>/info_results.md``. Returns ``None`` if ``save_path`` is missing."""
    save_path = Path(config.sample.save_path).expanduser()
    if not save_path.is_dir():
        return None
    text = build_results_info(
        save_path,
        workflow,
        sample_name=config.sample.name,
        atlas_label=_atlas_label(config, workflow),
        stage=stage,
        config_name=Path(config_path).name if config_path else None,
    )
    out = save_path / RESULTS_INFO_FILENAME
    _write_atomic(out, text)
    return out


def refresh_results_info(
    config: Any,
    workflow: str,
    *,
    stage: str | None = None,
    config_path: str | Path | None = None,
) -> Path | None:
    """Like :func:`write_results_info`, but never raises: failures only log a warning."""
    try:
        return write_results_info(config, workflow, stage=stage, config_path=config_path)
    except Exception as exc:  # noqa: BLE001 - the guide must never fail a stage
        from lightsuite.reporter import emit_pipeline_message

        emit_pipeline_message(f"[yellow]Could not update {RESULTS_INFO_FILENAME}: {exc}[/yellow]")
        return None


# ------------------------------------------------------------------- documentation page
def render_outputs_reference() -> str:
    """Return the text of ``docs/outputs.md``, generated from :data:`OUTPUT_ENTRIES`."""
    lines = [
        "# Output files",
        "",
        "<!-- Generated from src/lightsuite/export/results_info.py. Do not edit by hand:",
        "     uv run python -m lightsuite.export.results_info --write-docs docs/outputs.md -->",
        "",
        "Every stage writes into `sample.save_path`. After each stage PyLightSuite also "
        f"writes **`{RESULTS_INFO_FILENAME}`** there: a short guide listing the files that "
        "actually exist in that folder, what each one is, which metrics and channels the "
        "statistics tables contain, and what the table columns mean.",
        "",
        "Rebuild it at any time, for example for a sample processed with an older version:",
        "",
        "```bash",
        "uv run lightsuite brain info -c my_mouse.yaml",
        "uv run lightsuite spinal info -c my_spinal.yaml",
        "```",
        "",
        "Files that PyLightSuite does not recognise are listed under *Other files*. "
        "Temporary folders (`_work/`) are not listed.",
        "",
        "The tables below list every output each workflow can produce. `{label}` is the "
        "annotation label, `NN` / `N` the channel number and `{name}` the sample name.",
        "",
    ]
    for workflow, title in (("brain", "Brain lightsheet"), ("spinal", "Spinal cord")):
        lines += [f"## {title}", ""]
        for section, section_title in SECTIONS.items():
            entries = [e for e in entries_for(workflow) if e.section == section]
            if not entries:
                continue
            lines += [
                f"### {section_title}",
                "",
                "| File | Written by | What it is |",
                "|------|------------|------------|",
            ]
            for entry in entries:
                lines.append(f"| `{entry.name}` | {entry.stage} | {entry.description} |")
            lines.append("")
    lines += [
        "## Statistics table columns",
        "",
        "The long-format tables (`region_stats.csv`, `region_stats_sample.csv` and the spinal "
        "rollups) have one measurement per row. `segment` and `rollup_level` appear only in "
        "spinal cord tables.",
        "",
        "| Column | Meaning |",
        "|--------|---------|",
    ]
    lines += [f"| `{col}` | {text} |" for col, text in COLUMN_DESCRIPTIONS.items()]
    lines += [
        "",
        "## Metrics",
        "",
        "| Metric | Meaning |",
        "|--------|---------|",
    ]
    lines += [f"| `{name}` | {text} |" for name, text in METRIC_DESCRIPTIONS.items()]
    lines += [
        "",
        "Choose intensity metrics with `analysis.intensity_metrics`. `cell_count` and "
        "`cell_density` appear after an annotation import. See "
        "[registration spaces](registration_spaces.md) for why atlas-space and "
        "sample-space tables should not be compared directly.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-docs", type=Path, help="Write the outputs reference page here.")
    args = parser.parse_args(argv)
    text = render_outputs_reference()
    if args.write_docs:
        args.write_docs.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
