"""UI flows at discover time (C6; P7, docs/status/browser/001 D1-D6, D11, D14, D15). The model
proposes one flow (sign in, then create) from the page's accessibility snapshot; it proposes the
page actions only. Running them in the browser runner must show a 2xx write carrying the typed
value, and an API read-back must find that value (DR-1). Those two are then appended to the flow
by a fixed rule, and the flow is kept in `d.ui_flows` (request R1 puts it in `checks.value.ui`)."""

import json
import os
import re
import subprocess

from tp.core import secrets
from tp.stages.discover import ai

ROUNDS = 3  # one proposal and at most two repairs (D5)
WRITES = ("POST", "PUT", "PATCH")
MAX_READS = 5
SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": {
        "type": "object",
        "properties": {"action": {"enum": ["goto", "fill", "click"]}, "path": {"type": "string"},
                       "role": {"type": "string"}, "name": {"type": "string"},
                       "value": {"type": "string"}},
        "required": ["action"], "additionalProperties": False}}},
    "required": ["steps"],
    "additionalProperties": False,
}  # fmt: skip
# The flow prompt (D5): its answer only counts once running verifies it.
PROMPT = (
    "You are writing one browser test for a web app, as a list of steps. The test signs in, then "
    "creates one new item through the page.\n"
    'Steps: {{"action": "goto", "path": "/"}}, {{"action": "fill", "role": ..., "name": ..., '
    '"value": ...}}, {{"action": "click", "role": ..., "name": ...}}. Find elements only by their '
    "ARIA role and their accessible name, exactly as the snapshot shows them.\n"
    'Start with a goto to "/". Sign in with the username "{user}" and the password '
    '"$TP_SECRET_ADMIN_PASSWORD": type these strings exactly. Then type "$TP_UNIQUE" as the new '
    "item's text, and submit it. Controls that appear only after sign-in are not in the "
    "snapshot: name them as the page most likely does.\n{feedback}\n"
    "Accessibility snapshot of the page:\n{snapshot}"
)


def _skip(d) -> str | None:
    if d.values["reach"].get("strategy") == "external":
        return "a live system: discovery writes nothing to it (U1)"
    if "login" not in d.values.get("auth", {}):
        return "no password sign-in, so the runner has no password to type"
    _, status, body = d.http("GET", "/")
    if status != 200 or "<html" not in str(body).lower():  # the stack's client may parse it as YAML
        return "no HTML page at /"
    return None


def _browser(d, image: str | None, mode: str, flow: dict | None = None) -> dict:
    """`python -m platform_rt.ui <mode>` in the browser runner, on the run network (D6)."""

    def go():
        env = {"TP_SECRET_ADMIN_PASSWORD": secrets.get(d.slug, "admin_password")}
        with d.stack.egress(image, {}) as (network, base_url):
            cmd = ["docker", "run", "--rm", "-i", "--network", network, "-e",
                   f"TP_BASE_URL={base_url}", "-e", "TP_RUN=discover", "-e",
                   "TP_SECRET_ADMIN_PASSWORD", image, "python", "-m", "platform_rt.ui", mode]  # fmt: skip
            try:
                r = subprocess.run(cmd, input=json.dumps(flow) if flow else "", text=True,
                                   capture_output=True, timeout=300, env=os.environ | env,
                                   check=False)  # fmt: skip
            except subprocess.TimeoutExpired:
                return {"ok": False, "error_type": "RunnerError", "error": "no answer in 300 s"}
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except ValueError, IndexError:
            return {"ok": False, "error_type": "RunnerError",
                    "error": secrets.redact((r.stderr or r.stdout)[-800:])}  # fmt: skip

    return d.probes.call("docker", f"ui {mode}", go, req=flow)[1]


def _step(s: dict) -> dict:
    a = s.get("action")
    if a == "goto":
        return {"goto": s.get("path") or "/"}
    loc = {"role": s.get("role"), "name": s.get("name")}
    return {"fill": loc | {"value": s.get("value")}} if a == "fill" else {"click": loc}


def _propose(d, snapshot: str, feedback: str) -> list[dict]:
    raw = d.llm.call(PROMPT.format(user=d.cfg["admin_user"], snapshot=snapshot[:8000],
                                   feedback=feedback),
                     model=d.cfg["model"], schema=SCHEMA, max_tokens=1500)  # fmt: skip
    return [_step(s) for s in ai._json(raw).get("steps", [])]


def _template(spec: dict, method: str, path: str) -> str:
    """The spec path a request went to (`/api/notes/7` -> `/api/notes/{id}`), else the path."""
    segs = path.split("/")
    for p, ops in spec.get("paths", {}).items():
        want = p.split("/")
        if (
            method.lower() in ops
            and len(want) == len(segs)
            and all(w == g or (w.startswith("{") and g) for w, g in zip(want, segs, strict=True))
        ):
            return p
    return path


def verify(d, steps: list[dict], out: dict) -> tuple[dict | None, str]:
    """-> (the flow, why) once a 2xx write carried the typed value and a GET reads it back (D4)."""
    unique = out["unique"]
    create = next((e for e in out["exchanges"] if e.get("kind") != "ui"
                   and e["method"] in WRITES and 200 <= e["status"] < 300
                   and unique in json.dumps(e["json"])), None)  # fmt: skip
    if create is None:
        return None, "the steps ran, but no 2xx write carried the typed value"
    op = _template(d.spec, create["method"], create["path"])
    reads = [p for p, ops in d.spec.get("paths", {}).items() if "get" in ops and "{" not in p]
    for path in sorted(reads, key=lambda p: p != op)[:MAX_READS]:
        n, status, body = d.http("GET", path, headers=d.auth_headers())
        if status == 200 and unique in json.dumps(body):
            break
    else:
        why = f"{create['method']} {op} answered {create['status']}"
        return None, f"{why}, but no GET read back the typed value"
    name = "create_" + (re.sub(r"[^a-z0-9]+", "_", op.rstrip("/").rsplit("/", 1)[-1].lower())
                        .strip("_") or "item")  # fmt: skip
    flow = {"name": name, "steps": steps + [
        {"expect_request": {"method": create["method"], "path": op, "status": "2xx"}},
        {"read_back": {"path": path, "contains": "$TP_UNIQUE"}}],
        "verified": {"op": f"{create['method']} {op}", "read_back": f"GET {path}"}}  # fmt: skip
    d.mark("checks", f"ui {name}", "ai_proposed+verified", "exploration", n)
    return flow, f"{create['method']} {op} created it and GET {path} read it back (probe#{n})"


def run(d) -> None:
    if d.llm is None:  # no model, no proposal
        return
    d.ui_flows = []
    if why := _skip(d):
        d.emit("discover_ui", flow=None, reason=why)
        return
    image = None
    if d.probes.replay is None:  # a replay needs no image: every browser run is in the log
        from tp.stages.generate import ui as gen_ui

        image = gen_ui.build_image()["digest"]
    snap = _browser(d, image, "snapshot")
    if not snap.get("ok"):
        d.emit("discover_ui", flow=None, reason=f"no snapshot: {snap.get('error')}")
        return
    snapshot, feedback, why = snap["snapshot"], "", "no proposal"
    for _ in range(ROUNDS):
        steps = _propose(d, snapshot, feedback)
        out = _browser(d, image, "run", {"name": "proposal", "steps": steps})
        if out["ok"]:
            flow, why = verify(d, steps, out)
            if flow:
                d.ui_flows = [flow]
                d.emit("discover_ui", flow=flow["name"], reason=why)
                return
        elif out["error_type"] in ("UiPageError", "RunnerError", "ConnectError"):
            why = f"{out['error_type']}: {out['error']}"  # the page or the runner, not the flow
            break
        else:
            why = f"{out['error_type']}: {out['error']}"
        snapshot = (out.get("observed") or {}).get("snapshot") or snapshot
        feedback = (f"\nYour last steps were {json.dumps(steps)}. Running them failed: {why}. "
                    "The snapshot below was taken where they stopped.")  # fmt: skip
    d.emit("discover_ui", flow=None, reason=f"not verified: {why}")
