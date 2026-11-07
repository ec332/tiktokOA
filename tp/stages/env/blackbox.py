"""Black-box targets (stage B, docs/status/blackbox/001 + 002): a repo's own compose file, rewritten
into a sandbox, reached only over HTTP. No target names here: everything comes from the profile.

- `rewrite`: the repo's compose -> <env dir>/compose.yml (D3): host-touching keys dropped, bind
  mounts moved under the env dir, an internal run network, limits, and an inbound-only forwarder
  so the host can reach the app (D5). Secrets stay `${TP_SECRET_*}` references (D9).
- `Stack`: check out at the ref, tag-matched images or per-commit cached builds (D4), up/down,
  probes, the runner (`images/runner-blackbox`) on the run network, ledger cleanup (D1).
- Strategies: reach container, spec served, auth basic/token_header, data spec_derived,
  state namespace, mutation none, checks pytest_cases.
"""

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import yaml

from tp.core import db, secrets
from tp.core.hashing import sha256
from tp.core.profile import TargetProfile

ROOT = Path(__file__).parents[3]
SANDBOX = ROOT / "config" / "sandbox.yaml"
RUN_NET, EDGE_NET, FORWARDER = "run", "edge", "tp-forwarder"
UP_TIMEOUT = 900  # seconds: first pull of a large image plus app start
RUN_TIMEOUT = 1800  # seconds for one runner pytest call


class BlackboxError(Exception):
    pass


# ---- compose rewrite (D3, D5) ---------------------------------------------------------------


def sandbox() -> dict:
    return yaml.safe_load(SANDBOX.read_text())


def env_file(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'\"")
    return out


def _env_dict(env) -> dict[str, str]:
    if isinstance(env, list):
        return dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
    return {k: "" if v is None else str(v) for k, v in (env or {}).items()}


def _value(v, refs: set[str]) -> str:
    """A profile env value: a literal (compose's `$` escaped) or `{secret: name}` -> a reference."""
    if isinstance(v, dict) and "secret" in v:
        refs.add(v["secret"])
        return "${" + secrets.env_name(v["secret"]) + "}"
    return str(v).replace("$", "$$")


def retag(image: str, tag: str) -> str:
    """`repo/name:latest` or untagged -> `repo/name:<tag>`; other tags are pinned already."""
    name, _, cur = image.rpartition(":") if ":" in image.rsplit("/", 1)[-1] else (image, "", "")
    return f"{name}:{tag}" if cur in ("", "latest") else image


def _bind(src: str, run_dir: Path) -> str:
    dest = run_dir / "binds" / re.sub(r"[^\w.-]+", "_", src.strip("./~")).strip("_")
    dest.mkdir(parents=True, exist_ok=True)
    return str(dest.resolve())  # a relative path would read as a named volume


def _volumes(vols: list, run_dir: Path) -> list:
    out = []
    for v in vols:
        if isinstance(v, dict):
            v = dict(v)
            if v.get("type") == "bind":
                v["source"] = _bind(v["source"], run_dir)
            out.append(v)
            continue
        src, _, rest = v.partition(":")
        out.append(f"{_bind(src, run_dir)}:{rest}" if src[:1] in "./~" and rest else v)
    return out


def rewrite(compose: dict, compose_dir: Path, run_dir: Path, reach: dict, images: dict[str, str],
            cfg: dict | None = None, repo: Path | None = None) -> tuple[dict, set[str]]:  # fmt: skip
    """-> (the sandboxed compose dict, secret names it references). Fails closed on any service
    key that is neither kept nor known to be safe to drop, on build keys that can reach the host,
    and on a build context outside `repo` (default: the compose file's folder)."""
    cfg = cfg or sandbox()
    root = (repo or compose_dir).resolve()
    keep, drop = set(cfg["keep"]), set(cfg["drop"])
    extra = set(compose) - {"services", "volumes", "networks", "name", "version"}
    if extra and not all(k.startswith("x-") for k in extra):
        raise BlackboxError(f"unsupported top-level compose keys: {sorted(extra)}")
    app, refs, services = reach["service"], set(), {}
    if app not in compose.get("services", {}):
        raise BlackboxError(f"app service {app!r} is not in the compose file")
    for name, svc in compose["services"].items():
        if bad := set(svc) - keep - drop - {"volumes", "env_file", "image", "build"}:
            raise BlackboxError(f"service {name}: unsupported keys {sorted(bad)} (sandbox)")
        out = {k: v for k, v in svc.items() if k in keep}
        env = {}
        files = svc.get("env_file", [])
        for f in [files] if isinstance(files, str | dict) else files:
            env |= env_file(compose_dir / (f["path"] if isinstance(f, dict) else f))
        env = {k: v.replace("$", "$$") for k, v in env.items()}
        env |= _env_dict(svc.get("environment"))
        if name == app:
            env |= {k: _value(v, refs) for k, v in (reach.get("env") or {}).items()}
        if env:
            out["environment"] = env
        if "build" in svc:
            b = svc["build"] if isinstance(svc["build"], dict) else {"context": svc["build"]}
            if bad := set(b) - set(cfg["build_keep"]):
                raise BlackboxError(
                    f"service {name}: unsupported build keys {sorted(bad)} (sandbox)"
                )
            ctx_dir = (compose_dir / b.get("context", ".")).resolve()
            if ctx_dir != root and root not in ctx_dir.parents:
                raise BlackboxError(f"service {name}: build context {ctx_dir} is outside the repo")
            out["build"] = b | {"context": str(ctx_dir)}
            out["image"] = images[name]  # the per-commit cache tag (D4)
        elif name in images:
            out["image"] = images[name]  # a tag-matched image the stack has already pulled
        elif reach.get("image_tag_from") == "ref":
            out["image"] = retag(svc["image"], reach["ref"])
        if "volumes" in svc:
            out["volumes"] = _volumes(svc["volumes"], run_dir)
        dep = out.get("depends_on")
        if isinstance(dep, dict):
            out["depends_on"] = list(dep)  # health conditions need the dropped `restart` loops
        out |= {"networks": [RUN_NET], "labels": {"tp.project": run_dir.name},
                "cpus": cfg["limits"]["cpus"], "mem_limit": cfg["limits"]["mem_limit"]}  # fmt: skip
        services[name] = out
    fwd = cfg["forwarder"]
    services[FORWARDER] = {
        "image": fwd["image"], "networks": [RUN_NET, EDGE_NET], "depends_on": [app],
        "command": [f"TCP-LISTEN:{fwd['port']},fork,reuseaddr", f"TCP:{app}:{reach['port']}"],
        "ports": [f"127.0.0.1::{fwd['port']}"],
    }  # fmt: skip
    volumes = {k: {} for k in compose.get("volumes") or {}}
    out = {"services": services, "networks": {RUN_NET: {"internal": True}, EDGE_NET: {}}}
    return out | ({"volumes": volumes} if volumes else {}), refs


# ---- the stack ------------------------------------------------------------------------------


def _parse(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        try:
            return yaml.safe_load(r.text)  # e.g. a spec served as YAML
        except yaml.YAMLError:
            return r.text[:2000]


class Stack:
    """One black-box target's compose project `tp_bb_<slug>` (reference variant only)."""

    def __init__(self, p: TargetProfile):
        self.p, self.r = p, p.reach.value
        self.project = "tp_bb_" + re.sub(r"[^a-z0-9]+", "_", p.slug.lower()).strip("_")
        self.dir = db.data_dir() / "env" / self.project
        self.file = self.dir / "compose.yml"

    # ---- checkout + compose file ----

    @property
    def src(self) -> Path:
        """The repo at the profile's ref, cloned once (shallow: only that ref is ever tested)."""
        path = db.data_dir() / "sut" / self.p.slug
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git(path.parent, "clone", "-q", "--depth", "1", "--branch", self.r["ref"],
                      self.p.repo, path.name)  # fmt: skip
        return path

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=cwd, check=False, capture_output=True, text=True)
        if r.returncode:
            raise BlackboxError(f"git {args[0]}: {r.stderr.strip()[-500:]}")
        return r.stdout.strip()

    def commit(self) -> str:
        return self._git(self.src, "rev-parse", "HEAD")

    def write(self) -> dict:
        path = self.src / self.r["compose"]
        raw = yaml.safe_load(path.read_text())
        tag = self.commit()[:12]
        images = {n: f"tp-{self.project.removeprefix('tp_bb_')}-{n}:{tag}"
                  for n, s in raw["services"].items() if "build" in s}  # fmt: skip
        if self.r.get("image_tag_from") == "ref":
            images |= {n: self.pull_matching(s["image"]) for n, s in raw["services"].items()
                       if "build" not in s and retag(s["image"], "x") != s["image"]}  # fmt: skip
        compose, self.refs = rewrite(raw, path.parent, self.dir, self.r, images, repo=self.src)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file.write_text(yaml.safe_dump(compose, sort_keys=True))
        return compose

    def pull_matching(self, image: str) -> str:
        """The image at the checked-out tag: `v1.2.3`, else `1.2.3` (registries often drop the
        `v`). Fails if neither exists: an untagged image would not be the code under test (D4)."""
        ref = self.r["ref"]
        for tag in dict.fromkeys([ref, ref.removeprefix("v")]):
            cand = retag(image, tag)
            pulled = subprocess.run(["docker", "pull", "-q", cand], check=False,
                                    capture_output=True, text=True, timeout=UP_TIMEOUT)  # fmt: skip
            if pulled.returncode == 0:
                return cand
        raise BlackboxError(f"no image {image} at tag {ref}: refusing to test a different build")

    def _secret_env(self) -> dict[str, str]:
        if not hasattr(self, "refs"):
            self.write()
        return {secrets.env_name(n): secrets.get(self.p.slug, n, generate=True)
                for n in sorted(self.refs)}  # fmt: skip

    def compose(self, *args: str, check: bool = True, timeout: float | None = 300):
        if not self.file.exists():
            self.write()
        cmd = ["docker", "compose", "-p", self.project, "-f", str(self.file), *args]
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout,
                           env=os.environ | self._secret_env())  # fmt: skip
        if check and r.returncode:
            raise BlackboxError(f"compose {' '.join(args[:2])}: {(r.stderr or r.stdout)[-800:]}")
        return r

    # ---- lifecycle ----

    def build(self) -> list[str]:
        """Build services with `build:` once per commit (D4); returns the services built."""
        compose = self.write()
        built = []
        for name, svc in compose["services"].items():
            if "build" in svc and subprocess.run(["docker", "image", "inspect", svc["image"]],
                                                 check=False, capture_output=True).returncode:  # fmt: skip
                self.compose("build", name, timeout=3600)
                built.append(name)
        return built

    def up(self) -> dict:
        self.down()  # every bring-up starts from nothing: a fresh database, so a fresh login
        if "login" in self.p.auth.value:
            secrets.forget(self.p.slug, self.p.auth.value.get("token", {}).get("secret", "token"))
        self.build()
        self.compose(
            "up", "-d", "--wait", "--wait-timeout", str(UP_TIMEOUT), timeout=UP_TIMEOUT + 60
        )
        self.wait_http(self.r.get("health") or self.p.spec.value["openapi"])
        probes = self.probe()
        if failed := [p["name"] for p in probes if not p["ok"]]:
            raise BlackboxError(f"probes failed after bring-up: {failed}: {probes}")
        fingerprint = self.fingerprint()
        return {"variant": "reference", "project": self.project, "flags": [],
                "spec_hash": sha256(fingerprint), "fingerprint": fingerprint, "probes": probes,
                "base_url": self.base_url, "compose_file": str(self.file),
                "reproduce": f"docker compose -p {self.project} -f {self.file} up -d --wait"}  # fmt: skip

    def down(self) -> None:
        self.compose("down", "-v", "--remove-orphans", "-t", "10", check=False, timeout=120)

    def ensure(self) -> None:
        if not all(p["ok"] for p in self.probe()):
            self.up()

    def logs(self) -> str:
        return self.compose("logs", "--no-color", "--no-log-prefix", self.r["service"],
                            check=False).stdout  # fmt: skip

    @property
    def base_url(self) -> str:
        port = str(sandbox()["forwarder"]["port"])
        out = self.compose("port", FORWARDER, port, check=False).stdout.strip()
        if not out:
            raise BlackboxError(f"{self.project} is not up: no forwarder port")
        return f"http://{out.replace('0.0.0.0', '127.0.0.1')}"

    client: httpx.Client | None = None  # a test's scripted transport; else the forwarder

    def http(self, method: str, path: str, **kw) -> tuple[int, object]:
        if self.client is not None:
            r = self.client.request(method, path, **kw)
        else:
            r = httpx.request(method, self.base_url + path, timeout=30, **kw)
        return r.status_code, _parse(r)

    def wait_http(self, path: str, timeout: float = UP_TIMEOUT) -> None:
        t0, last = time.monotonic(), None
        while time.monotonic() - t0 < timeout:
            try:
                status, _ = self.http("GET", path)
                if status == 200:
                    return
                last = status
            except (httpx.HTTPError, BlackboxError) as e:
                last = type(e).__name__
            time.sleep(2)
        raise BlackboxError(f"GET {path} never returned 200 (last: {last})")

    def probe(self) -> list[dict]:
        """health: the health path answers 200; auth: the check path needs the principal and
        accepts it (D7). Never raises."""

        def health():
            status, _ = self.http("GET", self.r.get("health") or self.p.spec.value["openapi"])
            return status == 200, status

        def auth():
            check = self.p.auth.value["check"]
            anon, _ = self.http("GET", check)
            who = make_auth(self.p, self).principal("admin")
            ok, _ = self.http("GET", check, headers=header(who))
            return anon in (401, 403) and ok == 200, f"anonymous {anon}, admin {ok}"

        out = []
        for name, fn in (("health", health), ("auth", auth)):
            try:
                ok, raw = fn()
            except Exception as e:  # noqa: BLE001 — a probe reports failure, it never crashes
                ok, raw = False, f"{type(e).__name__}: {e}"
            out.append({"name": name, "ok": bool(ok), "raw": str(raw)})
        return out

    def fingerprint(self) -> dict:
        compose = yaml.safe_load(self.file.read_text())
        images = {}
        for n, s in sorted(compose["services"].items()):
            r = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", s["image"]],
                               check=False, capture_output=True, text=True)  # fmt: skip
            images[n] = r.stdout.strip()
        text = self.file.read_text().replace(str(self.dir), "<env>")
        built = sorted(n for n, svc in compose["services"].items() if "build" in svc)
        network = {
            "run": "none: an internal network, reached inbound through a forwarder",
            "build": f"unrestricted outbound while building {', '.join(built)}"
            if built
            else "no build: images pulled at the tested tag",
        }  # fmt: skip (D5)
        return {"compose": sha256(text), "images": images, "commit": self.commit(),
                "ref": self.r["ref"], "network": network}  # fmt: skip

    # ---- the runner ----

    def openapi(self) -> dict:
        cached = self.dir / "openapi.json"
        status, body = self.http("GET", self.p.spec.value["openapi"])
        if status != 200 or not isinstance(body, dict):
            raise BlackboxError(f"spec {self.p.spec.value['openapi']}: {status}")
        cached.write_text(json.dumps(body))
        return body

    def pytest(self, tests: Path, out: Path, variant: str = "reference",
               env: dict[str, str] | None = None, args: tuple[str, ...] = (),
               **_ignored) -> dict:  # fmt: skip
        """pytest in the black-box runner, on the run network only (no internet, D5). Secrets go
        in as env vars (D9); creates are logged to out/ledger.jsonl and deleted afterwards (D1)."""
        from tp.stages.generate import image

        runner = image.published_blackbox()["digest"]
        out.mkdir(parents=True, exist_ok=True)
        for f in ("results.jsonl", "junit.xml", "ledger.jsonl"):
            (out / f).unlink(missing_ok=True)
        who = make_auth(self.p, self).principal("admin")
        run_env = {
            "TP_MODE": "blackbox", "TP_RESULTS": "/work/out/results.jsonl",
            "TP_LEDGER": "/work/out/ledger.jsonl",
            "TP_BASE_URL": f"http://{self.r['service']}:{self.r['port']}",
            "TP_API_BASE": self.p.spec.value.get("base", ""),
            "TP_OPENAPI_PATH": self.p.spec.value["openapi"],
            "TP_AUTH_SCHEME": who["scheme"],
            "TP_READ_BACK_TIMEOUT": str(read_back := self.p.data.value.get("read_back_timeout_s", 5)),
            "TP_TIMEOUT": str(max(60, int(read_back) + 60)),  # a read-back must fit in one test
            "TP_ID_FIELD": self.p.data.value.get("id_field", "id"),
            "TP_FAMILIES": json.dumps(families_config(self.p)),
            "TP_RATE": str(families_config(self.p)["rate"]),
            **(env or {}),
        }  # fmt: skip
        secret_env = {"TP_SECRET_TOKEN": who["token"]}
        cmd = ["docker", "run", "--rm", "--network", f"{self.project}_{RUN_NET}",
               "-v", f"{tests.resolve()}:/work/tests:ro", "-v", f"{out.resolve()}:/work/out"]  # fmt: skip
        cmd += [a for k, v in sorted(run_env.items()) for a in ("-e", f"{k}={v}")]
        cmd += [a for k in sorted(secret_env) for a in ("-e", k)]  # value from our env, not argv
        cmd += [runner, "python", "-m", "pytest", "-p", "platform_rt.blackbox", "-p",
                "no:cacheprovider", "-q", "--continue-on-collection-errors",
                "--rootdir=/work/tests", "--junitxml=/work/out/junit.xml", "/work/tests", *args]  # fmt: skip
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=RUN_TIMEOUT,
                           env=os.environ | secret_env)  # fmt: skip
        log = secrets.redact((r.stdout + r.stderr)[-4000:])
        results = [json.loads(ln) for ln in (out / "results.jsonl").read_text().splitlines()] \
            if (out / "results.jsonl").exists() else []  # fmt: skip
        cleanup = self.cleanup(out / "ledger.jsonl")
        (out / "cleanup.json").write_text(json.dumps(cleanup, indent=1))
        if r.returncode not in (0, 1) and not results:
            raise BlackboxError(f"runner pytest exited {r.returncode}: {log[-1500:]}")
        return {"returncode": r.returncode, "results": results, "junit": str(out / "junit.xml"),
                "output": log, "cleanup": {k: len(v) for k, v in cleanup.items()}}  # fmt: skip

    def cleanup(self, ledger: Path) -> dict[str, list]:
        """Delete what the tests created, newest first, best effort (D1). Leftovers are listed,
        never fatal; a container target's teardown removes them anyway."""
        out: dict[str, list] = {"deleted": [], "failed": [], "unknown_delete_route": [],
                                "deleted_by_test": []}  # fmt: skip
        if not ledger.exists():
            return out
        cached = self.dir / "openapi.json"
        spec = json.loads(cached.read_text()) if cached.exists() else self.openapi()
        who = header(make_auth(self.p, self).principal("admin"))
        entries = [json.loads(ln) for ln in ledger.read_text().splitlines() if ln]
        gone = [e for e in entries if e.pop("deleted_by_test", False)]  # marker lines, now
        # equal to the entries they mark, so the filter below drops both
        out["deleted_by_test"] = gone
        for e in reversed([e for e in entries if e not in gone]):
            target = delete_path(spec, e)  # spec paths are full paths, as in the ledger
            if target is None:
                out["unknown_delete_route"].append(e)
                continue
            try:
                status, _ = self.http("DELETE", target, headers=who)
            except httpx.HTTPError as err:
                status = type(err).__name__
            (out["deleted"] if status in (200, 202, 204, 404) else out["failed"]).append(
                e | {"status": status}
            )
        return out


def families_config(p: TargetProfile) -> dict:
    """$TP_FAMILIES for platform_rt.families (checks/001): the data conventions (ID field and
    style, paging) from `data`, the family knobs (per_family, examples, rate, exclude) from
    `checks.families`."""
    d = p.data.value
    return {"id_field": d.get("id_field", "id"), "id_style": d.get("id_style", "integer"),
            "paging": d.get("paging"), "rate": 5, "per_family": 5, "examples": 10, "exclude": []
            } | (p.checks.value.get("families") or {})  # fmt: skip


def _matches(template: str, path: str) -> bool:
    """`/api/tags/{id}/` matches `/api/tags/7/` (a parameter is one non-empty segment)."""
    want, got = template.split("/"), path.split("/")
    return len(want) == len(got) and all(
        w == g or (w[:1] == "{" and w[-1:] == "}" and g) for w, g in zip(want, got, strict=True)
    )


def delete_path(spec: dict, entry: dict) -> str | None:
    """The DELETE for a created resource: its Location, or `<create path><id>[/]`, if the spec
    has a DELETE operation of that shape."""
    paths = [p for p, ops in spec.get("paths", {}).items() if "delete" in ops]
    cands = []
    if loc := entry.get("location"):
        cands.append(httpx.URL(loc).path)
    if entry.get("id") is not None:
        stem = entry["path"].rstrip("/") + f"/{str(entry['id']).split('/')[-1]}"  # names: last part
        cands += [stem + "/", stem]
    return next((c for c in cands for p in paths if _matches(p, c)), None)


# ---- auth (D7) --------------------------------------------------------------------------------


def header(principal: dict) -> dict:
    return {"Authorization": f"{principal['scheme']} {principal['token']}"}


def _resolve(slug: str, v):
    return secrets.get(slug, v["secret"], generate=True) if isinstance(v, dict) else v


class BasicAuth:
    """`Authorization: Basic base64(user:password)`; the encoded pair is a secret too (D9)."""

    def __init__(self, p: TargetProfile, stack: Stack | None = None):
        self.p, self.v = p, p.auth.value

    def principal(self, role: str = "admin", variant: str = "reference") -> dict:
        user, pw = (_resolve(self.p.slug, self.v[k]) for k in ("user", "password"))
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        secrets.put(self.p.slug, "basic", token)
        return {"scheme": "Basic", "token": token}


class TokenHeaderAuth:
    """`Authorization: <scheme> <token>`: a supplied token secret, or one fetched once from the
    profile's login route (`login: {path, body, token_field}`) and kept in the secret store."""

    def __init__(self, p: TargetProfile, stack: Stack | None = None):
        self.p, self.v, self.stack = p, p.auth.value, stack

    def principal(self, role: str = "admin", variant: str = "reference") -> dict:
        scheme, slug = self.v.get("scheme", "Bearer"), self.p.slug
        name = self.v.get("token", {}).get("secret", "token")
        if (token := secrets.load(slug).get(name)) is None:
            login = self.v["login"]
            body = {k: _resolve(slug, v) for k, v in login["body"].items()}
            status, resp = (self.stack or Stack(self.p)).http("POST", login["path"], data=body)
            if status != 200 or not isinstance(resp, dict) or login["token_field"] not in resp:
                raise BlackboxError(f"login {login['path']}: {status}")
            token = secrets.put(slug, name, str(resp[login["token_field"]]))
        return {"scheme": scheme, "token": token}


AUTH = {"basic": BasicAuth, "token_header": TokenHeaderAuth}


def make_auth(p: TargetProfile, stack: Stack | None = None):
    return AUTH[p.auth.strategy](p, stack)


# ---- the strategies ---------------------------------------------------------------------------


class Reach:
    def __init__(self, stack: Stack):
        self.stack = stack

    def provision(self, variant: str = "reference", expect: str | None = None) -> dict:
        return self.stack.up()

    def ensure(self, variant: str = "reference") -> None:
        self.stack.ensure()

    def probe(self, variant: str = "reference") -> list[dict]:
        return self.stack.probe()

    def guard(self, variant: str = "reference") -> None:
        if bad := [p for p in self.stack.probe() if not p["ok"]]:
            from tp.stages.env.service import EnvDown

            raise EnvDown("; ".join(f"{p['name']}: {p['raw'][:200]}" for p in bad))

    def teardown(self, variant: str = "reference") -> None:
        self.stack.down()

    def logs(self, variant: str = "reference") -> str:
        return self.stack.logs()

    def image(self, variant: str = "reference") -> str | None:
        return None  # the runner is the generic black-box image

    def exec(self, cmd: list[str], **kw) -> str | None:
        return None  # the exec allow-list is stage D (D7)


class Spec:
    def __init__(self, stack: Stack):
        self.stack = stack

    def openapi(self, variant: str = "reference") -> dict:
        return self.stack.openapi()


class SpecDerivedData:
    """Test data comes from the spec through the runner's `make` (platform_rt.blackbox)."""

    def __init__(self, stack: Stack):
        self.stack = stack

    def make(self, tc_id: str, variant: str = "reference") -> dict:
        return {"read_back_timeout_s": self.stack.p.data.value.get("read_back_timeout_s", 5)}

    def factories(self) -> list[str]:
        return ["admin"]  # the one principal the platform provisions; data comes from the spec

    def doc(self) -> dict[str, str]:
        return {"make": MAKE_DOC, "api": API_DOC, "imports": ["eventually"]}


MAKE_DOC = (
    "- `make`: `make.admin()` → `{token}`, the administrator (the only user the platform "
    "provides). Send every request as the admin (`token=make.admin()[\"token\"]`) unless the "
    "case says the caller is anonymous. `make.name(label)` → a name unique to this test, e.g. `make.name(\"tag\")`: use "
    "it for every name or title you create, because other tests share the same data. When you "
    "check a list, filter it to your own names and never count items you didn't create. "
    "`make.pdf(text)` → the bytes of a one-page PDF, for upload endpoints.\n"
    "- `eventually(fn, until)`: for a read-back after a write the system processes in the "
    "background, e.g. `eventually(lambda: api.get(path, token=t), lambda r: r.json()[\"count\"] "
    "== 1)`. It calls `fn` again until `until(result)` holds (or a timeout passes) and returns "
    "the last response. Use it only for read-backs.")  # fmt: skip
API_DOC = (
    "- `api.get/post/put/patch/delete(path, token=None, params=None, json=None, data=None, "
    "files=None)` → an `httpx.Response`. Paths are the full OpenAPI paths. `data`/`files` send "
    "a form or multipart upload, e.g. `files={\"document\": (\"a.pdf\", make.pdf(\"x\"), "
    "\"application/pdf\")}`. Pass the caller's token (e.g. `token=admin[\"token\"]`); "
    "`token=None` sends no credentials and is only for an anonymous caller "
    "(`REQUEST[case_id][\"auth\"]` is false).")  # fmt: skip


class NamespaceState:
    def __init__(self, stack: Stack):
        self.stack = stack

    def reset(self, variant: str = "reference") -> float:
        return 0.0  # isolation is by unique names (D1); nothing to reset


class NoMutation:
    def __init__(self, stack: Stack):
        self.stack = stack

    def available(self) -> bool:
        return False

    def flags(self) -> list[str]:
        return []

    def scope(self) -> dict:
        return {"mutate": [], "src": None, "changed": {}}

    def harness_files(self) -> dict[str, Path]:
        return {}

    def seeded(self) -> dict:
        return {}


class Checks:
    def __init__(self, stack: Stack):
        self.stack = stack

    def run(self, tests: Path, out: Path, variant: str = "reference", **kw) -> dict:
        return self.stack.pytest(tests, out, variant, **kw)


IMPLS = {("reach", "container"): Reach, ("spec", "served"): Spec,
         ("data", "spec_derived"): SpecDerivedData, ("state", "namespace"): NamespaceState,
         ("mutation", "none"): NoMutation, ("checks", "pytest_cases"): Checks}  # fmt: skip


_STACKS: dict[str, Stack] = {}  # one per profile slug per process, shared by its strategies


def strategy(concern: str, name: str, p: TargetProfile):
    stack = _STACKS.setdefault(p.slug, Stack(p))
    if concern == "auth":
        return make_auth(p, stack)
    return IMPLS[(concern, name)](stack)
