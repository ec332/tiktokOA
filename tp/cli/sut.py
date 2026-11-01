"""`tp sut ...`: apply, verify and reset the SUT patches."""

import typer

from tp.core import sut

app = typer.Typer(no_args_is_help=True)


def _do(fn) -> None:
    try:
        cfg = sut.load()
        results = fn(cfg)
    except sut.SutError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{cfg['name']} @ {cfg['ref'][:7]}  ({sut.checkout(cfg)})")
    for name, status in results:
        typer.echo(f"  {name:<12} {status}")


@app.command()
def verify():
    """Apply every patch in sut.yaml, in order, to the SUT tree (skips ones already applied)."""
    _do(sut.verify)


@app.command()
def reset():
    """Reverse the applied patches, leaving the SUT tree at the pinned ref."""
    _do(sut.reset)
