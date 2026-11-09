"""`tp pack run <pack> --targets a,b` (P4, docs/status/domains/001): onboard each implementation of
a domain pack from its repo URL, run the full pipeline on the first with the pack's brief and
answers, then the same sealed tests, by hash, on the rest (DR-5), and print the comparison."""

import hashlib
import re
from pathlib import Path
from typing import Annotated

import typer
import yaml

from tp.core import artifacts, review, runner, transfer

PACKS = Path(__file__).parents[2] / "packs"
KEYS = {"spec": dict, "brief": str, "answers": list, "suites": list, "implementations": list}  # C9
COMMIT = re.compile(r"^[0-9a-f]{40}$")

app = typer.Typer(no_args_is_help=True, help="Domain packs: one spec, several implementations")


def load(pack: str) -> tuple[dict, Path]:
    """A pack by name (packs/<name>/) or folder."""
    d = Path(pack) if "/" in pack else PACKS / pack
    return yaml.safe_load((d / "pack.yaml").read_text()), d


def validate(raw: dict, d: Path) -> list[str]:
    """Problems against C9 (D11); [] when the pack is sound."""
    out = [
        f"{k}: expected a {t.__name__}" for k, t in KEYS.items() if not isinstance(raw.get(k), t)
    ]
    if out:
        return out
    files = [("spec", raw["spec"], ("path", "sha", "source"))]
    files += [(f"suites[{i}]", s, ("format", "path", "sha", "source", "licence"))
              for i, s in enumerate(raw["suites"])]  # fmt: skip
    for where, f, keys in files:
        if missing := [k for k in keys if not isinstance(f.get(k), str)]:
            out.append(f"{where}: missing {', '.join(missing)}")
        elif not (d / f["path"]).is_file():
            out.append(f"{where}: no file {f['path']}")
        elif hashlib.sha256((d / f["path"]).read_bytes()).hexdigest() != f["sha"]:
            out.append(f"{where}: {f['path']} doesn't match its sha256")
    out += [f"answers[{i}]: expected a string" for i, a in enumerate(raw["answers"])
            if not isinstance(a, str)]  # fmt: skip
    if not raw["implementations"]:
        out.append("implementations: none")
    for i, impl in enumerate(raw["implementations"]):
        if not isinstance(impl, dict) or not isinstance(impl.get("repo"), str):
            out.append(f"implementations[{i}]: expected {{repo, ref}}")
        elif not COMMIT.match(str(impl.get("ref", ""))):
            out.append(f"implementations[{i}]: ref must be a 40-hex commit (SR3)")
    return out


def slug(impl: dict) -> str:
    from tp.stages.discover.discovery import Discovery

    return Discovery.slug_of(impl["repo"])


def select(impls: list[dict], targets: str | None) -> list[dict]:
    """`--targets a,b`: each name is an implementation's slug, or a unique part of one (D6)."""
    if not targets:
        return impls
    out = []
    for name in (t.strip() for t in targets.split(",") if t.strip()):
        hits = [i for i in impls if slug(i) == name] or [i for i in impls if name in slug(i)]
        if len(hits) != 1:
            raise typer.BadParameter(f"{name!r} matches {len(hits)} of: "
                                     + ", ".join(slug(i) for i in impls), param_hint="--targets")  # fmt: skip
        out.append(hits[0])
    return out


def brief(raw: dict) -> str:
    """The brief with the pack's settled answers (D7)."""
    answers = "".join(f"\n- {a}" for a in raw["answers"])
    return raw["brief"] + (f"\n\nSettled answers (domain pack):{answers}" if answers else "")


def onboard(impl: dict, pack: str) -> str | None:
    """The implementation's discovered profile folder, discovering it at the pinned ref with the
    pack's spec (one spec judges every implementation, DR-5) unless a profile at that commit with
    that spec exists (D8). None when discovery didn't finish."""
    from tp.stages.discover.stage import files

    profile = files(slug(impl))[0]
    if profile.exists():
        p = yaml.safe_load(profile.read_text())
        if p.get("commit_tested") == impl["ref"] and p["spec"]["value"].get("strategy") == "pack_file" \
                and p["spec"]["value"]["openapi"].startswith(f"{pack}/"):  # fmt: skip
            return str(profile.parent)
    opts = {"target": f"{impl['repo']}@{impl['ref']}", "answers": {"spec": f"pack:{pack}"}}
    run = runner.advance(runner.start_run("live", options=opts, pipeline=["discover"]))
    typer.echo(f"onboard {slug(impl)}: {run['name']} {run['status']}")
    return str(profile.parent) if run["status"] == "succeeded" else None


@app.command("run")
def run(
    pack: Annotated[str, typer.Argument(help="Pack name (packs/<name>) or folder")],
    targets: Annotated[str | None, typer.Option(help="Implementations, first = reference")] = None,
    first: Annotated[
        str | None, typer.Option(help="A finished run on the first: only transfer and compare")
    ] = None,
):
    """Run a domain pack: full pipeline on the first implementation, sealed tests on the rest."""
    raw, d = load(pack)
    if problems := validate(raw, d):
        typer.echo("\n".join(f"pack {d.name}: {p}" for p in problems), err=True)
        raise typer.Exit(1)
    impls = select(raw["implementations"], targets)
    paths = [onboard(i, d.name) for i in impls]
    if None in paths:
        typer.echo("resolve the discovery reviews (tp review list), then run this again", err=True)
        raise typer.Exit(1)
    if first:
        run_a = int(first.removeprefix("r-"))
    else:
        req = artifacts.put("requirements", {"kind": "brief", "text": brief(raw)}).id
        run_a = runner.start_run("live", inputs={"requirements": req},
                                 options={"profile": paths[0], "stops": review.FOCUSED_STOPS})  # fmt: skip
        a = runner.advance(run_a)
        typer.echo(f"{slug(impls[0])}: {a['name']} {a['status']}")
        if a["status"] != "succeeded":
            typer.echo(f"resolve its reviews (tp review list), then: tp pack run {pack} "
                       f"--targets {','.join(slug(i) for i in impls)} --first {a['name']}")  # fmt: skip
            raise typer.Exit(1 if a["status"] == "failed" else 0)
    runs = [run_a]
    for impl, path in zip(impls[1:], paths[1:], strict=True):
        b = runner.advance(transfer.start(run_a, path))
        typer.echo(f"{slug(impl)}: {b['name']} {b['status']} (sealed tests of r-{run_a:04d})")
        runs.append(b["id"])
    try:
        typer.echo(transfer.markdown(transfer.matrix(runs)))
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
