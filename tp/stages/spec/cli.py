"""`tp spec index`: the code index on its own (P1), no LLM."""

import json

import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def index(as_json: bool = typer.Option(False, "--json")):
    """Every route in the SUT at its pinned ref, with handler and file:line."""
    from tp.stages.spec.stage import load_index

    idx, _ = load_index()
    if as_json:
        typer.echo(json.dumps(idx, indent=2))
        return
    for r in idx["routes"]:
        cond = f"  [if {r['mounted_if']}]" if r["mounted_if"] else ""
        guard = "  [superuser]" if r["dependencies"] and "superuser" in r["dependencies"] else ""
        typer.echo(
            f"{r['method']:<6} {r['path']:<40} {r['file']}:{r['line']}  {r['handler']}{guard}{cond}"
        )
    typer.echo(f"{len(idx['routes'])} routes in {len(idx['files'])} files")
    if idx["unmounted"]:
        typer.echo(f"routers never mounted: {idx['unmounted']}")
