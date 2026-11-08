# testplat

AI test platform. The build plan is in `Testing platform build plan.md`, and progress is logged under `docs/status/<stage>/`.

```bash
uv sync
uv run pytest                                  # backend tests
uv run tp run --stub --unattended              # headless stub pipeline
uv run tp run --stub                           # stops at the sign-off review
uv run tp review resolve 1 approve             # resolve it; the run resumes
uv run tp stage run gate --from-run r-0001 --input cases=my_cases.json

uv run tp serve                                # API on :8000
cd web && npm install && npm run dev           # UI on http://localhost:5173
npm run gen:client                             # regenerate the TS client after API changes
```

## Demo (iteration 2 acceptance run)

Given a repo URL or a running system's URL, the platform works out how to reach, authenticate and exercise the target, finds defects, and writes a draft PR or an issue with a reproduction. A human steps in only at Clarify and Sign-off. Targets are data, not code: discovery proposes, fixed code verifies each proposal by probing, and the result is a target profile (`data/targets/<slug>/profile.yaml`, every field with source, layer and probe evidence).

Rehearsed end to end on 2026-11-08 (one sample, Claude Haiku 5.5, Docker runs under `caffeinate -i`; total model spend ≈ $0.07). Evidence: `docs/status/demo/`.

| # | Command | Run | Result |
|---|---|---|---|
| 1 | `tp target add https://github.com/paperless-ngx/paperless-ngx@v3.4.2` → `tp run --target paperless-ngx` | r-0039, r-0040 | Served spec, declared auth, admin from documented env vars; 18 fields verified, 0 overrides. Ceiling **"Stable, property-checked"** |
| 2 | `tp target add http://localhost:8010 --live --secret token=$TOKEN` → `tp run --target localhost-8010` → `tp target clean` | r-0041, r-0046 | A Paperless the platform didn't start and can't control (started by hand on the same machine; only URL + token). Safe mode, ceiling **"Seen on the live system"**, 0 leftovers, issue drafts with curl reproductions (`$TOKEN` placeholder) |
| 3 | `tp target add https://github.com/usememos/memos` → `tp run --target memos` | r-0043, r-0044 | Spec declares no security: auth found by probing, first admin created, resource-name IDs and token paging detected. 19 verified, 0 clarify, 0 overrides |
| 4 | `tp target add https://github.com/usememos/memos --no-conventions` | r-0045 | Same fields by exploration alone: 18/19 values equal, 4 fields move from the convention layer to algorithm/exploration. Side by side on the Targets page |
| 5 | `tp watch --target fastapi-template` on the template fork → push → Scan → draft PR | r-0037/38; see `docs/status/demo/` | Draft PRs with a failing test and a retest-proven fix: ec332/full-stack-fastapi-template#1, #2 |

**Why this generalises (step 6):**

- `tests/test_target_literals.py` bans target names and target-specific literals under `tp/`; its baseline is `{}`.
- Paperless and Memos ran with zero target code and zero overrides. Adding a repo means pasting its URL.
- Per-field source and layer counts (convention / algorithm / exploration / clarify) are on the Targets page, and the `--no-conventions` run shows the convention list is a shortcut, not a dependency.
- Extension points: the resolver's strategies (reach, state, mutation, auth, data, spec, safety, checks), the Check families and the target profile schema.
- The template (`targets/fastapi-template/`) is the calibration target: it keeps a hand-written profile plus the extras that can't be discovered (planted-bug patches, `seeded.yaml`, harness).

Not covered: browser surfaces, a domain pack and a held-out onboarding run (cut, `docs/status/demo/001`).
