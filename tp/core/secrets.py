"""Secret store and redaction (blackbox design D9).

Secrets live only in data/secrets/<target slug>.json (mode 600), never in artifacts: everything
sealed goes through `scrub`, which replaces every known secret, and its URL-encoded, JSON-escaped
and base64 forms, with a `$TP_SECRET_<NAME>` placeholder. Runners get secrets as env vars only.
"""

import base64
import json
import os
import secrets as _rand
import urllib.parse
from pathlib import Path

from tp.core import db

MIN_LEN = 8  # shorter values (e.g. a user name) would redact ordinary words


def _file(slug: str) -> Path:
    return db.data_dir() / "secrets" / f"{slug}.json"


def load(slug: str) -> dict[str, str]:
    f = _file(slug)
    return json.loads(f.read_text()) if f.exists() else {}


def put(slug: str, name: str, value: str) -> str:
    f = _file(slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(load(slug) | {name: value}, indent=2))
    os.chmod(f, 0o600)
    return value


def forget(slug: str, name: str) -> None:
    if name in (kept := load(slug)):
        del kept[name]
        _file(slug).write_text(json.dumps(kept, indent=2))


def get(slug: str, name: str, generate: bool = False) -> str:
    """A stored secret; with generate=True one is made (and kept) the first time it's asked for."""
    if (v := load(slug).get(name)) is not None:
        return v
    if not generate:
        raise KeyError(f"no secret {name!r} for {slug}")
    # 44 hex chars: plain in env vars and URLs, and also base64 of 33 bytes, which apps that read
    # a key as base64 of at least 32 random bytes accept (integration/002)
    return put(slug, name, _rand.token_hex(22))


def env_name(name: str) -> str:
    return "TP_SECRET_" + name.upper()


def variants(value: str) -> set[str]:
    """Every form a secret takes on the wire or in a file."""
    raw = value.encode()
    return {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value),
            json.dumps(value)[1:-1], base64.b64encode(raw).decode().rstrip("="),
            base64.urlsafe_b64encode(raw).decode().rstrip("=")}  # fmt: skip


def known() -> list[tuple[str, str]]:
    """(text, placeholder) for every stored secret of every target, longest text first."""
    d = db.data_dir() / "secrets"
    pairs = {(v, "$" + env_name(n)) for f in sorted(d.glob("*.json"))
             for n, s in json.loads(f.read_text()).items() if len(s) >= MIN_LEN
             for v in variants(s)}  # fmt: skip
    return sorted(pairs, key=lambda p: -len(p[0]))


def redact(text: str, pairs: list[tuple[str, str]] | None = None) -> str:
    for v, ph in known() if pairs is None else pairs:
        text = text.replace(v, ph)
    return text


def scrub(body):
    """`body` with every secret replaced; unchanged (same object) when there is nothing to hide."""
    pairs = known()
    if not pairs:
        return body
    text = json.dumps(body, sort_keys=True, default=str)
    clean = redact(text, pairs)
    return body if clean == text else json.loads(clean)


def leaks(root: Path) -> list[str]:
    """Files under `root` (the secret store aside) holding any form of a stored secret."""
    pairs, store = known(), (db.data_dir() / "secrets").resolve()
    out = []
    for f in sorted(p for p in root.rglob("*") if p.is_file() and store not in p.resolve().parents):
        text = f.read_bytes().decode(errors="replace")
        if any(v in text for v, _ in pairs):
            out.append(str(f))
    return out
