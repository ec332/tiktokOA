"""Exploration (discover/001 D1): fixed code gathers the context, one call returns ranked JSON
candidates, fixed code verifies them by probing. Every call goes through the LLM gateway."""

import json
import subprocess
from pathlib import Path

DOC_SUFFIXES = {".md", ".rst", ".txt", ".env", ".yml", ".yaml", ".toml", ".ini", ".conf", ".sh"}
MAX_LIST = 1500  # paths offered to the model, shallowest first
MAX_READ = 3  # files read in full (capped) for one step
PROMPT_CAP = 300_000  # characters of file text in one prompt

FILES_SCHEMA = {
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "string"}}},
    "required": ["files"],
    "additionalProperties": False,
}
ADMIN_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "user_env": {"type": "string"},
                    "password_env": {"type": "string"},
                    "file": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["user_env", "password_env", "file", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def doc_files(src: Path) -> list[str]:
    """Tracked non-code files (docs, env, compose, scripts), shallowest first."""
    r = subprocess.run(["git", "ls-files"], cwd=src, capture_output=True, text=True, check=False)
    names = [f for f in r.stdout.splitlines()
             if Path(f).suffix.lower() in DOC_SUFFIXES or Path(f).name.startswith("Dockerfile")]  # fmt: skip
    return sorted(names, key=lambda f: (f.count("/"), f))[:MAX_LIST]


def _json(raw: str) -> dict:
    return json.loads(raw[raw.find("{") : raw.rfind("}") + 1])


def admin_env(llm, model: str, src: Path, files: list[str], app_env: dict, cap: int):
    """-> {user, password} env var names that create the first admin at start-up, or None."""
    pick = _json(llm.call(
        "You are reading a repository to learn how to run its server for testing.\n"
        "Which files most likely document the environment variables that create the first admin "
        f"(superuser) account when the server starts? Pick at most {MAX_READ}, best first.\n\n"
        "Files:\n" + "\n".join(files),
        model=model, schema=FILES_SCHEMA, max_tokens=2000,
    ))["files"][:MAX_READ]  # fmt: skip
    texts, used = [], 0
    for f in pick:
        p = src / f
        if f in files and p.is_file():
            t = p.read_text(errors="replace")[:cap][: PROMPT_CAP - used]
            used += len(t)
            texts.append(f"=== {f}\n{t}")
    got = _json(llm.call(
        "From these repository files, list the environment variable pairs (user name, password) "
        "that make the server create an admin account at start-up, best first. Quote the line "
        "that documents each pair. Return no candidates if the files don't document one.\n\n"
        "The app service's environment from its compose file:\n" + "\n".join(sorted(app_env)) +
        "\n\n" + "\n\n".join(texts),
        model=model, schema=ADMIN_SCHEMA, max_tokens=2000,
    ))["candidates"]  # fmt: skip
    # shortcut: only the top candidate is brought up (a bring-up per candidate is minutes);
    # try the next ones when a target's top candidate fails (failure log, class exploration miss)
    return {"user": got[0]["user_env"], "password": got[0]["password_env"]} if got else None


PREFIX_SCHEMA = {
    "type": "object",
    "properties": {"prefixes": {"type": "array", "items": {"type": "string"}}},
    "required": ["prefixes"],
    "additionalProperties": False,
}


def auth_prefix(llm, model: str, path: str, op: dict, fields: list[str]) -> list[str]:
    """Header prefixes (`Authorization: <prefix> <token>`) for a login's token, best first;
    asked only when neither a convention nor the spec names one (`--no-conventions`)."""
    text = json.dumps({k: op.get(k) for k in ("operationId", "summary", "description")})[:4000]
    got = _json(llm.call(
        "A login operation of an HTTP API answered with these response fields: "
        f"{', '.join(fields)}.\nOperation POST {path}: {text}\n\n"
        "Which values could go before the token in the Authorization header "
        "(`Authorization: <prefix> <token>`)? List at most 3, best first.",
        model=model, schema=PREFIX_SCHEMA, max_tokens=500,
    ))["prefixes"]  # fmt: skip
    return [p.strip() for p in got if p.strip() and " " not in p.strip()][:3]
