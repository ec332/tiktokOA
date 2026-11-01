"""`tp` CLI: the same core functions the API calls, run synchronously."""

import json
from pathlib import Path
from typing import Annotated

import typer

from tp.cli import sut as sut_cli
from tp.core import artifacts, review, runner

app = typer.Typer(no_args_is_help=True)
stage_app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
artifact_app = typer.Typer(no_args_is_help=True)
app.add_typer(stage_app, name="stage")
app.add_typer(review_app, name="review")
app.add_typer(artifact_app, name="artifact")
app.add_typer(sut_cli.app, name="sut")


def _run_id(ref: str) -> int:
    return int(ref.removeprefix("r-"))


def _mode(mode: str, replay: bool) -> str:
    return "replay" if replay else mode


def _print_run(run: dict) -> None:
    typer.echo(f"{run['name']}  {run['status']}  mode={run['mode']}")
    for e in run["executions"]:
        outs = " ".join(f"{t}={i[:19]}" for t, i in e["outputs"].items())
        extra = f"  ({e['reason']})" if e["reason"] else ""
        typer.echo(f"  #{e['id']:<4} {e['stage']:<9} {e['status']:<16} {outs}{extra}")
    for item in review.list_(status="open"):
        if any(item["stage_execution"] == e["id"] for e in run["executions"]):
            hint = (
                "--all-lines approve | --line ID=reject:comment ... | defer"
                if item["line_options"]
                else "<" + "|".join(item["options"]) + ">"
            )
            typer.echo(
                f"  review {item['id']} ({item['kind']}) open: "
                f"tp review resolve {item['id']} {hint}"
            )


@app.command()
def run(
    stub: Annotated[bool, typer.Option(help="Use stubs for every stage that has one")] = False,
    replay: Annotated[bool, typer.Option(help="LLM calls from llm_cache/ only")] = False,
    mode: Annotated[str, typer.Option(help="live | record | replay")] = "live",
    unattended: Annotated[bool, typer.Option(help="Apply default review decisions")] = False,
):
    """Start a pipeline run and drive it until it finishes or needs a review."""
    run_id = runner.start_run(_mode(mode, replay), stub, unattended)
    result = runner.advance(run_id)
    _print_run(result)
    raise typer.Exit(1 if result["status"] == "failed" else 0)


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    """type=<artifact id> or type=<path to a JSON body> (stored as pre-generated)."""
    out = {}
    for pair in pairs:
        t, _, ref = pair.partition("=")
        if not ref:
            raise typer.BadParameter(f"expected type=ref, got {pair!r}")
        if ref.startswith("sha256:"):
            out[t] = ref
        else:
            body = json.loads(Path(ref).read_text())
            out[t] = artifacts.put(t, body, labels=["pre-generated"]).id
    return out


@stage_app.command("run")
def stage_run(
    name: str,
    input: Annotated[
        list[str] | None, typer.Option("--input", help="type=artifact-id|file.json")
    ] = None,
    from_run: Annotated[str | None, typer.Option(help="Reuse this run's inputs")] = None,
    run_ref: Annotated[str | None, typer.Option("--run", help="Re-run inside this run")] = None,
    stub: bool = False,
    replay: bool = False,
    mode: str = "live",
):
    """Run one stage against saved, hand-written or another run's inputs."""
    if name not in runner.PIPELINE:
        raise typer.BadParameter(f"unknown stage {name!r}")
    run_id = _run_id(run_ref) if run_ref else runner.start_run(_mode(mode, replay), stub)
    ex = runner.rerun(
        run_id, name, _parse_inputs(input or []), _run_id(from_run) if from_run else None
    )
    _print_run(runner.get_run(run_id))
    raise typer.Exit(0 if ex["status"] in ("succeeded", "awaiting_review") else 1)


@review_app.command("list")
def review_list(status: str = "open"):
    for item in review.list_(status=None if status == "all" else status):
        typer.echo(
            f"{item['id']:<4} {item['kind']:<18} {item['status']:<9} "
            f"exec #{item['stage_execution']}  options={','.join(item['options'])}  "
            f"{json.dumps(item['payload'])}"
        )


def _line(spec: str) -> dict:
    decision, _, comment = spec.partition(":")
    return {"decision": decision, "comment": comment}


@review_app.command("resolve")
def review_resolve(
    item_id: int,
    decision: Annotated[str | None, typer.Argument(help="Omit for line-level items")] = None,
    line: Annotated[
        list[str] | None, typer.Option("--line", help="ID=approve or ID=reject:comment")
    ] = None,
    all_lines: Annotated[
        str | None, typer.Option(help="Same decision for every line: approve | reject:comment")
    ] = None,
    choice: Annotated[str | None, typer.Option(help="Choice id for a question's pick")] = None,
    by: str = "cli",
    reason: str = "",
):
    """Record a decision and resume the waiting run."""
    try:
        item = review.get(item_id)
        detail: dict = {}
        if all_lines:
            detail["lines"] = {
                ln["id"]: _line(all_lines) for ln in item["payload"].get("lines", [])
            }
        if line:
            lines = dict(pair.partition("=")[::2] for pair in line)
            detail["lines"] = detail.get("lines", {}) | {k: _line(v) for k, v in lines.items()}
        if choice:
            detail["choice"] = choice
        review.resolve(item_id, decision, by, reason, detail)
    except (KeyError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    result = runner.resume(item_id)
    if result:
        _print_run(result)


@artifact_app.command("show")
def artifact_show(art_id: str):
    art = artifacts.get(art_id)
    typer.echo(json.dumps({"manifest": art.manifest, "body": art.body}, indent=2))


@app.command()
def openapi():
    """Print the platform's OpenAPI schema (input for the web client generator)."""
    from tp.api.app import app as api

    typer.echo(json.dumps(api.openapi(), indent=2))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn

    uvicorn.run("tp.api.app:app", host=host, port=port)


@app.command("check-folders")
def check_folders(
    commits: Annotated[str | None, typer.Option(help="git range, e.g. main..HEAD")] = None,
):
    """Fail if a folder exists before its milestone, or a stage commit touches another's folder."""
    from tp.core import folders

    problems = folders.check(Path("."), commits)
    for p in problems:
        typer.echo(p, err=True)
    raise typer.Exit(1 if problems else 0)
