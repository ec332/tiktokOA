"""`needs_dependency` (discover D11), moved verbatim from `Discovery.step_dependencies` (W0, C6).
P6 extends it with stand-in wiring (docs/iteration3/P6-standins.md)."""

import re
import subprocess
from pathlib import Path

from tp.stages.discover import ai


def run(d) -> None:
    """`needs_dependency` (D11): an operation that answered 5xx during discovery while the
    container log names an env var the code reads (a string literal in tracked source) and
    nothing supplies. Written to `checks.families.exclude`; otherwise the op stays in."""
    d.exclude = []
    if not d.errors_5xx:
        return
    _, log = d.probes.call("docker", "logs", lambda: d.stack.logs()[-50_000:])
    names = set(re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b", log or ""))
    supplied = set(d.app_env) | set(d.values["reach"].get("env") or {})
    names -= supplied
    if not names:
        return
    r = subprocess.run(["git", "ls-files"], cwd=d.src, capture_output=True, text=True,
                       check=False)  # fmt: skip
    read = set()
    for f in r.stdout.splitlines():
        p = d.src / f
        if Path(f).suffix in ai.DOC_SUFFIXES or not p.is_file() or p.stat().st_size > 1_000_000:
            continue
        text = p.read_text(errors="replace")
        read |= {n for n in names if f'"{n}"' in text or f"'{n}'" in text}
    # shortcut: one log for the whole discovery, so every 5xx op is attributed to the first
    # such variable; upgrade to per-request log windows when a target has two dependencies
    for op, probe in dict(d.errors_5xx).items() if read else ():
        var = min(read)
        d.exclude.append({"op": op, "reason": f"needs_dependency: {var} is not set"})
        d.mark("checks", f"exclude {op}", "probed", "algorithm", probe)
