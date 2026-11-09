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
import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import yaml

from tp.core import db, secrets
from tp.core.hashing import sha256
from tp.core.profile import TargetProfile
from tp.stages.discover import spec_base

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


def _discovery_platform() -> dict:
    """The platform rules in config/discovery.yaml (the secret-name pattern is shared, P6e)."""
    return yaml.safe_load((ROOT / "config" / "discovery.yaml").read_text())["platform"]


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


FLOATING = ("", "latest", "stable", "main", "master", "nightly", "edge", "canary")  # P6f


def retag(image: str, tag: str) -> str:
    """`repo/name:<floating tag>` or untagged -> `repo/name:<tag>`; other tags are pinned."""
    name, _, cur = image.rpartition(":") if ":" in image.rsplit("/", 1)[-1] else (image, "", "")
    return f"{name}:{tag}" if cur in FLOATING else image


class MissingValues(Exception):
    """Compose variables (or an env_file) with no value anywhere: a `setup` question (reach/001 D8).
    Not a BlackboxError, so a bring-up attempt can't swallow it as a failed candidate."""

    def __init__(self, names: list[str]):
        super().__init__(
            f"no value for {', '.join(names)}: supply one (it goes in the run's secrets)"
        )
        self.names = names


EXAMPLES = (".env.example", ".env.sample")
# ${X}, ${X:-d}, ${X?msg}, $X; `$$` is compose's escape (reach/001 D8)
VAR = re.compile(r"(?<!\$)\$(?:\{([A-Za-z_]\w*)(:?[-?+])?[^}]*\}|([A-Za-z_]\w*))")
RANK = ["required", "bare", "default"]


def compose_vars(text: str) -> dict[str, str]:
    """name -> required (`?`), bare (no value or default) or default (`-`/`+`)."""
    out: dict[str, str] = {}
    for m in VAR.finditer(text):
        op = m[2] or ""
        kind = "required" if "?" in op else "default" if op else "bare"
        name = m[1] or m[3]
        out[name] = min(out.get(name, kind), kind, key=RANK.index)
    return out


def _inside(path: Path, root: Path, what: str) -> Path:
    """`path` resolved (symlinks followed) must stay inside `root` (H2)."""
    if "$" in str(path):
        raise BlackboxError(f"{what} {path}: `$` in a path is refused (H2)")
    p = path.resolve()
    if p != root and root not in p.parents:
        raise BlackboxError(f"{what} {p} is outside the repo")
    return p


def _env_file(f, compose_dir: Path, root: Path) -> dict[str, str]:
    """One `env_file` entry, read from inside the checkout; a missing one falls back to the
    folder's .env.example/.env.sample, else it's a setup question (reach/001 D8)."""
    name, required = (f["path"], f.get("required", True)) if isinstance(f, dict) else (f, True)
    path = _inside(compose_dir / name, root, "env_file")
    if path.is_file():
        return env_file(path)
    for ex in EXAMPLES:
        if (alt := path.parent / ex).is_file():
            return env_file(_inside(alt, root, "env_file"))
    if required:
        raise MissingValues([f"env_file {name}"])
    return {}


def _source(v, compose_dir: Path) -> Path | None:
    """A relative bind source, resolved; None for named volumes and absolute or `~` sources."""
    src = (v.get("source", "") if v.get("type") == "bind" else "") if isinstance(v, dict) else (
        v.partition(":")[0] if ":" in v else "")  # fmt: skip
    return (compose_dir / src).resolve() if src[:1] == "." else None


def _bind(src: str, run_dir: Path) -> str:
    dest = run_dir / "binds" / re.sub(r"[^\w.-]+", "_", src.strip("./~")).strip("_")
    dest.mkdir(parents=True, exist_ok=True)
    return str(dest.resolve())  # a relative path would read as a named volume


def _volumes(vols: list, run_dir: Path) -> list:
    out = []
    for v in vols:
        if "$" in str(v.get("source", "") if isinstance(v, dict) else v.partition(":")[0]):
            raise BlackboxError(f"volume {v}: `$` in a volume source is refused (H2)")
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
            env |= _env_file(f, compose_dir, root)
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
            ctx_dir = _inside(
                compose_dir / b.get("context", "."), root, f"service {name}: build context"
            )
            if "dockerfile" in b:
                _inside(ctx_dir / b["dockerfile"], root, f"service {name}: dockerfile")
            out["build"] = b | {"context": str(ctx_dir)}
            out["image"] = images[name]  # the per-commit cache tag (D4)
        elif name in images:
            out["image"] = images[name]  # a tag-matched image the stack has already pulled
        elif reach.get("image_tag_from") == "ref":
            out["image"] = retag(svc["image"], reach["ref"])
        if "volumes" in svc:
            vols = svc["volumes"]
            if "build" in svc:  # a dev mount of the build context would hide the built image
                vols = [v for v in vols if _source(v, compose_dir) != ctx_dir]
            out["volumes"] = _volumes(vols, run_dir)
        dep = out.get("depends_on")
        if isinstance(dep, dict):
            out["depends_on"] = list(dep)  # health conditions need the dropped `restart` loops
        out |= {"networks": [RUN_NET], "labels": {"tp.project": run_dir.name},
                "cpus": cfg["limits"]["cpus"], "mem_limit": cfg["limits"]["mem_limit"]}  # fmt: skip
        services[name] = out
    fwd = cfg["forwarder"]
    for s in reach.get("standins") or []:  # C8: on the run network, started before the app
        sname = f"tp-standin-{s['name']}"
        services[sname] = {"image": images.get(sname, s["image"]), "networks": [RUN_NET],
                           "labels": {"tp.project": run_dir.name},
                           "cpus": cfg["limits"]["cpus"], "mem_limit": cfg["limits"]["mem_limit"]}  # fmt: skip
        if s.get("command"):  # standins/001 D3, request R3
            services[sname]["command"] = s["command"]
        if s.get("port"):  # the host reads its journal through this (standins/002 request R4)
            services[f"{FORWARDER}-{s['name']}"] = {
                "image": fwd["image"], "networks": [RUN_NET, EDGE_NET], "depends_on": [sname],
                "command": [f"TCP-LISTEN:{fwd['port']},fork,reuseaddr", f"TCP:{sname}:{s['port']}"],
                "ports": [f"127.0.0.1::{fwd['port']}"]}  # fmt: skip
        a = services[app]
        a["depends_on"] = [*a.get("depends_on", []), sname]
        a["environment"] = a.get("environment", {}) | {
            k: _value(v, refs) for k, v in (s.get("env") or {}).items()}  # fmt: skip
    services[FORWARDER] = {
        "image": fwd["image"], "networks": [RUN_NET, EDGE_NET], "depends_on": [app],
        "command": [f"TCP-LISTEN:{fwd['port']},fork,reuseaddr", f"TCP:{app}:{reach['port']}"],
        "ports": [f"127.0.0.1::{fwd['port']}"],
    }  # fmt: skip
    volumes = {k: {} for k in compose.get("volumes") or {}}
    out = {"services": services, "networks": {RUN_NET: {"internal": True}, EDGE_NET: {}}}
    return out | ({"volumes": volumes} if volumes else {}), refs


def _dig(resp, field: str):
    """A dotted token field (`user.token`) in a login answer; None when it isn't there."""
    for k in field.split("."):
        resp = resp.get(k) if isinstance(resp, dict) else None
    return resp


def _same(svc: dict, built: bool) -> dict:
    out = {k: v for k, v in svc.items() if k != "labels" and not (built and k == "image")}
    if built:
        out["build"] = {k: v for k, v in svc["build"].items() if k != "context"}
    return out


def _runner_image() -> str:
    """`image: runner` (config/dependencies.yaml): the black-box runner with this platform_rt."""
    from tp.stages.generate import image  # lazy: generate builds and publishes it

    try:
        pub = image.published_blackbox()
    except image.ImageError:
        pub = {}
    return (pub if pub.get("platform_rt") == image.rt_version() else image.build_blackbox())[
        "image"
    ]


# ---- the stack ------------------------------------------------------------------------------


def _parse(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        try:
            return yaml.safe_load(r.text)  # e.g. a spec served as YAML
        except yaml.YAMLError:
            return r.text[:2000]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


class Stack:
    """One black-box target's compose project: `tp_bb_<slug>` for the reference, and
    `tp_bb_<slug>__<variant>` for each variant, built from a patched copy (reach/001 D5-D7)."""

    def __init__(self, p: TargetProfile, variant: str = "reference"):
        self.p, self.r, self.variant_name = p, p.reach.value, variant
        self.project = "tp_bb_" + _slug(p.slug)
        if variant != "reference":
            self.project += "__" + _slug(variant)[:40]
        self.dir = db.data_dir() / "env" / self.project
        self.file = self.dir / "compose.yml"
        self.patches = self._patches(variant)
        self._variants: dict[str, Stack] = {}

    def _patches(self, variant: str) -> dict[str, Path]:
        """`bug:A+B` (seeds) and `fixed:<patch>` -> patch files: the profile's patches/ folder,
        or a `sha256:` patch artifact (the template's lookup, env/service.fix_patch)."""
        kind, _, rest = variant.partition(":")
        if variant == "reference":
            return {}
        if kind not in ("bug", "fixed") or not rest:
            raise BlackboxError(
                f"bad variant {variant!r}: use reference, bug:SEED[+SEED] or fixed:<patch>"
            )
        out = {}
        for name in sorted(set(rest.split("+") if kind == "bug" else [rest])):
            if name.startswith("sha256:"):
                from tp.stages.env.service import fix_patch

                out[name] = fix_patch(Path("."), name)
            elif self.p.dir and (f := self.p.dir / "patches" / f"{name}.patch").is_file():
                out[name] = f.resolve()
            else:
                raise BlackboxError(f"bad variant {variant!r}: no patches/{name}.patch")
        return out

    def variant(self, v: str) -> Stack:
        if v == "reference":
            return self
        if v not in self._variants:
            self._variants[v] = type(self)(self.p, v)
        return self._variants[v]

    @property
    def token_secret(self) -> str:
        """A variant has its own database, so its own login token."""
        name = self.p.auth.value.get("token", {}).get("secret", "token")
        return name if self.variant_name == "reference" else f"{name}@{self.variant_name}"

    def tree(self) -> Path:
        """The source to build: the clone, or (a variant) `git archive HEAD` plus its patches in
        the env dir. The clone is never written (reach/001 D5)."""
        if not self.patches:
            return self.src
        out = self.dir / "src"
        if out.exists():
            return out
        tmp = self.dir / "src.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        tar = subprocess.run(["git", "-C", str(self.src), "archive", "HEAD"], check=True,
                             capture_output=True).stdout  # fmt: skip
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=tar, check=True)
        self._git(tmp, "init", "-q")  # else `git apply` resolves paths against an enclosing repo
        for name, patch in self.patches.items():
            r = subprocess.run(["git", "apply", str(patch)], cwd=tmp, check=False,
                               capture_output=True, text=True)  # fmt: skip
            if r.returncode:
                raise BlackboxError(
                    f"patch {name} doesn't apply at {self.commit()[:12]}: {r.stderr[-300:]}"
                )
        shutil.rmtree(tmp / ".git")
        tmp.rename(out)
        return out

    def patch_hashes(self) -> dict[str, str]:
        return {n: sha256(f.read_text()) for n, f in self.patches.items()}

    # ---- checkout + compose file ----

    @property
    def src(self) -> Path:
        """The repo at the profile's ref, cloned once (shallow: only that ref is ever tested)."""
        path = db.data_dir() / "sut" / self.p.slug
        ref = self.r["ref"]
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            commit = re.fullmatch(r"[0-9a-f]{40}", ref)  # `--branch` takes no sha: fetched below
            self._git(path.parent, "clone", "-q", "--depth", "1",
                      *([] if commit else ["--branch", ref]), self.p.repo, path.name)  # fmt: skip
        if self._at(path) != ref:  # another ref's clone (discover/006), or a pinned commit (C9)
            if self._git(path, "status", "--porcelain"):
                raise BlackboxError(f"{path} has local changes; it can't be moved to {ref}")
            tag = self._remote_tag(path, ref)
            spec = f"+refs/tags/{ref}:refs/tags/{ref}" if tag else ref
            self._git(path, "fetch", "-q", "--depth", "1", "origin", spec)
            self._git(path, "checkout", "-q", "--detach", ref if tag else "FETCH_HEAD")
            if self._at(path) != ref:
                raise BlackboxError(f"{path}: checked out {ref} but HEAD doesn't match it")
        return path

    def _at(self, path: Path) -> str | None:
        """`ref` when HEAD is that tag, branch tip or commit; else None."""
        ref = self.r["ref"]
        head = self._git(path, "rev-parse", "HEAD")
        r = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"], cwd=path,
                           check=False, capture_output=True, text=True)  # fmt: skip
        hit = r.returncode == 0 and r.stdout.strip() == head
        return ref if hit or head.startswith(ref) else None

    def _remote_tag(self, path: Path, ref: str) -> bool:
        r = subprocess.run(["git", "ls-remote", "--tags", "origin", f"refs/tags/{ref}"], cwd=path,
                           check=False, capture_output=True, text=True, timeout=60)  # fmt: skip
        return bool(r.stdout.strip())

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=cwd, check=False, capture_output=True, text=True)
        if r.returncode:
            raise BlackboxError(f"git {args[0]}: {r.stderr.strip()[-500:]}")
        return r.stdout.strip()

    def commit(self) -> str:
        return self._git(self.src, "rev-parse", "HEAD")

    def raw(self, tree: Path) -> tuple[dict, Path]:
        """The repo's compose and its folder; Dockerfile reach (`reach.build`, C5) is a generated
        one-service compose through the same rewrite (reach/001 D4)."""
        if b := self.r.get("build"):
            svc = {"build": {"context": b["context"], "dockerfile": b["dockerfile"]},
                   "expose": [b["port"]]}  # fmt: skip
            return {"services": {self.r["service"]: svc}}, tree
        path = tree / self.r["compose"]
        return yaml.safe_load(path.read_text()), path.parent

    def write(self) -> dict:
        if self.patches and not any(
            "build" in s for s in self.raw(self.src)[0]["services"].values()
        ):
            raise BlackboxError(f"{self.variant_name}: {self.p.slug} is image-only, so a variant "
                                "can't be built; only targets built from source take one (DR-9)")  # fmt: skip
        tree = self.tree()  # after the refusal: an image-only target is never patched
        raw, compose_dir = self.raw(tree)
        built = [n for n, s in raw["services"].items() if "build" in s]
        tag = self.commit()[:12]
        if self.patches:  # a variant is a different build (reach/001 D3)
            tag += "-" + sha256(self.patch_hashes())[:8]
        base = self.project.removeprefix("tp_bb_").split("__")[0]
        images = {n: f"tp-{base}-{n}:{tag}" for n in built}
        if self.r.get("image_tag_from") == "ref":
            images |= {n: self.pull_matching(s["image"]) for n, s in raw["services"].items()
                       if "build" not in s and retag(s["image"], "x") != s["image"]}  # fmt: skip
        images |= {f"tp-standin-{st['name']}": _runner_image()
                   for st in self.r.get("standins") or [] if st.get("image") == "runner"}  # fmt: skip
        compose, self.refs = rewrite(raw, compose_dir, self.dir, self.r, images, repo=tree)
        self.dir.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(compose, sort_keys=True)
        self.values = self._values(text, compose_dir, tree)
        self.file.write_text(text)
        return compose

    def _values(self, text: str, compose_dir: Path, tree: Path) -> dict[str, str]:
        """Compose variables with no value (reach/001 D8), passed as compose's process env, never
        written to a file: answers and generated secrets from the slug's secret store, else the
        repo's .env.example/.env.sample. A required one with none raises MissingValues; a bare one
        with none stays empty, as compose has it."""
        example = next((f for d in (compose_dir, tree) for ex in EXAMPLES
                        if (f := d / ex).is_file()), None)  # fmt: skip
        known = env_file(_inside(example, tree, "env example")) if example else {}
        rx = re.compile(_discovery_platform()["secret_name"], re.IGNORECASE)
        store, out, missing = secrets.load(self.p.slug), {}, []
        for name, kind in compose_vars(text).items():
            if kind == "default" or name.startswith(("TP_SECRET_", "COMPOSE_", "DOCKER_")):
                continue
            if name in store:
                out[name] = store[name]
            elif rx.search(name):
                out[name] = secrets.get(self.p.slug, name, generate=True)
            elif name in known:
                out[name] = known[name]
            elif kind == "required":
                missing.append(name)
        if missing:
            raise MissingValues(missing)
        return out

    def compose_env(self) -> dict[str, str]:
        """H1: a minimal environment, never the platform's own (reach/001 D1)."""
        keep = {k: os.environ[k] for k in sandbox()["compose_env"] if k in os.environ}
        cfg = os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))
        return keep | {"DOCKER_CONFIG": cfg} | self.values | self._secret_env()

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
        if not hasattr(self, "values"):
            self.write()
        cmd = ["docker", "compose", "-p", self.project, "-f", str(self.file), *args]
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout,
                           env=self.compose_env())  # fmt: skip
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

    def start(self, health: str | None = None, wait: bool = True) -> None:
        """Bring the stack up from nothing and wait for `health` (discovery starts here, before
        the profile has auth or a spec)."""
        self.down()  # every bring-up starts from nothing: a fresh database, so a fresh login
        # bind mounts live under the env dir and outlive `down -v`: an app keeping its database
        # in one (SQLite) would otherwise start with the last run's users (found in discover/004)
        shutil.rmtree(self.dir / "binds", ignore_errors=True)
        if "login" in self.p.auth.value:
            secrets.forget(self.p.slug, self.token_secret)
        self.build()
        self.compose(
            "up", "-d", "--wait", "--wait-timeout", str(UP_TIMEOUT), timeout=UP_TIMEOUT + 60
        )
        if wait:
            self.wait_http(health or self.r.get("health") or self.p.spec.value["openapi"])
            self.bootstrap()

    def bootstrap(self) -> None:
        """Replay the profile's first-user creation (discover/001 D3): a fresh container has no
        users, so every bring-up creates the admin the login then uses."""
        b = self.p.auth.value.get("bootstrap")
        if not b:
            return
        body = _resolve(self.p.slug, b["body"])
        status, resp = self.http(
            "POST", b["path"], **({"json": body} if b.get("json") else {"data": body})
        )
        if not 200 <= status < 300:
            raise BlackboxError(f"bootstrap {b['path']}: {status} {str(resp)[:200]}")

    def up(self) -> dict:
        self.start()
        probes = self.probe()
        if failed := [p["name"] for p in probes if not p["ok"]]:
            raise BlackboxError(f"probes failed after bring-up: {failed}: {probes}")
        fingerprint = self.fingerprint()
        return {"variant": self.variant_name, "project": self.project, "flags": [],
                "spec_hash": sha256(fingerprint), "fingerprint": fingerprint, "probes": probes,
                "base_url": self.base_url, "compose_file": str(self.file),
                "reproduce": f"docker compose -p {self.project} -f {self.file} up -d --wait"}  # fmt: skip

    def down(self) -> None:
        with contextlib.suppress(BlackboxError, MissingValues):  # unwritable: never up
            self.compose("down", "-v", "--remove-orphans", "-t", "10", check=False, timeout=120)

    def ensure(self) -> None:
        if not all(p["ok"] for p in self.probe()):
            self.up()

    def logs(self) -> str:
        return self.compose("logs", "--no-color", "--no-log-prefix", self.r["service"],
                            check=False).stdout  # fmt: skip

    @property
    def base_url(self) -> str:
        return self._host_url(FORWARDER)

    def standin_url(self, name: str) -> str:
        """A stand-in's HTTP port from the host, for discovery's journal reads (request R4)."""
        return self._host_url(f"{FORWARDER}-{name}")

    def _host_url(self, service: str) -> str:
        port = str(sandbox()["forwarder"]["port"])
        out = self.compose("port", service, port, check=False).stdout.strip()
        if not out:
            raise BlackboxError(f"{self.project} is not up: no {service} port")
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
        built = sorted(n for n, svc in compose["services"].items() if "build" in svc)
        # what differs per variant by design: a built service's tag and source copy (`images`,
        # `commit` and the patch hashes cover them) and the project label, the only label set
        same = {
            **compose,
            "services": {n: _same(svc, n in built) for n, svc in compose["services"].items()},
        }
        text = yaml.safe_dump(same, sort_keys=True).replace(str(self.dir), "<env>")
        network = {
            "run": "none: an internal network, reached inbound through a forwarder",
            "build": f"unrestricted outbound while building {', '.join(built)}"
            if built
            else "no build: images pulled at the tested tag",
        }  # fmt: skip (D5)
        out = {"compose": sha256(text), "images": images, "commit": self.commit(),
               "ref": self.r["ref"], "network": network}  # fmt: skip
        if self.patches:  # the labelled seed or fix this build carries (DR-9, DR-10), in the
            # shape retest's env diff reads: a seed is a flag; a fix is a sut patch, only added
            hashes = self.patch_hashes()
            if self.variant_name.startswith("bug:"):
                out["flags"] = sorted(
                    f"{n}:{h}" for n, h in hashes.items()
                )  # a list, as the template's
            else:
                out["sut"] = {"patches": hashes}
        return out

    # ---- the runner ----

    def openapi(self) -> dict:
        """The served spec, or (`repo_file`) the spec file at the tested commit; cached as
        <env dir>/openapi.json, which the runner reads (TP_OPENAPI_FILE)."""
        cached = self.dir / "openapi.json"
        s = self.p.spec.value
        if s["strategy"] in ("repo_file", "pack_file"):  # a pack's spec lives in packs/ (C9)
            root = spec_base.PACKS if s["strategy"] == "pack_file" else self.src
            body = yaml.safe_load((root / s["openapi"]).read_text())
        else:
            status, body = self.http("GET", s["openapi"])
            if status != 200 or not isinstance(body, dict):
                raise BlackboxError(f"spec {s['openapi']}: {status}")
        body = spec_base.with_base(body)  # the paths carry the base, as discovery saw them (R2)
        self.dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(body))
        return body

    def pytest(self, tests: Path, out: Path, variant: str = "reference",
               env: dict[str, str] | None = None, args: tuple[str, ...] = (),
               **_ignored) -> dict:  # fmt: skip
        """pytest in the black-box runner, on the run network only (no internet, D5). Secrets go
        in as env vars (D9); creates are logged to out/ledger.jsonl and deleted afterwards (D1)."""
        from tp.stages.generate import image

        ui = next(tests.rglob("*.ui.yaml"), None) is not None  # C4: flows need the browser (R2)
        runner = (image.published_browser() if ui else image.published_blackbox())["digest"]
        out.mkdir(parents=True, exist_ok=True)
        for f in ("results.jsonl", "junit.xml", "ledger.jsonl"):
            (out / f).unlink(missing_ok=True)
        who = make_auth(self.p, self).principal("admin")
        self.openapi()  # the runner reads this copy, served or repo file (discover/002)
        run_env = {
            "TP_MODE": "blackbox", "TP_RESULTS": "/work/out/results.jsonl",
            "TP_LEDGER": "/work/out/ledger.jsonl",
            "TP_API_BASE": self.p.spec.value.get("base", ""),
            "TP_OPENAPI_PATH": self.p.spec.value["openapi"],
            "TP_AUTH_SCHEME": who["scheme"],
            "TP_READ_BACK_TIMEOUT": str(read_back := self.p.data.value.get("read_back_timeout_s", 5)),
            "TP_TIMEOUT": str(max(60, int(read_back) + 60)),  # a read-back must fit in one test
            "TP_ID_FIELD": self.p.data.value.get("id_field", "id"),
            "TP_FAMILIES": json.dumps(families_config(self.p)),
            "TP_RATE": str(families_config(self.p)["rate"]),
            "TP_OPENAPI_FILE": "/work/openapi.json",
            **(env or {}),
        }  # fmt: skip
        secret_env = {"TP_SECRET_TOKEN": who["token"]}
        if login := self.p.auth.value.get("login"):  # the runner refreshes an expiring JWT (P7)
            run_env["TP_LOGIN"] = json.dumps({k: login[k] for k in ("path", "token_field")}
                                             | {"json": bool(login.get("json")),
                                                "body": _placeholders(login["body"])})  # fmt: skip
            secret_env["TP_SECRET_ADMIN_PASSWORD"] = secrets.get(self.p.slug, "admin_password",
                                                                 generate=True)  # fmt: skip
        with self.egress(runner, run_env) as (network, base_url):
            run_env["TP_BASE_URL"] = base_url
            cmd = ["docker", "run", "--rm", "--network", network,
                   "-v", f"{tests.resolve()}:/work/tests:ro", "-v", f"{out.resolve()}:/work/out",
                   "-v", f"{(self.dir / 'openapi.json').resolve()}:/work/openapi.json:ro"]  # fmt: skip
            cmd += [a for k, v in sorted(run_env.items()) for a in ("-e", f"{k}={v}")]
            cmd += [
                a for k in sorted(secret_env) for a in ("-e", k)
            ]  # value from our env, not argv
            cmd += [runner, "python", "-m", "pytest", "-p", "platform_rt.blackbox", "-p",
                    "no:cacheprovider", "-q", "--continue-on-collection-errors",
                    "--rootdir=/work/tests", "--junitxml=/work/out/junit.xml", "/work/tests", *args]  # fmt: skip
            r = subprocess.run(cmd, check=False, capture_output=True, text=True,
                               timeout=RUN_TIMEOUT, env=os.environ | secret_env)  # fmt: skip
        log = secrets.redact((r.stdout + r.stderr)[-4000:])
        results = [json.loads(ln) for ln in (out / "results.jsonl").read_text().splitlines()] \
            if (out / "results.jsonl").exists() else []  # fmt: skip
        cleanup = self.cleanup(self.ledger_file(out))
        (out / "cleanup.json").write_text(json.dumps(cleanup, indent=1))
        if r.returncode not in (0, 1) and not results:
            raise BlackboxError(f"runner pytest exited {r.returncode}: {log[-1500:]}")
        return {"returncode": r.returncode, "results": results, "junit": str(out / "junit.xml"),
                "output": log, "cleanup": {k: len(v) for k, v in cleanup.items()}}  # fmt: skip

    def egress(self, runner: str, run_env: dict):
        """The runner's network and base URL: the run network, straight to the app service."""
        return contextlib.nullcontext((f"{self.project}_{RUN_NET}",
                                       f"http://{self.r['service']}:{self.r['port']}"))  # fmt: skip

    def ledger_file(self, out: Path) -> Path:
        return out / "ledger.jsonl"

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


# ---- external reach (docs/status/external/001) ------------------------------------------------

READS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def safety_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "safety.yaml").read_text())


class ExternalStack(Stack):
    """Reach `external` (E1, E3, E6): a running system the platform didn't start and can't
    control, reached only by URL + supplied token. No checkout, compose, logs or exec on it.
    Platform-side requests are reads and deletes of what runs created (`SafeMode`); the runner's
    go through the safe-mode proxy (platform_rt.proxy), its only route out. Docker here only
    runs the platform's own runner and proxy."""

    def __init__(self, p: TargetProfile):
        super().__init__(p)
        self.project = "tp_ext_" + self.project.removeprefix("tp_bb_")
        self.dir = db.data_dir() / "env" / self.project
        self.file = self.dir / "compose.yml"  # never written
        self.ledger = self.dir / "ledger.jsonl"  # the proxy's, kept across runs (E3)

    @property
    def src(self) -> Path:
        raise BlackboxError("external reach has no checkout: its spec must be served")

    def compose(self, *args: str, **kw):
        raise BlackboxError("external reach: the platform doesn't manage this system")

    def commit(self) -> str:
        return self.p.commit_tested

    @property
    def base_url(self) -> str:
        return self.r["url"].rstrip("/")

    def up(self) -> dict:
        before = self.cleanup(self.ledger)  # what an earlier, crashed or killed run left behind
        probes = self.probe()
        if failed := [p["name"] for p in probes if not p["ok"]]:
            raise BlackboxError(f"probes failed on {self.base_url}: {failed}: {probes}")
        fingerprint = self.fingerprint()
        return {"variant": "reference", "project": self.project, "flags": [],
                "spec_hash": sha256(fingerprint), "fingerprint": fingerprint, "probes": probes,
                "base_url": self.base_url, "cleanup_before": {k: len(v) for k, v in before.items()},
                "reproduce": f"curl {self.base_url}{self.r.get('health') or ''}"}  # fmt: skip

    def down(self) -> None:
        pass  # nothing of ours to tear down

    def build(self) -> list[str]:
        return []

    def logs(self) -> str:
        return ""

    def fingerprint(self) -> dict:
        return {"url": self.base_url, "commit": self.commit(),
                "spec": sha256(json.dumps(self.openapi(), sort_keys=True)),  # changes on redeploy
                "network": {"run": "runner on an internal network, out only through the "
                                   "safe-mode proxy",
                            "build": "none: the system was already running"}}  # fmt: skip

    def http(self, method: str, path: str, **kw) -> tuple[int, object]:
        if not SafeMode(self).allow(method, path):
            raise BlackboxError(f"NOT_RUN: safe mode: {method} {path} (the platform itself "
                                "only reads, and deletes what runs created)")  # fmt: skip
        return super().http(method, path, **kw)

    def owned(self) -> set[str]:
        """Item paths runs created and haven't deleted yet, from the ledger."""
        if not self.ledger.exists() or not (cached := self.dir / "openapi.json").exists():
            return set()
        spec = json.loads(cached.read_text())
        entries = [json.loads(ln) for ln in self.ledger.read_text().splitlines() if ln]
        gone = {json.dumps({k: v for k, v in e.items() if k != "deleted_by_test"}, sort_keys=True)
                for e in entries if e.get("deleted_by_test")}  # fmt: skip
        return {t for e in entries if not e.get("deleted_by_test")
                and json.dumps(e, sort_keys=True) not in gone and (t := delete_path(spec, e))}  # fmt: skip

    def cleanup(self, ledger: Path) -> dict[str, list]:
        out = super().cleanup(ledger)
        if ledger.exists():  # keep only what is still out there, for the next run to retry
            keep = [{k: v for k, v in e.items() if k != "status"}
                    for e in out["failed"] + out["unknown_delete_route"]]  # fmt: skip
            ledger.write_text("".join(json.dumps(e) + "\n" for e in keep))
        return out

    def ledger_file(self, out: Path) -> Path:
        return self.ledger

    @contextlib.contextmanager
    def egress(self, runner: str, run_env: dict):
        """Start the safe-mode proxy on an internal network shared with the runner; only the
        proxy also joins the default bridge, so it is the runner's one way out."""
        net, name = f"{self.project}_run", f"{self.project}_proxy"
        url = httpx.URL(self.base_url)
        if url.host in LOOPBACK:  # the proxy runs in a container: loopback is the host's
            url = url.copy_with(host="host.docker.internal")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.touch()
        run_env["TP_SAFE_MODE"] = "1"
        env = {
            "TP_UPSTREAM": str(url),
            "TP_OPENAPI_FILE": "/work/openapi.json",
            "TP_LEDGER": "/work/ledger.jsonl",
            "TP_RUN": run_env.get("TP_RUN", "local"),
            "TP_ID_FIELD": run_env.get("TP_ID_FIELD", "id"),
            "TP_SAFETY": json.dumps(safety_config()),
            "TP_UPSTREAM_HOST": httpx.URL(self.base_url).netloc.decode(),
        }  # fmt: skip  (L4)
        subprocess.run(
            ["docker", "network", "create", "--internal", net], check=False, capture_output=True
        )  # fmt: skip  (already there: fine)
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        cmd = ["docker", "run", "-d", "--rm", "--name", name, "--network", net,
               "-v", f"{(self.dir / 'openapi.json').resolve()}:/work/openapi.json:ro",
               "-v", f"{self.ledger.resolve()}:/work/ledger.jsonl"]  # fmt: skip
        cmd += [a for k, v in sorted(env.items()) for a in ("-e", f"{k}={v}")]
        cmd += [runner, "python", "-m", "platform_rt.proxy"]
        try:
            for step in (cmd, ["docker", "network", "connect", "bridge", name]):
                r = subprocess.run(step, check=False, capture_output=True, text=True, timeout=120)
                if r.returncode:
                    raise BlackboxError(f"safe-mode proxy: {(r.stderr or r.stdout)[-500:]}")
            t0 = time.monotonic()
            while "ready" not in subprocess.run(["docker", "logs", name], check=False,
                                                capture_output=True, text=True).stdout:  # fmt: skip
                if time.monotonic() - t0 > 30:
                    raise BlackboxError("safe-mode proxy never became ready")
                time.sleep(0.5)
            yield net, f"http://{name}:8000"
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def families_config(p: TargetProfile) -> dict:
    """$TP_FAMILIES for platform_rt.families (checks/001): the data conventions (ID field and
    style, paging) from `data`, the family knobs (per_family, examples, rate, exclude) from
    `checks.families`, and the discovered secured map (discover/002 P1) with its
    `confirm_observed` answer applied: confirm = intended, so public; reject = a hole, so secured."""
    d = p.data.value
    out = {"id_field": d.get("id_field", "id"), "id_style": d.get("id_style", "integer"),
           "paging": d.get("paging"), "rate": 2 if p.reach.strategy == "external" else 5, "per_family": 5, "examples": 10, "exclude": []
           } | (p.checks.value.get("families") or {})  # fmt: skip
    if sec := p.auth.value.get("secured"):
        from tp.core import review

        sec = dict(sec)
        # only an answer about exactly these operations: an old answer about a different list
        # (an earlier discovery, or another profile of the slug) doesn't carry over (discover/006)
        ops = sorted(sec.get("observed_open", []))
        answered = [i for i in review.list_(status="resolved") if i["kind"] == "confirm_observed"
                    and i["payload"].get("about") == "secured"
                    and i["payload"].get("target") == p.slug
                    and sorted(o["op"] for o in i["payload"].get("ops", [])) == ops]  # fmt: skip
        if answered:
            decision = answered[-1]["decision"]
            if decision == "confirm":
                sec["public"] = [*sec.get("public", []), *sec.get("observed_open", [])]
            if decision in ("confirm", "reject"):
                sec["observed_open"] = []  # out_of_scope leaves them spec-side
        out["secured"] = sec
    if standins := p.reach.value.get("standins"):  # C8 addition (reach/001 D9): journal addresses
        out["standins"] = [{"name": s["name"], "kind": s["kind"], "journal": s.get("journal"),
                            "url": f"http://tp-standin-{s['name']}:{s['port']}"}
                           for s in standins]  # fmt: skip
    return out


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
    """`{secret: name}` -> its value, anywhere in a (nested) body."""
    if isinstance(v, dict):
        if set(v) == {"secret"}:
            return secrets.get(slug, v["secret"], generate=True)
        return {k: _resolve(slug, x) for k, x in v.items()}
    return [_resolve(slug, x) for x in v] if isinstance(v, list) else v


def _placeholders(v):
    """`{secret: name}` -> `$TP_SECRET_<NAME>`, for the runner (values go in as env vars)."""
    if isinstance(v, dict):
        if set(v) == {"secret"}:
            return "$" + secrets.env_name(v["secret"])
        return {k: _placeholders(x) for k, x in v.items()}
    return [_placeholders(x) for x in v] if isinstance(v, list) else v


def _jwt_expired(token: str, margin: float = 60) -> bool:
    """True for a JWT (RFC 7519) within `margin` seconds of its `exp`; False for other tokens."""
    try:
        part = token.split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"]
    except IndexError, ValueError, KeyError, TypeError:
        return False
    return float(exp) - time.time() < margin


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
        name = (self.stack or Stack(self.p)).token_secret
        if (token := secrets.load(slug).get(name)) is None or _jwt_expired(token):
            login = self.v["login"]
            body = _resolve(slug, login["body"])
            kw = {"json": body} if login.get("json") else {"data": body}
            status, resp = (self.stack or Stack(self.p)).http("POST", login["path"], **kw)
            # any 2xx, as discovery's try_login (a sign-up answering 201 with a token, domains/002 R1)
            tok = _dig(resp, login["token_field"])
            if not 200 <= status < 300 or tok is None:
                raise BlackboxError(f"login {login['path']}: {status}")
            token = secrets.put(slug, name, str(tok))
        return {"scheme": scheme, "token": token}


AUTH = {"basic": BasicAuth, "token_header": TokenHeaderAuth}


def make_auth(p: TargetProfile, stack: Stack | None = None):
    return AUTH[p.auth.strategy](p, stack)


# ---- the strategies ---------------------------------------------------------------------------


class Reach:
    def __init__(self, stack: Stack):
        self.stack = stack

    def provision(self, variant: str = "reference", expect: str | None = None) -> dict:
        return self.stack.variant(variant).up()

    def ensure(self, variant: str = "reference") -> None:
        self.stack.variant(variant).ensure()

    def probe(self, variant: str = "reference") -> list[dict]:
        return self.stack.variant(variant).probe()

    def guard(self, variant: str = "reference") -> None:
        if bad := [p for p in self.stack.variant(variant).probe() if not p["ok"]]:
            from tp.stages.env.service import EnvDown

            raise EnvDown("; ".join(f"{p['name']}: {p['raw'][:200]}" for p in bad))

    def teardown(self, variant: str = "reference") -> None:
        self.stack.variant(variant).down()

    def logs(self, variant: str = "reference") -> str:
        return self.stack.variant(variant).logs()

    def image(self, variant: str = "reference") -> str | None:
        return None  # the runner is the generic black-box image

    def exec(self, cmd: list[str], **kw) -> str | None:
        return None  # the exec allow-list is stage D (D7)


class ExternalReach(Reach):
    live = True  # a system the platform doesn't own: gate K and the ceiling read this (E7, E8)

    def provision(self, variant: str = "reference", expect: str | None = None) -> dict:
        if variant != "reference":
            raise BlackboxError(f"external reach can't deploy {variant}: redeploy the system "
                                "yourself, then retest with after=reference")  # fmt: skip
        return self.stack.up()


class SafeMode:
    """Safety for external reach, platform side (E1): reads, and deletes of items runs created.
    The runner's requests are checked by the proxy's fuller policy (platform_rt.proxy)."""

    def __init__(self, stack: ExternalStack):
        self.stack = stack

    def allow(self, method: str, path: str, target_id: str | None = None) -> bool:
        m = method.upper()
        return m in READS or (m == "DELETE" and path in self.stack.owned())

    def plan(self, doc: dict) -> list[dict]:
        """Sign-off's planned writes (autonomy/001 F8): every write in the spec with the proxy's
        verdict, worked out without sending anything. Mirrors platform_rt.proxy.Policy.decide;
        test_planned_writes_agree_with_the_proxy holds the two together."""
        deny = [d.lower() for d in safety_config().get("deny", [])]
        paths, out = doc.get("paths", {}), []
        for tpl, ops in paths.items():
            for m, op in ops.items():
                if m.upper() in READS or not isinstance(op, dict) or m.startswith("x-"):
                    continue
                words = [s.lower() for s in tpl.split("/") if s and not s.startswith("{")]
                words += [str(s).lower() for k, v in op.items() if k.startswith("x-")
                          and "scope" in k.lower() for s in (v if isinstance(v, list) else [v])]  # fmt: skip
                hit = next((d for d in deny for w in words if d in w), None)
                base = tpl.rstrip("/") + "/"
                item = any(re.fullmatch(r"\{[^/}]+\}/?", c[len(base):]) and {"get", "delete"} <= set(o)
                           for c, o in paths.items() if c.startswith(base))  # fmt: skip
                if hit:
                    verdict, rule = "blocked", f"deny category {hit!r}"
                elif m.upper() == "POST":
                    verdict, rule = (("create", "create, with the run prefix") if item else
                                     ("blocked", "not a collection with read and delete on its items"))  # fmt: skip
                else:
                    verdict, rule = "own_items", "only on items this run created"
                out.append({"op": f"{m.upper()} {tpl}", "verdict": verdict, "rule": rule})
        return out


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
        return self.stack.variant(variant).pytest(tests, out, variant, **kw)


IMPLS = {("reach", "container"): Reach, ("reach", "external"): ExternalReach,
         ("safety", "safe_mode"): SafeMode, ("spec", "served"): Spec, ("spec", "repo_file"): Spec,
         ("spec", "pack_file"): Spec,
         ("data", "spec_derived"): SpecDerivedData, ("state", "namespace"): NamespaceState,
         ("mutation", "none"): NoMutation, ("checks", "pytest_cases"): Checks}  # fmt: skip


_STACKS: dict[str, Stack] = {}  # one per profile slug per process, shared by its strategies


def strategy(concern: str, name: str, p: TargetProfile):
    stack = _STACKS.setdefault(
        p.slug, (ExternalStack if p.reach.strategy == "external" else Stack)(p)
    )
    if concern == "auth":
        return make_auth(p, stack)
    return IMPLS[(concern, name)](stack)
