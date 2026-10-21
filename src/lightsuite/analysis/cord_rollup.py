"""Volume-weighted rollups of spinal cord region stats to division/structure levels."""

from __future__ import annotations

import re
from typing import Literal

import numpy as np
import pandas as pd

from lightsuite.analysis.cord_counts import CORD_TIDY_COLUMNS

RollupLevel = Literal["division", "structure", "horn"]

_HORN_ACRONYMS = frozenset({"DH", "VH", "C"})
_SUM_METRICS = frozenset({"cell_count", "volume_mm3"})
_WEIGHTED_METRICS = frozenset(
    {"median_intensity", "mean_intensity", "relative_median_intensity", "std", "variance"}
)
_REGION_ROLLUP_LEVEL = "region"


def _parse_children_ids(value: object) -> list[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, (int, np.integer)):
        return [int(value)]
    if isinstance(value, float):
        return [int(value)]
    text = str(value).strip()
    if not text:
        return []
    return [int(token) for token in re.findall(r"\d+", text)]


def _children_map(regions_df: pd.DataFrame) -> dict[int, list[int]]:
    """Direct children of every region, from ``parent_ID`` and ``children_IDs``."""
    children: dict[int, set[int]] = {}
    if "parent_ID" in regions_df.columns:
        for rid, parent in zip(regions_df["id"], regions_df["parent_ID"], strict=True):
            if pd.isna(parent):
                continue
            children.setdefault(int(parent), set()).add(int(rid))
    if "children_IDs" in regions_df.columns:
        # first row per id, as ``regions_df.loc[id == parent, "children_IDs"].iloc[0]``
        first = regions_df.drop_duplicates(subset="id", keep="first")
        for rid, value in zip(first["id"], first["children_IDs"], strict=True):
            children.setdefault(int(rid), set()).update(_parse_children_ids(value))
    return {key: sorted(vals) for key, vals in children.items()}


def _descendants_from_map(
    parent_id: int,
    children: dict[int, list[int]],
    cache: dict[int, list[int]],
) -> list[int]:
    parent_id = int(parent_id)
    if parent_id in cache:
        return cache[parent_id]
    cache[parent_id] = []  # guards against cycles in malformed tables
    descendants: set[int] = set()
    for child_id in children.get(parent_id, []):
        descendants.add(child_id)
        descendants.update(_descendants_from_map(child_id, children, cache))
    cache[parent_id] = sorted(descendants)
    return cache[parent_id]


def get_descendants(parent_id: int, regions_df: pd.DataFrame) -> list[int]:
    """Return all descendant atlas ids for ``parent_id`` (recursive, MATLAB-compatible)."""
    return list(_descendants_from_map(parent_id, _children_map(regions_df), {}))


def _resolve_target_row(regions_df: pd.DataFrame, target: str) -> pd.Series | None:
    if target.isdigit():
        rows = regions_df.loc[regions_df["id"] == int(target)]
        return rows.iloc[0] if not rows.empty else None

    rows = regions_df.loc[regions_df["acronym"].astype(str) == target]
    if rows.empty and target == "lf":
        rows = regions_df.loc[regions_df["acronym"].astype(str) == "lfc"]
    return rows.iloc[0] if not rows.empty else None


def _horn_lookup(regions_df: pd.DataFrame) -> dict[int, tuple[str, int]]:
    """``id -> (acronym, parent_ID)``, keeping the first row per id (like ``.loc``)."""
    first = regions_df.drop_duplicates(subset="id", keep="first")
    parents = (
        first["parent_ID"] if "parent_ID" in first.columns else pd.Series(0, index=first.index)
    )
    acronyms = first["acronym"] if "acronym" in first.columns else pd.Series("", index=first.index)
    out: dict[int, tuple[str, int]] = {}
    for rid, acronym, parent in zip(first["id"], acronyms, parents, strict=True):
        out[int(rid)] = (str(acronym), int(parent) if not pd.isna(parent) and parent else 0)
    return out


def _resolve_horn(region_id: int, lookup: dict[int, tuple[str, int]]) -> str | None:
    current = int(region_id)
    visited: set[int] = set()
    while current and current not in visited:
        visited.add(current)
        if current not in lookup:
            return None
        acronym, parent = lookup[current]
        if acronym in _HORN_ACRONYMS:
            return acronym
        if parent <= 0 or parent == current:
            break
        current = parent
    return None


def resolve_horn_acronym(region_id: int, regions_df: pd.DataFrame) -> str | None:
    """Return DH, VH, or C by walking ``parent_ID`` ancestors from a finest-level region."""
    return _resolve_horn(region_id, _horn_lookup(regions_df))


def _horn_member_ids(
    region_ids: list[int] | pd.Index,
    regions_df: pd.DataFrame,
    horn_acronym: str,
    *,
    lookup: dict[int, tuple[str, int]] | None = None,
) -> list[int]:
    """Finest-level region ids assigned exclusively to one horn target."""
    if lookup is None:
        lookup = _horn_lookup(regions_df)
    target = str(horn_acronym)
    return sorted(int(rid) for rid in region_ids if _resolve_horn(int(rid), lookup) == target)


def resolve_rollup_targets(
    aggtype: RollupLevel, regions_df: pd.DataFrame
) -> list[tuple[int, str, str]]:
    """Return rollup targets as ``(id, acronym, name)`` tuples."""
    agg = aggtype.lower()
    if agg == "division":
        target_keys = ["GM", "WM"]
    elif agg == "structure":
        target_keys = [str(rid) for rid in range(201, 211)] + ["df", "lf", "vf"]
    elif agg == "horn":
        # DH = Dorsal Horn, VH = Ventral Horn, C = Central (Lamina X + canal)
        target_keys = ["DH", "VH", "C"]
    else:
        msg = f"aggtype must be 'division', 'structure', or 'horn', got {aggtype!r}"
        raise ValueError(msg)

    targets: list[tuple[int, str, str]] = []
    for key in target_keys:
        row = _resolve_target_row(regions_df, key)
        if row is None:
            continue
        targets.append((int(row["id"]), str(row["acronym"]), str(row["name"])))
    return targets


def _aggregate_metric(
    metric: str,
    values: pd.Series,
    volumes: pd.Series,
) -> float:
    if values.empty:
        return np.nan

    if metric in _SUM_METRICS:
        return float(values.sum())

    if metric == "cell_density":
        if "cell_count" not in values.index.names and volumes.empty:
            return float(values.mean())
        # Recomputed upstream when both count and volume exist.
        return float(values.iloc[0]) if len(values) == 1 else float(np.nan)

    if metric in _WEIGHTED_METRICS:
        if volumes.empty or float(volumes.sum()) <= 0:
            return float(values.mean())
        weights = volumes / volumes.sum()
        aligned = values.reindex(weights.index)
        mask = aligned.notna() & weights.notna()
        if not mask.any():
            return np.nan
        return float((aligned[mask] * weights[mask]).sum())

    return float(values.mean())


def _region_metadata_row(regions_df: pd.DataFrame, region_id: int) -> dict[str, object]:
    row = regions_df.loc[regions_df["id"] == int(region_id)]
    if row.empty:
        return {
            "parcellation_index": int(region_id),
            "acronym": "",
            "name": "",
            "structure": "",
            "division": "",
        }
    item = row.iloc[0]
    parent_acronym = str(item.get("parent_acronym", "")) if "parent_acronym" in row.columns else ""
    return {
        "parcellation_index": int(region_id),
        "acronym": str(item.get("acronym", "")),
        "name": str(item.get("name", "")),
        "structure": parent_acronym,
        "division": parent_acronym if str(parent_acronym) in {"GM", "WM", "SC", "CNS"} else "",
    }


def rollup_cord_tidy(
    df: pd.DataFrame,
    regions_df: pd.DataFrame,
    aggtype: RollupLevel,
) -> pd.DataFrame:
    """Roll finest-level cord stats up to division or structure targets."""
    if df.empty:
        return pd.DataFrame(columns=CORD_TIDY_COLUMNS)

    work = df.copy()
    if "rollup_level" not in work.columns:
        work["rollup_level"] = _REGION_ROLLUP_LEVEL
    work = work[work["rollup_level"] == _REGION_ROLLUP_LEVEL].copy()
    if work.empty:
        return pd.DataFrame(columns=CORD_TIDY_COLUMNS)

    regions = regions_df.copy()
    regions["id"] = regions["id"].astype(int)
    targets = resolve_rollup_targets(aggtype, regions)
    if not targets:
        return pd.DataFrame(columns=CORD_TIDY_COLUMNS)

    group_cols = ["sample", "channel", "atlas", "hemisphere", "segment"]
    records: list[dict] = []

    # Everything that depends only on the atlas is resolved once, not once per group.
    is_horn = aggtype.lower() == "horn"
    horn_lookup = _horn_lookup(regions) if is_horn else {}
    children = {} if is_horn else _children_map(regions)
    desc_cache: dict[int, list[int]] = {}
    target_info = []
    for target_id, target_acronym, _name in targets:
        members = (
            None
            if is_horn
            else sorted(set(_descendants_from_map(target_id, children, desc_cache)) | {target_id})
        )
        target_info.append((target_acronym, members, _region_metadata_row(regions, target_id)))

    for group_key, group_df in work.groupby(group_cols, sort=False):
        if not isinstance(group_key, tuple):
            group_key = (group_key,)
        group_info = dict(zip(group_cols, group_key, strict=True))

        pivot = group_df.pivot_table(
            index="parcellation_index",
            columns="metric",
            values="value",
            aggfunc="first",
        )
        if pivot.empty:
            continue

        volume_series = (
            pivot["volume_mm3"] if "volume_mm3" in pivot.columns else pd.Series(dtype=float)
        )
        count_series = (
            pivot["cell_count"] if "cell_count" in pivot.columns else pd.Series(dtype=float)
        )

        for target_acronym, member_ids, meta in target_info:
            if is_horn:
                present = _horn_member_ids(pivot.index, regions, target_acronym, lookup=horn_lookup)
            else:
                present = [rid for rid in member_ids if rid in pivot.index]
            if not present:
                continue

            subset = pivot.loc[present]
            vol_subset = volume_series.reindex(present).fillna(0.0)
            base = {
                **group_info,
                **meta,
                "rollup_level": aggtype.lower(),
            }

            metrics = [m for m in subset.columns if m != "cell_density"]
            for metric in metrics:
                value = _aggregate_metric(metric, subset[metric], vol_subset)
                if not np.isfinite(value):
                    continue
                records.append({**base, "metric": metric, "value": value})

            if not count_series.empty and not vol_subset.empty:
                total_count = float(count_series.reindex(present).fillna(0.0).sum())
                total_vol = float(vol_subset.sum())
                if total_vol > 0 and total_count > 0:
                    records.append(
                        {
                            **base,
                            "metric": "cell_density",
                            "value": total_count / total_vol,
                        }
                    )

    if not records:
        return pd.DataFrame(columns=CORD_TIDY_COLUMNS)

    out = pd.DataFrame.from_records(records)
    return out.reindex(columns=CORD_TIDY_COLUMNS)


DEFAULT_CORD_ROLLUPS: tuple[str, ...] = ("division", "structure", "horn")


def apply_cord_rollups(
    df: pd.DataFrame,
    regions_df: pd.DataFrame,
    rollups: list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Append division/structure/horn rollup rows to finest-level cord stats."""
    if rollups is None:
        rollups = DEFAULT_CORD_ROLLUPS
    if not rollups:
        out = df.copy()
        if "rollup_level" not in out.columns:
            out["rollup_level"] = _REGION_ROLLUP_LEVEL
        return out.reindex(columns=CORD_TIDY_COLUMNS)

    finest = df.copy()
    if "rollup_level" not in finest.columns:
        finest["rollup_level"] = _REGION_ROLLUP_LEVEL
    else:
        finest = finest[finest["rollup_level"] == _REGION_ROLLUP_LEVEL]
    frames = [finest.reindex(columns=CORD_TIDY_COLUMNS)]

    for level in rollups:
        normalized = str(level).strip().lower()
        if normalized not in ("division", "structure", "horn"):
            continue
        rolled = rollup_cord_tidy(finest, regions_df, normalized)  # type: ignore[arg-type]
        if len(rolled):
            frames.append(rolled)

    return pd.concat(frames, ignore_index=True).reindex(columns=CORD_TIDY_COLUMNS)


__all__ = [
    "DEFAULT_CORD_ROLLUPS",
    "apply_cord_rollups",
    "get_descendants",
    "resolve_horn_acronym",
    "resolve_rollup_targets",
    "rollup_cord_tidy",
]
