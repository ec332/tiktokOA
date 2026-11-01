"""`tp generate image`: build the platform_rt wheel + runner image and publish its digest."""

import json

import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def image():
    """Build the runner image on the current patch set and write data/published.json."""
    from tp.core import contract
    from tp.stages.generate import image as img

    contract.load()
    try:
        runner = img.build(contract.SERVICES["env"])
    except img.ImageError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(runner, indent=2))


@app.command()
def show(unit: str, run: int | None = None):
    """Print one bound test file from the latest bound_tests artifact (or a run's)."""
    from tp.core import artifacts, runner

    if run is None:
        run = runner.list_runs(1)[0]["id"]
    art = artifacts.get(runner.heads(run)["bound_tests"])
    t = next(t for t in art.body["tests"] if t["unit"] == unit)
    typer.echo(t.get("source") or f"{t['status']}: {t.get('reason')}")
