"""LLM gateway. Every call goes through call(); replay serves only from llm_cache/."""

import hashlib
import json
from collections.abc import Callable

from tp.core import db, hashing

Provider = Callable[[str, str, dict], str]  # (prompt, model, params) -> response text


class ReplayMiss(Exception):
    """Replay asked for a call that was never recorded. Never falls back to live."""


def _no_provider(prompt: str, model: str, params: dict) -> str:
    # shortcut: no real provider until the first AI stage (M2) adds one.
    raise RuntimeError("no LLM provider configured")


def echo_provider(prompt: str, model: str, params: dict) -> str:
    """Deterministic fake used by stub runs, so record/replay is exercised end to end."""
    return f"[stub:{model}] " + prompt


class Gateway:
    def __init__(self, mode: str, provider: Provider = _no_provider):
        if mode not in ("live", "record", "replay"):
            raise ValueError(f"unknown mode: {mode}")
        self.mode, self.provider = mode, provider

    def call(self, prompt: str, *, model: str = "claude-opus-5-5", **params) -> str:
        key = hashing.sha256({"prompt": prompt, "model": model, "params": params})
        path = db.data_dir() / "llm_cache" / f"{key}.json"
        if self.mode == "replay":
            if not path.exists():
                raise ReplayMiss(key)
            return json.loads(path.read_bytes())["response"]
        response = self.provider(prompt, model, params)
        # live and record both record: every call is kept (plan Part 5).
        # Plain json, not canonical: canonical form rewrites newlines, replay must be exact.
        path.write_text(
            json.dumps({"prompt": prompt, "model": model, "params": params, "response": response})
        )
        with db.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO llm_calls VALUES (?,?,?,?,?)",
                (
                    key,
                    model,
                    json.dumps(params),
                    hashlib.sha256(response.encode()).hexdigest(),
                    db.now(),
                ),
            )
        return response
