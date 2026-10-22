"""Workflow decision tree for the CLI."""

from __future__ import annotations

import typer

WORKFLOW_GUIDE = """
LightSuite Python workflows
===========================

brain
  Whole-brain lightsheet → Allen or Perens atlas registration.
  Template: examples/brain_lightsheet.yaml
  Run:      lightsuite brain run -c my.yaml --through match-points

spinal
  Spinal cord lightsheet → Fiederling atlas + region stats.
  Template: examples/spinal_cord.yaml
  Run:      lightsuite spinal run -c my.yaml

Use the original MATLAB LightSuite (github.com/dimokaramanlis/LightSuite) for
  • Widefield coronal slices
  • Built-in cell detection
  • CZI native reader
  • Spinal cohort NNMF analysis
"""


def print_workflow_list() -> None:
    typer.echo(WORKFLOW_GUIDE.strip())


workflow_app = typer.Typer(help="Choose a LightSuite workflow.")


@workflow_app.command("list")
def workflow_list() -> None:
    """Print which workflow to use (brain vs spinal vs MATLAB)."""
    print_workflow_list()
