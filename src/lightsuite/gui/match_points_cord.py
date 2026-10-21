"""Spinal cord control-point GUI (matchControlPoints_unified.m, spine mode).

Same layout, controls and logic as the MATLAB GUI, in one napari canvas:

* top: straightened sample slice (left) and the atlas, pre-warped into the sample
  grid by the initial affine (right);
* bottom: maximum projections of sample and atlas along the cord, with the current
  sample slice (red) and atlas slice (blue) marked.

Controls::

    Click on sample / atlas   add a point (pairs are numbered 1, 2, ... per slice)
    Backspace                 delete the last added point on this slice
    c                         clear all points on this slice
    <- / ->                   previous / next slice
    Enter                     jump to a slice
    Space                     show / hide the atlas boundaries on the sample
    Scroll                    move the atlas plane by 2
    s                         save
    Ctrl+scroll, r / Home     zoom / reset the view (not in MATLAB)

Points are stored like MATLAB's ``corresponding_points.mat``: per slice, rows of
``[row, col, slice, timestamp]``, 1-based, timestamp = MATLAB datenum. Once 16 pairs
exist, an affine is fitted from atlas to sample points and its warped atlas
boundaries are drawn on the sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from rich.console import Console
from scipy import ndimage

from lightsuite.config.models import SpinalCordPipelineConfig
from lightsuite.gui.affine import fit_affine_transform
from lightsuite.gui.control_points import COORD_SCHEMA_VERSION, ControlPointSession
from lightsuite.gui.cord_data import (
    CORD_POINT_COORDS_MATLAB,
    CordMatchPointsData,
    default_cord_session_path,
    load_cord_match_points_data,
    matlab_datenum_now,
    prepare_cord_match_points_session,
)
from lightsuite.gui.stage_controller import (
    DockStageController,
    require_napari,
    run_attached_stage,
)

console = Console()

CORRESPONDING_POINTS_JSON = "corresponding_points.json"
MIN_AFFINE_PAIRS = 16  # MATLAB Nmin
CORD_CUT_AXIS = 3
ATLAS_SCROLL_STEP = 2  # MATLAB: atlas_slice + 2 * VerticalScrollCount
ATLAS_CLIM = (0.0, 250.0)
ZOOM_STEP = 1.25

BASE_TITLE = "Control point GUI (SPINE): match points between sample and atlas"
CONTROLS = (
    "Controls: ←/→: switch slice | Click: add | Backspace: delete | C: clear | "
    "Space: overlay | Enter: jump | S: save\n"
    "Scroll: atlas plane | Ctrl+scroll: zoom | R: reset view"
)


def default_session_path(save_path: Path) -> Path:
    """Backward-compatible alias for ``default_cord_session_path``."""
    return default_cord_session_path(save_path)


# --------------------------------------------------------------------------- MATLAB helpers
def matlab_round(x: float | np.ndarray) -> np.ndarray | int:
    """MATLAB ``round``: halves away from zero."""
    arr = np.asarray(x, dtype=float)
    out = np.sign(arr) * np.floor(np.abs(arr) + 0.5)
    return int(out) if out.ndim == 0 else out.astype(int)


def matlab_uint8(x: np.ndarray) -> np.ndarray:
    """MATLAB ``uint8()`` of floating values: round half away from zero, saturate.

    float32 input stays float32 (MATLAB does these products in ``single``).
    """
    arr = np.asarray(x)
    if arr.dtype != np.float32:
        arr = arr.astype(np.float64)
    out = np.floor(arr + arr.dtype.type(0.5))
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8)


def image_limits(image: np.ndarray, alpha: float = 0.001) -> tuple[float, float]:
    """MATLAB ``getImageLimits``: quantiles of the non-zero pixels, max >= 5 x min."""
    vals = np.asarray(image, dtype=np.float64)
    vals = vals[vals > 0]
    if vals.size == 0:
        return 0.0, 255.0
    lo, hi = np.quantile(vals, [alpha, 1.0 - alpha], method="hazen")  # MATLAB quantile
    return float(lo), float(max(hi, 5.0 * lo))


def clahe_uint8(image: np.ndarray) -> np.ndarray:
    """``adapthisteq`` defaults (8 x 8 tiles, clip 0.01, 256 bins) via skimage."""
    from skimage.exposure import equalize_adapthist

    img = np.asarray(image, dtype=np.uint8)
    if img.max() == img.min():
        return img.copy()
    return matlab_uint8(equalize_adapthist(img, clip_limit=0.01, nbins=256) * 255.0)


def fit_cord_alignment(
    session: ControlPointSession,
    auto_alignment: np.ndarray | None,
) -> tuple[np.ndarray, str]:
    """MATLAB ``align_ccf_to_histology``: atlas -> sample affine and the status line.

    Paired slices are those with the same, non-zero number of points on both sides.
    The matrix acts on 1-based ``[x, y, z, 1]`` column vectors (MATLAB ``tform.A``).
    """
    atlas_xyz, hist_xyz = session.paired_points_xyz()
    n = hist_xyz.shape[0]
    if n >= MIN_AFFINE_PAIRS and atlas_xyz.shape[0] == n:
        matrix, mse = fit_affine_transform(atlas_xyz, hist_xyz)
        return matrix, f"Current Fit: MSE: {mse:.2f} | Npoints: {n}"
    if auto_alignment is not None:
        return np.asarray(auto_alignment, dtype=float), "Current Fit: Using initial auto-alignment"
    return np.eye(4), "Current Fit: Identity/No alignment"


def auto_atlas_plane(matrix: np.ndarray, image_shape: tuple[int, int], sample_slice: int) -> int:
    """Atlas plane matching a sample slice under ``matrix`` (MATLAB ``update_slice``).

    The four corners of the sample slice are mapped into the atlas with the inverse
    transform; the plane is the rounded middle of their z range, at least 1.
    """
    rows, cols = image_shape
    inv = np.linalg.inv(np.asarray(matrix, dtype=float))
    corners = np.array(
        [
            [1, 1, sample_slice, 1],
            [cols, 1, sample_slice, 1],
            [1, rows, sample_slice, 1],
            [cols, rows, sample_slice, 1],
        ],
        dtype=float,
    )
    z = (inv @ corners.T)[2]
    return max(int(matlab_round(0.5 * (z.min() + z.max()))), 1)


def atlas_plane_for_slice(
    atlas_points: list[list[float]],
    matrix: np.ndarray,
    image_shape: tuple[int, int],
    sample_slice: int,
) -> int:
    """Median plane of this slice's atlas points, else :func:`auto_atlas_plane`."""
    if atlas_points:
        return int(matlab_round(np.median([float(p[2]) for p in atlas_points])))
    return auto_atlas_plane(matrix, image_shape, sample_slice)


def warped_annotation_plane(
    annotation: np.ndarray, matrix: np.ndarray, sample_slice: int
) -> np.ndarray:
    """Plane ``sample_slice`` of the annotation warped by ``matrix`` (nearest neighbour)."""
    rows, cols, depth = annotation.shape
    inv = np.linalg.inv(np.asarray(matrix, dtype=float))
    yy, xx = np.mgrid[1 : rows + 1, 1 : cols + 1].astype(float)
    hom = np.stack(
        [xx.ravel(), yy.ravel(), np.full(xx.size, float(sample_slice)), np.ones(xx.size)]
    )
    src = inv @ hom
    ix = np.floor(src[0] + 0.5).astype(np.int64) - 1
    iy = np.floor(src[1] + 0.5).astype(np.int64) - 1
    iz = np.floor(src[2] + 0.5).astype(np.int64) - 1
    ok = (ix >= 0) & (ix < cols) & (iy >= 0) & (iy < rows) & (iz >= 0) & (iz < depth)
    out = np.zeros(rows * cols, dtype=annotation.dtype)
    out[ok] = annotation[iy[ok], ix[ok], iz[ok]]
    return out.reshape(rows, cols)


def boundary_mask(label_slice: np.ndarray) -> np.ndarray:
    """MATLAB: ``round(conv2(slice, ones(3)/9, 'same')) ~= slice``."""
    lab = np.asarray(label_slice, dtype=np.float64)
    mean = ndimage.convolve(lab, np.full((3, 3), 1.0 / 9.0), mode="constant", cval=0.0)
    return np.floor(mean + 0.5) != lab


def delete_last_point(hist: list[list[float]], atlas: list[list[float]]) -> bool:
    """MATLAB Backspace: drop the most recent of the two last points (by timestamp)."""
    # NaN timestamps (legacy files) compare false, exactly as in MATLAB
    t_h = float(hist[-1][3]) if hist else -np.inf
    t_a = float(atlas[-1][3]) if atlas else -np.inf
    if t_h > t_a:
        hist.pop()
        return True
    if t_a > -np.inf:
        atlas.pop()
        return True
    return False


def sample_display_volume(sample: np.ndarray) -> np.ndarray:
    """MATLAB: ``uint8(volume * 255 / quantile(volume, 0.999, 'all'))``."""
    vol = np.asarray(sample, dtype=np.float32)
    q = np.float32(np.quantile(vol, 0.999, method="hazen"))
    return matlab_uint8(vol * (np.float32(255.0) / max(q, np.float32(1e-12))))


def atlas_display_volume(template: np.ndarray, template_max: float | None) -> np.ndarray:
    """MATLAB: ``uint8(medfilt3(tv) * 255 / max(...))`` (warped into the sample grid)."""
    vol = np.asarray(template, dtype=np.float32)
    top = np.float32(template_max) if template_max else np.float32(np.max(vol))
    return matlab_uint8(vol * (np.float32(255.0) / max(top, np.float32(1e-12))))


def save_cord_session(
    session: ControlPointSession, path: Path, manual_alignment: np.ndarray
) -> Path:
    """Write the session like MATLAB ``savedata`` (points, current alignment, ori_trans)."""
    session.atlas2histology_tform = np.asarray(manual_alignment, dtype=float).tolist()
    session.coord_schema_version = COORD_SCHEMA_VERSION
    session.point_coord_source = CORD_POINT_COORDS_MATLAB
    session.save(path)
    return path


# --------------------------------------------------------------------------- GUI
@dataclass
class _Layout:
    """World coordinates (napari row, col) of the panels in the single canvas."""

    rows: int
    cols: int
    nz: int
    gap: int
    atlas_col0: float
    mip_scale: float
    mip_col0: float
    mip_row0_sample: float
    mip_gap_px: int
    title_row: float = field(default=-6.0)

    @property
    def mip_row0_atlas(self) -> float:
        return self.mip_row0_sample + (self.cols + self.mip_gap_px) * self.mip_scale


def _layout(rows: int, cols: int, nz: int) -> _Layout:
    gap = int(max(4, round(0.05 * cols)))
    total = 2 * cols + gap
    # MATLAB: projections fill ~70 % of the width in the bottom 20 % of the figure
    scale = 0.7 * total / nz
    return _Layout(
        rows=rows,
        cols=cols,
        nz=nz,
        gap=gap,
        atlas_col0=float(cols + gap),
        mip_scale=scale,
        mip_col0=(total - nz * scale) / 2.0,
        mip_row0_sample=float(rows + gap),
        mip_gap_px=int(np.ceil(0.5 * gap / scale)),
    )


def _unit_range(image: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Map ``[lo, hi]`` to ``[0, 1]`` (imagesc with CLim ``[lo hi]``)."""
    out = (np.asarray(image, dtype=np.float32) - np.float32(lo)) / np.float32(max(hi - lo, 1e-12))
    return np.clip(out, 0.0, 1.0)


def attach_spinal_match_points(
    viewer: Any,
    config: SpinalCordPipelineConfig,
    *,
    data: CordMatchPointsData | None = None,
) -> DockStageController:
    """Attach the control-point GUI to an existing napari viewer."""
    from napari.utils.notifications import show_info
    from qtpy.QtCore import QEvent, QObject, QTimer
    from qtpy.QtWidgets import QInputDialog, QMessageBox

    require_napari()
    if data is None:
        data = load_cord_match_points_data(config)
    session = data.session
    chooselist = np.asarray(data.chooselist, dtype=int)
    n_slots = int(chooselist.shape[0])

    sample_u8 = sample_display_volume(data.sample_volume)
    atlas_u8 = atlas_display_volume(data.atlas_template, data.atlas_template_max)
    annotation = np.asarray(data.atlas_annotation)
    rows, cols, nz = sample_u8.shape
    depth = int(atlas_u8.shape[2])
    lay = _layout(rows, cols, nz)

    # Both maximum projections in one image (MATLAB: max over the rows, cord axis
    # horizontal), each strip scaled to its own range like imagesc.
    mip_sample = sample_u8.max(axis=0)
    mip_atlas = atlas_u8.max(axis=0)
    projections = np.zeros((2 * cols + lay.mip_gap_px, nz), dtype=np.float32)
    projections[:cols] = _unit_range(mip_sample, float(mip_sample.min()), float(mip_sample.max()))
    projections[cols + lay.mip_gap_px :] = _unit_range(
        mip_atlas, float(mip_atlas.min()), float(mip_atlas.max())
    )
    # Sample (left) and atlas (right) slices share one image, each half scaled to its
    # MATLAB CLim, so a slice change updates a single texture.
    panels = np.zeros((rows, 2 * cols + lay.gap), dtype=np.float32)

    auto = (
        np.asarray(session.atlas2histology_tform, dtype=float)
        if data.session_loaded_from_file and session.atlas2histology_tform is not None
        else None
    )
    state: dict[str, Any] = {
        "slot": 1,
        "atlas_slice": 1,
        "manual": np.eye(4) if auto is None else auto.copy(),
        "status": "",
        "saved_flash": False,
        "teardown": False,
        "dirty": False,
    }

    # ---------------------------------------------------------------- layers
    viewer.dims.ndisplay = 2
    panels_layer = viewer.add_image(panels, name="slices", colormap="gray", contrast_limits=(0, 1))
    overlay_layer = viewer.add_image(
        np.zeros((rows, cols), np.float32),
        name="atlas boundaries",
        colormap="red",
        contrast_limits=(0, 1),
        blending="additive",
    )
    proj_layer = viewer.add_image(
        projections,
        name="max projections",
        colormap="gray",
        contrast_limits=(0, 1),
        scale=(lay.mip_scale, lay.mip_scale),
        translate=(lay.mip_row0_sample, lay.mip_col0),
    )
    strip_height = cols * lay.mip_scale
    strip_top = -0.5 * lay.mip_scale  # first projection row starts half a pixel above its centre

    def _marker_vectors(x_sample: float, x_atlas: float) -> np.ndarray:
        # vertical lines across each projection strip: [[start], [direction]]
        return np.array(
            [
                [[lay.mip_row0_sample + strip_top, x_sample], [strip_height, 0.0]],
                [[lay.mip_row0_atlas + strip_top, x_atlas], [strip_height, 0.0]],
            ]
        )

    markers = viewer.add_vectors(
        _marker_vectors(lay.mip_col0, lay.mip_col0),
        edge_color=["red", "blue"],
        edge_width=max(0.4, 0.004 * (2 * cols + lay.gap)),  # ~2 px, MATLAB LineWidth 2
        vector_style="line",
        name="slice markers",
    )
    marker = max(rows, cols) / 60.0
    text_style = {"string": "{label}", "color": "yellow", "size": 10, "anchor": "upper_left"}
    hist_pts = viewer.add_points(
        np.zeros((0, 2)),
        features={"label": np.array([], dtype=object)},
        text=text_style,
        face_color="lime",
        border_color="lime",
        size=marker,
        name="sample points",
    )
    atlas_pts = viewer.add_points(
        np.zeros((0, 2)),
        features={"label": np.array([], dtype=object)},
        text=text_style,
        face_color="red",
        border_color="red",
        size=marker,
        translate=(0, lay.atlas_col0),
        name="atlas points",
    )

    def _label_layer(name: str, pos: list[list[float]], texts: list[str], color: str) -> Any:
        return viewer.add_points(
            np.asarray(pos, dtype=float),
            features={"label": np.asarray(texts, dtype=object)},
            text={"string": "{label}", "color": color, "size": 11, "anchor": "lower_left"},
            face_color="transparent",
            border_color="transparent",
            size=0.01,
            name=name,
        )

    titles = _label_layer(
        "titles",
        # third, empty label: headroom above the panels so napari's screen-space title
        # (the MATLAB figure title) does not cover the axis titles after "fit to view"
        [[lay.title_row, 0.0], [lay.title_row, lay.atlas_col0], [-0.45 * rows, 0.0]],
        ["Sample Slice", "Atlas slice", ""],
        "white",
    )
    proj_labels = _label_layer(
        "projection labels",
        [[lay.mip_row0_sample + 0.35 * strip_height, lay.mip_col0 + 2]],
        ["Sample Max Projection"],
        "red",
    )
    proj_labels_atlas = _label_layer(
        "atlas projection label",
        [[lay.mip_row0_atlas + 0.35 * strip_height, lay.mip_col0 + 2]],
        ["Atlas Max Projection"],
        "dodgerblue",
    )
    added = [
        panels_layer, overlay_layer, proj_layer, markers, hist_pts, atlas_pts,
        titles, proj_labels, proj_labels_atlas,
    ]  # fmt: skip

    # Slim napari down: no layer docks, no editing, no mouse pan/zoom, no double-click zoom.
    camera = viewer.camera
    camera_before = (camera.mouse_pan, camera.mouse_zoom)
    for layer in added:
        layer.mouse_pan = False
        layer.mouse_zoom = False
        if hasattr(layer, "editable"):
            layer.editable = False
    viewer.layers.selection.active = panels_layer
    camera.mouse_pan = False
    camera.mouse_zoom = False
    removed_double_click = [
        cb
        for cb in list(viewer.mouse_double_click_callbacks)
        if getattr(cb, "__name__", "") == "double_click_to_zoom"
    ]
    for cb in removed_double_click:
        viewer.mouse_double_click_callbacks.remove(cb)
    hidden_docks = []
    qt_viewer = getattr(viewer.window, "_qt_viewer", None)
    for name in ("dockLayerList", "dockLayerControls"):
        dock = getattr(qt_viewer, name, None)
        if dock is not None and dock.isVisible():
            dock.setVisible(False)
            hidden_docks.append(dock)
    overlay = viewer.text_overlay
    overlay_before = (
        overlay.visible,
        overlay.text,
        overlay.position,
        overlay.font_size,
        overlay.color,
    )
    overlay.visible = True
    overlay.position = "top_center"
    overlay.font_size = 10
    overlay.color = "white"
    viewer.reset_view()
    state["min_zoom"] = camera.zoom

    # ---------------------------------------------------------------- drawing
    def slot_lists(slot: int | None = None) -> tuple[list, list]:
        i = (state["slot"] if slot is None else slot) - 1
        return session.histology_control_points[i], session.atlas_control_points[i]

    def sample_slice() -> int:
        return int(chooselist[state["slot"] - 1, 0])

    def update_title() -> None:
        if state["teardown"]:
            return
        if state["saved_flash"]:
            overlay.text = "SAVED!"
            return
        overlay.text = f"{BASE_TITLE}\n{CONTROLS}\n{state['status']}"

    def set_points(layer: Any, pts: list[list[float]]) -> None:
        if pts:
            arr = np.asarray(pts, dtype=float)
            xy = np.column_stack([arr[:, 0] - 1.0, arr[:, 1] - 1.0])  # 1-based -> napari
        else:
            xy = np.zeros((0, 2))
        layer.data = xy
        layer.features = {"label": np.asarray([str(i + 1) for i in range(len(xy))], dtype=object)}

    def update_markers(kind: str) -> None:
        hist, atl = slot_lists()
        if kind == "histology":
            set_points(hist_pts, hist)
        else:
            set_points(atlas_pts, atl)

    def set_titles() -> None:
        titles.features = {
            "label": np.asarray(
                [
                    f"Sample Slice {sample_slice()}",
                    f"Atlas slice {state['atlas_slice']}/{depth}",
                    "",
                ],
                dtype=object,
            )
        }

    def set_slice_markers() -> None:
        xs = lay.mip_col0 + lay.mip_scale * (np.array([sample_slice(), state["atlas_slice"]]) - 1)
        markers.data = _marker_vectors(float(xs[0]), float(xs[1]))
        markers.edge_color = ["red", "blue"]

    def align() -> None:
        matrix, status = fit_cord_alignment(session, auto)
        state["manual"] = matrix
        state["status"] = status
        plane = warped_annotation_plane(annotation, matrix, sample_slice())
        overlay_layer.data = boundary_mask(plane).astype(np.float32)
        update_title()

    def draw_atlas_panel() -> None:
        img = clahe_uint8(atlas_u8[:, :, state["atlas_slice"] - 1])
        panels[:, cols + lay.gap :] = _unit_range(img, *ATLAS_CLIM)

    def update_atlas_slice() -> None:
        state["atlas_slice"] = int(np.clip(state["atlas_slice"], 1, depth))
        draw_atlas_panel()
        panels_layer.data = panels
        set_titles()
        set_slice_markers()
        update_markers("atlas")

    def update_slice() -> None:
        if state["teardown"]:
            return
        z = sample_slice()
        img = clahe_uint8(sample_u8[:, :, z - 1])
        _hist, atl = slot_lists()
        state["atlas_slice"] = atlas_plane_for_slice(atl, state["manual"], (rows, cols), z)
        panels[:, :cols] = _unit_range(img, 0.0, max(image_limits(img)[1], 1.0))
        update_markers("histology")
        align()
        update_atlas_slice()

    def mark_changed() -> None:
        state["dirty"] = True

    # ---------------------------------------------------------------- mouse
    def on_press(_viewer: Any, event: Any) -> None:
        if state["teardown"] or event.type != "mouse_press":
            return
        row, col = (float(v) for v in event.position[-2:])
        inside_rows = -0.5 <= row <= rows - 0.5
        hist, atl = slot_lists()
        if inside_rows and -0.5 <= col <= cols - 0.5:
            hist.append([row + 1.0, col + 1.0, float(sample_slice()), matlab_datenum_now()])
            update_markers("histology")
        elif inside_rows and -0.5 <= col - lay.atlas_col0 <= cols - 0.5:
            atl.append(
                [
                    row + 1.0,
                    col - lay.atlas_col0 + 1.0,
                    float(state["atlas_slice"]),
                    matlab_datenum_now(),
                ]
            )
            update_markers("atlas")
        else:
            return
        mark_changed()
        if len(hist) == len(atl):
            align()

    def zoom_at(position: Any, factor: float) -> None:
        new_zoom = float(np.clip(camera.zoom * factor, state["min_zoom"], state["min_zoom"] * 40))
        factor = new_zoom / camera.zoom
        cursor = np.asarray(position, dtype=float)[-2:]
        center = np.asarray(camera.center, dtype=float)
        center[-2:] = cursor + (center[-2:] - cursor) / factor
        camera.zoom = new_zoom
        camera.center = tuple(center)

    def on_wheel(_viewer: Any, event: Any) -> None:
        if state["teardown"]:
            return
        delta = getattr(event, "delta", None)
        if delta is None:
            return
        dy = float(delta[1]) if len(delta) > 1 else float(delta[0])
        if dy == 0:
            return
        event.handled = True
        if "Control" in getattr(event, "modifiers", ()):
            zoom_at(event.position, ZOOM_STEP if dy > 0 else 1.0 / ZOOM_STEP)
            return
        # MATLAB VerticalScrollCount is positive when scrolling down
        state["atlas_slice"] += ATLAS_SCROLL_STEP * (1 if dy < 0 else -1)
        update_atlas_slice()

    viewer.mouse_drag_callbacks.append(on_press)
    viewer.mouse_wheel_callbacks.append(on_wheel)

    # ---------------------------------------------------------------- keys
    def go_slot(slot: int) -> None:
        state["slot"] = int(np.clip(slot, 1, n_slots))
        update_slice()

    def jump() -> None:
        text, ok = QInputDialog.getText(
            viewer.window._qt_window, "Go to slice", f"Go to slice (max {n_slots}):"
        )
        if not ok:
            return
        try:
            value = float(text)
        except ValueError:
            return
        if np.isfinite(value) and value == int(value) and 1 <= value <= n_slots:
            go_slot(int(value))

    def toggle_overlay() -> None:
        overlay_layer.visible = not overlay_layer.visible

    def clear_slot() -> None:
        i = state["slot"] - 1
        session.histology_control_points[i] = []
        session.atlas_control_points[i] = []
        mark_changed()
        update_slice()

    def backspace() -> None:
        hist, atl = slot_lists()
        if delete_last_point(hist, atl):
            mark_changed()
        align()
        update_slice()

    def save() -> Path:
        path = save_cord_session(session, data.session_path, state["manual"])
        state["dirty"] = False
        state["saved_flash"] = True
        update_title()

        def _restore() -> None:
            state["saved_flash"] = False
            update_title()

        QTimer.singleShot(200, _restore)
        console.print(f"Saved {path}")
        show_info(f"Saved {path.name}")
        return path

    def reset_view() -> None:
        viewer.reset_view()
        state["min_zoom"] = camera.zoom

    key_actions = {
        "Left": lambda: go_slot(state["slot"] - 1),
        "Right": lambda: go_slot(state["slot"] + 1),
        "Enter": jump,
        "Return": jump,
        "Space": toggle_overlay,
        "c": clear_slot,
        "Backspace": backspace,
        "s": save,
        "r": reset_view,
        "Home": reset_view,
    }
    previous_bindings: dict[str, Any] = {}
    for key, action in key_actions.items():
        try:
            previous_bindings[key] = viewer.bind_key(
                key, lambda _v, _a=action: _a(), overwrite=True
            )
        except (KeyError, ValueError, TypeError):
            continue
        # the active layer's keymap wins over the viewer's (Image binds C, X, ...)
        for layer in added:
            layer.bind_key(key, lambda _layer, _a=action: _a(), overwrite=True)

    # MATLAB CloseRequestFcn: "Save changes?" Yes / No / Cancel
    class _CloseGuard(QObject):
        def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802 (Qt API)
            if event.type() != QEvent.Close or state["teardown"]:
                return False
            answer = QMessageBox.question(
                viewer.window._qt_window,
                "Confirm exit",
                "Save changes?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Cancel:
                event.ignore()
                return True
            if answer == QMessageBox.Yes:
                save_cord_session(session, data.session_path, state["manual"])
                console.print(f"Saved {data.session_path}")
            state["teardown"] = True
            return False

    close_guard = _CloseGuard()
    qt_window = getattr(viewer.window, "_qt_window", None)
    if qt_window is not None:
        qt_window.installEventFilter(close_guard)

    def refresh() -> None:
        state["dirty"] = False
        update_slice()

    def teardown() -> None:
        state["teardown"] = True
        if qt_window is not None:
            try:
                qt_window.removeEventFilter(close_guard)
            except RuntimeError:
                pass
        for cb_list, cb in (
            (viewer.mouse_drag_callbacks, on_press),
            (viewer.mouse_wheel_callbacks, on_wheel),
        ):
            if cb in cb_list:
                cb_list.remove(cb)
        for cb in removed_double_click:
            if cb not in viewer.mouse_double_click_callbacks:
                viewer.mouse_double_click_callbacks.append(cb)
        for key, previous in previous_bindings.items():
            try:
                viewer.bind_key(key, previous, overwrite=True)  # None unbinds
            except (KeyError, TypeError, ValueError):
                pass
        camera.mouse_pan, camera.mouse_zoom = camera_before
        for dock in hidden_docks:
            try:
                dock.setVisible(True)
            except RuntimeError:
                pass
        (overlay.visible, overlay.text, overlay.position, overlay.font_size, overlay.color) = (
            overlay_before
        )
        for layer in added:
            if layer in viewer.layers:
                viewer.layers.remove(layer)

    controller = DockStageController(
        dock_widgets=[],
        _refresh_fn=refresh,
        _teardown_fn=teardown,
        result=data.session_path,
    )
    controller._gui_state = state  # for tests
    return controller


def run_spinal_match_points(
    config: SpinalCordPipelineConfig,
    *,
    headless: bool = False,
) -> Path:
    """Launch the control-point GUI; returns the session path."""
    if headless:
        return prepare_cord_match_points_session(config)

    data = load_cord_match_points_data(config)

    def _attach(viewer: Any) -> DockStageController:
        return attach_spinal_match_points(viewer, config, data=data)

    run_attached_stage(
        f"Control point GUI - {config.sample.name}",
        _attach,
        before_run=lambda: console.print(
            "[bold]Control point GUI (spine)[/bold]: click on the sample (left) and atlas (right) "
            "to add matching points; scroll moves the atlas plane, ←/→ change slice, "
            "Backspace deletes, C clears, Space toggles the overlay, Enter jumps, S saves."
        ),
    )
    return data.session_path
