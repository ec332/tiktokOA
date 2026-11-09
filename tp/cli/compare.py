"""`tp compare <run>…` (P4, docs/status/domains/001 D9): the case × implementation matrix of runs
that ran the same sealed tests, with the witness rule (DR-6) applied to every row."""

from typing import Annotated

import typer

from tp.core import transfer


def compare(runs: Annotated[list[str], typer.Argument(help="Runs, e.g. r-0012 r-0013")]):
    """Print the case × implementation matrix in markdown, each disagreement triaged."""
    try:
        m = transfer.matrix([int(r.removeprefix("r-")) for r in runs])
    except (ValueError, KeyError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(transfer.markdown(m))
