"""Napari GUI for spinal cord straightening (spinal_cord_aligner.m port).

Layout and controls follow the MATLAB GUI:

* left: the current transverse slice with the clicked points and the fitted
  centre / dorsoventral axis;
* right: the fitted position (x, y) and angle against slice number, slice 1 at the
  top. Click either plot to jump to that slice.

Controls (no layer selection needed)::

    Left click    anterior / front   (green)
    Right click   posterior / back   (red)
    Middle click  central canal      (blue)
    Scroll, <- -> previous / next slice
    Ctrl+scroll   zoom in / out around the cursor; r or Home resets the view
    c             clear all points on this slice
    x             delete the point nearest the cursor
    Space         jump to the largest gap between annotated slices
    l             set the regularisation
    p             show / hide the fit
    s             save

Clicks are stored like MATLAB's: ``[x, y]`` = ``[column, row]``, 1-based.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from rich.console import Console

from lightsuite.config.models import SpinalCordPipelineConfig
from lightsuite.gui.stage_controller import (
    DockStageController,
    close_stage_or_viewer,
    require_napari,
    run_attached_stage,
)
from lightsuite.preprocess.cord_checkpoint import CordRegOptsCheckpoint, SpinalAlignmentCheckpoint
from lightsuite.registration.straightening_optimizer import run_straightening_optimizer

console = Console()

POINT_KINDS = ("cen", "ant", "pos")
ZOOM_STEP = 1.25  # per Ctrl+scroll tick
DELETE_RADIUS_PX = 50.0  # 'x' removes a point only if the cursor is this close (MATLAB)
KEY_HELP = (
    "[L-click] Ant | [R-click] Pos | [M-click] Cen | [scroll / ← →] Slice | [Space] Largest gap\n"
    "[Ctrl+scroll] Zoom | [r] Reset zoom | [c] Clear slice | [x] Delete nearest | "
    "[l] Set reg | [p] Show fit | [s] Save"
)


# --------------------------------------------------------------------------- pure helpers
def fit_overlay_geometry(
    fit: dict[str, np.ndarray], slice_index: int
) -> dict[str, np.ndarray] | None:
    """Napari (row, col) geometry of the fitted centre and axis on one slice (update_view)."""
    cx = float(fit["fit_x"][slice_index])
    cy = float(fit["fit_y"][slice_index])
    th = float(fit["fit_theta"][slice_index])
    if not np.isfinite(cx) or not np.isfinite(cy) or not np.isfinite(th):
        return None
    r = float(fit["fit_rad"][slice_index])
    if not np.isfinite(r) or r <= 0:
        r = 10.0
    dx = r * np.cos(th)
    dy = r * np.sin(th)
    row_c, col_c = cy, cx
    row_ant, col_ant = cy + dy, cx + dx
    row_pos, col_pos = cy - dy, cx - dx
    return {
        "center": np.array([[row_c, col_c]], dtype=float),
        "anterior": np.array([[row_ant, col_ant]], dtype=float),
        "posterior": np.array([[row_pos, col_pos]], dtype=float),
        "axis": np.array([[row_pos, col_pos], [row_ant, col_ant]], dtype=float),
    }


def clear_all_alignment_points(
    user_cen: np.ndarray, user_ant: np.ndarray, user_pos: np.ndarray
) -> None:
    user_cen[:] = np.nan
    user_ant[:] = np.nan
    user_pos[:] = np.nan


def pop_last_alignment_edit(
    history: list[tuple[int, str, tuple[float, float] | None]],
    user_cen: np.ndarray,
    user_ant: np.ndarray,
    user_pos: np.ndarray,
) -> int | None:
    """Undo the most recent point edit; return affected slice index."""
    if not history:
        return None
    slice_idx, kind, before = history.pop()
    arrays = {"cen": user_cen, "ant": user_ant, "pos": user_pos}
    arr = arrays[kind]
    if before is None:
        arr[slice_idx] = [np.nan, np.nan]
    else:
        arr[slice_idx] = list(before)
    return slice_idx


def delete_nearest_point(
    arrays: dict[str, np.ndarray],
    slice_idx: int,
    xy: tuple[float, float],
    max_dist: float = DELETE_RADIUS_PX,
) -> str | None:
    """Remove the point on ``slice_idx`` closest to ``xy`` (MATLAB 'x'); return its kind."""
    best_kind, best_d = None, np.inf
    for kind in POINT_KINDS:
        pt = arrays[kind][slice_idx]
        if np.isnan(pt[0]):
            continue
        d = float(np.hypot(pt[0] - xy[0], pt[1] - xy[1]))
        if d < best_d:
            best_kind, best_d = kind, d
    if best_kind is None or best_d >= max_dist:
        return None
    arrays[best_kind][slice_idx] = [np.nan, np.nan]
    return best_kind


def largest_gap_slice(has_data: np.ndarray, current: int) -> int:
    """Slice farthest from any annotated slice (MATLAB Space: max of bwdist)."""
    has_data = np.asarray(has_data, dtype=bool)
    n = has_data.size
    if not has_data.any():
        return n // 2
    idx = np.flatnonzero(has_data)
    pos = np.arange(n)
    j = np.clip(np.searchsorted(idx, pos), 1, len(idx)) - 1
    dist = np.minimum(np.abs(pos - idx[j]), np.abs(pos - idx[np.minimum(j + 1, len(idx) - 1)]))
    dist[current] = 0
    return int(np.argmax(dist))


# --------------------------------------------------------------------------- data
@dataclass
class StraightenCordData:
    regvol: np.ndarray
    display_vol: np.ndarray
    n_slices: int
    user_cen: np.ndarray
    user_ant: np.ndarray
    user_pos: np.ndarray
    lambda_pos: float
    lambda_ang: float
    fit: dict[str, np.ndarray]

    def arrays(self) -> dict[str, np.ndarray]:
        return {"cen": self.user_cen, "ant": self.user_ant, "pos": self.user_pos}

    def refit(self) -> None:
        self.fit = run_straightening_optimizer(
            self.user_cen,
            self.user_ant,
            self.user_pos,
            lambda_pos=self.lambda_pos,
            lambda_ang=self.lambda_ang,
        )


def _normalize_display(regvol: np.ndarray) -> np.ndarray:
    """uint8 display volume scaled between the 0.1 and 99.9 % quantiles (MATLAB)."""
    rng = np.random.default_rng(1)
    idx = rng.choice(regvol.size, size=min(regvol.size, 20_000), replace=False)
    sample = regvol.ravel()[idx].astype(np.float32)
    vmax = float(np.quantile(sample, 0.999))
    vmin = float(np.quantile(sample, 0.001))
    out = np.empty(regvol.shape, dtype=np.uint8)
    scale = 255.0 / max(vmax - vmin, 1e-6)
    for z in range(regvol.shape[2]):
        out[:, :, z] = np.clip((regvol[:, :, z].astype(np.float32) - vmin) * scale, 0, 255)
    return out


def load_straighten_data(config: SpinalCordPipelineConfig) -> StraightenCordData:
    save_path = config.sample.save_path.expanduser()
    regopts_path = save_path / "regopts.json"
    if not regopts_path.is_file():
        msg = f"Missing {regopts_path}. Run 'lightsuite spinal preprocess' first."
        raise FileNotFoundError(msg)
    checkpoint = CordRegOptsCheckpoint.load(regopts_path)
    regvol = tifffile.imread(checkpoint.regvol_path).astype(np.uint16)
    n_slices = regvol.shape[2]
    align_path = save_path / "spinal_alignment_opt.json"
    user_cen = np.full((n_slices, 2), np.nan)
    user_ant = np.full((n_slices, 2), np.nan)
    user_pos = np.full((n_slices, 2), np.nan)
    lambda_pos = config.registration.straightening_lambda_pos
    lambda_ang = config.registration.straightening_lambda_ang
    if align_path.is_file():
        prev = SpinalAlignmentCheckpoint.load(align_path)
        if len(prev.user_cen) == n_slices:
            user_cen = _restore_points(prev.user_cen)
            user_ant = _restore_points(prev.user_ant)
            user_pos = _restore_points(prev.user_pos)
            lambda_pos = prev.lambda_pos
            lambda_ang = prev.lambda_ang
            console.print(
                f"Loaded previous alignment ({align_path.name}; "
                f"Reg: Pos={lambda_pos:.0f}, Ang={lambda_ang:.0f})"
            )
        else:
            console.print(
                f"[yellow]Ignoring {align_path.name}: it covers {len(prev.user_cen)} slices, "
                f"the volume has {n_slices}.[/yellow]"
            )
    data = StraightenCordData(
        regvol=regvol,
        display_vol=_normalize_display(regvol),
        n_slices=n_slices,
        user_cen=user_cen,
        user_ant=user_ant,
        user_pos=user_pos,
        lambda_pos=lambda_pos,
        lambda_ang=lambda_ang,
        fit={},
    )
    data.refit()
    return data


def _restore_points(raw: list[list[float | None]]) -> np.ndarray:
    arr = np.full((len(raw), 2), np.nan)
    for i, pair in enumerate(raw):
        if pair[0] is not None and pair[1] is not None:
            arr[i, 0] = pair[0]
            arr[i, 1] = pair[1]
    return arr


def _serialize_points(points: np.ndarray) -> list[list[float | None]]:
    out: list[list[float | None]] = []
    for row in points:
        if np.isnan(row[0]):
            out.append([None, None])
        else:
            out.append([float(row[0]), float(row[1])])
    return out


def save_alignment_checkpoint(data: StraightenCordData, save_path: Path) -> Path:
    data.refit()
    fit = data.fit
    checkpoint = SpinalAlignmentCheckpoint(
        user_cen=_serialize_points(data.user_cen),
        user_ant=_serialize_points(data.user_ant),
        user_pos=_serialize_points(data.user_pos),
        fit_x=fit["fit_x"].tolist(),
        fit_y=fit["fit_y"].tolist(),
        fit_theta=fit["fit_theta"].tolist(),
        lambda_pos=data.lambda_pos,
        lambda_ang=data.lambda_ang,
    )
    out = save_path / "spinal_alignment_opt.json"
    checkpoint.save(out)
    return out


def _headless_default_alignment(data: StraightenCordData) -> StraightenCordData:
    """Place synthetic center/ant/pos clicks for tests without Napari."""
    cy, cx = data.regvol.shape[0] // 2, data.regvol.shape[1] // 2
    for z in range(data.n_slices):
        data.user_cen[z] = [cx + 1.0, cy + 1.0]
        data.user_ant[z] = [cx + 1.0, cy - 8.0]
        data.user_pos[z] = [cx + 1.0, cy + 8.0]
    data.refit()
    return data


# --------------------------------------------------------------------------- side plots
class _SidePlots:
    """Position and angle against slice number (MATLAB ax_pos / ax_ang)."""

    def __init__(self, data: StraightenCordData, on_pick_slice) -> None:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self.data = data
        n = data.n_slices
        rows, cols = data.regvol.shape[:2]
        self.slices = np.arange(1, n + 1)

        self.figure = Figure(figsize=(4.2, 6), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax_pos, self.ax_ang = self.figure.subplots(1, 2, sharey=True)

        ax = self.ax_pos
        ax.grid(True, alpha=0.4)
        ax.set_xlabel("Position (px)")
        ax.set_ylabel("Slice number")
        maxpos = max(rows, cols)
        ax.set_xlim(0, maxpos)
        ax.set_xticks([0, round(maxpos / 2), maxpos])
        ax.set_ylim(n, 1)  # slice 1 at the top
        (self.fit_x,) = ax.plot([], [], "b-", lw=1.2, label="Fit X")
        (self.fit_y,) = ax.plot([], [], "m-", lw=1.2, label="Fit Y")
        (self.obs_x,) = ax.plot([], [], "bo", ms=4, mfc="none")
        (self.obs_y,) = ax.plot([], [], "mo", ms=4, mfc="none")
        self.cursor_pos = ax.axhline(1, color="k", ls="--", lw=1)
        ax.legend(loc="upper right", fontsize=8)

        ax = self.ax_ang
        ax.grid(True, alpha=0.4)
        ax.set_xlabel("Angle (rad)")
        ax.set_xlim(-np.pi - 0.1, np.pi + 0.1)
        ax.tick_params(labelleft=False)
        (self.fit_th,) = ax.plot([], [], color=(0, 0.6, 0), lw=1.2)
        (self.obs_th,) = ax.plot([], [], "o", color=(0, 0.6, 0), ms=4, mfc="none")
        self.cursor_ang = ax.axhline(1, color="k", ls="--", lw=1)

        def _on_click(event) -> None:
            if event.inaxes in (self.ax_pos, self.ax_ang) and event.ydata is not None:
                on_pick_slice(int(round(event.ydata)) - 1)

        self.canvas.mpl_connect("button_press_event", _on_click)

    def update_fit(self) -> None:
        fit = self.data.fit
        s = self.slices
        self.fit_x.set_data(fit["fit_x"], s)
        self.fit_y.set_data(fit["fit_y"], s)
        self.obs_x.set_data(fit["obs_x"], s)
        self.obs_y.set_data(fit["obs_y"], s)
        self.fit_th.set_data(fit["fit_theta"], s)
        self.obs_th.set_data(fit["obs_th"], s)

    def update_cursor(self, slice_idx: int) -> None:
        y = slice_idx + 1
        self.cursor_pos.set_ydata([y, y])
        self.cursor_ang.set_ydata([y, y])
        self.canvas.draw_idle()


# --------------------------------------------------------------------------- GUI
def attach_spinal_straighten(
    viewer: Any,
    config: SpinalCordPipelineConfig,
    *,
    data: StraightenCordData | None = None,
) -> DockStageController:
    """Attach the straightening GUI to an existing napari viewer."""
    from napari.utils.notifications import show_info
    from qtpy.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QHBoxLayout,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    require_napari()
    save_path = config.sample.save_path.expanduser()
    if data is None:
        data = load_straighten_data(config)
    rows, cols = data.regvol.shape[:2]
    arrays = data.arrays()

    state: dict[str, Any] = {"slice": 0, "show_fit": True, "saved": False, "teardown": False}

    viewer.dims.ndisplay = 2
    camera = viewer.camera
    camera_before = (camera.mouse_pan, camera.mouse_zoom)
    camera.mouse_pan = False  # left-drag must not move the image
    camera.mouse_zoom = False  # the wheel changes slices, it never zooms

    marker = max(rows, cols) / 70.0
    image_layer = viewer.add_image(
        data.display_vol[:, :, 0], name="cord", colormap="gray", contrast_limits=(0, 255)
    )
    fit_axis = viewer.add_shapes(
        [np.zeros((2, 2))],
        shape_type="line",
        edge_color="yellow",
        edge_width=marker / 6,
        name="fit axis",
    )
    fit_cen = viewer.add_points(
        np.zeros((0, 2)),
        name="fit center",
        face_color="cyan",
        border_color="cyan",
        symbol="cross",
        size=marker * 0.8,
    )
    fit_ant = viewer.add_points(
        np.zeros((0, 2)),
        name="fit anterior",
        face_color="transparent",
        border_color="lime",
        border_width=0.2,
        symbol="square",
        size=marker * 0.5,
    )
    fit_pos = viewer.add_points(
        np.zeros((0, 2)),
        name="fit posterior",
        face_color="transparent",
        border_color="red",
        border_width=0.2,
        symbol="square",
        size=marker * 0.5,
    )
    user_layers = {
        "cen": viewer.add_points(
            np.zeros((0, 2)),
            name="center (middle click)",
            face_color="blue",
            border_color="blue",
            size=marker,
        ),
        "ant": viewer.add_points(
            np.zeros((0, 2)),
            name="anterior (left click)",
            face_color="lime",
            border_color="lime",
            size=marker,
        ),
        "pos": viewer.add_points(
            np.zeros((0, 2)),
            name="posterior (right click)",
            face_color="red",
            border_color="red",
            size=marker,
        ),
    }
    fit_layers = (fit_axis, fit_cen, fit_ant, fit_pos)
    added_layers = (image_layer, *fit_layers, *user_layers.values())
    for layer in (*fit_layers, *user_layers.values()):
        layer.editable = False
    # napari's camera follows the *active layer's* pan/zoom flags, so set them per layer
    for layer in added_layers:
        layer.mouse_pan = False
        layer.mouse_zoom = False
    viewer.layers.selection.active = image_layer
    camera.mouse_pan = False
    camera.mouse_zoom = False
    viewer.reset_view()
    state["min_zoom"] = camera.zoom

    # MATLAB has no layer list: hide napari's layer docks while this stage is open
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

    plots = _SidePlots(data, on_pick_slice=lambda idx: show_slice(idx))

    # ---------------------------------------------------------------- drawing
    def update_title() -> None:
        if state["teardown"]:
            return
        n_c, n_a, n_p = (int(np.sum(~np.isnan(arrays[k][:, 0]))) for k in POINT_KINDS)
        flags = "" if state["show_fit"] else " [FIT HIDDEN]"
        if state["saved"]:
            flags += " [SAVED]"
        overlay.text = (
            f"Slice: {state['slice'] + 1}/{data.n_slices} | Clicks: C={n_c} A={n_a} P={n_p} | "
            f"Regul.: Pos={data.lambda_pos:.0f} Ang={data.lambda_ang:.0f}{flags}\n{KEY_HELP}"
        )
        overlay.color = "lime" if state["saved"] else "white"

    def update_overlay() -> None:
        s = state["slice"]
        for kind, layer in user_layers.items():
            pt = arrays[kind][s]
            # stored [x, y] 1-based -> napari (row, col) 0-based
            layer.data = (
                np.zeros((0, 2)) if np.isnan(pt[0]) else np.array([[pt[1] - 1.0, pt[0] - 1.0]])
            )
        geom = fit_overlay_geometry(data.fit, s) if state["show_fit"] else None
        for layer in fit_layers:
            layer.visible = geom is not None
        if geom is not None:
            one = np.array([[1.0, 1.0]])
            fit_cen.data = geom["center"] - one
            fit_ant.data = geom["anterior"] - one
            fit_pos.data = geom["posterior"] - one
            fit_axis.data = [geom["axis"] - one]

    def show_slice(index: int) -> None:
        if state["teardown"]:
            return
        state["slice"] = int(np.clip(index, 0, data.n_slices - 1))
        image_layer.data = data.display_vol[:, :, state["slice"]]
        update_overlay()
        plots.update_cursor(state["slice"])
        update_title()

    def points_changed() -> None:
        state["saved"] = False
        data.refit()
        plots.update_fit()
        update_overlay()
        plots.update_cursor(state["slice"])
        update_title()

    # ---------------------------------------------------------------- mouse
    def on_mouse_press(_viewer, event) -> None:
        if state["teardown"] or event.type != "mouse_press":
            return
        kind = {1: "ant", 2: "pos", 3: "cen"}.get(int(event.button))
        if kind is None:
            return
        row, col = (float(v) for v in event.position[-2:])
        if not (-0.5 <= row <= rows - 0.5 and -0.5 <= col <= cols - 0.5):
            return
        arrays[kind][state["slice"]] = [col + 1.0, row + 1.0]
        points_changed()

    def on_mouse_wheel(_viewer, event) -> None:
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
            zoom_at(event.position[-2:], ZOOM_STEP if dy > 0 else 1.0 / ZOOM_STEP)
            return
        show_slice(state["slice"] + (1 if dy < 0 else -1))  # wheel down = next slice (MATLAB)

    def zoom_at(position, factor: float) -> None:
        """Zoom by ``factor`` keeping the image point under the cursor fixed."""
        new_zoom = float(np.clip(camera.zoom * factor, state["min_zoom"], state["min_zoom"] * 40))
        factor = new_zoom / camera.zoom
        cursor = np.asarray(position, dtype=float)
        center = np.asarray(camera.center, dtype=float)
        center[-2:] = cursor + (center[-2:] - cursor) / factor
        camera.zoom = new_zoom
        camera.center = tuple(center)

    def reset_zoom() -> None:
        viewer.reset_view()
        state["min_zoom"] = camera.zoom

    viewer.mouse_drag_callbacks.append(on_mouse_press)
    viewer.mouse_wheel_callbacks.append(on_mouse_wheel)
    # napari zooms on double-click with no easy way back; Ctrl+scroll and 'r' replace it
    removed_double_click = [
        cb
        for cb in list(viewer.mouse_double_click_callbacks)
        if getattr(cb, "__name__", "") == "double_click_to_zoom"
    ]
    for cb in removed_double_click:
        viewer.mouse_double_click_callbacks.remove(cb)

    # ---------------------------------------------------------------- keys
    def clear_slice() -> None:
        s = state["slice"]
        for kind in POINT_KINDS:
            arrays[kind][s] = [np.nan, np.nan]
        points_changed()

    def delete_nearest() -> None:
        row, col = (float(v) for v in viewer.cursor.position[-2:])
        if delete_nearest_point(arrays, state["slice"], (col + 1.0, row + 1.0)) is not None:
            points_changed()

    def jump_largest_gap() -> None:
        has = np.zeros(data.n_slices, dtype=bool)
        for kind in POINT_KINDS:
            has |= ~np.isnan(arrays[kind][:, 0])
        show_slice(largest_gap_slice(has, state["slice"]))

    def toggle_fit() -> None:
        state["show_fit"] = not state["show_fit"]
        update_overlay()
        update_title()

    def set_regularisation() -> None:
        dlg = QDialog(viewer.window._qt_window)
        dlg.setWindowTitle("Regularization Settings")
        form = QFormLayout(dlg)
        boxes = []
        for label, value in (
            ("Positional Lambda:", data.lambda_pos),
            ("Angular Lambda:", data.lambda_ang),
        ):
            box = QDoubleSpinBox()
            box.setDecimals(1)
            box.setRange(0.0, 1e9)
            box.setValue(float(value))
            form.addRow(label, box)
            boxes.append(box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec_():
            data.lambda_pos, data.lambda_ang = boxes[0].value(), boxes[1].value()
            points_changed()

    def save() -> Path:
        path = save_alignment_checkpoint(data, save_path)
        state["saved"] = True
        update_title()
        console.print(f"[green]Saved[/green] {path}")
        show_info(f"Saved {path.name}")
        return path

    key_actions = {
        "Left": lambda: show_slice(state["slice"] - 1),
        "Right": lambda: show_slice(state["slice"] + 1),
        "PageUp": lambda: show_slice(state["slice"] - 10),
        "PageDown": lambda: show_slice(state["slice"] + 10),
        "c": clear_slice,
        "x": delete_nearest,
        "Space": jump_largest_gap,
        "l": set_regularisation,
        "p": toggle_fit,
        "s": save,
        "r": reset_zoom,
        "Home": reset_zoom,
    }
    previous_bindings = {}
    for key, action in key_actions.items():
        previous_bindings[key] = viewer.bind_key(key, lambda _v, _a=action: _a(), overwrite=True)
        # The active layer's keymap is checked before the viewer's, and napari's Image layer
        # binds C (auto-contrast) and X (plane orientation); bind on the layer so ours win.
        image_layer.bind_key(key, lambda _layer, _a=action: _a(), overwrite=True)

    # ---------------------------------------------------------------- dock
    panel = QWidget()
    vbox = QVBoxLayout(panel)
    vbox.setContentsMargins(4, 4, 4, 4)
    vbox.addWidget(plots.canvas, stretch=1)
    row_buttons = QHBoxLayout()
    save_btn = QPushButton("Save (s)")
    close_btn = QPushButton("Save && close")
    save_btn.clicked.connect(save)

    def save_and_close() -> None:
        save()
        from qtpy.QtCore import QTimer

        QTimer.singleShot(0, lambda: close_stage_or_viewer(viewer))

    close_btn.clicked.connect(save_and_close)
    row_buttons.addWidget(save_btn)
    row_buttons.addWidget(close_btn)
    vbox.addLayout(row_buttons)
    panel.setMinimumWidth(380)

    def refresh() -> None:
        plots.update_fit()
        show_slice(state["slice"])

    def teardown() -> None:
        state["teardown"] = True
        for cb_list, cb in (
            (viewer.mouse_drag_callbacks, on_mouse_press),
            (viewer.mouse_wheel_callbacks, on_mouse_wheel),
        ):
            if cb in cb_list:
                cb_list.remove(cb)
        for key, previous in previous_bindings.items():
            try:
                viewer.bind_key(key, previous, overwrite=True)  # None unbinds
            except (KeyError, TypeError, ValueError):
                pass
        camera.mouse_pan, camera.mouse_zoom = camera_before
        for cb in removed_double_click:
            if cb not in viewer.mouse_double_click_callbacks:
                viewer.mouse_double_click_callbacks.append(cb)
        for dock in hidden_docks:
            try:
                dock.setVisible(True)
            except RuntimeError:
                pass
        (overlay.visible, overlay.text, overlay.position, overlay.font_size, overlay.color) = (
            overlay_before
        )
        for layer in added_layers:
            if layer in viewer.layers:
                viewer.layers.remove(layer)

    return DockStageController(
        dock_widgets=[(panel, "Centerline fit")],
        _refresh_fn=refresh,
        _teardown_fn=teardown,
        result=save_path / "spinal_alignment_opt.json",
    )


def run_spinal_straighten(
    config: SpinalCordPipelineConfig,
    *,
    headless: bool = False,
) -> Path:
    """Interactive or headless straightening GUI."""
    save_path = config.sample.save_path.expanduser()
    data = load_straighten_data(config)
    if headless:
        data = _headless_default_alignment(data)
        return save_alignment_checkpoint(data, save_path)

    def _attach(viewer: Any) -> DockStageController:
        return attach_spinal_straighten(viewer, config, data=data)

    run_attached_stage("Spinal Cord Optimizer", _attach)
    out = save_path / "spinal_alignment_opt.json"
    if not out.is_file():
        console.print(f"[yellow]Straightening closed without saving; expected {out}[/yellow]")
    return out
