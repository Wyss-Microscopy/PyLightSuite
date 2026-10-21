"""Load straightened sample and warped atlas data for spinal cord match-points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile
from scipy import ndimage

from lightsuite.config.models import SpinalCordPipelineConfig
from lightsuite.gui.cord_align_data import apply_longitudinal_correspondence_to_session
from lightsuite.gui.cord_display import CORD_ATLAS_PROVIDER, normalize_cord_display
from lightsuite.gui.chooselist import generate_cord_control_point_list
from lightsuite.gui.control_points import ControlPointSession
from lightsuite.gui.slice_correspondence import SliceCorrespondence
from lightsuite.gui.slices import prepare_display_slice, volume_index_to_image
from lightsuite.preprocess.cord_checkpoint import CordRegOptsCheckpoint
from lightsuite.registration.cord_affine import warp_cord_atlas_to_straightvol
from lightsuite.registration.cord_longitudinal import (
    CORD_LONGITUDINAL_AXIS,
    load_longitudinal_correspondence,
    resolve_cord_z_transinit,
)
from lightsuite.registration.cord_paths import (
    cord_affine_transform_path,
    cord_save_path,
    cord_work_dir,
)

CORRESPONDING_POINTS_JSON = "corresponding_points.json"


def _normalize_display(image: np.ndarray) -> np.ndarray:
    return normalize_cord_display(image)


@dataclass
class CordMatchPointsData:
    sample_volume: np.ndarray
    atlas_template: np.ndarray
    atlas_annotation: np.ndarray
    chooselist: np.ndarray
    session: ControlPointSession
    session_path: Path
    affine_atlas_to_samp: np.ndarray
    longitudinal_correspondence: SliceCorrespondence | None = None
    # max of the median-filtered template *before* warping (MATLAB: 255 / max(tv))
    atlas_template_max: float | None = None
    # True when the session file existed; its atlas2histology_tform is then MATLAB's
    # "initial auto-alignment"
    session_loaded_from_file: bool = False


def default_cord_session_path(save_path: Path) -> Path:
    return save_path.expanduser() / CORRESPONDING_POINTS_JSON


# Cord sessions store points like MATLAB's corresponding_points.mat:
# [row, col, slice, timestamp], 1-based, timestamp = MATLAB datenum (``now``).
CORD_POINT_COORDS_MATLAB = "cord_matlab_1based"
_UNIX_TIMESTAMP_MIN = 1e8  # anything above this is Unix seconds, not a datenum


def matlab_datenum_now() -> float:
    """MATLAB ``now``: local time as days since year 0."""
    from datetime import datetime

    return _datetime_to_datenum(datetime.now())


def _datetime_to_datenum(t) -> float:
    seconds = t.hour * 3600 + t.minute * 60 + t.second + t.microsecond * 1e-6
    return float(t.toordinal() + 366) + seconds / 86400.0


def _unix_to_datenum(ts: float) -> float:
    from datetime import datetime

    return _datetime_to_datenum(datetime.fromtimestamp(ts))


def normalize_cord_session(session: ControlPointSession) -> bool:
    """Bring a cord session to MATLAB's point convention; return True if anything changed.

    Earlier Python versions stored the in-plane coordinates (row, col) 0-based and the
    timestamp as Unix seconds. Sessions imported from MATLAB already use MATLAB's layout.
    """
    if session.point_coord_source in (CORD_POINT_COORDS_MATLAB, "matlab"):
        session.point_coord_source = CORD_POINT_COORDS_MATLAB
        return False
    changed = False
    for store in (session.histology_control_points, session.atlas_control_points):
        for pts in store:
            for pt in pts:
                pt[0] = float(pt[0]) + 1.0
                pt[1] = float(pt[1]) + 1.0
                if len(pt) > 3 and pt[3] is not None and float(pt[3]) > _UNIX_TIMESTAMP_MIN:
                    pt[3] = _unix_to_datenum(float(pt[3]))
                changed = True
    session.point_coord_source = CORD_POINT_COORDS_MATLAB
    if changed:
        # the stored fit was made in the old convention; it is refitted from the points
        session.atlas2histology_tform = np.eye(4).tolist()
    return changed


def remap_cord_session_slots(session: ControlPointSession, chooselist: np.ndarray) -> bool:
    """Move per-slot point lists to ``chooselist`` order, matching slots by sample slice.

    Returns True when the order changed. If the stored slice set differs from the new
    one (the straightened volume changed length), slots are left as they are.
    """
    new_rows = np.asarray(chooselist, dtype=int).reshape(-1, 4)
    if session.chooselist is None:
        return False
    old_rows = np.asarray(session.chooselist, dtype=int).reshape(-1, 4)
    if old_rows.shape == new_rows.shape and np.array_equal(old_rows, new_rows):
        return False
    n = new_rows.shape[0]
    old_slices = [int(v) for v in old_rows[:, 0]]
    new_slices = [int(v) for v in new_rows[:, 0]]
    if sorted(old_slices) != sorted(new_slices) or len(set(new_slices)) != n:
        return False
    slot_of = {sl: i for i, sl in enumerate(new_slices)}
    hist: list[list[list[float]]] = [[] for _ in range(n)]
    atl: list[list[list[float]]] = [[] for _ in range(n)]
    planes = [0] * n
    old_planes = session.atlas_slice_indices or []
    for i, sl in enumerate(old_slices):
        j = slot_of[sl]
        if i < len(session.histology_control_points):
            hist[j] = session.histology_control_points[i]
        if i < len(session.atlas_control_points):
            atl[j] = session.atlas_control_points[i]
        if i < len(old_planes):
            planes[j] = int(old_planes[i])
    session.histology_control_points = hist
    session.atlas_control_points = atl
    if session.atlas_slice_indices is not None:
        session.atlas_slice_indices = planes
    session.chooselist = new_rows.tolist()
    return True


def load_cord_session(path: Path) -> ControlPointSession:
    """Load ``corresponding_points.json`` in MATLAB's point convention."""
    session = ControlPointSession.load(path)
    normalize_cord_session(session)
    return session


def _atlas_warp_cache_key(
    elastix_affine_path: Path,
    transinit: np.ndarray,
    output_shape: tuple[int, ...],
    tv_path: Path,
    av_path: Path,
) -> str:
    import hashlib

    h = hashlib.sha1()
    if elastix_affine_path.is_file():
        h.update(elastix_affine_path.read_bytes())
    h.update(np.asarray(transinit, dtype=np.float64).tobytes())
    h.update(np.asarray(output_shape, dtype=np.int64).tobytes())
    for path in (tv_path, av_path):
        st = path.stat()
        h.update(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


def load_cord_match_points_data(config: SpinalCordPipelineConfig) -> CordMatchPointsData:
    """Load straightvol and atlas volumes warped into the same straightened grid.

    The warped atlas (median-filtered template and annotation) is cached in
    ``cache/match_points_atlas.npz`` and reused while the affine, z-init and atlas
    files are unchanged.
    """
    save_path = cord_save_path(config)
    regopts_path = save_path / "regopts.json"
    if not regopts_path.is_file():
        msg = f"Missing {regopts_path}. Run init-registration first."
        raise FileNotFoundError(msg)

    checkpoint = CordRegOptsCheckpoint.load(regopts_path)
    if checkpoint.straightvol_path is None or checkpoint.affine_atlas_to_samp is None:
        msg = "regopts.json missing straightvol / affine_atlas_to_samp from init-registration."
        raise RuntimeError(msg)

    straightvol = tifffile.imread(checkpoint.straightvol_path).astype(np.float32)
    affine_atlas_to_samp = np.asarray(checkpoint.affine_atlas_to_samp, dtype=float)
    nslices = checkpoint.ikeeprange[1] - checkpoint.ikeeprange[0] + 1
    correspondence = load_longitudinal_correspondence(save_path)
    tv_path, av_path = Path(checkpoint.tv_path), Path(checkpoint.av_path)
    tv = tifffile.imread(tv_path).astype(np.float32)
    atlas_shape = tuple(int(v) for v in tv.shape)

    transinit = resolve_cord_z_transinit(nslices, atlas_shape[2], correspondence)
    elastix_affine_path = cord_affine_transform_path(config)
    spacing_mm = config.registration.resolution_um * 1e-3
    output_shape = straightvol.shape

    cache_path = save_path / "cache" / "match_points_atlas.npz"
    key = _atlas_warp_cache_key(elastix_affine_path, transinit, output_shape, tv_path, av_path)
    cached = None
    if cache_path.is_file():
        try:
            with np.load(cache_path) as z:
                if str(z["key"]) == key:
                    cached = (z["template"], z["annotation"], float(z["template_max"]))
        except (OSError, KeyError, ValueError):
            cached = None
    if cached is not None:
        atlas_template, atlas_annotation, template_max = cached
    else:
        av = tifffile.imread(av_path).astype(np.uint16)
        tvtemp = ndimage.median_filter(tv, size=3)  # MATLAB medfilt3 (symmetric padding)
        template_max = float(tvtemp.max())
        atlas_template = warp_cord_atlas_to_straightvol(
            tvtemp,
            transinit=transinit,
            elastix_affine_path=elastix_affine_path,
            output_shape=output_shape,
            spacing_mm=spacing_mm,
            work_dir=cord_work_dir(config, "transformix", "match", "tv"),
            nearest=False,
        ).astype(np.float32)
        atlas_annotation = warp_cord_atlas_to_straightvol(
            av,
            transinit=transinit,
            elastix_affine_path=elastix_affine_path,
            output_shape=output_shape,
            spacing_mm=spacing_mm,
            work_dir=cord_work_dir(config, "transformix", "match", "av"),
            nearest=True,
        )
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_name("match_points_atlas.tmp.npz")
            np.savez(
                tmp,
                key=np.array(key),
                template=atlas_template,
                annotation=atlas_annotation,
                template_max=np.array(template_max),
            )
            tmp.replace(cache_path)
        except OSError:
            pass

    chooselist = generate_cord_control_point_list(output_shape[2])
    n_slices = int(chooselist.shape[0])
    session_path = default_cord_session_path(save_path)
    loaded = session_path.is_file()
    if loaded:
        session = load_cord_session(session_path)
        remap_cord_session_slots(session, chooselist)
    else:
        session = ControlPointSession.empty(np.eye(4), n_slices)
        session.point_coord_source = CORD_POINT_COORDS_MATLAB
    session.ori_trans = affine_atlas_to_samp.tolist()
    session.chooselist = chooselist.tolist()
    for store_name in ("histology_control_points", "atlas_control_points"):
        store = getattr(session, store_name)
        if len(store) != n_slices:
            setattr(
                session,
                store_name,
                [store[i] if i < len(store) else [] for i in range(n_slices)],
            )

    if correspondence is not None and correspondence.has_confirmed_anchors(CORD_LONGITUDINAL_AXIS):
        has_manual_planes = (
            session.atlas_slice_indices is not None
            and any(int(v) > 0 for v in session.atlas_slice_indices)
        )
        if not has_manual_planes:
            apply_longitudinal_correspondence_to_session(
                session,
                chooselist,
                correspondence,
                atlas_shape,
            )

    return CordMatchPointsData(
        sample_volume=straightvol,
        atlas_template=atlas_template,
        atlas_annotation=atlas_annotation,
        chooselist=chooselist,
        session=session,
        session_path=session_path,
        affine_atlas_to_samp=affine_atlas_to_samp,
        longitudinal_correspondence=correspondence,
        atlas_template_max=template_max,
        session_loaded_from_file=loaded,
    )


def atlas_cut_axis_size(atlas_shape: tuple[int, int, int], chooserow: np.ndarray) -> int:
    """Re-export for backward compatibility."""
    from lightsuite.gui.cord_display import atlas_cut_axis_size as _size

    return _size(atlas_shape, chooserow)


def chooserow_with_atlas_plane(chooserow: np.ndarray, atlas_plane: int) -> np.ndarray:
    row = np.asarray(chooserow, dtype=int).copy()
    row[0] = int(atlas_plane)
    return row


def resolve_atlas_plane_index(data: CordMatchPointsData, slice_idx: int) -> int:
    """Atlas plane for one chooselist entry (manual override, points, or auto-estimate)."""
    from lightsuite.gui.brain_data import estimate_atlas_plane_index

    chooserow = np.asarray(data.chooselist[slice_idx - 1], dtype=int)
    atlas_points = data.session.atlas_control_points[slice_idx - 1]
    if atlas_points:
        cut_axis = int(chooserow[1]) - 1
        return int(
            np.clip(
                int(np.round(float(np.median([p[cut_axis] for p in atlas_points])))),
                1,
                atlas_cut_axis_size(data.atlas_template.shape, chooserow),
            )
        )

    stored = data.session.atlas_slice_indices
    if stored is not None and len(stored) >= slice_idx and int(stored[slice_idx - 1]) > 0:
        return int(
            np.clip(
                int(stored[slice_idx - 1]),
                1,
                atlas_cut_axis_size(data.atlas_template.shape, chooserow),
            )
        )

    if data.longitudinal_correspondence is not None:
        plane = data.longitudinal_correspondence.interpolate_atlas_plane(
            int(chooserow[0]),
            CORD_LONGITUDINAL_AXIS,
            atlas_cut_axis_size(data.atlas_template.shape, chooserow),
        )
        if plane is not None:
            return int(plane)

    matrix = np.asarray(data.session.atlas2histology_tform, dtype=float)
    return estimate_atlas_plane_index(
        data.sample_volume,
        chooserow,
        matrix,
        data.atlas_template.shape,
    )


def set_atlas_plane_index(session: ControlPointSession, slice_idx: int, plane: int) -> None:
    n_slices = len(session.histology_control_points)
    if session.atlas_slice_indices is None or len(session.atlas_slice_indices) != n_slices:
        session.atlas_slice_indices = [0] * n_slices
    session.atlas_slice_indices[slice_idx - 1] = int(plane)


def slice_pair(
    data: CordMatchPointsData,
    slice_idx: int,
    *,
    atlas_plane: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    row = np.asarray(data.chooselist[slice_idx - 1], dtype=int)
    cut_axis = int(row[1])
    sample = _normalize_display(
        prepare_display_slice(
            volume_index_to_image(data.sample_volume, row),
            cut_axis,
            CORD_ATLAS_PROVIDER,
        )
    )
    plane = atlas_plane if atlas_plane is not None else resolve_atlas_plane_index(data, slice_idx)
    atlas_row = chooserow_with_atlas_plane(row, plane)
    atlas = _normalize_display(
        prepare_display_slice(
            volume_index_to_image(data.atlas_template, atlas_row),
            cut_axis,
            CORD_ATLAS_PROVIDER,
        )
    )
    return sample, atlas


def prepare_cord_match_points_session(config: SpinalCordPipelineConfig) -> Path:
    """Ensure corresponding_points.json exists (headless helper)."""
    data = load_cord_match_points_data(config)
    data.session.save(data.session_path)
    return data.session_path
