"""Build the platform_rt wheel and the runner image; publish its digest (plan G1, G1b).

data/published.json is the one place later stages read the runner image from (plan 1.4)."""

import json
import subprocess
import tempfile
from pathlib import Path

from tp.core import db
from tp.core.hashing import sha256

ROOT = Path(__file__).parents[3]
RT = ROOT / "platform_rt"
DOCKERFILE = ROOT / "images/runner/Dockerfile"
BLACKBOX = ROOT / "images/runner-blackbox/Dockerfile"


# mutmut's own --version needs a project config, so read package metadata
VERSIONS = (
    "import sys, importlib.metadata as m; print(sys.version.split()[0]); "
    "[print(p, m.version(p)) for p in ('pytest', 'mutmut', 'platform-rt', 'httpx')]"
)


class ImageError(Exception):
    pass


def _run(*cmd: str, timeout: int = 1800) -> str:
    r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise ImageError(f"{cmd[0]} {cmd[1]}: {(r.stderr or r.stdout).strip()[-1500:]}")
    return r.stdout.strip()


def rt_version() -> str:
    import tomllib

    return tomllib.loads((RT / "pyproject.toml").read_text())["project"]["version"]


def build(reach) -> dict:
    """reach: the target's Reach strategy; it owns the image this one starts from. A reach with
    no image (black-box targets) gets the generic black-box runner instead."""
    base = reach.image("reference")
    if base is None:
        return build_blackbox()
    base_id = _run("docker", "image", "inspect", "--format", "{{.Id}}", base)
    with tempfile.TemporaryDirectory() as ctx:
        _run("uv", "build", "--wheel", "--out-dir", ctx, str(RT))
        wheel = next(Path(ctx).glob("platform_rt-*.whl"))
        src = {"base": base_id, "dockerfile": DOCKERFILE.read_text(),
               "wheel": sha256(wheel.read_bytes().hex())}  # fmt: skip
        tag = f"tp-runner:{sha256(src)[:12]}"
        _run("docker", "build", "-q", "-f", str(DOCKERFILE), "--build-arg", f"BASE={base}",
             "-t", tag, ctx)  # fmt: skip
    digest = _run("docker", "image", "inspect", "--format", "{{.Id}}", tag)
    versions = _run("docker", "run", "--rm", digest, "python", "-c", VERSIONS)
    runner = {"image": tag, "digest": digest, "base": base, "base_id": base_id,
              "platform_rt": rt_version(), "versions": versions.splitlines()}  # fmt: skip
    path = db.data_dir() / "published.json"
    published = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(published | {"runner": runner}, indent=2))
    return runner


def build_blackbox() -> dict:
    """images/runner-blackbox: python + platform_rt + schemathesis, one image for every target."""
    with tempfile.TemporaryDirectory() as ctx:
        _run("uv", "build", "--wheel", "--out-dir", ctx, str(RT))
        wheel = next(Path(ctx).glob("platform_rt-*.whl"))
        src = {"dockerfile": BLACKBOX.read_text(), "wheel": sha256(wheel.read_bytes().hex())}
        tag = f"tp-runner-blackbox:{sha256(src)[:12]}"
        _run("docker", "build", "-q", "-f", str(BLACKBOX), "-t", tag, ctx)
    digest = _run("docker", "image", "inspect", "--format", "{{.Id}}", tag)
    runner = {"image": tag, "digest": digest, "base": None, "platform_rt": rt_version()}
    path = db.data_dir() / "published.json"
    published = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(published | {"runner_blackbox": runner}, indent=2))
    return runner


def published() -> dict:
    path = db.data_dir() / "published.json"
    if not path.exists():
        raise ImageError("no runner image published: run `tp generate image`")
    return json.loads(path.read_text())["runner"]


def published_blackbox() -> dict:
    path = db.data_dir() / "published.json"
    pub = json.loads(path.read_text()) if path.exists() else {}
    if "runner_blackbox" not in pub:
        raise ImageError("no black-box runner published: run `tp generate image`")
    return pub["runner_blackbox"]


def published_browser() -> dict:
    """The browser runner for UI flows (C4). P7 builds and publishes it as `runner_browser`."""
    path = db.data_dir() / "published.json"
    pub = json.loads(path.read_text()) if path.exists() else {}
    if "runner_browser" not in pub:
        raise ImageError("no browser runner published: P7 builds it (docs/iteration3/)")
    return pub["runner_browser"]
