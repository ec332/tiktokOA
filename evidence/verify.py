"""Check the exported runs are unchanged since the platform wrote them.

- Every artifact (`<name>.<hash>.json`) still hashes to its sha256 id.
- Every sealed run folder still matches its `records.sha256`, and that seal matches the root
  hash an artifact recorded for it.

    uv run python evidence/verify.py
"""

import json
import re
import sys
from pathlib import Path

from tp.core.hashing import sha256
from tp.stages.execute.record import verify

RUNS = Path(__file__).resolve().parent / "runs"
ARTIFACT = re.compile(r"\.[0-9a-f]{12}\.json$")


def roots(o, out: dict) -> dict:
    """Folder name -> root hash, from every {"folder": ..., "root": ...} in an artifact body."""
    if isinstance(o, dict):
        if isinstance(o.get("folder"), str) and isinstance(o.get("root"), str):
            out[Path(o["folder"]).name] = o["root"]
        for v in o.values():
            roots(v, out)
    elif isinstance(o, list):
        for v in o:
            roots(v, out)
    return out


problems, known = [], {}
arts = [f for f in sorted(RUNS.glob("*/*/*.json")) if ARTIFACT.search(f.name)]
for f in arts:
    art = json.loads(f.read_bytes())
    m = art["manifest"]
    hashed = {k: v for k, v in m.items() if k not in ("id", "created_at")} | {"body": art["body"]}
    if "sha256:" + sha256(hashed) != m["id"] or not m["id"][7:].startswith(f.name[-17:-5]):
        problems.append(f"artifact changed: {f.relative_to(RUNS)}")
    roots(art["body"], known)

seals = sorted(p.parent for p in RUNS.rglob("records.sha256"))
for folder in seals:
    problems += [f"{folder.relative_to(RUNS)}: {p}" for p in verify(folder, known.get(folder.name))]
rooted = sum(f.name in known for f in seals)

print(f"{len(arts)} artifacts, {len(seals)} sealed run folders ({rooted} against a recorded root)")
print("\n".join(problems) or "all intact")
sys.exit(1 if problems else 0)
