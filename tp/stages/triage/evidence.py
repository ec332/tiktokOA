"""EvidenceIndex: resolves evidence ids to run records, HTTP exchanges, log lines and tracebacks
(design D1, D7). Ids: `ex:<batch>/<case>` (a record), `ex:<batch>/<case>#<step>` (one exchange),
`log:<trace>` (the trace line + the traceback that follows it), `probe:<batch>/<pre|post>`."""

import re

FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<fn>\S+)')
EXC = re.compile(r"^(?P<cls>[A-Za-z_][\w.]*): (?P<msg>.*)$")
APP_PREFIX = "/app/backend/app/"  # the SUT repo is at /app in the backend container


class EvidenceIndex:
    def __init__(self, rows: list[dict], probes: list[dict], logs: dict[str, str]):
        self.rows = {r["id"]: r for r in rows}
        self.probes = {p["id"]: p for p in probes}
        self.logs = {v: text.splitlines() for v, text in logs.items()}

    def resolve(self, eid: str):
        """The evidence an id names, or None if it doesn't exist (never invented)."""
        kind, _, rest = eid.partition(":")
        if kind == "ex":
            rid, _, step = rest.partition("#")
            row = self.rows.get(f"ex:{rid}")
            if row is None or not step:
                return row
            return next((e for e in row.get("exchanges", []) if str(e["step"]) == step), None)
        if kind == "probe":
            return self.probes.get(eid)
        if kind == "log":
            return self.log(rest)
        return None

    def exists(self, eid: str) -> bool:
        return self.resolve(eid) is not None

    def log(self, trace: str) -> dict | None:
        """The trace line for one request and the traceback printed right after it.
        shortcut: the traceback is linked by adjacency (uvicorn prints it without the trace id,
        after the trace middleware's line); valid because tests run one request at a time.
        Upgrade: have the trace patch log the traceback itself."""
        needle = f"x-test-trace={trace}"
        for variant, lines in self.logs.items():
            for i, line in enumerate(lines):
                if line.endswith(needle) or f"{needle} " in line:
                    return {"variant": variant, "line_no": i + 1, "line": line,
                            "traceback": _traceback_after(lines, i)}  # fmt: skip
        return None


def _traceback_after(lines: list[str], i: int) -> dict | None:
    """The 'Exception in ASGI application' block within a few lines of the trace line."""
    start = next((j for j in range(i + 1, min(i + 4, len(lines)))
                  if "Exception in ASGI application" in lines[j]), None)  # fmt: skip
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and not lines[end].startswith(("INFO:", "WARNING:", "ERROR:")):
        end += 1
    block = lines[start:end]
    frames = [m.groupdict() | {"line": int(m["line"])} for x in block if (m := FRAME.match(x))]
    errors = [m.groupdict() for x in block if (m := EXC.match(x)) and "." in m["cls"]]
    return {"text": "\n".join(block), "frames": frames,
            "app_frames": [f for f in frames if f["path"].startswith(APP_PREFIX)],
            "root_cause": errors[0] if errors else None}  # fmt: skip


def repo_path(container_path: str) -> str:
    return container_path.removeprefix("/app/")
