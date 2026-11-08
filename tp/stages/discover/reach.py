"""Dockerfile reach and setup answers for compose values (P2, docs/status/reach/001 D4, D8)."""

import re
from pathlib import Path

from tp.core import secrets


def dockerfile(src: Path) -> dict | None:
    """`reach.build` (C5) for a repo with a root Dockerfile: its last `EXPOSE` port, or None."""
    f = src / "Dockerfile"
    if not f.is_file():
        return None
    ports = re.findall(r"(?im)^\s*EXPOSE\s+(\d+)", f.read_text())
    return {"dockerfile": "Dockerfile", "context": ".", "port": int(ports[-1]) if ports else None}


def store_answers(slug: str, answers: dict[str, str]) -> None:
    """`var:<NAME>` setup answers go to the run's secrets, never the checkout (D8)."""
    for key, text in answers.items():
        if key.startswith("var:") and text.strip():
            secrets.put(slug, key.removeprefix("var:"), text.strip())
