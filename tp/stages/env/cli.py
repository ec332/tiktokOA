"""`tp env up|probe|reset|down <variant>`: works with no other stage (plan 3.2)."""

import json
import statistics
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True)
Variant = Annotated[str, typer.Argument(help="reference, or bug:FLAG[+FLAG]")]


def _call(fn):
    from tp.stages.env.service import EnvError

    try:
        return fn()
    except EnvError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)


def _probes(probes: list[dict]) -> None:
    for p in probes:
        typer.echo(f"  {'ok ' if p['ok'] else 'BAD'} {p['name']:<7} {p['raw'][:110]}")


@app.command()
def up(
    variant: Variant = "reference",
    expect: Annotated[str | None, typer.Option(help="Fail unless spec_hash matches")] = None,
):
    """Fresh bring-up of a variant: reference, or bug:FLAG[+FLAG]."""
    from tp.stages.env.service import Stack

    spec = _call(lambda: Stack(variant).up(expect))
    typer.echo(f"{spec['project']}  {spec['base_url']}  spec_hash={spec['spec_hash']}")
    _probes(spec["probes"])
    typer.echo(f"  lock: {spec['lock_file']}\n  reproduce: {spec['reproduce']}")


@app.command()
def probe(variant: Variant = "reference"):
    """Run every probe against a running variant."""
    from tp.stages.env.service import Stack

    probes = _call(lambda: Stack(variant).probe())
    _probes(probes)
    raise typer.Exit(0 if all(p["ok"] for p in probes) else 1)


@app.command()
def reset(
    variant: Variant = "reference",
    repeat: Annotated[int, typer.Option(help="Reset this many times and report timings")] = 1,
):
    """Recreate the app database from the seed template."""
    from tp.stages.env.service import Stack

    stack = _call(lambda: Stack(variant))
    times = [_call(stack.reset) for _ in range(repeat)]
    ms = sorted(round(t * 1000) for t in times)
    typer.echo(json.dumps({"resets": repeat, "median_ms": statistics.median(ms), "max_ms": ms[-1]}))


@app.command()
def down(variant: Variant = "reference"):
    """Tear the variant down, volumes included."""
    from tp.stages.env.service import Stack

    _call(lambda: Stack(variant).down())
    typer.echo(f"down: {variant}")
