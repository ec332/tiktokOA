"""Env and data service (plan 3.2): variant stacks, probes, fingerprint, DB reset.

Reached by other stages only through `ctx.env` (the SERVICE at the bottom).
"""

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from tp.core import db, sut
from tp.core.hashing import sha256

# † tuning numbers (plan 3.2); changing one bumps the stage version
HEALTH = {"interval": "2s", "start_interval": "1s", "retries": 5, "timeout": "5s"}
TEARDOWN_TIMEOUT = 60  # seconds
BACKEND_PORT = 8000  # inside the container
RUN_TIMEOUT = 1800  # seconds for one runner pytest call
HARNESS_FILES = ("harness.py", "factories.py")  # bind-mounted into the runner (design D14)


class EnvError(Exception):
    pass


class EnvDown(EnvError):
    """A probe failed: anything that ran against this env since the last good probe is suspect."""


def parse_variant(variant: str, known: list[str]) -> list[str]:
    """`reference`, `fixed:<patch>` -> no flags; `bug:A+B` -> the sorted flags A and B."""
    kind, _, rest = variant.partition(":")
    if variant == "reference" or (kind == "fixed" and rest):
        return []  # fixed:<patch> carries no flags; Stack checks the patch exists (retest D3)
    flags = rest.split("+") if kind == "bug" and rest else []
    if not flags or (unknown := set(flags) - set(known)):
        raise EnvError(
            f"bad variant {variant!r}: use reference, fixed:<patch> or bug:FLAG[+FLAG] with flags from {known}"
            + (f" (unknown: {sorted(unknown)})" if flags else "")
        )
    return sorted(set(flags))


def project_name(variant: str) -> str:
    return "tp_" + re.sub(r"[^a-z0-9]+", "_", variant.lower()).strip("_")


def normalise_config(config: dict, replace: dict[str, str]) -> dict:
    """Machine-specific strings (SUT path, project dir, project name) -> placeholders."""
    text = json.dumps(config, sort_keys=True)
    for old, new in sorted(replace.items(), key=lambda kv: -len(kv[0])):  # longest first
        text = text.replace(old, new)
    return json.loads(text)


def _http(method: str, url: str, token: str | None = None, json_body=None, form=None):
    """(status, parsed body or text). Never raises for HTTP status codes."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if json_body is not None:
        data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode()
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


class Stack:
    """One variant's compose project, `tp_<variant>`, on the patched SUT tree."""

    def __init__(self, variant: str, root: Path = Path(".")):
        self.root = root
        self.cfg = sut.load(root)
        self.variant = variant
        # fixed:<patch> = no flags, plus that fix patch on top, in the image only (retest D3)
        self.fix = variant.removeprefix("fixed:") if variant.startswith("fixed:") else None
        if self.fix and not (root / "sut/patches" / f"{self.fix}.patch").is_file():
            raise EnvError(f"bad variant {variant!r}: no sut/patches/{self.fix}.patch")
        self.flags = parse_variant(variant, self.cfg.get("flags", []))
        self.project = project_name(variant)
        self.dir = db.data_dir() / "env" / self.project
        self.src = sut.checkout(self.cfg, root)
        self.patch_hashes = {
            n: sha256((root / "sut/patches" / f"{n}.patch").read_text())
            for n in [*self.cfg["patches"], *([self.fix] if self.fix else [])]
        }
        source = {"ref": self.cfg["ref"], "patches": self.patch_hashes}
        self.image = f"tp-sut-backend:{sha256(source)[:12]}"  # one image per patch set (D1)

    # ---- plumbing ----------------------------------------------------------

    @property
    def override(self) -> Path:
        return self.dir / "override.yml"

    @property
    def state_file(self) -> Path:
        return self.dir / "state.json"

    def compose(self, *args: str, check: bool = True, timeout: float | None = None):
        files = [a for f in self.cfg["compose"]["files"] for a in ("-f", str(self.src / f))]
        cmd = ["docker", "compose", "-p", self.project, *files, "-f", str(self.override), *args]
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
        if check and r.returncode:
            raise EnvError(f"{' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[-800:]}")
        return r

    def psql(self, sql: str, database: str = "app") -> str:
        r = self.compose(
            "exec", "-T", "db", "psql", "-U", "postgres", "-d", database, "-v",
            "ON_ERROR_STOP=1", "-tAc", sql,
        )  # fmt: skip
        return r.stdout.strip()

    def write_override(self) -> None:
        env = {**self.cfg.get("env", {})}
        env |= {f: ("true" if f in self.flags else "false") for f in self.cfg.get("flags", [])}
        env |= {"SMTP_HOST": "mailpit", "SMTP_PORT": "1025", "SMTP_TLS": "false"}
        override = {
            "services": {
                "db": {"healthcheck": HEALTH, "networks": ["default", "internal"]},
                "backend": {
                    "image": self.image,
                    "networks": ["default", "internal"],
                    "ports": [f"127.0.0.1::{BACKEND_PORT}"],  # a free host port (D3)
                    "environment": env,
                    "healthcheck": HEALTH,
                },
                "mailpit": {"image": "axllent/mailpit"},
            },
            # the runner joins only this one: no outbound network (design D16)
            "networks": {"internal": {"internal": True}},
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        self.override.write_text(yaml.safe_dump(override, sort_keys=True))

    def state(self) -> dict:
        if not self.state_file.exists():
            raise EnvError(f"{self.variant} is not up: run `tp env up {self.variant}`")
        return json.loads(self.state_file.read_text())

    @property
    def base_url(self) -> str:
        out = self.compose("port", "backend", str(BACKEND_PORT)).stdout.strip()
        if not out:
            raise EnvDown("backend has no published port")
        return f"http://{out}"

    def api(self, path: str) -> str:
        return self.base_url + self.cfg["surfaces"]["api"]["base"] + path

    # ---- lifecycle ---------------------------------------------------------

    def build(self) -> str:
        """The patched backend image for this patch set, built once (D1). Returns its tag."""
        sut.verify(self.cfg, self.root)
        if stray := self.stray_patches():  # else the reference image could bake in a fix
            raise EnvError(
                f"{stray} still applied to {self.src} (an interrupted fixed: build?); "
                f"undo with: git -C {self.src} apply -R sut/patches/<name>.patch"
            )
        self.write_override()
        if subprocess.run(
            ["docker", "image", "inspect", self.image], check=False, capture_output=True
        ).returncode:
            if not self.fix:
                self.compose("build", "backend", timeout=1800)
                return self.image
            patch = str((self.root / "sut/patches" / f"{self.fix}.patch").resolve())
            git = ["git", "-C", str(self.src), "apply"]
            r = subprocess.run([*git, patch], check=False, capture_output=True, text=True)
            if r.returncode:
                raise EnvError(f"{self.fix} does not apply: {r.stderr.strip()}")
            try:
                self.compose("build", "backend", timeout=1800)
            finally:  # the tree goes back to the sut.yaml patch set; only the image is fixed
                subprocess.run([*git, "-R", patch], check=True, capture_output=True)
        return self.image

    def stray_patches(self) -> list[str]:
        """Patches in sut/patches/ that sut.yaml doesn't list but that are applied to the tree."""
        listed = set(self.cfg["patches"])
        git = ["git", "-C", str(self.src), "apply", "-R", "--check"]
        return [
            p.stem
            for p in sorted((self.root / "sut/patches").glob("*.patch"))
            if p.stem not in listed
            and subprocess.run(
                [*git, str(p.resolve())], check=False, capture_output=True
            ).returncode
            == 0
        ]

    def up(self, expect: str | None = None) -> dict:
        """Fresh bring-up: sweep → patch → build (once per patch set) → db → migrate+seed →
        template DB → backend + mailpit → probes → fingerprint."""
        sut.verify(self.cfg, self.root)
        self.write_override()
        self.down()  # orphan sweep: every bring-up starts from nothing
        self.build()
        self.compose("up", "-d", "--wait", "db")
        self.compose("run", "--rm", "-T", "backend", "bash", "-c", self.cfg["lifecycle"]["migrate"])
        seed = self.seed_hash()
        template = f"app_seed_{seed[:12]}"
        self.psql(f"CREATE DATABASE {template} TEMPLATE app", database="postgres")
        self.compose("up", "-d", "--wait", "backend", "mailpit", timeout=300)
        creds = {
            k: self.compose("exec", "-T", "backend", "printenv", k).stdout.strip()
            for k in ("FIRST_SUPERUSER", "FIRST_SUPERUSER_PASSWORD")
        }
        self.state_file.write_text(json.dumps({"template": template, "seed_hash": seed, **creds}))
        probes = self.probe()
        if failed := [p["name"] for p in probes if not p["ok"]]:
            raise EnvError(f"probes failed after bring-up: {failed}")
        fingerprint = self.fingerprint(seed)
        spec_hash = sha256(fingerprint)
        lock = self.dir / "env.lock.json"
        lock.write_text(
            json.dumps({"spec_hash": spec_hash, **fingerprint}, indent=2, sort_keys=True)
        )
        if expect and expect != spec_hash:
            raise EnvError(f"spec_hash {spec_hash} != expected {expect}; diff {lock}")
        return {
            "variant": self.variant,
            "project": self.project,
            "flags": self.flags,
            "spec_hash": spec_hash,
            "fingerprint": fingerprint,
            "probes": probes,
            "base_url": self.base_url,
            "lock_file": str(lock),
            "reproduce": f"tp env up {self.variant} --expect {spec_hash}",
        }

    def down(self) -> None:
        if not self.override.exists():
            self.write_override()
        self.compose("down", "-v", "--remove-orphans", "-t", "10", timeout=TEARDOWN_TIMEOUT)
        self.state_file.unlink(missing_ok=True)

    def reset(self) -> float:
        """Fresh `app` database from the seed template; returns seconds taken."""
        template = self.state()["template"]
        t0 = time.perf_counter()
        self.psql("DROP DATABASE app WITH (FORCE)", database="postgres")
        self.psql(f"CREATE DATABASE app TEMPLATE {template}", database="postgres")
        return time.perf_counter() - t0

    def factories(self, tc_id: str):
        from tp.stages.env.factories import Factories

        st = self.state()
        return Factories(
            lambda method, path, **kw: _http(method, self.api(path), **kw),
            (st["FIRST_SUPERUSER"], st["FIRST_SUPERUSER_PASSWORD"]),
            tc_id,
        )

    def backend_env(self) -> dict[str, str]:
        """The backend service's environment as compose resolves it (DB URL, secrets, flags)."""
        config = json.loads(self.compose("config", "--format", "json").stdout)
        return {k: str(v) for k, v in config["services"]["backend"]["environment"].items()}

    # ---- probes ------------------------------------------------------------

    def probe(self) -> list[dict]:
        """Every probe, never raising: [{name, ok, raw}]."""
        life = self.cfg["lifecycle"]
        st = self.state()

        def health():
            status, body = _http("GET", self.base_url + life["health"])
            return status == 200 and body is True, f"{status} {body}"

        def database():
            got = self.psql(life["db_probe"]["sql"])
            return got == life["db_probe"]["expect"], got

        def testinfo():
            status, body = _http("GET", self.base_url + life["testinfo"])
            return status, body

        def flags():
            status, body = testinfo()
            return status == 200 and body["flags"] == self.flags, f"{status} {body}"

        def clock():
            status, body = testinfo()
            return status == 200 and body["clock"]["source"] == "system", f"{status} {body}"

        def seed():
            n = self.psql(f"SELECT count(*) FROM \"user\" WHERE email = '{st['FIRST_SUPERUSER']}'")
            return n == "1", f"superuser rows: {n}"

        out = []
        for name, fn in [
            ("health", health), ("db", database), ("flags", flags), ("clock", clock), ("seed", seed),
        ]:  # fmt: skip
            try:
                ok, raw = fn()
            except Exception as e:  # noqa: BLE001 — a probe reports failure, it never crashes
                ok, raw = False, f"{type(e).__name__}: {e}"
            out.append({"name": name, "ok": bool(ok), "raw": str(raw)})
        return out

    def guard(self) -> None:
        """Raise EnvDown unless every probe passes. Callers re-check after any failure."""
        if bad := [p for p in self.probe() if not p["ok"]]:
            raise EnvDown("; ".join(f"{p['name']}: {p['raw'][:200]}" for p in bad))

    # ---- fingerprint -------------------------------------------------------

    def seed_hash(self) -> str:
        """Alembic head + row count per table + user emails (password hashes are salted)."""
        tables = self.psql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ).split()
        counts = {t: self.psql(f'SELECT count(*) FROM "{t}"') for t in tables}
        emails = self.psql('SELECT email FROM "user" ORDER BY email').split()
        head = self.psql(self.cfg["lifecycle"]["db_probe"]["sql"])
        return sha256({"head": head, "counts": counts, "emails": emails})

    def fingerprint(self, seed: str) -> dict:
        config = json.loads(self.compose("config", "--format", "json").stdout)
        config = normalise_config(
            config, {str(self.src): "<sut>", str(self.dir): "<env>", self.project: "<project>"}
        )
        services = config.get("services", {})
        images = {}
        for name in self.cfg["compose"]["services"]:
            image = services[name]["image"]
            fmt = "{{.Id}}" if name == "backend" else "{{json .RepoDigests}}"
            r = subprocess.run(
                ["docker", "image", "inspect", "--format", fmt, image],
                check=False, capture_output=True, text=True,
            )  # fmt: skip
            images[name] = r.stdout.strip()
        tools = {
            "docker": subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                check=False, capture_output=True, text=True,
            ).stdout.strip(),
            "compose": subprocess.run(
                ["docker", "compose", "version", "--short"],
                check=False, capture_output=True, text=True,
            ).stdout.strip(),
        }  # fmt: skip
        try:  # the runner carries platform_rt (expect helpers): part of what a verdict means
            runner = published()["runner"]["digest"]
        except EnvError:
            runner = None  # no runner image published yet (before generate, M3)
        return {
            "services": {n: sha256(services[n]) for n in self.cfg["compose"]["services"]},
            "runner": runner,
            "images": images,
            "sut": {"name": self.cfg["name"], "ref": self.cfg["ref"], "patches": self.patch_hashes},
            "flags": self.flags,
            "seed_hash": seed,
            "schema_head": self.psql(self.cfg["lifecycle"]["db_probe"]["sql"]),
            "fake_now": None,  # until the clock patch (Stage 3)
            "tools": tools,
        }


def published() -> dict:
    """data/published.json: the runner image digest, written by generate (plan 1.4)."""
    path = db.data_dir() / "published.json"
    if not path.exists():
        raise EnvError("no runner image published: run `tp generate image`")
    return json.loads(path.read_text())


def _runner_digest(stack: Stack) -> str:
    pub = published()["runner"]
    if pub.get("base") != stack.image:  # the in-process SUT code lives in the runner image
        raise EnvError(f"runner image was built on {pub.get('base')}, but the current patch set "
                       f"is {stack.image}: run `tp generate image`")  # fmt: skip
    return pub["digest"]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


# One pytest process per job inside one container (gate design D5): each child gets its own
# interpreter, so its own SUT engine and its own MUTANT_UNDER_TEST.
MATRIX_DRIVER = """
import json, os, subprocess, sys
plan = json.load(open("/work/out/matrix.json"))
os.makedirs("/work/out/m", exist_ok=True)
for job in plan["jobs"]:
    out = "/work/out/m/" + job["key"]
    env = {**os.environ, **job["env"], "TP_RESULTS": out + ".jsonl"}
    subprocess.run([sys.executable, "-m", "pytest", *plan["base"], "--junitxml=" + out + ".xml",
                    *job["args"]], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""


def run_pytest(
    tests: Path,
    out: Path,
    variant: str = "reference",
    mode: str = "in_process",
    env: dict[str, str] | None = None,
    args: tuple[str, ...] = (),
    mounts: dict[str, Path] | None = None,
    matrix: list[dict] | None = None,
) -> dict:
    """Run pytest over `tests` in the runner image (design D14).

    in_process: the SUT app in the runner, on the reference stack's DB, with the variant's
    flags as settings; every test rolls back (harness.py). network: against the variant's
    running backend. Every batch starts from a freshly reset DB (gate design D6).
    mounts: extra read-only files, container path -> host path (e.g. mutated SUT files).
    matrix: [{key, env, args}] runs pytest once per job in one container; results per key.
    Results: platform_rt's JSONL records + junit XML, written to `out`."""
    if mode not in ("in_process", "network"):
        raise EnvError(f"unknown mode {mode!r}")
    stack = Stack("reference" if mode == "in_process" else variant)
    stack.state()  # must be up
    stack.reset()  # every batch starts from the seed (a leak can't carry over)
    flags = parse_variant(variant, stack.cfg.get("flags", []))
    runner = _runner_digest(Stack("reference"))  # sut.yaml's patch set; a fixed: image differs
    out.mkdir(parents=True, exist_ok=True)
    for f in ("results.jsonl", "junit.xml"):
        (out / f).unlink(missing_ok=True)
    shutil.rmtree(out / "m", ignore_errors=True)
    tp_env = {
        **stack.backend_env(),
        **{f: ("true" if f in flags else "false") for f in stack.cfg.get("flags", [])},
        "PYTHONPATH": "/tp_env",
        "TP_MODE": mode,
        "TP_RESULTS": "/work/out/results.jsonl",
        "TP_API_BASE": stack.cfg["surfaces"]["api"]["base"],
        "TP_BASE_URL": f"http://backend:{BACKEND_PORT}",
        **(env or {}),
    }
    here = Path(__file__).parent
    vols = [f"{tests.resolve()}:/work/tests:ro", f"{out.resolve()}:/work/out"]
    vols += [f"{here / f}:/tp_env/{f}:ro" for f in HARNESS_FILES]
    vols += [f"{Path(h).resolve()}:{c}:ro" for c, h in sorted((mounts or {}).items())]
    base = ["-p", "harness", "-p", "no:cacheprovider", "-q", "--continue-on-collection-errors",
            "--rootdir=/work/tests"]  # fmt: skip
    cmd = ["docker", "run", "--rm", "--network", f"{stack.project}_internal"]
    cmd += [a for v in vols for a in ("-v", v)]
    cmd += [a for k, v in sorted(tp_env.items()) for a in ("-e", f"{k}={v}")]
    timeout = RUN_TIMEOUT
    if matrix is None:
        cmd += [runner, "python", "-m", "pytest", *base, "--junitxml=/work/out/junit.xml",
                "/work/tests", *args]  # fmt: skip
    else:
        (out / "matrix.json").write_text(json.dumps({"base": base, "jobs": matrix}))
        cmd += [runner, "python", "-c", MATRIX_DRIVER]
        timeout = RUN_TIMEOUT + 120 * len(matrix)
    r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    log = (r.stdout + r.stderr)[-4000:]
    if matrix is not None:
        if r.returncode:
            raise EnvError(f"runner matrix exited {r.returncode}: {log[-1500:]}")
        return {"matrix": {j["key"]: _read_jsonl(out / "m" / f"{j['key']}.jsonl") for j in matrix},
                "output": log}  # fmt: skip
    results = _read_jsonl(out / "results.jsonl")
    if r.returncode not in (0, 1) and not results:  # 0 pass, 1 failures; others: pytest broke
        raise EnvError(f"runner pytest exited {r.returncode}: {log[-1500:]}")
    return {"returncode": r.returncode, "results": results, "junit": str(out / "junit.xml"),
            "output": log}  # fmt: skip


def run_in_runner(
    cmd: list[str],
    mounts: dict[str, Path] | None = None,
    writable: dict[str, Path] | None = None,
    timeout: int = 600,
) -> str:
    """Run a command in the runner image with no network (e.g. mutant generation). Returns
    stdout. mounts are read-only; writable mounts are for its output."""
    runner = _runner_digest(Stack("reference"))
    vols = [f"{Path(h).resolve()}:{c}:ro" for c, h in sorted((mounts or {}).items())]
    vols += [f"{Path(h).resolve()}:{c}" for c, h in sorted((writable or {}).items())]
    r = subprocess.run(["docker", "run", "--rm", "--network", "none",
                        *[a for v in vols for a in ("-v", v)], runner, *cmd],
                       check=False, capture_output=True, text=True, timeout=timeout)  # fmt: skip
    if r.returncode:
        raise EnvError(f"runner command failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


class EnvService:
    """What `ctx.env` is. Every method takes the variant name."""

    def up(self, variant: str, expect: str | None = None) -> dict:
        return Stack(variant).up(expect)

    def ensure(self, variant: str) -> None:
        """Bring the variant up only if it isn't already up and healthy (D17)."""
        stack = Stack(variant)
        try:
            stack.guard()
        except EnvError:
            stack.up()

    def openapi(self, variant: str) -> dict:
        stack = Stack(variant)
        status, body = _http("GET", stack.base_url + stack.cfg["surfaces"]["api"]["openapi"])
        if status != 200:
            raise EnvError(f"openapi: {status}")
        return body

    def backend_image(self, variant: str = "reference") -> str:
        return Stack(variant).build()

    def pytest(self, tests: Path, out: Path, variant: str = "reference", **kw) -> dict:
        return run_pytest(tests, out, variant, **kw)

    def runner(self, cmd: list[str], **kw) -> str:
        return run_in_runner(cmd, **kw)

    def code_scope(self) -> dict:
        """The SUT manifest's mutation scope and where the patched source is on the host."""
        stack = Stack("reference")
        return {"mutate": stack.cfg.get("code_scope", {}).get("mutate", []), "src": stack.src}

    def probe(self, variant: str) -> list[dict]:
        return Stack(variant).probe()

    def guard(self, variant: str) -> None:
        Stack(variant).guard()

    def reset(self, variant: str) -> float:
        return Stack(variant).reset()

    def down(self, variant: str) -> None:
        Stack(variant).down()

    def logs(self, variant: str) -> str:
        """The backend's log since bring-up: trace lines and tracebacks (execute evidence)."""
        return Stack(variant).compose("logs", "--no-color", "--no-log-prefix", "backend").stdout

    def factories(self, variant: str, tc_id: str):
        return Stack(variant).factories(tc_id)


SERVICE = EnvService()
