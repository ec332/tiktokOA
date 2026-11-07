"""Discover & bootstrap (stage D, docs/status/discover/001 + 002): from a repo URL to a verified
target profile. Candidates come from conventions (config/discovery.yaml), fixed algorithms over the
repo and the spec, exploration (one model call per step, D1) and `setup` answers. Every one is
accepted only when its probe passes. No target names here: everything comes from the repo, the
spec and the config.

Every outside call (git, Docker, HTTP) goes through `Probes`, which records it; a replay serves the
same sequence from the recorded log with no Docker and no network, so the profile comes out
byte-identical (D2).
"""

import base64
import json
import re
import subprocess
import time
from pathlib import Path

import httpx
import yaml

from tp.core import db, profile, secrets
from tp.stages.discover import ai
from tp.stages.env import blackbox as bb

ROOT = Path(__file__).parents[3]
CONFIG = ROOT / "config" / "discovery.yaml"
PARAM = re.compile(r"\{[^}]+\}")
SOURCE_RANK = ["ai_proposed+verified", "clarify", "repo", "resolver", "probed"]  # weakest first
USERISH = re.compile("user|login|email|name", re.IGNORECASE)
SPEC_SUFFIXES = (".yaml", ".yml", ".json")
SPEC_MAX_BYTES = 5_000_000


class DiscoveryError(Exception):
    """A step found no verified candidate. `step` + `question` become a `setup` question (D5)."""

    def __init__(self, step: str, message: str, question: str = "", key: str | None = None):
        super().__init__(message)
        self.step, self.question, self.key = step, question or message, key  # key: answerable


class ReplayMiss(Exception):
    pass


def config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


# ---- the probe log (D2) -----------------------------------------------------------------------


class Probes:
    def __init__(self, replay: list[dict] | None = None):
        self.log: list[dict] = []
        self.replay = None if replay is None else list(replay)

    def call(self, kind: str, key: str, fn, req=None) -> tuple[int, object]:
        """-> (probe id, result). Live: run `fn` and record it. Replay: the next recorded result,
        which must be for the same call."""
        if self.replay is not None:
            if not self.replay:
                raise ReplayMiss(f"probe {len(self.log)}: nothing recorded for {kind} {key}")
            e = self.replay.pop(0)
            if (e["kind"], e["key"]) != (kind, key):
                raise ReplayMiss(f"probe {len(self.log)}: recorded {e['kind']} {e['key']}, "
                                 f"asked {kind} {key}")  # fmt: skip
            out = e["out"]
        else:
            out = fn()
        self.log.append({"n": len(self.log), "kind": kind, "key": key, "req": req, "out": out})
        return len(self.log) - 1, out

    def sealed(self) -> list[dict]:
        # redacted (D7), key order kept: a replay walks response fields in their served order
        return json.loads(secrets.redact(json.dumps(self.log)))


# ---- spec helpers -----------------------------------------------------------------------------


def deref(spec: dict, node):
    """Follow `$ref` and single-item `allOf` until a plain schema."""
    for _ in range(20):
        if isinstance(node, dict) and "$ref" in node:
            cur = spec
            for part in node["$ref"].lstrip("#/").split("/"):
                cur = cur[part]
            node = cur
        elif isinstance(node, dict) and len(node.get("allOf") or []) == 1 and "type" not in node:
            node = {**node["allOf"][0], **{k: v for k, v in node.items() if k != "allOf"}}
        else:
            return node or {}
    return node or {}


def ops(spec: dict):
    for path, item in spec.get("paths", {}).items():
        for m, op in item.items():
            if m in ("get", "post", "put", "patch", "delete") and isinstance(op, dict):
                yield m.upper(), path, op


def body_schema(spec: dict, op: dict) -> tuple[dict, str | None]:
    """-> (the request body schema, the encoding to send): form when the spec accepts it (the
    platform's token login posts forms), else JSON."""
    content = (op.get("requestBody") or {}).get("content") or {}
    for ctype, enc in (("application/x-www-form-urlencoded", "form"),
                       ("application/json", "json")):  # fmt: skip
        if ctype in content:
            return deref(spec, content[ctype].get("schema")), enc
    return {}, None


def response_schema(spec: dict, op: dict) -> dict:
    for code, r in sorted((op.get("responses") or {}).items()):
        if str(code).startswith("2"):
            return deref(
                spec, ((r.get("content") or {}).get("application/json") or {}).get("schema")
            )
    return {}


def declares_security(spec: dict) -> bool:
    return "security" in spec or any("security" in op for _, _, op in ops(spec))


def secured(spec: dict, op: dict) -> bool:
    sec = op.get("security", spec.get("security")) or []
    return bool(sec) and {} not in sec


def is_spec(body) -> bool:
    return isinstance(body, dict) and isinstance(body.get("paths"), dict) \
        and ("openapi" in body or "swagger" in body)  # fmt: skip


def common_base(paths: list[str]) -> str:
    """The longest common segment prefix of the spec's paths, parameters excluded (P6d)."""
    parts = [p.strip("/").split("/") for p in paths]
    base = []
    for segs in zip(*parts, strict=False):
        if len(set(segs)) != 1 or PARAM.search(segs[0]) or ":" in segs[0]:
            break
        base.append(segs[0])
    return "/" + "/".join(base) if base else ""


def example(spec: dict, schema: dict, n: int):
    """A minimal valid value for a schema: required properties only. None when it can't make one.
    Enum zero values named `*_UNSPECIFIED` are skipped (proto3 enums, Google AIP-126: "the zero
    value ... should be named `<ENUM>_UNSPECIFIED`" and means "not set")."""
    s = deref(spec, schema)
    if "enum" in s:
        return next((e for e in s["enum"] if e not in (None, "")
                     and not str(e).endswith("_UNSPECIFIED")), None)  # fmt: skip
    t = s.get("type")
    if t == "string":
        v = f"tp-disc-{n}"
        return v[: s["maxLength"]] if s.get("maxLength") else v
    if t == "integer":
        return max(int(s.get("minimum", 1)), 1)
    if t == "number":
        return 1
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object" or "properties" in s:
        out = {}
        for k in s.get("required", []):
            p = deref(spec, s.get("properties", {}).get(k, {}))
            if p.get("readOnly"):
                continue
            if (v := example(spec, p, n)) is None:
                return None
            out[k] = v
        return out
    return None


def items(body) -> list:
    """The listed items of a collection read: a bare list, or the first list of objects."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and all(isinstance(i, dict) for i in v):
                return v
    return []


def fingerprint(status: int, body) -> tuple:
    return status, tuple(sorted(body)) if isinstance(body, dict) else type(body).__name__


# ---- discovery --------------------------------------------------------------------------------


class Discovery:
    def __init__(self, url: str, ref: str | None, llm, probes: Probes,
                 conventions: bool = True, emit=lambda *a, **k: None,
                 answers: dict[str, str] | None = None):  # fmt: skip
        self.url, self.ref, self.llm, self.probes, self.emit = url, ref, llm, probes, emit
        self.answers = answers or {}  # setup answers: step key -> text (source: clarify)
        cfg = config()
        self.cfg = cfg["platform"]
        self.conv = cfg["conventions"] if conventions else []
        self.slug = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1]).lower()
        self.prov: dict[str, dict] = {}  # concern -> {sub-key: {source, layer, probe}}
        self.values: dict[str, dict] = {}
        self.stack: bb.Stack | None = None
        self.samples: list[tuple] = []  # (fingerprint) of real-route answers (P5)
        self.errors_5xx: list[tuple[str, int]] = []  # ("METHOD template", probe) (D11)
        self.created: list[str] = []  # discovery's own items, deleted at the end
        self.observed_open: list[dict] = []  # [{op, description}] for confirm_observed (D9)

    def conventions(self, kind: str) -> list:
        return [c["value"] for c in self.conv if c["kind"] == kind]

    def mark(self, concern: str, key: str, source: str, layer: str, probe: int | None = None):
        self.prov.setdefault(concern, {})[key] = {"source": source, "layer": layer, "probe": probe}

    def http(self, method: str, path: str, *, headers=None, data=None, json_body=None,
             params=None, template: str | None = None) -> tuple[int, int, object]:  # fmt: skip
        """-> (probe id, status, body). Never raises: a transport error is status 0."""
        kw = {k: v for k, v in (("headers", headers), ("data", data), ("json", json_body),
                                ("params", params)) if v is not None}  # fmt: skip

        def go():
            try:
                status, body = self.stack.http(method, path, **kw)
            except (httpx.HTTPError, bb.BlackboxError) as e:
                return {"status": 0, "body": f"{type(e).__name__}: {e}"}
            return {"status": status, "body": body}

        q = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
        req = {"auth": bool(headers)} | ({"body": data or json_body} if data or json_body else {})
        n, out = self.probes.call("http", f"{method} {path}{q}", go, req=req)
        if out["status"] >= 500:
            self.errors_5xx.append((f"{method} {template or path}", n))
        return n, out["status"], out["body"]

    # ---- the profile skeleton the env strategies need to run the stack ----

    def draft(self) -> profile.TargetProfile:
        v = self.values
        raw = {"target": {"name": self.slug, "repo": self.url}, "commit_tested": None} | {
            c: {"source": "resolver", "value": v.get(c) or {"strategy": "none"}}
            for c in profile.CONCERNS
        }
        return profile.parse(raw, self.slug)

    def run(self) -> dict:
        self.step_reach()
        self.step_spec()
        self.step_auth()
        self.step_data()
        self.step_secured()
        self.step_spec_check()
        self.step_cleanup()
        self.step_dependencies()
        self.step_resolver()
        return self.profile()

    # ---- reach -------------------------------------------------------------------------------

    def latest_tag(self) -> tuple[int, str]:
        def tags():
            r = subprocess.run(["git", "ls-remote", "--tags", "--refs", self.url], check=False,
                               capture_output=True, text=True, timeout=120)  # fmt: skip
            return sorted(line.rsplit("refs/tags/", 1)[-1] for line in r.stdout.splitlines()) \
                if r.returncode == 0 else {"error": r.stderr.strip()[-300:]}  # fmt: skip

        n, names = self.probes.call("git", f"ls-remote {self.url}", tags)
        if isinstance(names, dict):
            raise DiscoveryError("reach", f"git ls-remote {self.url}: {names['error']}")
        rx = re.compile(self.cfg["release_tag"])
        rel = [t for t in names if rx.match(t)]
        if not rel:
            raise DiscoveryError("reach", f"{self.url}: no release tag matches "
                                 f"{self.cfg['release_tag']}; add @<ref> to the URL")  # fmt: skip
        return n, max(rel, key=lambda t: tuple(int(x) for x in t.lstrip("v").split(".")))

    def composes(self, src: Path) -> list[tuple[str, dict, str]]:
        """Compose files with an app service publishing a port, fewest services first:
        (path, compose, app service)."""
        out = []
        for f in sorted(src.rglob("*compose*.y*ml")):
            if any(part.startswith(".") or part == "node_modules" for part in f.parts):
                continue
            try:
                raw = yaml.safe_load(f.read_text())
            except yaml.YAMLError:
                continue
            svcs = (raw or {}).get("services") if isinstance(raw, dict) else None
            if not isinstance(svcs, dict):
                continue
            app = next((n for n, s in svcs.items() if isinstance(s, dict) and s.get("ports")
                        and ("image" in s or "build" in s)), None)  # fmt: skip
            if app:
                out.append((str(f.relative_to(src)), raw, app))
        return sorted(out, key=lambda c: (len(c[1]["services"]), c[0]))

    def env_files(self, src: Path, compose_path: str, svc: dict) -> dict[str, str]:
        files = svc.get("env_file", [])
        env = {}
        for f in [files] if isinstance(files, str | dict) else files:
            p = (src / compose_path).parent / (f["path"] if isinstance(f, dict) else f)
            if p.exists():
                env |= bb.env_file(p)
        return env | {k: v for k, v in bb._env_dict(svc.get("environment")).items()}

    def admin_env(self, src: Path, app_env: dict[str, str]) -> tuple[dict | None, str]:
        """The env vars that create the first admin at start-up: a setup answer, conventions
        (searched in the repo's docs and compose files), then exploration. -> (pair, layer)."""
        if a := self.answers.get("admin_env"):
            user, _, pw = a.replace(",", " ").partition(" ")
            return {"user": user.strip(), "password": pw.strip()}, "clarify"
        files = ai.doc_files(src)
        text = "\n".join(app_env) + "\n" + "\n".join(
            (src / f).read_text(errors="replace")[: self.cfg["file_cap"]] for f in files[:200]
            if (src / f).stat().st_size < self.cfg["file_cap"]
        )  # fmt: skip
        for pat in self.conventions("admin_env_pattern"):
            if pat["user"] in text and pat["password"] in text:
                return pat, "convention"
        got = ai.admin_env(self.llm, self.cfg["model"], src, files, app_env, self.cfg["file_cap"])
        return got, "exploration"

    def step_reach(self) -> None:
        if self.ref:
            tag_probe, ref = None, self.ref
        else:
            tag_probe, ref = self.latest_tag()
        self.values["reach"] = {"strategy": "container", "ref": ref}
        self.stack = bb.Stack(self.draft())
        clone = db.data_dir() / "sut" / self.slug
        if self.probes.replay is not None and not (
            (clone / ".git").exists() and self.stack._at(clone) == ref
        ):  # a replay never touches the network (D2): the clone must already be at the ref
            raise ReplayMiss(f"replay needs {clone} checked out at {ref}; run discovery live once")
        src = self.stack.src
        self.src = src
        self.commit = self.stack.commit()
        cands = self.composes(src)[: self.cfg["max_compose"]]
        if not cands:
            raise DiscoveryError("reach", "no compose file with a service publishing a port")
        admin = admin_layer = None
        errors = []
        for path, raw, app in cands:
            svc = raw["services"][app]
            app_env = self.env_files(src, path, svc)
            if admin is None:
                admin, admin_layer = self.admin_env(src, app_env)
            env: dict = {}
            gen = []
            rx = re.compile(self.cfg["secret_name"], re.IGNORECASE)
            for k, v in app_env.items():
                readers = sum(
                    k in json.dumps(s) or k in self.env_files(src, path, s)
                    for s in raw["services"].values()
                )  # one reader: no desync (P6e)
                if rx.search(k) and v.strip().lower() in self.cfg["placeholders"] and readers <= 1:
                    env[k] = {"secret": k.lower()}
                    gen.append(k)
            if admin:
                env[admin["user"]] = self.cfg["admin_user"]
                env[admin["password"]] = {"secret": "admin_password"}
            port = str(svc["ports"][0]).rsplit(":", 1)[-1].split("/")[0]
            image = str(svc.get("image", ""))
            reach = {"strategy": "container", "ref": ref, "compose": path, "service": app,
                     "port": int(port)}  # fmt: skip
            if image and bb.retag(image, "x") != image:
                reach["image_tag_from"] = "ref"  # floating tag: test the image of the ref (P6f)
            reach["env"] = env
            self.values["reach"] = reach
            self.stack = bb.Stack(self.draft())
            n, out = self.probes.call("docker", f"up {path}", self._up)
            if out["ok"]:
                break
            errors.append(f"{path}: {out['error']}")
        else:
            raise DiscoveryError("reach", f"no compose file came up: {errors}")
        self.app_env = app_env
        self.mark("reach", "ref", "repo", "algorithm", tag_probe)
        for k in ("compose", "service", "port"):
            self.mark("reach", k, "repo", "algorithm", n)
        if admin:
            self.mark("reach", "env", {"exploration": "ai_proposed+verified", "clarify": "clarify"}
                      .get(admin_layer, "repo"), admin_layer, None)  # verified by the login  # fmt: skip
        self.admin = admin
        self.emit("discover_reach", compose=reach["compose"], service=app, generated=gen)

    def _up(self) -> dict:
        try:
            self.stack.start(wait=False)
        except bb.BlackboxError as e:
            return {"ok": False, "error": str(e)[-400:]}
        t0 = time.monotonic()
        while time.monotonic() - t0 < bb.UP_TIMEOUT:  # the app answers anything below 500
            try:
                status, _ = self.stack.http("GET", "/")
                if 0 < status < 500:
                    return {"ok": True, "error": ""}
            except httpx.HTTPError, bb.BlackboxError:
                pass
            time.sleep(2)
        return {"ok": False, "error": "GET / never answered below 500"}

    # ---- spec --------------------------------------------------------------------------------

    def spec_files(self) -> list[tuple[str, dict]]:
        """Tracked YAML/JSON files that are OpenAPI documents, most paths first (an algorithm,
        not a name list: any file whose content declares `openapi`/`swagger` and `paths`)."""
        r = subprocess.run(["git", "ls-files"], cwd=self.src, capture_output=True, text=True,
                           check=False)  # fmt: skip
        out = []
        for f in r.stdout.splitlines():
            p = self.src / f
            if (
                not f.endswith(SPEC_SUFFIXES)
                or not p.is_file()
                or p.stat().st_size > SPEC_MAX_BYTES
            ):
                continue
            head = p.read_text(errors="replace")[:2000]
            if "openapi" not in head and "swagger" not in head:
                continue
            try:
                doc = yaml.safe_load(p.read_text())
            except yaml.YAMLError:
                continue
            if is_spec(doc):
                out.append((f, doc))
        return sorted(out, key=lambda c: -len(c[1]["paths"]))

    def step_spec(self) -> None:
        answer = self.answers.get("spec", "").strip()
        served = [(answer, "clarify")] if answer.startswith("/") else []
        for path, layer in served + [(p, "convention") for p in self.conventions("spec_path")]:
            n, status, body = self.http("GET", path)
            if status == 200 and is_spec(body):
                self.spec = body
                self.values["spec"] = {"strategy": "served", "openapi": path,
                                       "base": common_base(list(body["paths"]))}  # fmt: skip
                self.values["reach"]["health"] = path.split("?")[0]
                self.mark(
                    "spec", "openapi", "clarify" if layer == "clarify" else "probed", layer, n
                )
                self.mark("spec", "base", "probed", "algorithm", n)
                self.mark("reach", "health", "probed", "algorithm", n)
                self.stack = bb.Stack(self.draft())
                return
        files = self.spec_files()
        layer = "algorithm"
        if answer and not answer.startswith("/"):
            files = [(f, d) for f, d in files if f == answer]
            layer = "clarify"
        if not files:
            raise DiscoveryError(
                "spec", "no served spec at a convention path and no OpenAPI file in the repo",
                "Where is the OpenAPI spec? Give a served path (e.g. /openapi.json) or a file "
                "in the repo (e.g. api/openapi.yaml).", "spec")  # fmt: skip
        rel, self.spec = files[0]
        self.values["spec"] = {"strategy": "repo_file", "openapi": rel,
                               "base": common_base(list(self.spec["paths"]))}  # fmt: skip
        self.mark("spec", "openapi", "clarify" if layer == "clarify" else "repo", layer)
        self.mark("spec", "base", "repo", "algorithm")
        for path, hl in [(p, "convention") for p in self.conventions("health_path")] + [
            ("/", "algorithm")
        ]:
            n, status, _ = self.http("GET", path)
            if status == 200:
                self.values["reach"]["health"] = path
                self.mark("reach", "health", "probed", hl, n)
                break
        else:
            raise DiscoveryError("reach", "no health path answers 200",
                                 "Which path answers 200 when the server is up?")  # fmt: skip
        self.stack = bb.Stack(self.draft())

    # ---- auth --------------------------------------------------------------------------------

    def check_op(self) -> tuple[int, str]:
        """The first GET without path parameters that refuses an anonymous call: among the
        secured ones when the spec declares security, else among all of them."""
        declared, tried = declares_security(self.spec), 0
        for m, path, op in ops(self.spec):
            if m == "GET" and not PARAM.search(path) and (not declared or secured(self.spec, op)):
                n, status, _ = self.http("GET", path)
                if status in (401, 403):
                    return n, path
                if (tried := tried + 1) >= 5:
                    break
        raise DiscoveryError("auth", "no GET refuses an anonymous call",
                             "Which read operation needs credentials (e.g. GET /api/items)?")  # fmt: skip

    def login_body(self, schema: dict, depth: int = 0) -> dict | None:
        """A body for a schema that takes a password: the password, one user-ish field and the
        other required fields; else the first nested object that does (e.g. `credentials`)."""
        s = deref(self.spec, schema)
        props = {k: deref(self.spec, v) for k, v in (s.get("properties") or {}).items()}
        pw = [k for k, v in props.items() if "password" in k.lower() and v.get("type") == "string"]
        if pw:
            body = {pw[0]: {"secret": "admin_password"}}
            users = sorted((k for k, v in props.items() if USERISH.search(k)
                            and v.get("type") == "string" and not v.get("readOnly")),
                           key=lambda k: (k.lower() != "username", "user" not in k.lower()))  # fmt: skip
            if users:
                body[users[0]] = self.cfg["admin_user"]
            for k in s.get("required", []):
                if k not in body and not props.get(k, {}).get("readOnly"):
                    if (v := example(self.spec, props.get(k, {}), 1)) is None:
                        return None
                    body[k] = v
            return body if users else None
        if depth < 2:
            for k, v in props.items():
                if (v.get("type") == "object" or "properties" in v) and (
                    sub := self.login_body(v, depth + 1)
                ):
                    return {k: sub}
        return None

    def logins(self) -> list[tuple[str, dict, str, str]]:
        """POST operations without path parameters whose body takes a password and whose
        response has a string field: (path, body, encoding, layer), ranked by
        `signin_op_pattern` (or a setup answer)."""
        pats = [re.compile(p, re.IGNORECASE) for p in self.conventions("signin_op_pattern")]
        answer = self.answers.get("login", "").strip()
        out = []
        for m, path, op in ops(self.spec):
            if m != "POST" or PARAM.search(path):
                continue
            schema, enc = body_schema(self.spec, op)
            if not any(deref(self.spec, v).get("type") == "string"
                       for v in response_schema(self.spec, op).get("properties", {}).values()):  # fmt: skip
                continue
            if body := self.login_body(schema):
                hit = any(p.search(path + " " + op.get("operationId", "")) for p in pats)
                layer = "clarify" if path == answer else "convention" if hit else "algorithm"
                out.append((path, body, enc, layer))
        rank = {"clarify": 0, "convention": 1, "algorithm": 2}
        return sorted(out, key=lambda c: rank[c[3]])

    def prefixes(self) -> list[tuple[str, str]]:
        """Header prefixes to try: conventions, then the spec's own http schemes."""
        out = [(p, "convention") for p in self.conventions("auth_prefix")]
        for s in ((self.spec.get("components") or {}).get("securitySchemes") or {}).values():
            if s.get("type") == "http" and s.get("scheme", "").lower() not in ("basic", ""):
                out.append((s["scheme"].capitalize(), "algorithm"))
        return list(dict.fromkeys(out))

    def resolve_body(self, body):
        if isinstance(body, dict):
            if set(body) == {"secret"}:
                return secrets.get(self.slug, body["secret"], generate=True)
            return {k: self.resolve_body(v) for k, v in body.items()}
        return body

    def try_login(self, cand, check: str, prefixes) -> str | None:
        """-> "ok" (token verified), "created" (2xx without a usable token), or None."""
        path, body, enc, layer = cand
        sent = self.resolve_body(body)
        ln, status, resp = self.http("POST", path, **({"data": sent} if enc == "form" else
                                                      {"json_body": sent}))  # fmt: skip
        if not (200 <= status < 300 and isinstance(resp, dict)):
            return None
        tokens = [(f, t) for f, t in resp.items() if isinstance(t, str) and len(t) >= 16]
        if not prefixes and tokens:  # no convention, no spec scheme: ask exploration, once
            if not hasattr(self, "ai_prefixes"):
                self.ai_prefixes = [(p, "exploration") for p in ai.auth_prefix(
                    self.llm, self.cfg["model"], path, self.spec["paths"][path]["post"], list(resp))]  # fmt: skip
            prefixes = self.ai_prefixes
        for field, tok in tokens:
            for prefix, player in prefixes:
                vn, ok, _ = self.http("GET", check, headers={"Authorization": f"{prefix} {tok}"})
                if ok == 200:
                    if self.probes.replay is None:  # a replayed token is the placeholder
                        secrets.put(self.slug, "token", tok)  # so the probe log redacts it
                    login = {"path": path, "body": body, "token_field": field}
                    if enc == "json":
                        login["json"] = True
                    self.values["auth"] = {"strategy": "token_header", "scheme": prefix,
                                           "login": login, "check": check}  # fmt: skip
                    self.mark("auth", "login", "clarify" if layer == "clarify" else "probed",
                              layer, ln)  # fmt: skip
                    self.mark("auth", "scheme",
                              "ai_proposed+verified" if player == "exploration" else "probed",
                              player, vn)  # fmt: skip
                    self.verified_probe = vn
                    return "ok"
        return "created"

    def step_auth(self) -> None:
        cn, check = self.check_op()
        self.mark("auth", "check", "probed", "algorithm", cn)
        cands, prefixes = self.logins(), self.prefixes()
        bootstrap = None
        # A first-user creation is a 2xx login candidate without a token (D3): container reach
        # only, then the other candidates are tried again with the user it made.
        for _ in range(2):
            for c in cands:
                if c is bootstrap:
                    continue
                got = self.try_login(c, check, prefixes)
                if got == "ok":
                    return self._auth_done(bootstrap)
                if got == "created" and bootstrap is None:
                    bootstrap = (c, len(self.probes.log) - 1)
            if bootstrap is None:
                break
        schemes = (self.spec.get("components") or {}).get("securitySchemes") or {}
        user = self.cfg["admin_user"]
        if any(s.get("type") == "http" and s.get("scheme", "").lower() == "basic"
               for s in schemes.values()):  # fmt: skip
            pw = secrets.get(self.slug, "admin_password", generate=True)
            raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
            vn, ok, _ = self.http("GET", check, headers={"Authorization": f"Basic {raw}"})
            if ok == 200:
                self.values["auth"] = {"strategy": "basic", "user": user,
                                       "password": {"secret": "admin_password"}, "check": check}  # fmt: skip
                self.mark("auth", "scheme", "probed", "algorithm", vn)
                self.verified_probe = vn
                return self._auth_done(None)
        if self.admin is None and not self.answers.get("admin_env"):
            raise DiscoveryError(
                "auth", f"no login verified against {check}, and no admin env vars found",
                "Which env vars create the first admin at start-up? Answer `USER_VAR PASSWORD_VAR`"
                " (e.g. APP_ADMIN_USER APP_ADMIN_PASSWORD).", "admin_env")  # fmt: skip
        raise DiscoveryError(
            "auth", f"no login verified against {check} (admin env: {self.admin})",
            "How does an admin log in? Give the login operation's path (e.g. /api/token/).",
            "login")  # fmt: skip

    def _auth_done(self, bootstrap) -> None:
        if bootstrap:
            (path, body, enc, layer), n = bootstrap
            self.values["auth"]["bootstrap"] = {"path": path, "body": body} | (
                {"json": True} if enc == "json" else {})  # fmt: skip
            self.mark("auth", "bootstrap", "probed", layer, n)
            if self.admin:  # the env pair didn't make the admin: the bootstrap did
                for k in self.admin.values():
                    self.values["reach"]["env"].pop(k, None)
                self.prov["reach"].pop("env", None)
                self.admin = None
        elif "env" in self.prov.get("reach", {}):
            self.prov["reach"]["env"]["probe"] = self.verified_probe
        self.public = [f"POST {self.values['auth']['login']['path']}"] \
            if "login" in self.values["auth"] else []  # fmt: skip
        if bootstrap:
            self.public.append(f"POST {bootstrap[0][0]}")

    # ---- data --------------------------------------------------------------------------------

    def resources(self) -> list[tuple[str, str, dict]]:
        """(collection, item path, create op) with POST collection + GET and DELETE item;
        collections that can be paged (an integer query parameter on their list) first."""
        paths = self.spec.get("paths", {})
        out = []
        for coll, item in paths.items():
            if PARAM.search(coll) or "post" not in item:
                continue
            for p, ops_ in paths.items():
                rest = p[len(coll.rstrip("/")) :]
                if p.startswith(coll.rstrip("/") + "/") and re.fullmatch(r"/\{[^}]+\}/?", rest) \
                        and "get" in ops_ and "delete" in ops_:  # fmt: skip
                    out.append((coll, p, item["post"]))
                    break
        return sorted(out, key=lambda r: not self.query_params(r[0], "integer"))

    def query_params(self, coll: str, typ: str | None = None) -> dict:
        op = self.spec["paths"][coll].get("get") or {}
        out = {}
        for p in op.get("parameters", []):
            p = deref(self.spec, p)
            if p.get("in") == "query":
                s = deref(self.spec, p.get("schema"))
                if typ is None or s.get("type") == typ:
                    out[p.get("name")] = s
        return out

    def auth_headers(self) -> dict:
        a = self.values["auth"]
        if a["strategy"] == "basic":
            pw = secrets.get(self.slug, "admin_password")
            raw = base64.b64encode(f"{a['user']}:{pw}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        return {"Authorization": f"{a['scheme']} {secrets.load(self.slug).get('token', '')}"}

    def body_for(self, coll: str, schema: dict, n: int):
        if a := self.answers.get(f"body:{coll}"):
            try:
                return json.loads(a), "clarify"
            except ValueError:
                return None, "clarify"
        return example(self.spec, schema, n), "algorithm"

    def step_data(self) -> None:
        h = self.auth_headers()
        data = {"strategy": "spec_derived"}
        tried = []
        for coll, item, op in self.resources()[: self.cfg["max_resources"]]:
            schema, _ = body_schema(self.spec, op)
            body, blayer = self.body_for(coll, schema, 1)
            if body is None:
                tried.append(coll)
                continue
            _, status, created = self.http("POST", coll, headers=h, json_body=body)
            if not (200 <= status < 300 and isinstance(created, dict)):
                tried.append(coll)
                continue
            self.samples.append(fingerprint(status, created))
            found = self.id_field(item, created, h)
            if not found:
                tried.append(coll)
                continue
            field, style, transform, n = found
            data |= {"id_field": field, "id_style": style}
            self.mark("data", "id_field", "probed", "algorithm", n)
            if blayer == "clarify":
                self.mark("data", "body", "clarify", "clarify", n)
            first = self.path_id(created[field], transform)
            self.created.append(PARAM.sub(first, item, count=1))
            self.item = (item, first)
            body2 = body if blayer == "clarify" else example(self.spec, schema, 2)
            _, s2, second = self.http("POST", coll, headers=h, json_body=body2)
            if 200 <= s2 < 300 and isinstance(second, dict) and field in second:
                self.created.append(
                    PARAM.sub(self.path_id(second[field], transform), item, count=1)
                )
                paging = self.paging(coll, field, h)
            else:
                paging = None
            data["paging"] = paging[0] if paging else None
            if paging:
                self.mark("data", "paging", "probed", paging[1], paging[2])
            break
        else:
            coll = tried[0] if tried else "<none>"
            raise DiscoveryError(
                "data", f"no resource could be created and read back (tried {tried})",
                f"Give a valid JSON body for POST {coll} (it is created, read back and deleted).",
                f"body:{coll}")  # fmt: skip
        data["read_back_timeout_s"] = self.cfg["read_back_timeout_s"]
        self.mark("data", "read_back_timeout_s", "resolver", "algorithm")
        self.values["data"] = data

    @staticmethod
    def path_id(value, transform: str) -> str:
        return str(value).rsplit("/", 1)[-1] if transform == "suffix" else str(value)

    def id_field(self, item: str, created: dict, h: dict):
        """Each scalar of the create response as the item path parameter: verbatim, then (strings
        with `/`) its last segment (P2). -> (field, style, transform, probe) or None."""
        param = PARAM.search(item).group()[1:-1]
        cands = []
        for k, v in created.items():
            if isinstance(v, bool) or not isinstance(v, int | str) or v == "":
                continue
            cands.append((0, k != param, k, v, "verbatim"))
            if isinstance(v, str) and "/" in v:
                cands.append((1, k != param, k, v, "suffix"))
        for _, _, k, v, how in sorted(cands, key=lambda c: c[:2]):
            n, status, body = self.http("GET", PARAM.sub(self.path_id(v, how), item, count=1),
                                        headers=h, template=item)  # fmt: skip
            if status == 200:
                self.samples.append(fingerprint(status, body))
                return k, "integer" if isinstance(v, int) else "string", how, n
        return None

    def paging(self, coll: str, field: str, h: dict):
        """Page of size 1, then the next page must hold a different item (P3). Candidates are the
        list operation's own query parameters, ranked by `paging_param`. -> (paging, layer, probe)."""
        params = self.query_params(coll)
        if not params:
            return None
        ints = [k for k, s in params.items() if s.get("type") == "integer"]
        strs = [k for k, s in params.items() if s.get("type") == "string"]
        cands = [(c, "convention") for c in self.conventions("paging_param")
                 if all(c[k] in params for k in c if k != "style")]  # fmt: skip
        cands += [({"style": "page", "page": p, "size": s}, "algorithm")
                  for s in ints for p in ints if p != s][:4]  # fmt: skip
        cands += [({"style": "token", "size": s, "token": t}, "algorithm")
                  for s in ints for t in strs][:4]  # fmt: skip
        seen = []
        for pg, layer in cands:
            if pg in seen:
                continue
            seen.append(pg)
            first_q = {pg["size"]: 1} | ({pg["page"]: 1} if pg["style"] == "page" else {})
            n, s1, b1 = self.http("GET", coll, headers=h, params=first_q)
            one = items(b1)
            if s1 != 200 or len(one) != 1:
                continue
            if pg["style"] == "page":
                q2 = {pg["size"]: 1, pg["page"]: 2}
            else:
                tok = next((v for k, v in (b1 or {}).items() if isinstance(v, str) and v
                            and "token" in k.lower()), None)  # fmt: skip
                if not tok:
                    continue
                q2 = {pg["size"]: 1, pg["token"]: tok}
            _, s2, b2 = self.http("GET", coll, headers=h, params=q2)
            two = items(b2)
            if s2 == 200 and len(two) == 1 and two[0].get(field) != one[0].get(field):
                return pg, layer, n
        return None

    # ---- secured map, spec check, cleanup, dependencies ------------------------------------

    def step_secured(self) -> None:
        """Only when the spec declares no security (D9, P1): anonymous reads of every
        parameterless GET plus the created item; the majority sets the default."""
        if declares_security(self.spec):
            return
        reads = [(p, p, op) for m, p, op in ops(self.spec) if m == "GET" and not PARAM.search(p)]
        if hasattr(self, "item") and "get" in self.spec["paths"][self.item[0]]:
            tpl, first = self.item
            reads.append((tpl, PARAM.sub(first, tpl, count=1), self.spec["paths"][tpl]["get"]))
        refused, opened, probes = 0, [], []
        for tpl, path, op in reads:
            n, status, body = self.http("GET", path, template=tpl)
            probes.append(n)
            if status not in (401, 403):  # an auth gate answers 401 for any path, real or not:
                self.samples.append(fingerprint(status, body))  # it says nothing about the route
            if status in (401, 403):
                refused += 1
            elif 200 <= status < 300 and f"GET {tpl}" not in self.public:
                opened.append(f"GET {tpl}")
                self.observed_open.append({"op": f"GET {tpl}", "description": (
                    op.get("description") or op.get("summary") or "")[:300]})  # fmt: skip
        default = "secured" if refused * 2 > len(reads) else "open"
        self.values["auth"]["secured"] = {"default": default, "public": sorted(self.public),
                                          "observed_open": sorted(opened)}  # fmt: skip
        self.mark("auth", "secured", "probed", "algorithm", probes[0] if probes else None)

    def step_spec_check(self) -> None:
        """A repo spec is accepted when most real-route answers differ from a garbage path's
        fingerprint (P5); a served spec comes from the running code and needs no check. The
        samples are the authenticated answers plus anonymous ones that weren't refused: a
        refusal comes before routing, so it can't tell a real route from a missing one (006)."""
        if self.values["spec"]["strategy"] != "repo_file":
            return
        base = self.values["spec"]["base"]
        n, status, body = self.http(
            "GET", f"{base}/tp-no-such-route-0", headers=self.auth_headers()
        )
        garbage = fingerprint(status, body)
        real = [s for s in self.samples if s != garbage]
        if len(real) * 2 <= len(self.samples):
            raise DiscoveryError(
                "spec", f"{self.values['spec']['openapi']}: {len(real)}/{len(self.samples)} "
                f"answers differ from a missing route's", "Which OpenAPI file matches the "
                "running server? (the repo file found answers like missing routes)", "spec")  # fmt: skip
        self.prov["spec"]["openapi"]["probe"] = n

    def step_cleanup(self) -> None:
        h = self.auth_headers()
        for path in self.created:
            self.http("DELETE", path, headers=h)

    def step_dependencies(self) -> None:
        """`needs_dependency` (D11): an operation that answered 5xx during discovery while the
        container log names an env var the code reads (a string literal in tracked source) and
        nothing supplies. Written to `checks.families.exclude`; otherwise the op stays in."""
        self.exclude = []
        if not self.errors_5xx:
            return
        _, log = self.probes.call("docker", "logs", lambda: self.stack.logs()[-50_000:])
        names = set(re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b", log or ""))
        supplied = set(self.app_env) | set(self.values["reach"].get("env") or {})
        names -= supplied
        if not names:
            return
        r = subprocess.run(["git", "ls-files"], cwd=self.src, capture_output=True, text=True,
                           check=False)  # fmt: skip
        read = set()
        for f in r.stdout.splitlines():
            p = self.src / f
            if Path(f).suffix in ai.DOC_SUFFIXES or not p.is_file() or p.stat().st_size > 1_000_000:
                continue
            text = p.read_text(errors="replace")
            read |= {n for n in names if f'"{n}"' in text or f"'{n}'" in text}
        # shortcut: one log for the whole discovery, so every 5xx op is attributed to the first
        # such variable; upgrade to per-request log windows when a target has two dependencies
        for op, probe in dict(self.errors_5xx).items() if read else ():
            var = min(read)
            self.exclude.append({"op": op, "reason": f"needs_dependency: {var} is not set"})
            self.mark("checks", f"exclude {op}", "probed", "algorithm", probe)

    # ---- resolver rules (blackbox/001 D6) ------------------------------------------------------

    def step_resolver(self) -> None:
        self.values["state"] = {"strategy": "namespace"}
        self.values["mutation"] = {"strategy": "none", "reason": "black-box reach"}
        self.values["safety"] = {"strategy": "unrestricted"}
        fam = dict(self.cfg["families"]) | {"exclude": self.exclude}
        self.values["checks"] = {"strategy": "pytest_cases", "families": fam}
        for c in ("state", "mutation", "safety", "checks"):
            self.mark(c, "strategy", "resolver", "algorithm")

    # ---- the artifact --------------------------------------------------------------------------

    def profile(self) -> dict:
        out = {"schema_version": 1,
               "target": {"name": self.slug, "repo": self.url, "type": "api"},
               "commit_tested": self.commit}  # fmt: skip
        for c in profile.CONCERNS:
            prov = self.prov.get(c, {})
            weakest = min(prov.values(), key=lambda p: SOURCE_RANK.index(p["source"]))
            probes = sorted(p["probe"] for p in prov.values() if p["probe"] is not None)
            ev = (f"probe#{probes[0]}" + (f"-{probes[-1]}" if probes[-1] != probes[0] else "")
                  if probes else None)  # fmt: skip
            out[c] = {"source": weakest["source"], "layer": weakest["layer"], "evidence": ev,
                      "value": self.values[c], "provenance": prov}  # fmt: skip
        return out


def log_failure(slug: str, step: str, detail: str) -> None:
    """data/discovery/failures.jsonl; the class is set when a person reviews it (stage doc)."""
    f = db.data_dir() / "discovery" / "failures.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as fh:
        fh.write(json.dumps({"at": db.now(), "target": slug, "step": step, "detail": detail,
                             "class": None}) + "\n")  # fmt: skip
