"""UI flows (P7, docs/status/browser/001): a `.ui.yaml` flow is a test case in its own format
(C3, DR-4). It is collected by suffix and writes the same per-test record as any pytest test, so
the gate, the hash lock, triage and retest apply unchanged.

A flow is `{name, steps}`; each step is one of `goto: <path>`, `fill: {role, name, value}`,
`click: {role, name}`, `expect_request: {method, path, status}`, `read_back: {path, contains}`.
Elements are found by ARIA role and accessible name only. Values may use `$TP_UNIQUE` (new for
each execution) and `$TP_SECRET_ADMIN_PASSWORD`; nothing else from the env reaches a page.

The browser is Playwright's Chromium (images/runner-browser), driven synchronously, with no
retries. `python -m platform_rt.ui snapshot|run` is discovery's verifier (D6): the same
`execute()` as the test item."""

import contextlib
import json
import os
import re
import socket
import sys
from pathlib import Path

import httpx
import pytest

from platform_rt import record
from platform_rt.api import _ledger, api, route_matches, token
from platform_rt.blackbox import eventually
from platform_rt.expect import _record, status_matches
from platform_rt.origin import LocatorError, UiPageError
from platform_rt.standin import SENSITIVE

ARGS = {"goto": None, "fill": {"role", "name", "value"}, "click": {"role", "name"},
        "expect_request": {"method", "path", "status"}, "read_back": {"path", "contains"}}  # fmt: skip
PLACEHOLDER = re.compile(r"\$(TP_[A-Z_]+)")
PLACEHOLDERS = {"TP_UNIQUE", "TP_SECRET_ADMIN_PASSWORD"}
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
ROLE = re.compile(r"[a-z]+")
API_TYPES = ("document", "fetch", "xhr")  # the responses recorded as HTTP exchanges


class FlowError(ValueError):
    """A flow that isn't valid IR (a proposal is model output, so it is checked here)."""


def page_base() -> str:
    """$TP_BASE_URL for the browser. Chromium treats a one-label host as a TLD, and upgrades the
    HSTS-preloaded ones (`app`, `dev`, ...) to HTTPS, so a service name is reached by its IP.
    shortcut: the page's Host header is then the IP; map a dotted alias with
    --host-resolver-rules when a target checks Host."""
    url = httpx.URL(os.environ["TP_BASE_URL"].rstrip("/"))
    if "." in url.host or url.host == "localhost" or ":" in url.host:
        return str(url)
    return str(url.copy_with(host=socket.gethostbyname(url.host)))


def timeout_s() -> float:
    return float(os.environ.get("TP_UI_TIMEOUT", "5"))


def parse(flow) -> dict:
    if not (isinstance(flow, dict) and NAME.fullmatch(str(flow.get("name", "")))
            and isinstance(flow.get("steps"), list) and flow["steps"]):  # fmt: skip
        raise FlowError("a flow is {name: snake_case, steps: [one or more]}")
    for i, step in enumerate(flow["steps"]):
        if not (isinstance(step, dict) and len(step) == 1 and next(iter(step)) in ARGS):
            raise FlowError(f"step {i + 1}: one of {sorted(ARGS)}, as a one-key map")
        ((kind, arg),) = step.items()
        want = ARGS[kind]
        ok = (isinstance(arg, str) and arg.startswith("/")) if want is None else (
            isinstance(arg, dict) and set(arg) == want
            and all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in arg.values()))  # fmt: skip
        if ok and kind in ("fill", "click"):
            ok = bool(ROLE.fullmatch(arg["role"]) and str(arg["name"]).strip())
        if not ok:
            raise FlowError(f"step {i + 1}: {kind} takes "
                            + ("a path" if want is None else str(sorted(want))))  # fmt: skip
        values = arg.values() if isinstance(arg, dict) else [arg]
        if bad := {p for v in values for p in PLACEHOLDER.findall(str(v))} - PLACEHOLDERS:
            raise FlowError(f"step {i + 1}: unknown placeholder(s) {sorted(bad)}")
    return flow


# ---- the interpreter -------------------------------------------------------------------------


def _redact(v):
    if isinstance(v, dict):
        return {k: "<redacted>" if SENSITIVE.search(k) else _redact(x) for k, x in v.items()}
    return [_redact(x) for x in v] if isinstance(v, list) else v


def _json(raw):
    try:
        return json.loads(raw)
    except ValueError, TypeError:
        return raw.decode(errors="replace")[:2000] if isinstance(raw, bytes) else raw


def _http(resp) -> dict:
    """A Playwright response as an HTTP exchange of the usual shape (plugin records them all)."""
    req, url = resp.request, httpx.URL(resp.request.url)
    try:
        sent = req.post_data
    except Exception:  # noqa: BLE001 — a binary body: not recorded
        sent = None
    try:  # a server-sent stream (Memos' live refresh) never ends: body() would wait forever
        stream = "text/event-stream" in (resp.headers.get("content-type") or "")
        raw = b"" if stream else resp.body()
    except Exception:  # noqa: BLE001 — unavailable after a redirect or once the page moved on
        raw = b""
    return {"step": record.current().next_step(), "setup": False, "method": req.method,
            "path": url.path, "query": dict(url.params), "json": _redact(_json(sent or None)),
            "form": None, "auth": "authorization" in req.headers, "status": resp.status,
            "body": _redact(_json(raw)), "traceparent": None, "x_test_trace": None}  # fmt: skip


def _ui(kind: str, arg) -> None:
    """A UI action as an exchange step (`kind: ui`). Status 0: no HTTP answer (classify needs an
    int). A filled value is kept as written, so a placeholder never becomes its secret."""
    target = arg if kind == "goto" else f'{arg["role"]} "{arg["name"]}"'
    record.current().exchanges.append({
        "step": record.current().next_step(), "setup": False, "kind": "ui", "method": "UI",
        "path": f"{kind} {target}", "query": {}, "json": {"value": arg["value"]} if kind == "fill"
        else None, "form": None, "auth": False, "status": 0, "body": None, "traceparent": None,
        "x_test_trace": None})  # fmt: skip


class Run:
    def __init__(self, page):
        self.page, self.rec = page, record.current()
        self.base = page_base().rstrip("/")
        self.unique, self.errors, self.pending = self.rec.name("ui"), [], []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("response", lambda r: self.pending.append(r)
                if r.request.resource_type in API_TYPES else None)  # fmt: skip

    def value(self, s) -> str:
        return PLACEHOLDER.sub(lambda m: self.unique if m[1] == "TP_UNIQUE"
                               else os.environ.get(m[1], ""), str(s))  # fmt: skip

    def drain(self) -> None:
        while self.pending:
            self.rec.exchanges.append(_http(self.pending.pop(0)))

    def page_errors(self) -> None:
        if self.errors:
            raise UiPageError(f"the page's script failed: {self.errors[0][:500]}")

    def act(self, kind: str, arg) -> None:
        _ui(kind, arg)
        ms = timeout_s() * 1000
        try:
            if kind == "goto":
                self.page.goto(self.base + arg, wait_until="domcontentloaded", timeout=ms)
                return
            loc = self.page.get_by_role(arg["role"], name=arg["name"], exact=True)
            if kind == "fill":
                loc.fill(self.value(arg["value"]), timeout=ms)
            else:
                loc.click(timeout=ms)
        except Exception as e:
            if not type(e).__module__.startswith("playwright"):
                raise
            self.drain()
            self.page_errors()  # a page error first: it is likely why the element never came
            msg = str(e).splitlines()[0][:300]
            if kind == "goto":
                raise httpx.ConnectError(f"goto {arg}: {msg}") from e  # ENV: the SUT didn't answer
            raise LocatorError(f'{kind} {arg["role"]} "{arg["name"]}": {msg}') from e

    def expect_request(self, arg: dict) -> None:
        route, hit = f"{arg['method']} {arg['path']}", None
        for _ in range(int(timeout_s() * 10)):
            self.drain()
            hit = next((e for e in reversed(self.rec.exchanges) if e.get("kind") != "ui"
                        and route_matches(route, e["method"], e["path"])), None)  # fmt: skip
            if hit:
                break
            self.page.wait_for_timeout(100)  # lets Playwright deliver events
        _record(f"{self.rec.tc}/request", hit is not None, route, "sent" if hit else "not sent")
        self.rec.under_test = hit
        if hit["method"] == "POST" and 200 <= hit["status"] < 300:  # namespace clean-up (D1)
            _ledger("POST", httpx.Response(hit["status"], json=hit["body"],
                                           request=httpx.Request("POST", self.base + hit["path"])))  # fmt: skip
        _record(f"{self.rec.tc}/status", status_matches(hit["status"], arg["status"]),
                arg["status"], hit["status"])  # fmt: skip

    def read_back(self, arg: dict) -> None:
        want = self.value(arg["contains"])
        r = eventually(lambda: api.get(arg["path"], token=token()), until=lambda r: want in r.text)
        _record(f"{self.rec.tc}/read_back", want in r.text, f"GET {arg['path']} has {want}",
                f"{r.status_code}, " + ("found" if want in r.text else "not found"))  # fmt: skip

    def evidence(self) -> None:
        """On failure only: the aria snapshot and a screenshot next to $TP_RESULTS."""
        with contextlib.suppress(Exception):
            self.rec.observed["snapshot"] = snapshot(self.page)[:4000]
        if out := os.environ.get("TP_RESULTS"):
            shot = Path(out).parent / "ui" / f"{self.rec.nonce}.png"
            with contextlib.suppress(Exception):
                shot.parent.mkdir(parents=True, exist_ok=True)
                self.page.screenshot(path=str(shot))
                self.rec.observed["screenshot"] = f"ui/{shot.name}"


def snapshot(page) -> str:
    return page.locator("body").aria_snapshot()


def execute(flow: dict, page) -> None:
    """Run a flow on `page`, recording into `record.current()`; raises on the first failure."""
    run = Run(page)
    try:
        for step in flow["steps"]:
            ((kind, arg),) = step.items()
            run.page_errors()
            if kind in ("goto", "fill", "click"):
                run.act(kind, arg)
            else:
                getattr(run, kind)(arg)
            run.drain()
        page.wait_for_timeout(100)
        run.drain()
        run.page_errors()
    except BaseException:
        run.evidence()
        raise


@contextlib.contextmanager
def browser():
    from playwright.sync_api import sync_playwright  # in images/runner-browser only

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            yield b.new_page()
        finally:
            b.close()


# ---- the collector (C3) -----------------------------------------------------------------------


class FlowItem(pytest.Item):
    def runtest(self):
        with browser() as page:
            execute(self.flow, page)

    def reportinfo(self):
        return self.path, None, f"ui flow {self.flow['name']}"


class FlowFile(pytest.File):
    def collect(self):
        import yaml  # in the runner images (schemathesis, images/runner-browser), not a dependency

        flow = parse(yaml.safe_load(self.path.read_text()))
        cid = f"U:{flow['name']}"
        item = FlowItem.from_parent(self, name=f"{flow['name']}[{cid}]")
        item.flow, item.tp_case_id, item.tp_kind = flow, cid, "ui"
        yield item


def collect(file_path, parent):
    return FlowFile.from_parent(parent, path=file_path)


# ---- discovery's verifier (browser/001 D6) ----------------------------------------------------


def main(mode: str, stdin: str) -> dict:
    """`snapshot`: the start page's aria snapshot. `run`: a flow (JSON) -> what running showed."""
    with browser() as page:
        if mode == "snapshot":
            page.goto(page_base().rstrip("/") + "/", wait_until="domcontentloaded",
                      timeout=timeout_s() * 1000)  # fmt: skip
            with contextlib.suppress(Exception):  # an app's script renders it: wait for a control
                page.locator("input, button, textarea").first.wait_for(timeout=timeout_s() * 1000)
            return {"ok": True, "snapshot": snapshot(page)[:8000]}
        rec = record.start("discover", "U:proposal")
        try:
            execute(parse(json.loads(stdin)), page)
            err = None
        except Exception as e:  # noqa: BLE001 — reported to discovery, which decides
            err = e
        return {"ok": err is None, "error_type": err and type(err).__name__,
                "error": err and str(err)[:1000], "unique": rec.name("ui"),
                "exchanges": rec.exchanges, "observed": rec.observed}  # fmt: skip


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.stdin.read() if sys.argv[1] == "run" else ""),
                     default=str))  # fmt: skip
