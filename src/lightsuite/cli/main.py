"""LightSuite Typer CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from lightsuite import __version__
from lightsuite.cli.doctor import doctor_command
from lightsuite.cli.config_cmd import config_app
from lightsuite.cli.gui_cmd import register_gui_command
from lightsuite.cli.pipeline_commands import (
    register_brain_commands,
    register_spinal_commands,
)
from lightsuite.cli.workflows import workflow_app
from lightsuite.exceptions import LightsuiteConfigError

app = typer.Typer(
    name="lightsuite",
    help="LightSuite — mouse lightsheet and histology atlas registration.",
    no_args_is_help=True,
)
brain_app = typer.Typer(help="Brain lightsheet pipeline stages.")
spinal_app = typer.Typer(help="Spinal cord lightsheet pipeline stages.")
app.add_typer(brain_app, name="brain")
app.add_typer(spinal_app, name="spinal")
app.add_typer(config_app, name="config")
app.add_typer(workflow_app, name="workflow")

register_brain_commands(brain_app)
register_spinal_commands(spinal_app)
register_gui_command(app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lightsuite {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show package version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """LightSuite CLI."""


@app.command("doctor")
def doctor(
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Optional YAML config to validate paths (scratch, atlas_dir, etc.).",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Treat optional atlas warnings as errors.",
    ),
) -> None:
    """Verify Python environment, Elastix, atlases, and optional GPU."""
    doctor_command(config_path=config, strict=strict)


@brain_app.command("validate-config")
def validate_config(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
) -> None:
    """Load and validate a brain pipeline configuration file."""
    from lightsuite.config.loader import load_config

    try:
        cfg = load_config(config)
    except LightsuiteConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Config valid for sample '{cfg.sample.name}' ({cfg.sample.source.format.value}).")


@brain_app.command("probe-content-box")
def brain_probe_content_box(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    target: str = typer.Option(
        "atlas",
        "--target",
        "-t",
        help="Crop target: atlas (native BrainGlobe grid) or sample (registration TIFF).",
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Auto-detect and print the crop box without opening Napari.",
    ),
    write_config: bool = typer.Option(
        False,
        "--write-config",
        help="Write manual crop mode and box into the YAML (works with --headless).",
    ),
) -> None:
    """Probe or interactively pick atlas/sample content crop boxes."""
    from lightsuite.config.loader import load_config, save_content_box_to_config
    from lightsuite.gui.content_box_brain import run_content_box_picker
    from lightsuite.registration.content_probe import (
        ContentProbeTarget,
        format_content_box_report,
        load_content_probe_data,
    )

    config_path = Path(config).expanduser().resolve()
    cfg = load_config(config_path)
    probe_target = ContentProbeTarget(target)
    if headless:
        data = load_content_probe_data(cfg, target=probe_target)
        typer.echo(format_content_box_report(data))
        if write_config:
            path = save_content_box_to_config(
                config_path,
                target=probe_target.value,
                box=data.box.to_manual_list(),
            )
            typer.echo(f"Updated config: {path}")
        return

    box = run_content_box_picker(
        cfg,
        config_path,
        target=probe_target,
        write_config=write_config,
    )
    typer.echo(f"Selected box: {box.to_manual_list()}")


@brain_app.command("check-orientation")
def brain_check_orientation(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Update registration.orientation in the config YAML without opening Napari.",
    ),
) -> None:
    """Interactive brain axis/orientation checker (getBrainOrientation.m)."""
    from lightsuite.config.loader import load_config
    from lightsuite.gui.orientation_brain import run_brain_orientation_check

    config_path = Path(config).expanduser().resolve()
    cfg = load_config(config_path)
    path = run_brain_orientation_check(cfg, config_path, headless=headless)
    typer.echo(f"Brain orientation saved to {path}")


@brain_app.command("import-annotations")
def brain_import_annotations(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    write_csv: bool = typer.Option(
        None,
        "--write-csv/--no-write-csv",
        help="Write atlas coordinate CSVs for point imports.",
    ),
) -> None:
    """Register native sample-space annotations into atlas space."""
    from lightsuite.config.loader import load_config
    from lightsuite.import_.brain_import import run_brain_import_annotations

    cfg = load_config(config)
    results = run_brain_import_annotations(cfg, write_csv=write_csv)
    for item in results:
        typer.echo(f"{item.label}: {item.kind} — {item.n_atlas} atlas features")


@brain_app.command("convert-annotations")
def brain_convert_annotations_cmd(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
) -> None:
    """Convert vendor segmentation exports to Sample Space and validate."""
    from lightsuite.config.loader import load_config
    from lightsuite.import_.convert import run_convert_annotations_for_pipeline

    cfg = load_config(config)
    result = run_convert_annotations_for_pipeline(cfg)
    typer.echo(
        f"suite={result.suite} converted={result.n_converted} "
        f"layers={len(result.annotations)} → {result.summary_path}"
    )


@brain_app.command("convert-fiji-points")
def brain_convert_fiji_points(
    source: str = typer.Option(..., "--source", "-s", help="FIJI point-tool Results.csv export."),
    output: str = typer.Option(..., "--output", "-o", help="Output points.csv path."),
    voxel_um: str | None = typer.Option(
        None,
        "--voxel-um",
        help=(
            "ImageJ XY calibration as comma-separated x,y,z in the same units as the "
            "Results table (typically µm). Omit when X/Y are already in pixels."
        ),
    ),
) -> None:
    """Convert FIJI point-tool Results.csv to LightSuite points.csv."""
    from pathlib import Path

    from lightsuite.import_.fiji import convert_fiji_points_to_csv

    source_path = Path(source).expanduser()
    if not source_path.is_file():
        msg = f"Source CSV not found: {source_path}"
        raise typer.BadParameter(msg)

    voxel: list[float] | None = None
    if voxel_um is not None:
        parts = [float(v.strip()) for v in voxel_um.split(",")]
        if len(parts) != 3:
            msg = "--voxel-um must have exactly three values: x,y,z"
            raise typer.BadParameter(msg)
        voxel = parts

    output_path = Path(output).expanduser()
    n = convert_fiji_points_to_csv(source_path, output_path, voxel_um=voxel)
    typer.echo(f"Wrote {n} points → {output_path}")


@brain_app.command("convert-smartspim-points")
def brain_convert_smartspim_points(
    source: str = typer.Option(
        ...,
        "--source",
        "-s",
        help="SmartSPIM / LCT detection JSON ([[z,y,x],…] 0-based native voxels).",
    ),
    output: str = typer.Option(..., "--output", "-o", help="Output points.csv path."),
) -> None:
    """Convert SmartSPIM cell-detection points JSON to LightSuite points.csv."""
    from pathlib import Path

    from lightsuite.import_.smartspim_detection import convert_smartspim_points_json_to_csv

    source_path = Path(source).expanduser()
    if not source_path.is_file():
        msg = f"Source JSON not found: {source_path}"
        raise typer.BadParameter(msg)

    output_path = Path(output).expanduser()
    n = convert_smartspim_points_json_to_csv(source_path, output_path)
    typer.echo(f"Wrote {n} points → {output_path}")


@brain_app.command("split-smartspim-channels")
def brain_split_smartspim_channels(
    source: str = typer.Option(
        ...,
        "--source",
        "-s",
        help="Flat SmartSPIM All_Channels folder (interleaved Z*_ChN.tif planes).",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Parent for Ch0/Ch1/Ch2 folders (default: create inside --source).",
    ),
    mode: str = typer.Option(
        "symlink",
        "--mode",
        "-m",
        help="How to place files into ChN folders: symlink (default), hardlink, or move (no copies).",
    ),
    max_channels: int = typer.Option(
        3,
        "--max-channels",
        help="Maximum channel count (1–3). Channels Ch0…Ch{N-1} are written.",
        min=1,
        max=3,
    ),
    allow_unequal: bool = typer.Option(
        False,
        "--allow-unequal",
        help="Allow channels with different plane counts (default: require equal).",
    ),
) -> None:
    """Split flat SmartSPIM All_Channels TIFFs into Ch0/Ch1/Ch2 for planeperfile."""
    from pathlib import Path

    from lightsuite.io.smartspim_channels import SplitMode, split_smartspim_all_channels

    source_path = Path(source).expanduser()
    if not source_path.is_dir():
        msg = f"Source folder not found: {source_path}"
        raise typer.BadParameter(msg)

    try:
        mode_enum = SplitMode(mode.lower())
    except ValueError as exc:
        msg = "--mode must be one of: symlink, hardlink, move"
        raise typer.BadParameter(msg) from exc

    output_path = Path(output).expanduser() if output else None
    result = split_smartspim_all_channels(
        source_path,
        output_dir=output_path,
        mode=mode_enum,
        max_channels=max_channels,
        require_equal_plane_counts=not allow_unequal,
    )
    for channel, ch_dir in sorted(result.channel_dirs.items()):
        n = result.plane_counts[channel]
        typer.echo(f"Ch{channel}: {n} planes → {ch_dir}")
    typer.echo(
        f"Split complete ({result.mode.value}). "
        "Point sample.source.channels at the ChN folders for preprocess."
    )


@brain_app.command("import-matlab-control-points")
def brain_import_matlab_control_points(
    mat: str = typer.Option(
        ...,
        "--mat",
        "-m",
        help="MATLAB atlas2histology_tform.mat (or other *tform.mat with control points).",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output JSON path (default: same folder as --mat, atlas2histology_tform.json).",
    ),
    save_path: str | None = typer.Option(
        None,
        "--save-path",
        help="Sample save_path from config; writes atlas2histology_tform.json there.",
    ),
) -> None:
    """Import MATLAB match-points session for Python register (parity with multiobjRegistration.m)."""
    from lightsuite.gui.control_points import ControlPointSession
    from lightsuite.import_.matlab_control_points import import_matlab_control_points

    mat_path = Path(mat).expanduser()
    if not mat_path.is_file():
        msg = f"MATLAB file not found: {mat_path}"
        raise typer.BadParameter(msg)

    if save_path is not None:
        out_path = Path(save_path).expanduser() / "atlas2histology_tform.json"
    elif output is not None:
        out_path = Path(output).expanduser()
    else:
        out_path = mat_path.with_name("atlas2histology_tform.json")

    written = import_matlab_control_points(mat_path, output_json=out_path)
    matched, total_sample, total_atlas = ControlPointSession.load(written).point_counts()
    typer.echo(
        f"Wrote {written} — {matched} matched pairs "
        f"({total_sample} sample / {total_atlas} atlas points across slices)."
    )


@brain_app.command("migrate-control-point-session")
def brain_migrate_control_point_session(
    json_path: str | None = typer.Option(
        None,
        "--json",
        "-j",
        help="atlas2histology_tform.json to migrate.",
    ),
    save_path: str | None = typer.Option(
        None,
        "--save-path",
        help="Sample save_path; migrates atlas2histology_tform.json there.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report how many points would be updated without writing.",
    ),
) -> None:
    """Fix in-plane axis swap in Napari-authored control-point sessions (pre-v2 schema).

    Do not run on sessions imported from MATLAB (``point_coord_source: matlab``).
    Re-import those from ``.mat`` if needed.
    """
    from lightsuite.gui.control_points import (
        COORD_SCHEMA_VERSION,
        POINT_COORD_SOURCE_MATLAB,
        ControlPointSession,
        default_session_path,
        migrate_napari_transposed_control_points,
        session_needs_napari_transpose_migration,
    )

    if save_path is not None:
        session_path = default_session_path(Path(save_path).expanduser())
    elif json_path is not None:
        session_path = Path(json_path).expanduser()
    else:
        msg = "Provide --json or --save-path."
        raise typer.BadParameter(msg)

    if not session_path.is_file():
        msg = f"Session not found: {session_path}"
        raise typer.BadParameter(msg)

    session = ControlPointSession.load(session_path)
    if session.point_coord_source == POINT_COORD_SOURCE_MATLAB:
        typer.echo(
            f"Skipping {session_path}: imported from MATLAB "
            f"(point_coord_source={POINT_COORD_SOURCE_MATLAB!r})."
        )
        raise typer.Exit(0)

    if not session_needs_napari_transpose_migration(session):
        typer.echo(
            f"No migration needed for {session_path} "
            f"(coord_schema_version={session.coord_schema_version})."
        )
        raise typer.Exit(0)

    if session.chooselist is not None:
        chooselist = session.chooselist
    else:
        typer.echo(
            "Warning: session has no chooselist; cannot migrate in-plane axes. "
            "Open match-points once to populate chooselist, then re-run."
        )
        raise typer.Exit(1)

    if dry_run:
        n_hist = sum(len(s) for s in session.histology_control_points)
        n_atlas = sum(len(s) for s in session.atlas_control_points)
        typer.echo(
            f"Would migrate {n_hist + n_atlas} point rows in {session_path} "
            f"(schema v{session.coord_schema_version} -> v{COORD_SCHEMA_VERSION})."
        )
        raise typer.Exit(0)

    updated = migrate_napari_transposed_control_points(session, chooselist)
    session.save(session_path)
    typer.echo(
        f"Migrated {updated} point rows in {session_path} "
        f"(coord_schema_version={COORD_SCHEMA_VERSION})."
    )


@brain_app.command("export-matlab-control-points")
def brain_export_matlab_control_points(
    json_path: str | None = typer.Option(
        None,
        "--json",
        "-j",
        help="atlas2histology_tform.json from Napari match-points.",
    ),
    save_path: str | None = typer.Option(
        None,
        "--save-path",
        help="Sample save_path; reads atlas2histology_tform.json there.",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output .mat path (default: atlas2histology_tform.mat beside the JSON).",
    ),
) -> None:
    """Export Napari control points for MATLAB register (multiobjRegistration.m)."""
    from lightsuite.gui.control_points import ControlPointSession, default_session_path
    from lightsuite.import_.matlab_control_points import export_matlab_control_points

    if save_path is not None:
        session_path = default_session_path(Path(save_path).expanduser())
    elif json_path is not None:
        session_path = Path(json_path).expanduser()
    else:
        msg = "Provide --json or --save-path."
        raise typer.BadParameter(msg)

    out_path = Path(output).expanduser() if output is not None else None
    written = export_matlab_control_points(session_path, output_mat=out_path)
    matched, total_sample, total_atlas = ControlPointSession.load(session_path).point_counts()
    typer.echo(
        f"Wrote {written} — {matched} matched pairs "
        f"({total_sample} sample / {total_atlas} atlas points across slices)."
    )


@brain_app.command("view-registration")
def brain_view_registration(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    space: str = typer.Option(
        "sample",
        "--space",
        help="View space: sample (20 µm registration grid) or atlas (Perens/Allen export grid).",
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Validate view inputs without opening Napari.",
    ),
) -> None:
    """Napari QC: registered channels, atlas overlays, divisions, and import previews."""
    from lightsuite.cli.spaces import parse_view_space_option
    from lightsuite.config.loader import load_config
    from lightsuite.gui.view_registration_brain import run_brain_view_registration

    cfg = load_config(config)
    view_space = parse_view_space_option(space)
    paths = run_brain_view_registration(
        cfg,
        space=view_space,  # type: ignore[arg-type]
        headless=headless,
    )
    if paths.sample_space_dir is not None:
        typer.echo(f"sample_space: {paths.sample_space_dir}")
    typer.echo(f"volume_registered: {paths.volume_registered_dir}")
    if paths.registered_channels:
        typer.echo(f"  channels: {sorted(paths.registered_channels)}")
    if paths.point_npz_paths:
        typer.echo(f"  point layers: {list(paths.point_npz_paths)}")
    if paths.mask_paths:
        typer.echo(f"  mask layers: {list(paths.mask_paths)}")


@brain_app.command("export")
def brain_export(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    write_csv: bool = typer.Option(
        None,
        "--write-csv/--no-write-csv",
        help="Write parcellation intensity CSVs (default: export.write_cells_csv).",
    ),
    save_volume: bool = typer.Option(
        None,
        "--save-volume/--no-save-volume",
        help="Save registered atlas-space volumes (default: export.save_registered_volume).",
    ),
    space: str | None = typer.Option(
        None,
        "--space",
        help="Output space: atlas, sample, or both (default: export.spaces in YAML).",
    ),
) -> None:
    """Apply transforms and export registered volumes (generateRegisteredBrainVolumes.m)."""
    from lightsuite.cli.spaces import parse_spaces_option
    from lightsuite.config.loader import load_config
    from lightsuite.export.brain_export import export_registered_brain_volumes

    cfg = load_config(config)
    result = export_registered_brain_volumes(
        cfg,
        write_csv=write_csv,
        save_registered_volume=save_volume,
        spaces=parse_spaces_option(space),
    )
    if result.registered_volumes:
        typer.echo(f"Registered volumes: {len(result.registered_volumes)} channel(s)")
    if result.parcellation_paths:
        typer.echo(f"Parcellation CSVs: {len(result.parcellation_paths)} channel(s)")


@brain_app.command("register")
def brain_register(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    single_step: bool = typer.Option(
        False,
        "--single-step",
        help="Use a single-resolution B-spline schedule (faster, lower quality).",
    ),
) -> None:
    """Run elastix registration (multiobjRegistration.m)."""
    from lightsuite.config.loader import load_config
    from lightsuite.registration.brain_register import run_brain_registration

    cfg = load_config(config)
    run_brain_registration(cfg, use_multistep=not single_step)


@brain_app.command("align-slices")
def brain_align_slices(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Auto-estimate slice correspondence without opening Napari (for tests).",
    ),
) -> None:
    """Interactive sample-to-atlas slice alignment before control-point matching."""
    from lightsuite.config.loader import load_config
    from lightsuite.gui.align_slices_brain import run_brain_align_slices

    cfg = load_config(config)
    path = run_brain_align_slices(cfg, headless=headless)
    typer.echo(f"Slice correspondence: {path}")


@brain_app.command("match-points")
def brain_match_points(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Load data and write an empty session without opening napari (for tests).",
    ),
) -> None:
    """Interactive control-point matching (matchControlPoints_unified.m)."""
    from lightsuite.config.loader import load_config
    from lightsuite.gui.match_points_brain import run_brain_match_points

    cfg = load_config(config)
    path = run_brain_match_points(cfg, headless=headless)
    typer.echo(f"Control points session: {path}")


@brain_app.command("init-registration")
def brain_init_registration(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
) -> None:
    """Coarse-align sample to atlas (initializeRegistration.m)."""
    from lightsuite.config.loader import load_config
    from lightsuite.registration.init_brain import initialize_brain_registration

    cfg = load_config(config)
    initialize_brain_registration(cfg)


@brain_app.command("preprocess")
def brain_preprocess(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-run downsampling even when cached registration TIFFs match the current config.",
    ),
) -> None:
    """Downsample sample volumes for registration (preprocessLightSheetVolume.m)."""
    from lightsuite.config.loader import load_config
    from lightsuite.preprocess.brain import preprocess_lightsheet_volume

    cfg = load_config(config)
    result = preprocess_lightsheet_volume(cfg, force=force)
    typer.echo(f"Primary registration volume: {result.checkpoint.regvolpath}")


@spinal_app.command("validate-config")
def spinal_validate_config(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
) -> None:
    """Load and validate a spinal cord pipeline configuration file."""
    from lightsuite.config.loader import load_spinal_config

    cfg = load_spinal_config(config)
    typer.echo(
        f"Config valid for sample '{cfg.sample.name}' "
        f"({cfg.sample.source.tiff_type.value})."
    )


@spinal_app.command("check-orientation")
def spinal_check_orientation(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Write cord_orientation.txt without opening Napari (for tests).",
    ),
) -> None:
    """Manually set the cord rostrocaudal direction (writes cord_orientation.txt)."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.gui.orientation_cord import run_spinal_orientation

    cfg = load_spinal_config(config)
    path = run_spinal_orientation(cfg, headless=headless)
    typer.echo(f"Cord orientation saved to {path}")


@spinal_app.command("preprocess")
def spinal_preprocess(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Fail if cord_orientation.txt is missing instead of opening Napari (for tests).",
    ),
) -> None:
    """Prepare cord sample and atlas (prepareCordSampleForRegistration.m)."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.preprocess.cord import preprocess_spinal_cord_sample

    cfg = load_spinal_config(config)
    result = preprocess_spinal_cord_sample(cfg, headless=headless)
    typer.echo(f"Wrote checkpoint: {result.checkpoint.lsfolder}/regopts.json")


@spinal_app.command("straighten")
def spinal_straighten(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Write default alignment without opening Napari (for tests).",
    ),
) -> None:
    """Interactive cord straightening GUI (spinal_cord_aligner.m)."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.gui.straighten_cord import run_spinal_straighten

    cfg = load_spinal_config(config)
    path = run_spinal_straighten(cfg, headless=headless)
    typer.echo(f"Alignment checkpoint: {path}")


@spinal_app.command("align-longitudinal")
def spinal_align_longitudinal(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Auto-confirm centered z-init anchors without Napari (for tests).",
    ),
) -> None:
    """Interactive rostrocaudal sample-to-atlas alignment before init-registration."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.gui.align_longitudinal_cord import run_spinal_align_longitudinal

    cfg = load_spinal_config(config)
    path = run_spinal_align_longitudinal(cfg, headless=headless)
    typer.echo(f"Longitudinal correspondence: {path}")


@spinal_app.command("init-registration")
def spinal_init_registration(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
) -> None:
    """Apply straightening and coarse affine registration (initializeCordRegistration.m)."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.registration.init_cord import initialize_cord_registration

    cfg = load_spinal_config(config)
    initialize_cord_registration(cfg)


@spinal_app.command("match-points")
def spinal_match_points(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Write an empty control-point session without Napari.",
    ),
) -> None:
    """Interactive control-point matching (matchControlPointsSpine.m)."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.gui.match_points_cord import run_spinal_match_points

    cfg = load_spinal_config(config)
    path = run_spinal_match_points(cfg, headless=headless)
    typer.echo(f"Control points session: {path}")


@spinal_app.command("register")
def spinal_register(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
) -> None:
    """Run B-spline registration (multiobjCordRegistration.m)."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.registration.cord_register import run_spinal_registration

    cfg = load_spinal_config(config)
    path = run_spinal_registration(cfg)
    typer.echo(f"Transform params: {path}")


@spinal_app.command("export")
def spinal_export(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    space: str | None = typer.Option(
        None,
        "--space",
        help="Output space: atlas, sample, or both (default: export.spaces in YAML).",
    ),
) -> None:
    """Export registered cord volumes (generateRegisteredCordVolume.m)."""
    from lightsuite.cli.spaces import parse_spaces_option
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.export.cord_export import export_registered_cord_volumes

    cfg = load_spinal_config(config)
    result = export_registered_cord_volumes(cfg, spaces=parse_spaces_option(space))
    typer.echo(f"Registered volumes in {result.output_dir} ({len(result.channel_paths)} channels)")
    if result.region_stats is not None and result.region_stats.combined_path is not None:
        typer.echo(
            f"Region stats: {result.region_stats.combined_path} "
            f"({result.region_stats.n_rows} rows)"
        )


@spinal_app.command("import-annotations")
def spinal_import_annotations(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    write_csv: bool = typer.Option(
        None,
        "--write-csv/--no-write-csv",
        help="Write atlas coordinate CSVs for point imports.",
    ),
) -> None:
    """Register native sample-space annotations into Fiederling atlas space."""
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.import_.cord_import import run_cord_import_annotations

    cfg = load_spinal_config(config)
    results = run_cord_import_annotations(cfg, write_csv=write_csv)
    for item in results:
        typer.echo(f"{item.label}: {item.kind} — {item.n_atlas} atlas features")


@spinal_app.command("convert-imaris-spots")
def spinal_convert_imaris_spots(
    source: str = typer.Option(..., "--source", "-s", help="Imaris Spot_*_Detailed.csv export."),
    output_dir: str = typer.Option(..., "--output-dir", "-o", help="Directory for points.csv files."),
    voxel_um: str = typer.Option(
        ...,
        "--voxel-um",
        help=(
            "Size of one LightSuite native voxel in the same units as Imaris Position columns "
            "(Image Properties → Voxel Size), as comma-separated x,y,z. "
            "Use 1,1,1 when the .ims is 1 µm isotropic on the same grid; "
            "use hybrid values such as 1,1,1.8 when XY matches LightSuite indices but Z "
            "plane counts differ; use the microscope size (e.g. 1.8,1.8,1.8) only when "
            "Imaris is calibrated to that size."
        ),
    ),
    label_prefix: str = typer.Option("imaris", help="Filename prefix for converted CSVs."),
    shape_yxz: str | None = typer.Option(
        None,
        "--shape-yxz",
        help="Optional native grid Y,X,Z (e.g. from sample_reference.json) to warn on unit mismatch.",
    ),
) -> None:
    """Convert Imaris multi-component spot CSV to LightSuite points.csv files."""
    from pathlib import Path

    from lightsuite.import_.imaris import (
        convert_imaris_spots_by_component,
        list_imaris_component_names,
        warn_if_positions_look_like_voxel_indices,
    )

    parts = [float(v.strip()) for v in voxel_um.split(",")]
    if len(parts) != 3:
        msg = "--voxel-um must have exactly three values: x,y,z"
        raise typer.BadParameter(msg)

    source_path = Path(source).expanduser()
    if not source_path.is_file():
        msg = f"Source CSV not found: {source_path}"
        raise typer.BadParameter(msg)

    shape: tuple[int, int, int] | None = None
    if shape_yxz is not None:
        shape_parts = [int(v.strip()) for v in shape_yxz.split(",")]
        if len(shape_parts) != 3:
            msg = "--shape-yxz must have exactly three integers: Y,X,Z"
            raise typer.BadParameter(msg)
        shape = (shape_parts[0], shape_parts[1], shape_parts[2])

    warning = warn_if_positions_look_like_voxel_indices(
        source_path, voxel_um=parts, shape_yxz=shape
    )
    if warning:
        typer.echo(f"WARNING: {warning}", err=True)

    components = list_imaris_component_names(source_path)
    if not components:
        msg = f"No component labels found in {source_path}"
        raise typer.BadParameter(msg)

    written = convert_imaris_spots_by_component(
        source_path,
        Path(output_dir),
        voxel_um=parts,
        label_prefix=label_prefix,
    )
    for component, path in written.items():
        typer.echo(f"{component}: {path}")
    typer.echo(f"Wrote {len(written)} component CSV(s) to {output_dir}")


@spinal_app.command("region-stats")
def spinal_region_stats(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    count_points: bool = typer.Option(
        None,
        "--count-points/--no-count-points",
        help="Bin imported atlas-space points into cell counts (default: analysis.count_points).",
    ),
    parcellate_intensities: bool = typer.Option(
        None,
        "--parcellate-intensities/--no-parcellate-intensities",
        help="Median intensity per region × segment from exported channels (default: analysis.parcellate_intensities).",
    ),
    space: str | None = typer.Option(
        None,
        "--space",
        help="Stats space: atlas, sample, or both (default: analysis.stats_spaces).",
    ),
) -> None:
    """Assemble region_stats.csv with per-region, per-segment intensities and cell counts."""
    from lightsuite.analysis.cord_runner import run_cord_region_stats
    from lightsuite.cli.spaces import parse_spaces_option
    from lightsuite.config.loader import load_spinal_config

    cfg = load_spinal_config(config)
    result = run_cord_region_stats(
        cfg,
        count_points=count_points,
        parcellate_intensities=parcellate_intensities,
        stats_spaces=parse_spaces_option(space),
    )
    if result.combined_path is not None:
        typer.echo(
            f"Region stats: {result.combined_path} ({result.n_rows} rows, "
            f"{len(result.intensity_channels)} intensity channel(s), "
            f"{len(result.count_labels)} point source(s))"
        )
    else:
        typer.echo(
            "No region stats produced. Run 'lightsuite spinal export' for intensities "
            "and/or 'lightsuite spinal import-annotations' for point counts."
        )


@spinal_app.command("inspect-imports")
def spinal_inspect_imports(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    space: str = typer.Option(
        "atlas",
        "--space",
        help="Inspect space: atlas (Fiederling export grid) or sample (straightened 20 µm grid).",
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Validate inspect inputs without opening Napari.",
    ),
    recompute_annotation: bool = typer.Option(
        False,
        "--recompute-annotation",
        help="Rebuild annotation_registered.tiff from transform_params.json (atlas space only).",
    ),
) -> None:
    """Napari QC: registered channels, atlas, and imported points (alias for view)."""
    from lightsuite.cli.spaces import parse_view_space_option
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.gui.view_registered_cord import run_spinal_view_registration

    cfg = load_spinal_config(config)
    inspect_space = parse_view_space_option(space)
    paths = run_spinal_view_registration(
        cfg,
        space=inspect_space,  # type: ignore[arg-type]
        headless=headless,
        recompute_annotation=recompute_annotation,
    )
    if paths.sample_space_dir is not None:
        typer.echo(f"sample_space: {paths.sample_space_dir}")
    else:
        typer.echo(f"volume_registered: {paths.volume_registered_dir}")
    if paths.registered_channels:
        typer.echo(f"  channels: {sorted(paths.registered_channels)}")
    if paths.point_npz_paths:
        typer.echo(f"  point layers: {list(paths.point_npz_paths)}")


@spinal_app.command("view")
def spinal_view(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    space: str = typer.Option(
        "atlas",
        "--space",
        help="View space: atlas (Fiederling export grid) or sample (straightened 20 µm grid).",
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Validate view inputs without opening Napari.",
    ),
    recompute_annotation: bool = typer.Option(
        False,
        "--recompute-annotation",
        help="Rebuild annotation_registered.tiff from transform_params.json (atlas space only).",
    ),
) -> None:
    """Open Napari with registered sample channel(s) and warped atlas annotation."""
    from lightsuite.cli.spaces import parse_view_space_option
    from lightsuite.config.loader import load_spinal_config
    from lightsuite.gui.view_registered_cord import run_spinal_view_registration

    cfg = load_spinal_config(config)
    view_space = parse_view_space_option(space)
    paths = run_spinal_view_registration(
        cfg,
        space=view_space,  # type: ignore[arg-type]
        headless=headless,
        recompute_annotation=recompute_annotation,
    )
    if paths.space == "sample":
        typer.echo(f"Sample-space data: {paths.sample_space_dir}")
        typer.echo(f"Annotation: {paths.annotation_path.name}")
        if paths.registered_channels:
            typer.echo(f"Channels: {sorted(paths.registered_channels)}")
        if paths.point_npz_paths:
            typer.echo(f"Point layers: {list(paths.point_npz_paths)}")
    else:
        typer.echo(f"Registered data: {paths.volume_registered_dir}")
        if paths.annotation_path.is_file():
            typer.echo(f"Annotation: {paths.annotation_path.name}")
        if paths.point_npz_paths:
            typer.echo(f"Point layers: {list(paths.point_npz_paths)}")


@spinal_app.command("plot-heatmap")
def spinal_plot_heatmap(
    config: str = typer.Option(..., "--config", "-c", help="Spinal cord pipeline YAML config."),
    metric: str = typer.Option(
        "median_intensity",
        "--metric",
        "-m",
        help="Value to plot: median_intensity, relative_median_intensity, cell_count, cell_density, std.",
    ),
    channel: str | None = typer.Option(
        None,
        "--channel",
        help="Intensity channel index (e.g. 1) or import label (e.g. imaris_TA).",
    ),
    hemisphere: str = typer.Option(
        "whole",
        "--hemisphere",
        help="Hemisphere filter: whole, left, or right.",
    ),
    segments: str = typer.Option(
        "C1:Co2",
        "--segments",
        help="Segment range (C1:Co2) or comma-separated labels.",
    ),
    rollup_level: str = typer.Option(
        "structure",
        "--rollup",
        help="Rollup level to plot (structure = Lamina I–X + df/lf/vf).",
    ),
    input_path: str | None = typer.Option(
        None,
        "--input",
        help="Override path to region_stats.csv.",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output PNG path (default: <save_path>/plots/cord_heatmap_<metric>_<channel>.png).",
    ),
    cmap: str | None = typer.Option(None, "--cmap", help="Matplotlib colormap name."),
    vmin: float | None = typer.Option(None, "--vmin", help="Color scale minimum."),
    vmax: float | None = typer.Option(None, "--vmax", help="Color scale maximum."),
    log_scale: bool = typer.Option(False, "--log-scale", help="Use logarithmic color scaling."),
    normalize: str = typer.Option(
        "none",
        "--normalize",
        help="Rescale values before plotting: none, row, or column.",
    ),
) -> None:
    """Plot a paper-style segment × lamina/WM heatmap from region_stats.csv."""
    from lightsuite.analysis.cord_heatmap import (
        MetricName,
        parse_segment_range,
        run_cord_heatmap,
    )
    from lightsuite.config.loader import load_spinal_config

    cfg = load_spinal_config(config)
    parsed_channel: int | str | None
    if channel is None:
        parsed_channel = None
    elif channel.isdigit():
        parsed_channel = int(channel)
    else:
        parsed_channel = channel

    segment_labels = parse_segment_range(segments, atlas_dir=cfg.atlas.atlas_dir)
    output_path = Path(output).expanduser() if output else None
    stats_input = Path(input_path).expanduser() if input_path else None

    path = run_cord_heatmap(
        cfg,
        metric=metric,  # type: ignore[arg-type]
        channel=parsed_channel,
        rollup_level=rollup_level,
        hemisphere=hemisphere,
        segments=segment_labels,
        input_path=stats_input,
        output_path=output_path,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        log_scale=log_scale,
        normalize=normalize,
    )
    typer.echo(f"Heatmap saved: {path}")


@spinal_app.command("validate-parity")
def spinal_validate_parity(
    fixture_root: str = typer.Option(
        "tests/fixtures/spinal_cord",
        "--fixture-root",
        help="Directory containing parity reference fixtures.",
    ),
) -> None:
    """Run automated MATLAB-parity checks for MVP stages (optimizer reference)."""
    from lightsuite.validation.spinal_parity import run_mvp_parity_checks

    report = run_mvp_parity_checks(Path(fixture_root).expanduser().resolve())
    for message in report.messages:
        typer.echo(message)
    if not report.passed:
        raise typer.Exit(code=1)


def run() -> None:
    app()


if __name__ == "__main__":
    run()
