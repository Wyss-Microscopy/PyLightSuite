"""Per-region, per-segment intensity statistics for registered spinal cord volumes."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lightsuite.analysis.cord_counts import (
    CORD_ATLAS_ID,
    CORD_HEMISPHERE,
    CORD_NATIVE_VOXEL_UM_YXZ,
    CORD_TIDY_COLUMNS,
    assign_segment_names,
)
from lightsuite.analysis.cord_ontology import CordRegionTable
from lightsuite.analysis.hemisphere import SIDE_LABELS
from lightsuite.analysis.intensity_metrics import normalize_intensity_metrics

_META_COLUMNS = ["acronym", "name", "structure", "division"]


def _attach_cord_region_metadata(
    df: pd.DataFrame, region_table: CordRegionTable | None
) -> pd.DataFrame:
    out = df.copy()
    if region_table is not None:
        meta = region_table.df[["parcellation_index", *_META_COLUMNS]].drop_duplicates(
            subset="parcellation_index"
        )
        out = out.drop(columns=[c for c in _META_COLUMNS if c in out.columns])
        out = out.merge(meta, on="parcellation_index", how="left")
    else:
        for col in _META_COLUMNS:
            if col not in out.columns:
                out[col] = pd.NA
    return out


def _voxel_mm3_yxz(voxel_um: tuple[float, float, float]) -> float:
    vy, vx, vz = voxel_um
    return (vy * 1e-3) * (vx * 1e-3) * (vz * 1e-3)


def _segment_index_per_z(nz: int, segments: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Segment index for every plane along the last axis (-1 = no segment) and the names."""
    seg_per_z = assign_segment_names(np.arange(1, nz + 1, dtype=np.int64), segments)
    names, inverse = np.unique(seg_per_z.astype(str), return_inverse=True)
    idx = inverse.astype(np.int64)
    if "" in names:
        empty = int(np.flatnonzero(names == "")[0])
        idx = np.where(idx == empty, -1, idx - (idx > empty))
        names = names[names != ""]
    return idx, names


def _grouped_stats(keys: np.ndarray, values: np.ndarray) -> dict[str, np.ndarray]:
    """Per-key count, median, mean and population std of ``values``.

    One sort by (key, value), then reductions over the group boundaries. The median is
    the middle of the sorted group (mean of the two middle values when even), as pandas.
    """
    vals = np.asarray(values, dtype=np.float64)
    keys = np.asarray(keys, dtype=np.int64)
    if vals.size == 0:
        empty = np.zeros(0)
        return {"key": keys[:0], "n": empty, "median": empty, "mean": empty, "std": empty}
    integral = bool(np.all(vals >= 0) and np.all(vals <= 65535) and np.all(vals == np.floor(vals)))
    if integral and keys.max() < (1 << 46):
        order = np.argsort(keys * 65536 + vals.astype(np.int64), kind="stable")
    else:
        order = np.lexsort((vals, keys))
    k = keys[order]
    v = vals[order]
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    n = np.diff(np.r_[starts, k.size])
    mean = np.add.reduceat(v, starts) / n
    median = 0.5 * (v[starts + (n - 1) // 2] + v[starts + n // 2])
    dev = v - np.repeat(mean, n)
    std = np.sqrt(np.add.reduceat(dev * dev, starts) / n)
    std[n <= 1] = 0.0
    return {"key": k[starts], "n": n.astype(np.int64), "median": median, "mean": mean, "std": std}


def _background_median_by_segment(
    av_flat: np.ndarray, seg_flat: np.ndarray, val_flat: np.ndarray, seg_names: np.ndarray
) -> dict[str, float]:
    bg = (av_flat == 0) & (seg_flat >= 0)
    if not np.any(bg):
        return {}
    st = _grouped_stats(seg_flat[bg], val_flat[bg])
    return {
        str(seg_names[int(key)]): float(med)
        for key, med in zip(st["key"], st["median"], strict=True)
    }


def _aggregate_intensity_groups(
    region: np.ndarray,
    seg: np.ndarray,
    side: np.ndarray,
    values: np.ndarray,
    *,
    seg_names: np.ndarray,
    side_labels: tuple[str, ...],
    voxel_mm3: float,
    bg_medians: dict[str, float] | None,
) -> pd.DataFrame:
    """One row per region x segment x hemisphere, sorted like ``groupby(sort=True)``."""
    if region.size == 0:
        return pd.DataFrame()
    n_seg = max(len(seg_names), 1)
    n_side = len(side_labels)
    key = (region.astype(np.int64) * n_seg + seg.astype(np.int64)) * n_side + side.astype(np.int64)
    st = _grouped_stats(key, values)
    gkey = st["key"]
    side_idx = gkey % n_side
    seg_idx = (gkey // n_side) % n_seg
    region_id = gkey // (n_side * n_seg)
    grouped = pd.DataFrame(
        {
            "parcellation_index": region_id.astype(np.int64),
            "segment": seg_names[seg_idx].astype(object),
            "hemisphere": np.asarray(side_labels, dtype=object)[side_idx],
            "median_intensity": st["median"],
            "mean_intensity": st["mean"],
            "std": st["std"],
            "n_voxels": st["n"],
        }
    )
    grouped = grouped.sort_values(
        ["parcellation_index", "segment", "hemisphere"], kind="stable"
    ).reset_index(drop=True)
    grouped["variance"] = grouped["std"].astype(np.float64) ** 2
    grouped["volume_mm3"] = grouped["n_voxels"].astype(np.float64) * voxel_mm3
    if bg_medians:
        bg = grouped["segment"].map(bg_medians).astype(np.float64).to_numpy()
        med = grouped["median_intensity"].to_numpy(dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = (med - bg) / bg
        rel[~np.isfinite(bg) | (bg == 0.0)] = np.nan
        grouped["relative_median_intensity"] = rel
    return grouped


def parcellate_cord_intensities(
    registered_volume: np.ndarray,
    annotation: np.ndarray,
    segments: pd.DataFrame,
    *,
    sample: str,
    channel: int | str,
    region_table: CordRegionTable | None = None,
    voxel_um_yxz: tuple[float, float, float] = CORD_NATIVE_VOXEL_UM_YXZ,
    atlas_id: str = CORD_ATLAS_ID,
    relative_to: str = "none",
    drop_background_regions: bool = True,
    hemisphere_side: np.ndarray | None = None,
    split_hemispheres: bool = False,
    keep_whole: bool = False,
    intensity_metrics: list[str] | None = None,
) -> pd.DataFrame:
    """Compute per-region intensity statistics per Fiederling region and segment."""
    av = np.asarray(annotation)
    vol = np.asarray(registered_volume)
    if av.shape != vol.shape:
        msg = f"Annotation shape {av.shape} != intensity volume {vol.shape}"
        raise ValueError(msg)
    if av.ndim != 3:
        msg = f"Expected 3D volumes, got shape {av.shape}"
        raise ValueError(msg)
    if split_hemispheres:
        if hemisphere_side is None:
            msg = "hemisphere_side is required when split_hemispheres=True."
            raise ValueError(msg)
        side_shape = np.asarray(hemisphere_side).shape
        if side_shape != av.shape:
            msg = f"Hemisphere side shape {side_shape} != annotation {av.shape}"
            raise ValueError(msg)

    seg_per_z, seg_names = _segment_index_per_z(av.shape[2], segments)
    if seg_names.size == 0:
        return pd.DataFrame(columns=CORD_TIDY_COLUMNS)
    av_flat = av.reshape(-1).astype(np.int64, copy=False)
    seg_flat = np.broadcast_to(seg_per_z, av.shape).reshape(-1)
    val_flat = vol.reshape(-1).astype(np.float64, copy=False)
    finite = ~np.isnan(val_flat)  # pandas median/mean/count skip NaN

    rel_mode = str(relative_to).strip().lower()
    voxel_mm3 = _voxel_mm3_yxz(voxel_um_yxz)
    bg_medians = (
        _background_median_by_segment(
            av_flat[finite], seg_flat[finite], val_flat[finite], seg_names
        )
        if rel_mode == "background"
        else None
    )
    keep = finite & (seg_flat >= 0)
    if drop_background_regions:
        keep &= av_flat > 0
    frames: list[pd.DataFrame] = []

    if split_hemispheres:
        side_flat = np.asarray(hemisphere_side, dtype=np.int8).reshape(-1)
        sel = keep & (side_flat >= 0) & (side_flat < len(SIDE_LABELS))
        if np.any(sel):
            frames.append(
                _aggregate_intensity_groups(
                    av_flat[sel],
                    seg_flat[sel],
                    side_flat[sel],
                    val_flat[sel],
                    seg_names=seg_names,
                    side_labels=SIDE_LABELS,
                    voxel_mm3=voxel_mm3,
                    bg_medians=bg_medians,
                )
            )

    if not split_hemispheres or keep_whole:
        if np.any(keep):
            frames.append(
                _aggregate_intensity_groups(
                    av_flat[keep],
                    seg_flat[keep],
                    np.zeros(int(keep.sum()), dtype=np.int8),
                    val_flat[keep],
                    seg_names=seg_names,
                    side_labels=(CORD_HEMISPHERE,),
                    voxel_mm3=voxel_mm3,
                    bg_medians=bg_medians,
                )
            )

    if not frames:
        return pd.DataFrame(columns=CORD_TIDY_COLUMNS)

    grouped = pd.concat(frames, ignore_index=True)
    requested = normalize_intensity_metrics(intensity_metrics)
    metric_columns = [metric for metric in requested if metric in grouped.columns]
    if rel_mode == "background" and "relative_median_intensity" in grouped.columns:
        if "relative_median_intensity" not in metric_columns:
            metric_columns.append("relative_median_intensity")

    records: list[dict] = []
    for row in grouped.itertuples(index=False):
        base = {
            "parcellation_index": int(row.parcellation_index),
            "segment": str(row.segment),
            "hemisphere": str(row.hemisphere),
        }
        for metric in metric_columns:
            value = float(getattr(row, metric))
            if not np.isfinite(value):
                continue
            records.append({**base, "metric": metric, "value": value})

    df = pd.DataFrame.from_records(
        records, columns=["parcellation_index", "segment", "hemisphere", "metric", "value"]
    )
    df["sample"] = sample
    df["channel"] = channel
    df["atlas"] = atlas_id
    df["rollup_level"] = "region"
    df = _attach_cord_region_metadata(df, region_table)
    return df.reindex(columns=CORD_TIDY_COLUMNS)


def _relative_to_background(median: float, background: float) -> float:
    if not np.isfinite(background) or background == 0.0:
        return np.nan
    return (median - background) / background


__all__ = [
    "parcellate_cord_intensities",
]
