"""The discover stage (discover/001 D5): `tp target add <repo-url>[@ref]` is a run holding only
this stage. Output: the `target_profile` artifact (also written to data/targets/<slug>/profile.yaml
so `TP_TARGET=data/targets/<slug>` selects it) and the sealed, redacted `discovery_probes` log,
which a replay serves instead of Docker and the network (D2).

A step nothing could verify becomes a `setup` review item (at most MAX_SETUP per run); its answer
is a candidate like any other, accepted only when its probe passes (`source: clarify`). Reads that
answer anonymously become one `confirm_observed` item (D9), which never blocks discovery."""

import json
from typing import Annotated

import typer
import yaml

from tp.core import artifacts, contract, db, runner
from tp.core.contract import StageResult
from tp.stages.discover import discovery

MAX_SETUP = 5  # stage document: at most 5 setup questions


def target_dir(slug: str):
    return db.data_dir() / "targets" / slug


def files(slug: str, no_conventions: bool = False) -> tuple:
    """(profile, probe log) paths; a `--no-conventions` run keeps its own pair (D4)."""
    tag = ".no-conventions" if no_conventions else ""
    return target_dir(slug) / f"profile{tag}.yaml", target_dir(slug) / f"probes{tag}.json"


def split(target: str) -> tuple[str, str | None]:
    """`<url>@<ref>` -> (url, ref); a URL without `@<ref>` -> (url, None): the latest release."""
    url, sep, ref = target.rpartition("@")
    return (url, ref) if sep and "/" not in ref and ":" not in ref else (target, None)


def busy(slug: str) -> str | None:
    """A run on this target that may hold its stack (discover/006): discovery shares the compose
    project, clone and stored token with it, so starting would tear down that run's environment.
    One rule with the run lock (harden/002 request 2)."""
    return runner.busy(slug)


def asked_before(d) -> bool:
    """The same "answer without credentials" question for this target is already open or
    answered: don't ask it again (one per distinct operation list, discover/006)."""
    from tp.core import review

    ops = sorted(o["op"] for o in d.observed_open)
    return any(i["kind"] == "confirm_observed" and i["payload"].get("about") == "secured"
               and i["payload"].get("target") == d.slug
               and sorted(o["op"] for o in i["payload"].get("ops", [])) == ops
               and i["status"] in ("open", "resolved") for i in review.list_())  # fmt: skip


class Discover:
    name = "discover"
    version = "0.2.0"
    consumes = {"run_options": 1}  # noqa: RUF012
    produces = {"target_profile": 1, "discovery_probes": 1}  # noqa: RUF012
    budget_usd = 0.50  # discover/001 D5

    def run(self, ctx, inputs):
        opts = inputs["run_options"].body
        url, ref = split(opts["target"])
        nc = bool(opts.get("no_conventions"))
        asked = [i for i in (ctx.review.items() if ctx.review else []) if i["kind"] == "setup"]
        if pending := [i["id"] for i in asked if i["status"] == "open"]:
            return StageResult.awaiting_review(pending)
        if skipped := [i for i in asked if i["decision"] == "skip"]:
            return StageResult.failed(
                f"setup question skipped: {skipped[-1]['payload']['question']}"
            )
        # settled answers given with the run (a domain pack's `spec`), then a person's
        answers = opts.get("answers", {}) | {
            i["payload"]["key"]: i["detail"]["text"] for i in asked
        }
        kind = discovery.LiveDiscovery if opts.get("live") else discovery.Discovery
        d = kind(url, ref, ctx.llm, discovery.Probes(), not nc, ctx.emit, answers)
        profile_path, log_path = files(d.slug, nc)
        if ctx.llm.mode != "replay" and not opts.get("live") and (other := busy(d.slug)):
            return StageResult.failed(
                f"{other} is using {d.slug}'s stack; cancel it or let it finish, then discover"
            )
        replay = None
        if ctx.llm.mode == "replay":
            if not log_path.exists():
                return StageResult.failed(f"replay: no recorded probe log at {log_path}")
            recorded = json.loads(log_path.read_text())
            replay = recorded["probes"]
            d.probes = discovery.Probes(replay)
        try:
            prof = d.run()
        except (discovery.DiscoveryError, discovery.ReplayMiss) as e:
            discovery.log_failure(d.slug, getattr(e, "step", type(e).__name__), str(e))
            key = getattr(e, "key", None)
            if ctx.review and key and key not in answers and len(asked) < MAX_SETUP:
                item = ctx.review.create("setup", {"target": d.slug, "key": key, "step": e.step,
                                                   "question": e.question, "detail": str(e)})  # fmt: skip
                return StageResult.awaiting_review([item])
            return StageResult.failed(str(e))
        finally:
            if d.stack is not None and replay is None and not opts.get("keep_up"):
                d.stack.down()
        if d.observed_open and ctx.review and replay is None and not asked_before(d):
            n = len(d.observed_open)
            ctx.review.create("confirm_observed", {
                "about": "secured", "target": d.slug, "ops": d.observed_open,
                "title": f"{n} operation{'s' if n > 1 else ''} answer without credentials",
                "then": ", ".join(o["op"] for o in d.observed_open) + ": is that intended?",
                "evidence": "discovery probes (the spec declares no security)"})  # fmt: skip
        sealed = d.probes.sealed()
        if replay is not None and recorded.get("commit") not in (None, d.commit):
            return StageResult.failed(f"replay: the log was recorded at {recorded['commit']}, "
                                      f"the clone is at {d.commit}")  # fmt: skip
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(yaml.safe_dump(prof, sort_keys=False))
        if replay is None:
            log_path.write_text(json.dumps({"commit": d.commit, "probes": sealed}, indent=1))
        return StageResult.succeeded(
            {"target_profile": prof, "discovery_probes": {"probes": sealed}}
        )


cli = typer.Typer(no_args_is_help=True)


@cli.command("add")
def add(
    target: Annotated[str, typer.Argument(help="Repo URL, optionally @<tag>")],
    no_conventions: Annotated[bool, typer.Option(help="Exploration only (D12)")] = False,
    replay: Annotated[
        bool, typer.Option(help="Probes from the recorded log, LLM from cache")
    ] = False,
    keep_up: Annotated[bool, typer.Option(help="Leave the discovered stack running")] = False,
    live: Annotated[
        bool, typer.Option(help="A running system's URL: read-only discovery (external/001)")
    ] = False,
    secret: Annotated[
        list[str] | None, typer.Option(help="name=value, stored before discovery (e.g. token=…)")
    ] = None,
):
    """Discover a target from its repo URL, or (`--live`) a running system's URL: a run holding
    only the discover stage."""
    from tp.core import secrets

    for pair in secret or []:
        name, sep, value = pair.partition("=")
        if not (sep and name and value):
            raise typer.BadParameter(f"--secret expects name=value, got {name!r}")
        slug = (
            discovery.live_slug(target) if live else discovery.Discovery.slug_of(split(target)[0])
        )
        secrets.put(slug, name, value)
    opts = {"target": target} | ({"no_conventions": True} if no_conventions else {}) \
        | ({"keep_up": True} if keep_up else {}) | ({"live": True} if live else {})  # fmt: skip
    run_id = runner.start_run("replay" if replay else "live", options=opts, pipeline=["discover"])
    report(runner.advance(run_id), no_conventions)


def report(run: dict, no_conventions: bool = False) -> None:
    from tp.core import review

    ex = run["executions"][-1]
    typer.echo(f"{run['name']}  {run['status']}" + (f"  ({ex['reason']})" if ex["reason"] else ""))
    for item in review.list_(status="open", execution_id=ex["id"]):
        p = item["payload"]
        if item["kind"] == "setup":
            typer.echo(f"  setup {item['id']}: {p['question']}\n"
                       f"    tp review resolve {item['id']} answer --text '...'  (or: skip)")  # fmt: skip
        else:
            typer.echo(f"  review {item['id']} ({item['kind']}): {p.get('title')}: {p.get('then')}")
    if "target_profile" in ex["outputs"]:
        body = artifacts.get(ex["outputs"]["target_profile"]).body
        typer.echo(f"profile: {files(body['target']['name'], no_conventions)[0]}")
    raise typer.Exit(0 if run["status"] in ("succeeded", "awaiting_review") else 1)


@cli.command("show")
def show(slug: str, no_conventions: bool = False):
    """Print a discovered profile: each concern's source, layer and evidence."""
    p = yaml.safe_load(files(slug, no_conventions)[0].read_text())
    typer.echo(f"{p['target']['repo']} @ {p['commit_tested']}")
    for c in ("reach", "spec", "auth", "data", "state", "mutation", "safety", "checks"):
        f = p[c]
        typer.echo(f"  {c:<9} {f['source']:<21} {f['layer'] or '':<12} {f['evidence'] or ''}")
        for k, pv in (f.get("provenance") or {}).items():
            typer.echo(f"    {k:<20} {pv['source']:<21} {pv['layer']:<12} "
                       f"{'' if pv['probe'] is None else 'probe#' + str(pv['probe'])}")  # fmt: skip


@cli.command("secret")
def secret(slug: str, pair: Annotated[str, typer.Argument(help="name=value, e.g. token=…")]):
    """Store a supplied secret for a target (master §10), e.g. an external system's token."""
    from tp.core import secrets

    name, sep, value = pair.partition("=")
    if not (sep and name and value):
        raise typer.BadParameter("expected name=value")
    secrets.put(slug, name, value)
    typer.echo(f"stored {name} for {slug}")


@cli.command("clean")
def clean(target: Annotated[str, typer.Argument(help="Slug or folder of an external target")]):
    """Delete what earlier runs left on an external target (its ledger), and list the rest."""
    from tp.core import profile
    from tp.stages.env import blackbox

    with profile.selected(target):
        p = profile.load()
    if p.reach.strategy != "external":
        raise typer.BadParameter(
            f"{p.slug} has {p.reach.strategy} reach; only external keeps a ledger"
        )
    stack = blackbox.ExternalStack(p)
    out = stack.cleanup(stack.ledger)
    typer.echo(" · ".join(f"{k} {len(v)}" for k, v in out.items()))
    raise typer.Exit(1 if out["failed"] or out["unknown_delete_route"] else 0)


contract.register(Discover(), cli=cli, cli_name="target")
