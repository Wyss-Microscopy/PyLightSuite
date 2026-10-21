"""Tests for Fiederling spinal cord intensity parcellation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lightsuite.analysis.cord_counts import assign_segment_names
from lightsuite.analysis.cord_ontology import CordRegionTable
from lightsuite.analysis.cord_parcellation import parcellate_cord_intensities


def _segments() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Segment": ["C1", "C2"],
            "Start": [1, 3],
            "End": [2, 4],
        }
    )


def _annotation() -> np.ndarray:
    av = np.zeros((4, 4, 4), dtype=np.int32)
    av[:, :, 0] = 0
    av[:, :, 1] = 7
    av[:, :, 2] = 7
    av[:, :, 3] = 9
    return av


def _intensity() -> np.ndarray:
    vol = np.zeros((4, 4, 4), dtype=np.float32)
    vol[:, :, 0] = 10.0
    vol[:, :, 1] = 100.0
    vol[:, :, 2] = 200.0
    vol[:, :, 3] = 50.0
    return vol


def _region_table() -> CordRegionTable:
    df = pd.DataFrame(
        {
            "parcellation_index": [7, 9],
            "acronym": ["a7", "a9"],
            "name": ["Region7", "Region9"],
            "structure": ["DH", "VH"],
            "division": ["GM", "GM"],
        }
    )
    return CordRegionTable(
        atlas="fiederling", df=df, source_csv=__import__("pathlib").Path("x.csv")
    )


def test_parcellate_cord_intensities_median_and_volume() -> None:
    tidy = parcellate_cord_intensities(
        _intensity(),
        _annotation(),
        _segments(),
        sample="op87",
        channel=1,
        region_table=_region_table(),
        voxel_um_yxz=(10.0, 10.0, 20.0),
    )

    medians = tidy[tidy["metric"] == "median_intensity"]
    r7_c1 = medians[(medians["parcellation_index"] == 7) & (medians["segment"] == "C1")]
    r7_c2 = medians[(medians["parcellation_index"] == 7) & (medians["segment"] == "C2")]
    r9_c2 = medians[(medians["parcellation_index"] == 9) & (medians["segment"] == "C2")]

    assert r7_c1["value"].iloc[0] == 100.0
    assert r7_c2["value"].iloc[0] == 200.0
    assert r9_c2["value"].iloc[0] == 50.0

    volumes = tidy[tidy["metric"] == "volume_mm3"]
    r7_c1_vol = volumes[(volumes["parcellation_index"] == 7) & (volumes["segment"] == "C1")]
    assert r7_c1_vol["value"].iloc[0] > 0


def test_parcellate_relative_to_background() -> None:
    tidy = parcellate_cord_intensities(
        _intensity(),
        _annotation(),
        _segments(),
        sample="op87",
        channel=1,
        relative_to="background",
    )
    rel = tidy[tidy["metric"] == "relative_median_intensity"]
    r7_c1 = rel[(rel["parcellation_index"] == 7) & (rel["segment"] == "C1")]
    # background median in C1 z=1 is 10; region 7 median in C1 is 100 -> (100-10)/10 = 9
    assert r7_c1["value"].iloc[0] == 9.0


def test_parcellate_skips_background_regions() -> None:
    annotation = np.zeros((2, 2, 2), dtype=np.int32)
    intensity = np.ones((2, 2, 2), dtype=np.float32) * 5.0
    segments = pd.DataFrame({"Segment": ["C1"], "Start": [1], "End": [2]})
    tidy = parcellate_cord_intensities(
        intensity,
        annotation,
        segments,
        sample="op87",
        channel=1,
        drop_background_regions=True,
    )
    assert len(tidy) == 0


def test_assign_segment_names_matches_parcellation() -> None:
    names = assign_segment_names(np.array([1, 2, 3, 4]), _segments())
    assert names.tolist() == ["C1", "C1", "C2", "C2"]


def test_grouped_stats_match_pandas() -> None:
    """Sort-based group stats equal pandas median/mean/count and np.std(ddof=0)."""
    from lightsuite.analysis.cord_parcellation import _grouped_stats

    rng = np.random.default_rng(3)
    for values in (
        rng.integers(0, 5000, 4000).astype(np.float64),  # integral fast path
        rng.normal(100.0, 20.0, 4000),  # general lexsort path
    ):
        keys = rng.integers(0, 37, values.size)
        got = _grouped_stats(keys, values)
        ref = pd.DataFrame({"k": keys, "v": values}).groupby("k")["v"]
        np.testing.assert_array_equal(got["key"], ref.median().index.to_numpy())
        np.testing.assert_array_equal(got["median"], ref.median().to_numpy())
        np.testing.assert_array_equal(got["n"], ref.count().to_numpy())
        np.testing.assert_allclose(got["mean"], ref.mean().to_numpy(), rtol=1e-13)
        np.testing.assert_allclose(
            got["std"], ref.apply(lambda s: np.std(s.to_numpy(), ddof=0)).to_numpy(), rtol=1e-12
        )


def test_parcellation_skips_nan_voxels_for_every_metric() -> None:
    annotation = np.ones((4, 4, 3), dtype=np.int32)
    volume = np.arange(48, dtype=np.float64).reshape(4, 4, 3)
    volume[0, 0, :] = np.nan
    segments = pd.DataFrame({"Segment": ["C1"], "Start": [1], "End": [3]})
    tidy = parcellate_cord_intensities(
        volume,
        annotation,
        segments,
        sample="s",
        channel=1,
        intensity_metrics=["median_intensity", "std", "volume_mm3"],
    )
    metrics = set(tidy["metric"])
    assert {"median_intensity", "std", "volume_mm3"} <= metrics
    finite = volume[np.isfinite(volume)]
    std = float(tidy.loc[tidy["metric"] == "std", "value"].iloc[0])
    assert std == pytest.approx(np.std(finite))
