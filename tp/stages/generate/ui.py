"""UI flows as BOUND entries (C4; P7, docs/status/browser/001 D10, D13): `checks.value.ui` becomes
one entry per flow, `kind: "ui"`, the flow inlined as a `.ui.yaml` file and hashed. The browser
runner (images/runner-browser) is built here, because P7 owns only `published_browser()` in
image.py; it is tagged by its inputs' hash, so an unchanged one is a cached no-op."""

import json

import yaml

from tp.core import db
from tp.core.hashing import sha256
from tp.stages.generate import image

DOCKERFILE = image.ROOT / "images/runner-browser/Dockerfile"


def entry(flow: dict) -> dict:
    src = yaml.safe_dump({"name": flow["name"], "steps": flow["steps"]}, sort_keys=False,
                         allow_unicode=True)  # fmt: skip
    return {"unit": f"U:{flow['name']}", "ac": None, "file": f"{flow['name']}.ui.yaml",
            "status": "BOUND", "kind": "ui", "generic": True, "source": src,
            "lock_hash": sha256(src)}  # fmt: skip


def entries(ctx, opts: dict) -> list[dict]:
    """Not when a run is limited to some units, as for the families (generate/stage.py)."""
    flows = (
        [] if opts.get("units") or ctx.profile is None else ctx.profile.checks.value.get("ui") or []
    )
    if flows:
        build_image()
    return [entry(f) for f in flows]


def build_image(tag: str | None = None, base: str | None = None) -> dict:
    """The black-box runner (`base`, built if not given) plus Playwright and Chromium; published
    as `runner_browser`, which `image.published_browser()` reads."""
    base = base or image.build_blackbox()["image"]
    base_id = image._run("docker", "image", "inspect", "--format", "{{.Id}}", base)
    src = {"base": base_id, "dockerfile": DOCKERFILE.read_text()}
    tag = tag or f"tp-runner-browser:{sha256(src)[:12]}"
    image._run("docker", "build", "-q", "-f", str(DOCKERFILE), "--build-arg", f"BASE={base}",
               "-t", tag, str(DOCKERFILE.parent))  # fmt: skip
    digest = image._run("docker", "image", "inspect", "--format", "{{.Id}}", tag)
    runner = {"image": tag, "digest": digest, "base": base, "base_id": base_id,
              "platform_rt": image.rt_version()}  # fmt: skip
    path = db.data_dir() / "published.json"
    published = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(published | {"runner_browser": runner}, indent=2))
    return runner
