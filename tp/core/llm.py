"""LLM gateway. Every call goes through call(); replay serves only from llm_cache/."""

import hashlib
import json
from collections.abc import Callable

from tp.core import db, hashing

# (prompt, model, params) -> response text, or (text, usage) when the provider reports usage
Provider = Callable[[str, str, dict], "str | tuple[str, dict]"]

# USD per million tokens (input, output). Unknown models are priced as Opus, never as free.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-haiku-5-5": (0.10, 0.50)}
DEFAULT_BUDGET_USD = 3.00  # per Gateway, i.e. per stage execution (spec design D9)


class ReplayMiss(Exception):
    """Replay asked for a call that was never recorded. Never falls back to live."""


class BudgetExceeded(Exception):
    """This execution already spent its LLM budget; no further live calls are made."""


class LLMError(Exception):
    """The model refused, or stopped before finishing (max_tokens)."""


def cost_usd(model: str, usage: dict) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (usage.get("input_tokens", 0) * pin + usage.get("output_tokens", 0) * pout) / 1e6


def _no_provider(prompt: str, model: str, params: dict) -> str:
    raise RuntimeError("no LLM provider configured")


def anthropic_provider(prompt: str, model: str, params: dict) -> tuple[str, dict]:
    """One Messages API call. params: schema (JSON schema for structured output), system,
    effort, max_tokens. Credentials come from the environment (ANTHROPIC_API_KEY etc.)."""
    import anthropic

    kwargs: dict = {
        "model": model,
        "max_tokens": params.get("max_tokens", 16000),
        "messages": [{"role": "user", "content": prompt}],
    }
    if params.get("system"):
        kwargs["system"] = params["system"]
    output_config: dict = {}
    if params.get("effort"):
        output_config["effort"] = params["effort"]
    if params.get("schema"):
        output_config["format"] = {"type": "json_schema", "schema": params["schema"]}
    if output_config:
        kwargs["output_config"] = output_config
    client = anthropic.Anthropic()
    if model.startswith("claude-opus"):
        # Server-side refusal fallback, routed by refusal category (Haiku has none).
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    else:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise LLMError(f"refused ({getattr(resp.stop_details, 'category', None)})")
    if resp.stop_reason == "max_tokens":
        raise LLMError("hit max_tokens before finishing")
    text = next(b.text for b in resp.content if b.type == "text")
    usage = {
        "model": resp.model,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    return text, usage


def echo_provider(prompt: str, model: str, params: dict) -> str:
    """Deterministic fake used by stub runs, so record/replay is exercised end to end."""
    return f"[stub:{model}] " + prompt


class Gateway:
    def __init__(
        self, mode: str, provider: Provider = _no_provider, budget_usd: float | None = None
    ):
        if mode not in ("live", "record", "replay"):
            raise ValueError(f"unknown mode: {mode}")
        self.mode, self.provider, self.budget_usd = mode, provider, budget_usd
        self.spent_usd = 0.0

    def call(self, prompt: str, *, model: str = "claude-opus-5-5", **params) -> str:
        key = hashing.sha256({"prompt": prompt, "model": model, "params": params})
        path = db.data_dir() / "llm_cache" / f"{key}.json"
        if self.mode == "replay":
            if not path.exists():
                raise ReplayMiss(key)
            return json.loads(path.read_bytes())["response"]
        if self.budget_usd is not None and self.spent_usd >= self.budget_usd:
            raise BudgetExceeded(f"spent ${self.spent_usd:.2f} of ${self.budget_usd:.2f}")
        result = self.provider(prompt, model, params)
        response, usage = result if isinstance(result, tuple) else (result, {})
        cost = cost_usd(model, usage) if usage else 0.0
        self.spent_usd += cost
        # live and record both record: every call is kept (plan Part 5).
        # Plain json, not canonical: canonical form rewrites newlines, replay must be exact.
        record = {"prompt": prompt, "model": model, "params": params, "response": response}
        path.write_text(json.dumps(record | {"usage": usage, "cost_usd": cost}))
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
