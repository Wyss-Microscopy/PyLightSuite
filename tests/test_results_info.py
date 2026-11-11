"""Tests for the info_results.md guide and the generated docs/outputs.md page."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lightsuite.analysis.cord_counts import CORD_TIDY_COLUMNS
from lightsuite.analysis.region_stats import METRICS, TIDY_COLUMNS
from lightsuite.cli.main import app
from lightsuite.cli.stage_registry import StageContext, get_workflow, run_stage
from lightsuite.export import results_info
from lightsuite.export.results_info import (
    COLUMN_DESCRIPTIONS,
    METRIC_DESCRIPTIONS,
    OUTPUT_ENTRIES,
    RESULTS_INFO_FILENAME,
    SECTIONS,
    build_results_info,
    entries_for,
    refresh_results_info,
    render_outputs_reference,
    write_results_info,
)

DOCS_PAGE = Path(__file__).resolve().parents[1] / "docs" / "outputs.md"

_BRAIN_STATS = (
    "sample,channel,atlas,parcellation_index,acronym,name,structure,division,hemisphere,metric,value\n"
    "M1,1,allen,5,CA1,Field CA1,HPF,grey,left,median_intensity,3.0\n"
    "M1,1,allen,5,CA1,Field CA1,HPF,grey,right,volume_mm3,0.1\n"
    "M1,cells,allen,5,CA1,Field CA1,HPF,grey,left,cell_count,7\n"
)


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def _render(save_path: Path, workflow: str = "brain") -> str:
    return build_results_info(
        save_path, workflow, sample_name="M1", atlas_label="allen 10 µm", stage="export"
    )


# ----------------------------------------------------------------------- the entry list
def test_docs_outputs_page_is_up_to_date() -> None:
    assert DOCS_PAGE.read_text(encoding="utf-8") == render_outputs_reference(), (
        "docs/outputs.md is out of date. Regenerate it with:\n"
        "  uv run python -m lightsuite.export.results_info --write-docs docs/outputs.md"
    )


def test_entries_are_well_formed() -> None:
    seen: set[tuple[str, str]] = set()
    for entry in OUTPUT_ENTRIES:
        assert entry.section in SECTIONS
        assert entry.workflows and entry.workflows <= {"brain", "spinal"}
        assert "|" not in entry.description, "breaks the Markdown table in docs/outputs.md"
        for workflow in entry.workflows:
            key = (entry.pattern, workflow)
            assert key not in seen, f"duplicate entry {key}"
            seen.add(key)


@pytest.mark.parametrize("workflow", ["brain", "spinal"])
def test_every_entry_is_reachable(workflow: str) -> None:
    """A file named after each pattern must match that entry, not an earlier one."""
    entries = entries_for(workflow)
    for entry in entries:
        sample = entry.pattern.replace("*", "x1")
        first = next(e for e in entries if e.matches(sample))
        assert first is entry, f"{entry.pattern} is shadowed by {first.pattern}"


def test_wildcards_do_not_cross_directories() -> None:
    entry = next(e for e in OUTPUT_ENTRIES if e.pattern == "stats/*_region_counts.csv")
    assert entry.matches("stats/cells_region_counts.csv")
    assert not entry.matches("stats/old/cells_region_counts.csv")


def test_column_and_metric_descriptions_cover_the_schema() -> None:
    assert set(TIDY_COLUMNS) <= set(COLUMN_DESCRIPTIONS)
    assert set(CORD_TIDY_COLUMNS) <= set(COLUMN_DESCRIPTIONS)
    assert set(METRICS) <= set(METRIC_DESCRIPTIONS)


# --------------------------------------------------------------------------- the guide
def test_brain_guide_lists_existing_files_only(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "regopts.json",
        "chan_1_sample_register_20um.tif",
        "chan_2_sample_register_20um.tif",
        "volume_registered/chan_01_registered_atlas.tif",
        "qc/M1_dim1_bspline_registration.png",
        "imports/cells_atlas_coords.npz",
        "stats/region_stats_top10.csv",
        "mystery.bin",
        "_work/elastix/result.0.mhd",
        ".hidden",
    )
    (tmp_path / "stats" / "region_stats.csv").write_text(_BRAIN_STATS, encoding="utf-8")

    text = _render(tmp_path)

    assert "# PyLightSuite results: M1" in text
    assert "**`regopts.json`**" in text
    assert "**`chan_N_sample_register_20um.tif`** (2 files)" in text
    assert "`imports/cells_atlas_coords.npz`" in text
    assert "**`stats/region_stats_topN.csv`** (1 file)" in text
    # Nothing that is absent is described.
    assert "transform_params.json" not in text
    assert "sample_space" not in text
    # Unknown files are listed, temporary and hidden ones are not.
    assert "## Other files" in text and "`mystery.bin`" in text
    assert "result.0.mhd" not in text and ".hidden" not in text
    assert "`_work/` holds temporary Elastix files" in text
    # Statistics summary and column guide come from the real CSV.
    assert "Metrics: `median_intensity`, `volume_mm3`, `cell_count`" in text
    assert "Channels: `1`, `cells`" in text
    assert "| `parcellation_index` | Atlas label value of the region. |" in text
    assert "| `segment` |" not in text
    assert "**Output spaces present:** atlas" in text


def test_guide_warns_about_comparing_spaces_only_when_both_exist(tmp_path: Path) -> None:
    (tmp_path / "stats").mkdir()
    (tmp_path / "stats" / "region_stats.csv").write_text(_BRAIN_STATS, encoding="utf-8")
    assert "Do not compare" not in _render(tmp_path)
    (tmp_path / "stats" / "region_stats_sample.csv").write_text(_BRAIN_STATS, encoding="utf-8")
    text = _render(tmp_path)
    assert "Do not compare" in text
    assert "**Output spaces present:** atlas, sample" in text


def test_spinal_guide_uses_spinal_layout(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "cord_orientation.txt",
        "cache/chan_1_sample_register_20um.tif",
        "transforms/bspline_atlas_to_samp_20um.txt",
        "volume_registered/chan01_channel1.tiff",
        "volume_registered/annotation_registered.tiff",
        "volume_registered/TA_atlas_coords.npz",
        "volume_registered/sample_space/segments_in_sample_20um.tif",
        "stats/region_stats_horn.csv",
        "plots/cord_heatmap_cell_density_TA.png",
        # A brain-only output must not be described in a spinal folder.
        "brain_orientation.txt",
    )
    text = _render(tmp_path, "spinal")
    assert "**workflow:** spinal cord".lower() in text.lower()
    for name in (
        "cord_orientation.txt",
        "cache/chan_N_sample_register_20um.tif",
        "transforms/bspline_atlas_to_samp_20um.txt",
        "volume_registered/chanNN_channelN.tiff",
        "volume_registered/annotation_registered.tiff",
        "volume_registered/{label}_atlas_coords.npz",
        "volume_registered/sample_space/segments_in_sample_20um.tif",
        "stats/region_stats_horn.csv",
        "plots/cord_heatmap_{metric}_{channel}.png",
    ):
        assert f"**`{name}`**" in text, name
    other = text.split("## Other files", 1)[1]
    assert "`brain_orientation.txt`" in other
    assert "`lightsuite spinal view -c <config>`" in text
    assert "view-registration" not in text


def test_guide_without_results_has_no_start_here(tmp_path: Path) -> None:
    _touch(tmp_path, "regopts.json")
    assert "## Start here" not in _render(tmp_path)


def test_malformed_csv_does_not_break_the_guide(tmp_path: Path) -> None:
    (tmp_path / "stats").mkdir()
    (tmp_path / "stats" / "region_stats.csv").write_bytes(b"\x00\xff not a csv")
    text = _render(tmp_path)
    assert "**`stats/region_stats.csv`**" in text


# ---------------------------------------------------------------------- writing to disk
def _write_brain_config(tmp_path: Path) -> Path:
    save_path = tmp_path / "results"
    save_path.mkdir()
    data = {
        "sample": {
            "name": "test_mouse",
            "source": {
                "format": "tiff_stack",
                "path": str(tmp_path),
                "tiff_type": "channelperfile",
            },
            "scratch": str(tmp_path / "scratch"),
            "save_path": str(save_path),
            "voxel_um": [5.0, 5.0, 5.0],
        },
        "atlas": {"provider": "allen", "resolution_um": 10, "atlas_dir": str(tmp_path)},
    }
    cfg_path = tmp_path / "brain.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return cfg_path


def test_write_results_info_writes_into_save_path(tmp_path: Path) -> None:
    from lightsuite.config.loader import load_config

    cfg_path = _write_brain_config(tmp_path)
    cfg = load_config(cfg_path)
    _touch(cfg.sample.save_path, "regopts.json")

    out = write_results_info(cfg, "brain", stage="preprocess", config_path=cfg_path)

    assert out == cfg.sample.save_path / RESULTS_INFO_FILENAME
    text = out.read_text(encoding="utf-8")
    assert "**Config:** `brain.yaml`" in text
    assert str(tmp_path) not in text  # no absolute paths
    assert "after stage `preprocess`" in text
    # The atomic write leaves no temporary file behind.
    assert {p.name for p in cfg.sample.save_path.iterdir()} == {
        "regopts.json",
        RESULTS_INFO_FILENAME,
    }
    # Rewriting does not list the guide itself.
    assert RESULTS_INFO_FILENAME not in write_results_info(cfg, "brain").read_text(
        encoding="utf-8"
    )


def test_write_results_info_does_not_create_missing_save_path(tmp_path: Path) -> None:
    from lightsuite.config.loader import load_config

    cfg = load_config(_write_brain_config(tmp_path))
    cfg.sample.save_path.rmdir()
    assert write_results_info(cfg, "brain") is None
    assert not cfg.sample.save_path.exists()


def test_refresh_results_info_never_raises(tmp_path: Path, monkeypatch) -> None:
    from lightsuite.config.loader import load_config

    cfg = load_config(_write_brain_config(tmp_path))

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(results_info, "build_results_info", _boom)
    messages: list[str] = []
    monkeypatch.setattr("lightsuite.reporter.emit_pipeline_message", messages.append)

    assert refresh_results_info(cfg, "brain", stage="export") is None
    assert any("disk full" in m for m in messages)
    assert not (cfg.sample.save_path / RESULTS_INFO_FILENAME).exists()


def test_run_stage_refreshes_guide_only_on_success(tmp_path: Path, monkeypatch) -> None:
    from lightsuite.config.loader import load_config

    cfg_path = _write_brain_config(tmp_path)
    cfg = load_config(cfg_path)
    guide = cfg.sample.save_path / RESULTS_INFO_FILENAME
    runners = get_workflow("brain").runners
    ctx = StageContext(config_path=cfg_path)

    def _fail(config, ctx):
        raise RuntimeError("stage failed")

    monkeypatch.setitem(runners, "register", _fail)
    with pytest.raises(RuntimeError):
        run_stage("brain", "register", cfg, ctx)
    assert not guide.exists()

    def _ok(config, ctx):
        _touch(config.sample.save_path, "transform_params.json")
        return "done"

    monkeypatch.setitem(runners, "register", _ok)
    assert run_stage("brain", "register", cfg, ctx) == "done"
    text = guide.read_text(encoding="utf-8")
    assert "after stage `register`" in text
    assert "**`transform_params.json`**" in text


def test_info_command(tmp_path: Path) -> None:
    cfg_path = _write_brain_config(tmp_path)
    result = CliRunner().invoke(app, ["brain", "info", "-c", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "results" / RESULTS_INFO_FILENAME).is_file()
