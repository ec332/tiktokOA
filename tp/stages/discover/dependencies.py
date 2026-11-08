"""Outbound dependencies (C6 hook; discover D11, then P6: docs/status/standins/001).

1. `needs_dependency` (D11): an op that answered 5xx while the log names an unsupplied env var.
2. Stand-ins (P6): env names the code reads, request-body fields that register a receiver, and
   guard knobs, matched by `config/dependencies.yaml`; the model proposes names only when the
   code sends mail or webhooks and no convention matched (D5). Each is replaced by a SIMULATED
   stand-in, and counts as wired only when a triggered call leaves a line in its journal (D4).
   Writes `reach.value.standins` and `d.dependencies` (C5; the resolver keeps it, request R1)."""

import re
import subprocess
import time
from pathlib import Path

import httpx
import yaml

from tp.stages.discover import ai
from tp.stages.env import blackbox as bb

CONFIG = Path(__file__).parents[3] / "config" / "dependencies.yaml"
LITERAL = re.compile(r"""["']([A-Z][A-Z0-9]*_[A-Z0-9_]{2,})["']""")
SMTP_PORT = re.compile(r"^(SMTP|EMAIL|MAIL)_PORT$")
PARAM = re.compile(r"\{[^}]+\}")
WIRING_SCHEMA = {
    "type": "object",
    "properties": {"candidates": {"type": "array", "items": {
        "type": "object",
        "properties": {"env": {"type": "string"}, "kind": {"enum": ["http", "smtp"]},
                       "quote": {"type": "string"}},
        "required": ["env", "kind", "quote"], "additionalProperties": False}}},
    "required": ["candidates"],
    "additionalProperties": False,
}  # fmt: skip
# The wiring prompt (D5): asked once, and only its verified answers count.
WIRING_PROMPT = (
    "This code sends email or calls webhooks. For testing, each outbound destination will be "
    "replaced by a local stand-in, set through an environment variable the server reads at "
    "start-up. Which environment variables set the destination (an SMTP host, or the URL a "
    "webhook or callback is sent to)? Give the full variable name the process reads, including "
    "any prefix the configuration library adds, and quote the line that shows it. Return no "
    "candidates if the code doesn't show one.\n\n{snippets}"
)


def _disc():
    from tp.stages.discover import discovery  # it imports this module: the spec helpers, lazily

    return discovery


def config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _code(src: Path):
    """(path, text) of each tracked source file: no docs, nothing over 1 MB."""
    r = subprocess.run(["git", "ls-files"], cwd=src, capture_output=True, text=True,
                       check=False)  # fmt: skip
    for f in r.stdout.splitlines():
        p = src / f
        if Path(f).suffix in ai.DOC_SUFFIXES or not p.is_file() or p.stat().st_size > 1_000_000:
            continue
        yield f, p.read_text(errors="replace")


def run(d) -> None:
    needs_dependency(d)
    d.dependencies = []
    if getattr(d, "src", None) is not None:
        standins(d)


def needs_dependency(d) -> None:
    """`needs_dependency` (D11): an operation that answered 5xx during discovery while the
    container log names an env var the code reads (a string literal in tracked source) and
    nothing supplies. Written to `checks.families.exclude`; otherwise the op stays in."""
    d.exclude = []
    if not d.errors_5xx:
        return
    _, log = d.probes.call("docker", "logs", lambda: d.stack.logs()[-50_000:])
    names = set(re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b", log or ""))
    supplied = set(d.app_env) | set(d.values["reach"].get("env") or {})
    names -= supplied
    if not names:
        return
    read = set()
    for _, text in _code(d.src):
        read |= {n for n in names if f'"{n}"' in text or f"'{n}'" in text}
    # shortcut: one log for the whole discovery, so every 5xx op is attributed to the first
    # such variable; upgrade to per-request log windows when a target has two dependencies
    for op, probe in dict(d.errors_5xx).items() if read else ():
        var = min(read)
        d.exclude.append({"op": op, "reason": f"needs_dependency: {var} is not set"})
        d.mark("checks", f"exclude {op}", "probed", "algorithm", probe)


# ---- stand-ins (P6) ---------------------------------------------------------------------------


def candidates(d, cfg: dict) -> tuple[list[dict], list[str], list[str]]:
    """-> (dependency candidates, guard knob names, SMTP port names), all from the code and spec."""
    code = list(_code(d.src))
    supplied = set(d.app_env) | set(d.values["reach"].get("env") or {})
    names = {n for _, t in code for n in LITERAL.findall(t)} - supplied
    cands = []
    for n in sorted(names):
        kind = next((e["kind"] for e in cfg["env"] if re.search(e["pattern"], n)), None)
        if kind and not re.search(cfg["deny"], n):
            cands.append({"kind": kind, "via": "env", "key": n, "layer": "convention"})
    for m, p, op in _posts(d):
        props = (_disc().body_schema(d.spec, op)[0] or {}).get("properties") or {}
        for k in props:
            if re.search(cfg["spec_field"], k, re.IGNORECASE):
                cands.append({"kind": "http", "via": "spec", "key": k, "op": f"{m} {p}",
                              "layer": "algorithm"})  # fmt: skip
    if not cands and d.llm is not None:
        cands = (
            _proposed(d, cfg, code) if any(re.search(cfg["signals"], t) for _, t in code) else []
        )
    guards = sorted(n for n in names if re.search(cfg["guard"], n))
    return cands, guards, sorted(n for n in names if SMTP_PORT.match(n))


def _proposed(d, cfg: dict, code: list[tuple[str, str]]) -> list[dict]:
    """One model call (D5): env names for the code's mail and webhook destinations."""
    lines = [f"{f}: {ln.strip()[:200]}" for f, t in code for ln in t.splitlines()
             if re.search(cfg["signals"], ln) or "env" in ln.lower()][:400]  # fmt: skip
    got = ai._json(d.llm.call(WIRING_PROMPT.format(snippets="\n".join(lines)),
                              model=d.cfg["model"], schema=WIRING_SCHEMA, max_tokens=1000))  # fmt: skip
    return [{"kind": c["kind"], "via": "env", "key": c["env"], "layer": "exploration"}
            for c in got["candidates"] if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c["env"])]  # fmt: skip


def _posts(d):
    return [(m, p, op) for m, p, op in _disc().ops(d.spec) if m == "POST" and not PARAM.search(p)]


def _host(s: dict) -> str:
    return f"tp-standin-{s['name']}"


def plan(cands: list[dict], guards: list[str], ports: list[str], cfg: dict) -> list[dict]:
    """The stand-in entries (C5): one HTTP fake and one Mailpit at most (D6)."""
    out = {}
    for c in cands:
        s = out.setdefault(c["kind"], dict(cfg["standins"][c["kind"]], kind=c["kind"], env={}))
        c["name"] = s["name"]
        if c["kind"] == "smtp":
            s["env"] |= {c["key"]: _host(s)} | {p: "25" for p in ports}
        elif c["via"] == "env":
            s["env"][c["key"]] = f"http://{_host(s)}:{s['port']}/{c['key'].lower()}"
    if "http" in out:
        out["http"]["env"] |= {g: _host(out["http"]) for g in guards}
    return list(out.values())


def standins(d) -> None:
    cfg = config()
    cands, guards, ports = candidates(d, cfg)
    if not cands:
        return
    sts = plan(cands, guards, ports, cfg)
    d.values["reach"]["standins"] = sts
    restart(d)
    verify(d, cands, sts, cfg)
    wired = {c["key"] for c in cands if c["status"] == "wired"}
    for s in sts:  # an unwired name keeps its own value: only wired ones point at a stand-in
        s["env"] = {k: v for k, v in s["env"].items() if k in wired or k in guards or k in ports}
    d.values["reach"]["standins"] = [s for s in sts if any(c["name"] == s["name"] and
                                                           c["status"] == "wired" for c in cands)]  # fmt: skip
    d.dependencies = [{k: c.get(k) for k in ("name", "kind", "via", "key", "status", "reason",
                                             "evidence", "trigger", "setup")} for c in cands]  # fmt: skip
    d.dependencies += [{"name": "http", "kind": "http", "via": "guard", "key": g,
                        "status": "wired" if any(c["status"] == "wired" and c["kind"] == "http"
                                                 for c in cands) else "unwired",
                        "reason": "set to the HTTP stand-in's host only", "evidence": None,
                        "trigger": None, "setup": None} for g in guards]  # fmt: skip
    d.emit("discover_dependencies", wired=sorted(wired),
           unwired=sorted(c["key"] for c in cands if c["status"] != "wired"))  # fmt: skip


def restart(d) -> None:
    """Bring the stack up again with the stand-ins, and log in afresh (`start` forgets the token)."""

    def go():
        d.stack = d.new_stack()
        try:
            d.stack.start()
            bb.make_auth(d.draft(), d.stack).principal()
        except bb.BlackboxError as e:
            return {"ok": False, "error": str(e)[-400:]}
        return {"ok": True, "error": ""}

    _, out = d.probes.call("docker", "up standins", go)
    if not out["ok"]:
        from tp.stages.discover.discovery import DiscoveryError

        raise DiscoveryError("dependencies", f"the stack didn't come up with stand-ins: "
                             f"{out['error']}")  # fmt: skip


def journal(d, s: dict) -> list:
    """What the stand-in has received so far (probe-recorded, so a replay needs no network)."""

    def go():
        try:
            r = httpx.get(d.stack.standin_url(s["name"]) + s["journal"], timeout=10)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return {"error": f"{type(e).__name__}: {e}"}
        return {"items": body.get("messages", []) if isinstance(body, dict) else body}

    _, out = d.probes.call("http", f"journal {s['name']}", go)
    return out.get("items", [])


def _hits(c: dict, before: list, after: list) -> bool:
    new = after[len(before) :] if c["kind"] == "http" else after[: len(after) - len(before)]
    return any(c["kind"] == "smtp" or e["path"].split("?")[0] == f"/{c['key'].lower()}"
               for e in new)  # fmt: skip


def _body(d, op: dict, n: int):
    disc = _disc()
    schema, _ = disc.body_schema(d.spec, op)
    body = disc.example(d.spec, schema, n) if schema else {}
    if isinstance(body, dict):
        for k, p in (schema.get("properties") or {}).items():
            if k in body and disc.deref(d.spec, p).get("format") == "email":
                body[k] = f"tp-disc-{n}@example.test"
    return body


def verify(d, cands: list[dict], sts: list[dict], cfg: dict) -> None:
    """Register any `via: spec` receiver, then call each POST op once and see which stand-in
    journal grows; the op that adds a line is the dependency's trigger (D4)."""
    h, by_name = d.auth_headers(), {s["name"]: s for s in sts}
    http = by_name.get("http")
    setups = set()
    for c in cands:
        c |= {"status": "unwired", "reason": None, "evidence": None, "trigger": None, "setup": None}
        if c["via"] == "spec":
            op = d.spec["paths"][c["op"].split(" ", 1)[1]]["post"]
            body = _body(d, op, 1)
            if not isinstance(body, dict):
                c["reason"] = f"no valid body for {c['op']}"
                continue
            body[c["key"]] = f"http://{_host(http)}:{http['port']}/{c['key'].lower()}"
            _, status, _ = d.http("POST", c["op"].split(" ", 1)[1], headers=h, json_body=body)
            if not 200 <= status < 300:
                c["reason"] = f"registering the stand-in through {c['op']} answered {status}"
                continue
            c["setup"] = {"op": c["op"], "body": body}
            setups.add(c["op"])
    skip = setups | set(getattr(d, "public", []))
    tried = 0
    for i, (m, p, op) in enumerate(_posts(d)):
        if tried >= cfg["max_triggers"] or all(
            c["status"] == "wired" or c["reason"] for c in cands
        ):
            break
        if f"{m} {p}" in skip or p == (d.values["auth"].get("bootstrap") or {}).get("path"):
            continue
        body = _body(d, op, 100 + i)
        if body is None:
            continue
        before = {s["name"]: journal(d, s) for s in sts}
        n, status, _ = d.http(m, p, headers=h, json_body=body or None)
        tried += 1
        if not 200 <= status < 300:
            continue
        for c in cands:
            if c["status"] == "wired" or c["reason"]:
                continue
            s = by_name[c["name"]]
            after = _await(d, s, before[s["name"]])
            if _hits(c, before[s["name"]], after):
                c |= {"status": "wired", "trigger": f"{m} {p}", "evidence": f"probe#{n}",
                      "reason": f"{m} {p} left a line in {_host(s)}'s journal"}  # fmt: skip
                source = "ai_proposed+verified" if c["layer"] == "exploration" else "probed"
                d.mark("checks", f"dependency {c['key']}", source, c["layer"], n)
    for c in cands:
        c["reason"] = c["reason"] or f"no call left a line in the stand-in's journal ({tried} " \
            "POST operation(s) tried); not tested"  # fmt: skip


def _await(d, s: dict, before: list, tries: int = 3) -> list:
    """The journal once it has grown, or after `tries` reads: async senders take a moment."""
    after = journal(d, s)
    for _ in range(tries - 1):
        if len(after) > len(before):
            break
        if d.probes.replay is None:
            time.sleep(0.5)
        after = journal(d, s)
    return after
