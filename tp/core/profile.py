"""Target profiles (iteration 2, seams/001 D1-D3).

A target is data: `targets/<slug>/profile.yaml` (hand-written, `source: override`) or, from stage D,
a discovered profile artifact. Every concern is a field `{value: {strategy, ...}, source, evidence}`.

`legacy()` is the reverse shim: it rebuilds the iteration 1 `sut.yaml` dict from a profile, so call
sites not yet moved onto `ctx.strategies` keep working. It is deleted when the allow-list test
(tests/test_target_literals.py) reaches zero.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TARGETS = "targets"
CONCERNS = ("reach", "spec", "auth", "data", "state", "mutation", "safety", "checks")
SOURCES = {"probed", "repo", "ai_proposed+verified", "clarify", "override", "resolver"}


class ProfileError(Exception):
    pass


@dataclass(frozen=True)
class Field:
    value: dict
    source: str
    evidence: str | None = None
    layer: str | None = None  # convention | algorithm | exploration | clarify (stage D)
    provenance: dict | None = None  # {sub-key: {source, layer, probe}} (discover/001 D7)

    @property
    def strategy(self) -> str:
        return self.value["strategy"]


@dataclass(frozen=True)
class TargetProfile:
    slug: str
    name: str
    repo: str
    type: str
    commit_tested: str
    fields: dict[str, Field] = field(default_factory=dict)
    dir: Path | None = None  # targets/<slug>/ for hand-written inputs (patches, overrides)

    def __getattr__(self, concern: str) -> Field:
        if concern in CONCERNS:
            return self.fields[concern]
        raise AttributeError(concern)


def active_slug(root: Path = Path("."), t: str | None = None) -> str:
    """TP_TARGET if set, else the only folder under targets/. A TP_TARGET holding a `/` is a
    path to a target folder (blackbox D13), e.g. a test fixture: its slug is the folder name."""
    if slug := os.environ.get("TP_TARGET") if t is None else t:
        return Path(slug).name if "/" in slug else slug
    found = sorted(p.name for p in (root / TARGETS).glob("*/profile.yaml") for p in [p.parent])
    if len(found) != 1:
        raise ProfileError(f"{len(found)} targets under {root / TARGETS}: set TP_TARGET")
    return found[0]


def _discovered(slug: str) -> Path:
    """Where stage D writes a discovered profile: <data>/targets/<slug>/ (discover/001 D5)."""
    from tp.core import db

    return db.data_dir() / TARGETS / slug


def target_dir(root: Path = Path("."), t: str | None = None) -> Path:
    """TP_TARGET as a path, else targets/<slug>/ (hand-written), else the discovered profile of
    that slug (discover/006: `TP_TARGET=<slug>` finds data/targets/<slug>). `t` instead of
    TP_TARGET: for threads beside a run, which must not swap the process's env (integration/002)."""
    if "/" in (t := os.environ.get("TP_TARGET", "") if t is None else t):
        return root / t
    slug = active_slug(root, t or None)
    hand = root / TARGETS / slug
    if not (hand / "profile.yaml").exists() and (_discovered(slug) / "profile.yaml").exists():
        return _discovered(slug)
    return hand


@contextmanager
def selected(path: str | None):
    """Run a block against one target (a run's `run_options.profile`, discover/006). TP_TARGET
    is set, not a context variable, because stages start subprocesses that read it; runs are
    executed one at a time per process, so the swap can't race."""
    if not path:
        yield
        return
    old = os.environ.get("TP_TARGET")
    os.environ["TP_TARGET"] = path
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TP_TARGET", None)
        else:
            os.environ["TP_TARGET"] = old


def choices(root: Path = Path(".")) -> list[dict]:
    """Every selectable target: hand-written (targets/<slug>/) and discovered (<data>/targets/).
    `path` is absolute: what a run stores and TP_TARGET accepts."""
    out = []
    for kind, base in (("hand_written", root / TARGETS), ("discovered", _discovered("x").parent)):
        for f in sorted(base.glob("*/profile.yaml")) if base.exists() else []:
            raw = yaml.safe_load(f.read_text()) or {}
            out.append({"slug": f.parent.name, "kind": kind, "path": str(f.parent.resolve()),
                        "repo": (raw.get("target") or {}).get("repo"),
                        "commit_tested": raw.get("commit_tested"),
                        "black_box": (raw.get("mutation") or {}).get("value", {})
                        .get("strategy") != "in_process"})  # fmt: skip
    return out


def source_dir(root: Path = Path(".")) -> str:
    """Repo-relative folder holding the app's code (`spec.value.source_dir`)."""
    return load(root).spec.value["source_dir"].rstrip("/")


def image_source_prefix(root: Path = Path(".")) -> str:
    """Where `source_dir` sits inside the target image: traceback frames under it are app code."""
    m = load(root).mutation.value
    return m["image_root"] + source_dir(root).removeprefix(m["repo_prefix"]) + "/"


def seeded_bytes(root: Path = Path(".")) -> bytes:
    """The raw planted-bug targets file named by `mutation.value.seeded` (b"" if none)."""
    name = load(root).mutation.value.get("seeded")
    return (target_dir(root) / name).read_bytes() if name else b""


def timing_flags(root: Path = Path(".")) -> dict[str, str]:
    """Planted bugs marked `timing: true` in the seeded file -> their not-covered note."""
    seeded = yaml.safe_load(seeded_bytes(root)) or {}
    return {f: v.get("not_covered", "") for f, v in seeded.items() if v.get("timing")}


def parse(raw: dict, slug: str, where: Path | None = None) -> TargetProfile:
    t = raw.get("target") or {}
    if missing := [k for k in ("name", "repo") if k not in t] + (
        [] if "commit_tested" in raw else ["commit_tested"]
    ):
        raise ProfileError(f"profile {slug} is missing {missing}")
    fields = {}
    for c in CONCERNS:
        f = raw.get(c)
        if f is None:
            raise ProfileError(f"profile {slug} is missing {c}")
        if f.get("source") not in SOURCES or "strategy" not in (f.get("value") or {}):
            raise ProfileError(
                f"profile {slug}: {c} needs value.strategy and a source in {SOURCES}"
            )
        fields[c] = Field(
            f["value"], f["source"], f.get("evidence"), f.get("layer"), f.get("provenance")
        )
    return TargetProfile(slug, t["name"], t["repo"], t.get("type", "api"), raw["commit_tested"],
                         fields, where)  # fmt: skip


def load(root: Path = Path("."), t: str | None = None) -> TargetProfile:
    d = target_dir(root, t)
    path = d / "profile.yaml"
    if not path.exists():
        raise ProfileError(f"no profile at {path}")
    return parse(yaml.safe_load(path.read_text()), d.name, d)


def legacy(p: TargetProfile) -> dict:
    """The iteration 1 sut.yaml dict (reverse shim; see the module docstring)."""
    reach, spec, state, mut = p.reach.value, p.spec.value, p.state.value, p.mutation.value
    return {
        "name": p.name,
        "repo": p.repo,
        "ref": p.commit_tested,
        "type": p.type,
        "patches": list(mut.get("patches", [])),
        "flags": list(mut.get("flags", [])),
        "code_scope": dict(mut.get("code_scope", {})),
        "surfaces": {"api": {"openapi": spec.get("openapi"), "base": spec.get("base", "")}},
        "compose": dict(c) if isinstance(c := reach.get("compose"), dict) else {},
        "env": dict(reach.get("env", {})),
        "lifecycle": {
            "migrate": reach.get("migrate"),
            "health": reach.get("health"),
            "testinfo": reach.get("testinfo"),
            "db_probe": dict(state.get("db_probe", {})),
            "migrations": state.get("migrations", ""),
        },
        "auth": {k: v for k, v in p.auth.value.items() if k != "strategy"},
    }
