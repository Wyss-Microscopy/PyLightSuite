"""Merged convert → import segmentation stage for GUI / pipeline checklists.

CLI still exposes ``convert-annotations`` and ``import-annotations`` separately.
This helper runs convert (always when configured), then import when registration
transforms exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from lightsuite.import_.convert import ConvertAnnotationsResult, run_convert_annotations_for_pipeline

console = Console()


@dataclass
class ImportSegmentationResult:
    """Outcome of the merged Import segmentation stage."""

    convert: ConvertAnnotationsResult
    imported: Any | None
    skipped_import_reason: str | None = None


def _brain_or_spinal_can_import(save_path: Path) -> bool:
    return (save_path.expanduser() / "transform_params.json").is_file()


def run_import_segmentation_brain(config: Any) -> ImportSegmentationResult:
    """Convert/validate, then warp to atlas when ``transform_params.json`` exists."""
    convert = run_convert_annotations_for_pipeline(config)
    save_path = Path(config.sample.save_path).expanduser()
    if not _brain_or_spinal_can_import(save_path):
        reason = (
            "Converted/validated Sample Space layers under converted/. "
            "Run register first, then re-run Import segmentation to warp to atlas."
        )
        console.print(f"[yellow]{reason}[/yellow]")
        return ImportSegmentationResult(convert=convert, imported=None, skipped_import_reason=reason)

    from lightsuite.import_.brain_import import run_brain_import_annotations

    imported = run_brain_import_annotations(config, annotations=convert.annotations)
    return ImportSegmentationResult(convert=convert, imported=imported)


def run_import_segmentation_spinal(config: Any) -> ImportSegmentationResult:
    """Convert/validate, then warp to Fiederling atlas when registration exists."""
    convert = run_convert_annotations_for_pipeline(config)
    save_path = Path(config.sample.save_path).expanduser()
    if not _brain_or_spinal_can_import(save_path):
        reason = (
            "Converted/validated Sample Space layers under converted/. "
            "Run register first, then re-run Import segmentation to warp to atlas."
        )
        console.print(f"[yellow]{reason}[/yellow]")
        return ImportSegmentationResult(convert=convert, imported=None, skipped_import_reason=reason)

    from lightsuite.import_.cord_import import run_cord_import_annotations

    imported = run_cord_import_annotations(config, annotations=convert.annotations)
    return ImportSegmentationResult(convert=convert, imported=imported)

