"""Tests for spinal cord match-points GUI helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import tifffile
import yaml

from lightsuite.config.loader import load_spinal_config
from lightsuite.gui.chooselist import generate_cord_control_point_list
from lightsuite.gui.control_points import ControlPointSession, through_axis_column_for_cut
from lightsuite.gui.cord_data import (
    default_cord_session_path,
    load_cord_match_points_data,
    prepare_cord_match_points_session,
    slice_pair,
)
from lightsuite.gui.match_points_cord import (
    atlas_plane_for_slice,
    auto_atlas_plane,
    boundary_mask,
    delete_last_point,
    fit_cord_alignment,
    matlab_round,
    warped_annotation_plane,
)


def test_generate_cord_control_point_list_shape_and_axis() -> None:
    chooselist = generate_cord_control_point_list(50, n_slices=100)
    assert chooselist.shape == (100, 4)
    assert np.all(chooselist[:, 1] == 3)
    assert chooselist[:, 0].min() >= 1
    assert chooselist[:, 0].max() <= 50


def test_generate_cord_control_point_list_is_deterministic() -> None:
    a = generate_cord_control_point_list(40)
    b = generate_cord_control_point_list(40)
    assert np.array_equal(a, b)


def _write_minimal_cord_checkpoint(tmp_path: Path) -> Path:
    save_path = tmp_path / "out"
    cache = save_path / "cache"
    cache.mkdir(parents=True)
    shape = (20, 24, 30)
    straightvol = np.random.default_rng(0).random(shape).astype(np.float32)
    tv = np.random.default_rng(1).random(shape).astype(np.float32)
    av = np.zeros(shape, dtype=np.uint16)
    av[8:14, 8:16, :] = 42

    straightvol_path = cache / "straightvol.tif"
    tv_path = cache / "tv.tif"
    av_path = cache / "av.tif"
    tifffile.imwrite(straightvol_path, straightvol)
    tifffile.imwrite(tv_path, tv)
    tifffile.imwrite(av_path, av)

    regopts = {
        "sample_name": "t",
        "data_folder": str(tmp_path),
        "lsfolder": str(save_path),
        "orisize": list(shape),
        "nchans": 1,
        "sampleres_um": [20.0, 20.0, 20.0],
        "registrationres_um": [20.0, 20.0, 20.0],
        "reg_channel": 1,
        "sample_perm": [1, 2, 3],
        "tofliprc": False,
        "ikeeprange": [1, shape[2]],
        "xrange": [1, shape[1]],
        "yrange": [1, shape[0]],
        "regvol_path": str(straightvol_path),
        "tv_path": str(tv_path),
        "av_path": str(av_path),
        "smpts_path": str(tmp_path / "sm.npy"),
        "tvpts_path": str(tmp_path / "tv.npy"),
        "atlas_res_um": [20.0, 10.0, 10.0],
        "segments_path": str(tmp_path / "seg.csv"),
        "regions_path": str(tmp_path / "reg.csv"),
        "tiff_type": "channelperfile",
        "straightvol_path": str(straightvol_path),
        "affine_atlas_to_samp": np.eye(4).tolist(),
    }
    np.save(tmp_path / "sm.npy", np.zeros((0, 3), dtype=np.float32))
    np.save(tmp_path / "tv.npy", np.zeros((0, 3), dtype=np.float32))
    save_path.mkdir(parents=True, exist_ok=True)
    regopts_path = save_path / "regopts.json"
    regopts_path.write_text(json.dumps(regopts, indent=2), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    atlas_dir = tmp_path / "atlas"
    atlas_dir.mkdir()
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "sample": {
                    "name": "t",
                    "save_path": str(save_path),
                    "scratch": str(tmp_path / "scratch"),
                    "voxel_um": [20.0, 20.0, 20.0],
                    "source": {"format": "tiff_stack", "path": str(data_dir)},
                },
                "atlas": {"atlas_dir": str(atlas_dir)},
                "registration": {"resolution_um": 20, "channel_primary": 1},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_load_cord_match_points_data_matching_shapes(tmp_path: Path) -> None:
    config_path = _write_minimal_cord_checkpoint(tmp_path)
    cfg = load_spinal_config(config_path)
    data = load_cord_match_points_data(cfg)
    assert data.sample_volume.shape == data.atlas_template.shape == data.atlas_annotation.shape
    assert data.chooselist.shape[0] == len(data.session.histology_control_points) == 100
    sample, atlas = slice_pair(data, 1)
    assert sample.shape == atlas.shape


def test_prepare_cord_match_points_session_headless(tmp_path: Path) -> None:
    config_path = _write_minimal_cord_checkpoint(tmp_path)
    cfg = load_spinal_config(config_path)
    path = prepare_cord_match_points_session(cfg)
    assert path == default_cord_session_path(cfg.sample.save_path)
    session = ControlPointSession.load(path)
    assert len(session.histology_control_points) == 100


def test_through_axis_column_for_cut() -> None:
    assert through_axis_column_for_cut(3) == 2
    assert through_axis_column_for_cut(1) == 1
    assert through_axis_column_for_cut(2) == 0


def test_cord_manual_alignment_constrains_through_axis() -> None:
    """Z offsets from atlas-plane scrolling must not shear the overlay affine."""
    session = ControlPointSession.empty(np.eye(4), n_slices=16)
    sample_z = [861.0, 65.0, 54.0, 585.0, 362.0, 712.0, 596.0]
    atlas_z = [911.0, 226.0, 219.0, 634.0, 451.0, 762.0, 645.0]
    for i in range(16):
        z_sample = sample_z[i % len(sample_z)]
        z_atlas = atlas_z[i % len(atlas_z)]
        session.histology_control_points[i] = [[60.0 + i, 80.0 + i, z_sample, 1.0]]
        session.atlas_control_points[i] = [[61.0 + i, 81.0 + i, z_atlas, 1.0]]

    session.update_manual_alignment(min_pairs=16, constrain_cut_axis=3)
    constrained = np.asarray(session.atlas2histology_tform, dtype=float)
    assert abs(constrained[2, 3]) < 1.0


def test_paired_points_xyz_dim_order() -> None:
    session = ControlPointSession.empty(np.eye(4), n_slices=2)
    session.histology_control_points[0] = [[10.0, 20.0, 5.0, 1.0]]
    session.atlas_control_points[0] = [[11.0, 21.0, 6.0, 1.0]]
    atlas_pts, sample_pts = session.paired_points_xyz()
    assert atlas_pts.shape == (1, 3)
    assert sample_pts.shape == (1, 3)
    assert np.allclose(atlas_pts[0], [21.0, 11.0, 6.0])
    assert np.allclose(sample_pts[0], [20.0, 10.0, 5.0])


def test_paired_points_volume_yxz_zero_based() -> None:
    session = ControlPointSession.empty(np.eye(4), n_slices=2)
    session.histology_control_points[0] = [[10.0, 20.0, 5.0, 1.0]]
    session.atlas_control_points[0] = [[11.0, 21.0, 6.0, 1.0]]
    atlas_pts, sample_pts = session.paired_points_volume_yxz()
    assert np.allclose(sample_pts[0], [10.0, 20.0, 4.0])
    assert np.allclose(atlas_pts[0], [11.0, 21.0, 5.0])


# --------------------------------------------------------------------------- MATLAB parity
def test_chooselist_matches_matlab_randperm() -> None:
    """Slot order = MATLAB rng(1); randperm(100) over round(linspace(1, N, 100))."""
    cl = generate_cord_control_point_list(1893)
    # MATLAB: slicescheck(randperm) for N = 1893 starts with slices 39, 1874, 727, 957, 269
    assert cl[:5, 0].tolist() == [39, 1874, 727, 957, 269]
    assert sorted(cl[:, 0].tolist()) == sorted(
        np.floor(np.linspace(1, 1893, 100) + 0.5).astype(int).tolist()
    )


def test_matlab_round_halves_away_from_zero() -> None:
    assert matlab_round(2.5) == 3
    assert matlab_round(-2.5) == -3
    assert matlab_round(3.49) == 3


def test_fit_cord_alignment_branches() -> None:
    session = ControlPointSession.empty(np.eye(4), n_slices=20)
    matrix, status = fit_cord_alignment(session, None)
    assert np.array_equal(matrix, np.eye(4)) and "Identity" in status
    auto = np.diag([2.0, 2.0, 1.0, 1.0])
    matrix, status = fit_cord_alignment(session, auto)
    assert np.array_equal(matrix, auto) and "auto-alignment" in status
    # 16 pairs from a known affine on [x y z 1] (MATLAB tform.A) are recovered exactly
    true = np.array(
        [[1.1, 0.05, 0.0, 3.0], [-0.02, 0.95, 0.01, -2.0], [0.0, 0.03, 1.2, 5.0], [0, 0, 0, 1]]
    )
    rng = np.random.default_rng(0)
    for i in range(16):
        xyz = rng.uniform(1, 100, 3)
        x_s, y_s, z_s = (true @ np.r_[xyz, 1.0])[:3]
        session.atlas_control_points[i] = [[xyz[1], xyz[0], xyz[2], float(i)]]  # [row col z t]
        session.histology_control_points[i] = [[y_s, x_s, z_s, float(i)]]
    matrix, status = fit_cord_alignment(session, auto)
    assert np.allclose(matrix, true, atol=1e-9)
    assert status == "Current Fit: MSE: 0.00 | Npoints: 16"


def test_atlas_plane_rules() -> None:
    # identity: the atlas plane is the sample slice
    assert auto_atlas_plane(np.eye(4), (126, 161), 500) == 500
    shift = np.eye(4)
    shift[2, 3] = 10.0  # atlas -> sample shifts z by +10, so the atlas plane is 10 lower
    assert auto_atlas_plane(shift, (126, 161), 500) == 490
    assert auto_atlas_plane(shift, (126, 161), 3) == 1  # MATLAB max(sluse, 1)
    # atlas points on the slice win; their median is rounded half away from zero
    assert atlas_plane_for_slice([[1, 1, 10, 0], [1, 1, 11, 0]], shift, (126, 161), 500) == 11


def test_boundary_mask_matches_conv2_rule() -> None:
    lab = np.zeros((7, 7))
    lab[2:5, 2:5] = 9.0
    mask = boundary_mask(lab)
    assert mask[3, 3] == False  # noqa: E712  (interior: mean of 9s is 9)
    assert mask[2, 2] and mask[1, 1]  # edges on both sides of the label border
    assert mask[0, 0] == False  # noqa: E712  (zero padding keeps background 0)


def test_warped_annotation_plane_identity_is_slice() -> None:
    av = np.random.default_rng(1).integers(0, 50, size=(6, 7, 5)).astype(np.uint16)
    assert np.array_equal(warped_annotation_plane(av, np.eye(4), 3), av[:, :, 2])


def test_delete_last_point_uses_timestamps_like_matlab() -> None:
    hist = [[1, 1, 1, 5.0]]
    atlas = [[1, 1, 1, 6.0]]
    assert delete_last_point(hist, atlas) and atlas == [] and len(hist) == 1
    assert delete_last_point(hist, atlas) and hist == []
    assert not delete_last_point(hist, atlas)
    # NaN timestamps compare false (legacy files): nothing is removed when the atlas one is NaN
    hist, atlas = [[1, 1, 1, 5.0]], [[1, 1, 1, float("nan")]]
    assert not delete_last_point(hist, atlas) and len(hist) == 1 and len(atlas) == 1


def test_legacy_python_session_is_converted_to_matlab_convention(tmp_path: Path) -> None:
    from lightsuite.gui.cord_data import CORD_POINT_COORDS_MATLAB, load_cord_session

    session = ControlPointSession.empty(np.eye(4), n_slices=2)
    session.histology_control_points[0] = [[10.0, 20.0, 5.0, 1.7e9]]  # 0-based in-plane, Unix time
    session.atlas_control_points[0] = [[11.0, 21.0, 6.0, 1.7e9 + 1]]
    session.point_coord_source = "napari"
    path = tmp_path / "corresponding_points.json"
    session.save(path)
    loaded = load_cord_session(path)
    h, a = loaded.histology_control_points[0][0], loaded.atlas_control_points[0][0]
    assert h[:3] == [11.0, 21.0, 5.0] and a[:3] == [12.0, 22.0, 6.0]
    assert 7e5 < h[3] < 8e5 and a[3] > h[3]  # MATLAB datenum, order kept
    assert loaded.point_coord_source == CORD_POINT_COORDS_MATLAB
    # converting again is a no-op
    loaded.save(path)
    again = load_cord_session(path)
    assert again.histology_control_points[0][0][:3] == [11.0, 21.0, 5.0]


def test_slots_are_remapped_by_slice_when_order_changes() -> None:
    from lightsuite.gui.cord_data import remap_cord_session_slots

    new = generate_cord_control_point_list(1893)
    old = new[::-1].copy()
    session = ControlPointSession.empty(np.eye(4), n_slices=100)
    session.chooselist = old.tolist()
    session.histology_control_points[0] = [[5.0, 6.0, float(old[0, 0]), 1.0]]
    assert remap_cord_session_slots(session, new)
    j = int(np.flatnonzero(new[:, 0] == old[0, 0])[0])
    assert session.histology_control_points[j][0][2] == old[0, 0]
    assert sum(len(p) for p in session.histology_control_points) == 1


def test_match_points_atlas_warp_is_cached(tmp_path: Path, monkeypatch) -> None:
    import lightsuite.gui.cord_data as cord_data

    config_path = _write_minimal_cord_checkpoint(tmp_path)
    cfg = load_spinal_config(config_path)
    first = load_cord_match_points_data(cfg)

    def _fail(*_args, **_kwargs):
        raise AssertionError("atlas was re-warped although the cache is valid")

    monkeypatch.setattr(cord_data, "warp_cord_atlas_to_straightvol", _fail)
    second = load_cord_match_points_data(cfg)
    assert np.array_equal(first.atlas_annotation, second.atlas_annotation)
    assert np.array_equal(first.atlas_template, second.atlas_template)
