"""Env and data service (plan 3.2): variant stacks, probes, fingerprint, DB reset.

Reached by other stages only through `ctx.env` (the SERVICE at the bottom).
"""

import json
import re
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


class EnvError(Exception):
    pass


class EnvDown(EnvError):
    """A probe failed: anything that ran against this env since the last good probe is suspect."""


def parse_variant(variant: str, known: list[str]) -> list[str]:
    """`reference` -> no flags; `bug:A+B` -> the sorted flags A and B."""
    if variant == "reference":
        return []
    kind, _, rest = variant.partition(":")
    flags = rest.split("+") if kind == "bug" and rest else []
    if not flags or (unknown := set(flags) - set(known)):
        raise EnvError(
            f"bad variant {variant!r}: use reference or bug:FLAG[+FLAG] with flags from {known}"
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
        self.flags = parse_variant(variant, self.cfg.get("flags", []))
        self.project = project_name(variant)
        self.dir = db.data_dir() / "env" / self.project
        self.src = sut.checkout(self.cfg, root)
        self.patch_hashes = {
            n: sha256((root / "sut/patches" / f"{n}.patch").read_text())
            for n in self.cfg["patches"]
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
                "db": {"healthcheck": HEALTH},
                "backend": {
                    "image": self.image,
                    "ports": [f"127.0.0.1::{BACKEND_PORT}"],  # a free host port (D3)
                    "environment": env,
                    "healthcheck": HEALTH,
                },
                "mailpit": {"image": "axllent/mailpit"},
            }
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

    def up(self, expect: str | None = None) -> dict:
        """Fresh bring-up: sweep → patch → build (once per patch set) → db → migrate+seed →
        template DB → backend + mailpit → probes → fingerprint."""
        sut.verify(self.cfg, self.root)
        self.write_override()
        self.down()  # orphan sweep: every bring-up starts from nothing
        if subprocess.run(
            ["docker", "image", "inspect", self.image], check=False, capture_output=True
        ).returncode:
            self.compose("build", "backend", timeout=1800)
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
        return {
            "services": {n: sha256(services[n]) for n in self.cfg["compose"]["services"]},
            "images": images,
            "sut": {"name": self.cfg["name"], "ref": self.cfg["ref"], "patches": self.patch_hashes},
            "flags": self.flags,
            "seed_hash": seed,
            "schema_head": self.psql(self.cfg["lifecycle"]["db_probe"]["sql"]),
            "fake_now": None,  # until the clock patch (Stage 3)
            "tools": tools,
        }


class EnvService:
    """What `ctx.env` is. Every method takes the variant name."""

    def up(self, variant: str, expect: str | None = None) -> dict:
        return Stack(variant).up(expect)

    def probe(self, variant: str) -> list[dict]:
        return Stack(variant).probe()

    def guard(self, variant: str) -> None:
        Stack(variant).guard()

    def reset(self, variant: str) -> float:
        return Stack(variant).reset()

    def down(self, variant: str) -> None:
        Stack(variant).down()

    def factories(self, variant: str, tc_id: str):
        from tp.stages.env.factories import Factories

        return Factories(Stack(variant), tc_id)


SERVICE = EnvService()
