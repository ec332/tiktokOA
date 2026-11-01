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
