"""Safe-mode egress proxy for external reach (docs/status/external/001 E1-E5).

The runner's only route out: every request a test sends, Schemathesis included, passes `Policy`
before it reaches the live system. Deny by default:

- the request target must be origin-form (`/…`): an absolute URL would leave for any host (H3);
- method overrides are stripped, and `_method` refuses the request (M3);
- reads are allowed outside the deny categories (M4);
- a create only on a spec collection whose item path has both GET and DELETE, outside the deny
  categories, with the run prefix in its JSON body;
- update and delete only on items this proxy saw the run create (its ledger, on disk): a 201, or
  a 2xx whose body carries the run prefix (M1);
- anything else answers 599 with `X-TP-Blocked: <rule>` and never leaves.

Run as `python -m platform_rt.proxy` with $TP_UPSTREAM, $TP_OPENAPI_FILE, $TP_LEDGER, $TP_RUN,
$TP_ID_FIELD, $TP_SAFETY (`{"deny": [...]}`) and, optionally, $TP_UPSTREAM_HOST (the `Host` the
live system expects, L4). Prints `ready` once listening.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from platform_rt.api import dig, route_matches

READS = {"GET", "HEAD", "OPTIONS"}
BLOCKED = 599
HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive",
       "content-encoding", "upgrade", "proxy-connection"}  # fmt: skip
OVERRIDES = {"x-http-method-override", "x-http-method", "x-method-override"}  # M3


def _has_method(target: str, body: bytes) -> bool:
    """`_method` anywhere a framework might read it: query, form body, top-level JSON key (M3)."""
    query = httpx.URL(target).params
    try:
        parsed = json.loads(body or b"null")
    except ValueError:
        parsed = None
    if parsed is None and body:
        parsed = dict(httpx.QueryParams(body.decode("latin-1")))  # a form body
    return "_method" in query or (isinstance(parsed, dict) and "_method" in parsed)


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


class Policy:
    def __init__(self, spec: dict, deny: list[str], run: str, id_field: str = "id",
                 ledger: str | None = None):  # fmt: skip
        self.paths = spec.get("paths", {})
        self.deny = [d.lower() for d in deny]
        self.prefix = f"tp-{run}-"  # platform_rt.record.Recorder.name
        self.id_field, self.ledger = id_field, ledger
        self.owned: dict[str, dict] = {}  # item path -> ledger entry
        self.lock = threading.Lock()
        if ledger and os.path.exists(ledger):
            with open(ledger) as f:
                for e in (json.loads(ln) for ln in f if ln.strip()):
                    gone = e.pop("deleted_by_test", False)
                    if item := self.item_path(e):
                        if gone:
                            self.owned.pop(item, None)
                        else:
                            self.owned[item] = e

    def _op(self, method: str, path: str) -> tuple[str, dict] | None:
        for tpl, ops in self.paths.items():
            if method.lower() in ops and route_matches(f"{method} {tpl}", method, path):
                return tpl, ops[method.lower()]
        return None

    def _denied(self, tpl: str, op: dict) -> str | None:
        words = [s.lower() for s in tpl.split("/") if s and not s.startswith("{")]
        # scope markers (x-required-scope and the like) only ever make an operation stricter
        words += [s.lower() for k, v in op.items() if k.startswith("x-") and "scope" in k.lower()
                  for s in _strings(v)]  # fmt: skip
        return next((d for d in self.deny for w in words if d in w), None)

    def _item_tpl(self, tpl: str) -> str | None:
        base = tpl.rstrip("/")
        for cand, ops in self.paths.items():
            rest = cand[len(base) + 1 :] if cand.startswith(base + "/") else ""
            if re.fullmatch(r"\{[^/}]+\}/?", rest) and {"get", "delete"} <= set(ops):
                return cand
        return None

    def item_path(self, entry: dict) -> str | None:
        """The concrete item path of a created resource (its Location, or `<create path>/<id>`)."""
        cands = [httpx.URL(entry["location"]).path] if entry.get("location") else []
        if entry.get("id") is not None:
            stem = entry["path"].rstrip("/") + "/" + str(entry["id"]).split("/")[-1]
            cands += [stem + "/", stem]
        return next((c for c in cands for t in self.paths
                     if "delete" in self.paths[t] and route_matches(f"DELETE {t}", "DELETE", c)),
                    None)  # fmt: skip

    def decide(self, method: str, path: str, body: bytes = b"") -> tuple[bool, str]:
        method = method.upper()
        found = self._op(method, path)
        if method in READS:  # deny words on reads too: GET /logout or an export trigger acts (M4)
            word = self._denied(*found) if found else self._denied(path, {})
            return (False, f"deny category {word!r}") if word else (True, "read")
        if found is None:
            return False, "not in the spec"
        tpl, op = found
        if word := self._denied(tpl, op):
            return False, f"deny category {word!r}"
        if method == "POST":
            if self._item_tpl(tpl) is None:
                return False, "not a collection with read and delete on its items"
            try:
                parsed = json.loads(body or b"null")
            except ValueError:
                return False, "create body isn't JSON, so it can't carry the run prefix"
            if not any(s.startswith(self.prefix) for s in _strings(parsed)):
                return False, "create body doesn't carry the run prefix"
            return True, "create"
        with self.lock:
            owned = path in self.owned
        return (True, "created by this run") if owned else (False, "not created by this run")

    def seen(self, method: str, path: str, status: int, body: bytes, location: str | None):
        """Record a create; mark a deleted item gone (same lines as the runner's ledger)."""
        if not 200 <= status < 300 or method.upper() not in ("POST", "DELETE"):
            return
        if method.upper() == "POST":
            try:
                parsed = json.loads(body or b"null")
            except ValueError:
                parsed = None
            if status != 201 and not any(s.startswith(self.prefix) for s in _strings(parsed)):
                return  # a get-or-create answering 200 with an existing item isn't ours (M1)
            rid = dig(parsed, self.id_field)
            entry = {"path": path, "id": rid, "location": location}
            if (item := self.item_path(entry)) is None:
                return
            line = entry
        else:
            with self.lock:
                entry = self.owned.get(path)
            if entry is None:
                return
            item, line = path, entry | {"deleted_by_test": True}
        with self.lock:
            if method.upper() == "POST":
                self.owned[item] = entry
            else:
                self.owned.pop(item, None)
            if self.ledger:
                with open(self.ledger, "a") as f:
                    f.write(json.dumps(line) + "\n")


def server(policy: Policy, upstream: httpx.Client, port: int = 8000,
           host: str | None = None) -> ThreadingHTTPServer:  # fmt: skip
    class Handler(BaseHTTPRequestHandler):
        def _any(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            path = self.path.split("?")[0]
            if not self.path.startswith("/") or self.path.startswith("//"):
                ok, rule = False, "not an origin-form request target"  # httpx ignores base_url (H3)
            elif _has_method(self.path, body):
                ok, rule = False, "_method override"
            else:
                ok, rule = policy.decide(self.command, path, body)
            if not ok:
                self.send_response(BLOCKED)
                self.send_header("X-TP-Blocked", rule)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP | OVERRIDES}
            if host:
                headers["Host"] = host
            try:
                r = upstream.request(self.command, self.path, content=body, headers=headers)
            except httpx.HTTPError as e:
                self.send_error(502, f"upstream: {type(e).__name__}")
                return
            policy.seen(self.command, path, r.status_code, r.content, r.headers.get("location"))
            self.send_response(r.status_code)
            for k, v in r.headers.multi_items():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(r.content)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(r.content)

        do_GET = do_HEAD = do_OPTIONS = do_POST = do_PUT = do_PATCH = do_DELETE = _any

        def log_message(self, *args):  # requests are recorded by the runner, not here
            pass

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def serve(port: int = 8000) -> None:
    with open(os.environ["TP_OPENAPI_FILE"]) as f:
        spec = json.load(f)
    policy = Policy(spec, json.loads(os.environ.get("TP_SAFETY") or "{}").get("deny", []),
                    os.environ["TP_RUN"], os.environ.get("TP_ID_FIELD") or "id",
                    os.environ.get("TP_LEDGER"))  # fmt: skip
    srv = server(policy, httpx.Client(base_url=os.environ["TP_UPSTREAM"], timeout=60), port,
                 os.environ.get("TP_UPSTREAM_HOST"))  # fmt: skip
    print("ready", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    serve()
