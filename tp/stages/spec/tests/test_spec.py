"""Spec stage with a scripted fake model (no API key): P1–P5 behaviour end to end."""

import json
import subprocess

import pytest

from tp.core import artifacts, hashing, llm, review, runner
from tp.stages.spec import ai, checks, code_index, questions
from tp.stages.spec import stage as spec_stage

MAIN = """from fastapi import APIRouter
from app.api.routes import items, private
api_router = APIRouter()
api_router.include_router(items.router)
if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
"""
ITEMS = """from fastapi import APIRouter
router = APIRouter(prefix="/items", tags=["items"])


@router.get("/")
def read_items(skip: int = 0, limit: int = 100):
    return session.exec(select(Item).offset(skip).limit(limit)).all()


@router.get("/{id}")
def read_item(id: uuid.UUID):
    return session.get(Item, id)


@router.post("/")
def create_item(item_in: ItemCreate):
    return item_in
"""
PRIVATE = """from fastapi import APIRouter
router = APIRouter(prefix="/private")


@router.post("/users/")
def create_user(user_in: dict):
    return user_in
"""
FILES = {
    "backend/app/api/main.py": MAIN,
    "backend/app/api/routes/items.py": ITEMS,
    "backend/app/api/routes/private.py": PRIVATE,
}
STORY = """# Items
- Every item must have a title of at most 255 characters.
- A page shows a reasonable number of items by default.
"""
ITEMS_PY = "backend/app/api/routes/items.py"


def test_code_index_routes_prefixes_and_dev_only_mounts():
    idx = code_index.build(FILES, "/api/v1")
    got = {(r["method"], r["path"], r["line"], r["mounted_if"]) for r in idx["routes"]}
    assert got == {
        ("GET", "/api/v1/items/", 5, None),
        ("GET", "/api/v1/items/{id}", 10, None),
        ("POST", "/api/v1/items/", 15, None),
        ("POST", "/api/v1/private/users/", 5, "settings.FASTAPI_ENV == 'development'"),
    }
    assert idx["unmounted"] == []  # the aggregator in main.py is not a stray router


def test_code_index_reads_the_pinned_ref_not_the_patched_tree(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                       cwd=tmp_path, check=True, capture_output=True)  # fmt: skip

    git("init", "-q")
    for path, text in FILES.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    (tmp_path / "backend/app/alembic").mkdir()
    (tmp_path / "backend/app/alembic/env.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    ref = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, check=True,
                         text=True).stdout.strip()  # fmt: skip
    (tmp_path / ITEMS_PY).write_text(ITEMS + '\n\n@router.get("/seeded-bug")\ndef bug(): ...\n')
    files = code_index.source_files(tmp_path, ref, ["backend/app/alembic/**"])
    assert set(files) == set(FILES)  # alembic excluded
    paths = [r["path"] for r in code_index.build(files, "/api/v1")["routes"]]
    assert "/api/v1/items/seeded-bug" not in paths


def test_reverse_trace_matches_path_params():
    idx = code_index.build(FILES, "/api/v1")
    left = code_index.reverse_trace(idx, [{"routes": ["GET /api/v1/items/{item_id}"]}])
    assert {(r["method"], r["path"]) for r in left} == {
        ("GET", "/api/v1/items/"), ("POST", "/api/v1/items/"), ("POST", "/api/v1/private/users/"),
    }  # fmt: skip


def test_find_quote_ignores_whitespace_and_case():
    assert checks.find_quote(STORY, "every item must have a title  of at most 255") == (2, 2)
    assert checks.find_quote(STORY, "titles are optional") is None


def test_lint_taxonomy():
    vague = {"title": "page size", "actor": "user", "given": "a user", "when": "lists items",
             "then": "a reasonable number of items is returned", "status_code": None}  # fmt: skip
    assert "vague_quantifier" in checks.lint(vague)
    limit = {"title": "title", "actor": "user", "given": "a user", "when": "posts a title",
             "then": "titles up to a maximum length are accepted", "status_code": 200}  # fmt: skip
    assert {"missing_value", "missing_error_path"} <= set(checks.lint(limit))
    exact = {"title": "t", "actor": "user", "given": "a user",
             "when": "posts a title longer than 255 characters",
             "then": "rejected", "status_code": 422}  # fmt: skip
    assert "unspecified_boundary" in checks.lint(exact) and "missing_actor" not in checks.lint(
        exact
    )


# ---- the stage, end to end, with a scripted model ----------------------------------------

EXTRACTED = {
    "criteria": [
        {"title": "Title length", "actor": "user", "given": "a logged-in user",
         "when": "creates an item with a 256-character title", "then": "the item is rejected",
         "status_code": 422, "human_judgement": False, "routes": [],
         "source_quote": "Every item must have a title of at most 255 characters."},
        {"title": "Default page size", "actor": "user", "given": "a user with items",
         "when": "lists items without a limit", "then": "a reasonable number of items is shown",
         "status_code": None, "human_judgement": False, "routes": [],
         "source_quote": "A page shows a reasonable number of items by default."},
        {"title": "Invented", "actor": "user", "given": "x", "when": "y", "then": "z",
         "status_code": 200, "human_judgement": False, "routes": [],
         "source_quote": "this sentence is not in the story"},
    ]
}  # fmt: skip
INVESTIGATED = {
    "criterion_routes": [
        {"criterion": "C-1", "routes": ["POST /api/v1/items/"]},
        {"criterion": "C-2", "routes": ["GET /api/v1/items/", "GET /api/v1/not-a-route"]},
    ],
    "findings": [
        {"kind": "missing_error_path", "summary": "skip/limit have no bounds",
         "evidence": [{"file": ITEMS_PY, "line": 6}], "criteria": ["C-2"]},
        {"kind": "no_requirement", "summary": "cites a line that doesn't exist",
         "evidence": [{"file": ITEMS_PY, "line": 999}], "criteria": []},
    ],
    "observed": [
        {"title": "Negative skip", "actor": "user", "given": "a user", "when": "lists with skip=-1",
         "then": "the server errors", "status_code": 500, "routes": ["GET /api/v1/items/"],
         "evidence": [{"file": ITEMS_PY, "line": 7}], "suspected_defect": True,
         "alternatives": ["Rejected with a 422 validation error", "Treated as skip=0"]},
        {"title": "Unknown item", "actor": "user", "given": "a user", "when": "gets a missing id",
         "then": "returns nothing", "status_code": 404, "routes": ["GET /api/v1/items/{id}"],
         "evidence": [{"file": ITEMS_PY, "line": 11}], "suspected_defect": False,
         "alternatives": []},
    ],
    "questions": [
        {"criterion": "C-2", "about": "default page size", "why": "the default is not stated",
         "options": ["100 items", "20 items"], "changes": "expected_value"},
        {"criterion": "C-1", "about": "wording", "why": "cosmetic", "options": ["a", "b"],
         "changes": "none"},
    ],
}  # fmt: skip


def fake_model(prompt, model, params):
    if params["schema"] == ai.EXTRACT_SCHEMA:
        assert "items.py" not in prompt and "def read_items" not in prompt  # never sees code
        return json.dumps(EXTRACTED), {"input_tokens": 1000, "output_tokens": 500}
    if params["schema"] == ai.INVESTIGATE_SCHEMA:
        assert "def read_items" in prompt and model == "claude-opus-5-5"
        return json.dumps(INVESTIGATED), {"input_tokens": 9000, "output_tokens": 2000}
    assert model == "claude-haiku-5-5"
    qs = json.loads(prompt.split("<questions>")[1].split("</questions>")[0])
    out = [{"key": q["key"], "text": f"Q: {q['about']}?",
            "choices": [{"id": chr(97 + i), "text": o} for i, o in enumerate(q["options"])],
            "recommended": "a"} for q in qs]  # fmt: skip
    return json.dumps({"questions": out}), {"input_tokens": 300, "output_tokens": 200}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(llm, "anthropic_provider", fake_model)
    monkeypatch.setattr(spec_stage, "load_index", lambda: (
        code_index.build(FILES, "/api/v1") | {"sut": {"name": "fake", "ref": "a" * 40}}, FILES))  # fmt: skip


def _start(mode="record", unattended=False, text=STORY, kind="story"):
    req = artifacts.put("requirements", {"kind": kind, "text": text})
    run_id = runner.start_run(
        mode, stub=False, unattended=unattended, inputs={"requirements": req.id}
    )
    runner.advance(run_id)
    return run_id


def _spec(run_id):
    ex = next(e for e in runner.get_run(run_id)["executions"] if e["stage"] == "spec")
    return ex, (artifacts.get(ex["outputs"]["spec"]).body if ex["outputs"] else None)


def _answer_all(run_id, defect_choice="a", page_choice="b"):
    for item in review.list_(status="open"):
        p = item["payload"]
        if item["kind"] not in ("question", "confirm_observed"):
            continue  # e.g. the cases stub's sign-off, once the spec is frozen
        if item["kind"] == "confirm_observed":
            review.resolve(item["id"], "confirm", "po")
        elif p["kind"] == "defect":
            review.resolve(item["id"], "pick", "po", detail={"choice": defect_choice})
        else:
            review.resolve(item["id"], "pick", "po", detail={"choice": page_choice})
    return runner.resume(item["id"])


def test_story_round_trip(model):
    run_id = _start()
    ex, _ = _spec(run_id)
    assert ex["status"] == "awaiting_review"
    items = review.list_(status="open")
    kinds = sorted((i["kind"], i["payload"].get("about")) for i in items)
    assert kinds == [("confirm_observed", None), ("question", "default page size"),
                     ("question", "intended behaviour")]  # fmt: skip
    # suspected defect asked first; the cosmetic question (changes: none) was filtered out
    assert items[0]["payload"]["about"] == "intended behaviour"

    _answer_all(run_id)
    ex, spec = _spec(run_id)
    assert ex["status"] == "succeeded", ex["reason"]
    c = {x["id"]: x for x in spec["criteria"]}
    assert c["C-1"]["status"] == "confirmed" and c["C-1"]["routes"] == ["POST /api/v1/items/"]
    assert c["C-2"]["routes"] == ["GET /api/v1/items/"]  # the invented route was dropped
    assert c["C-2"]["resolution"] == "20 items" and c["C-2"]["status"] == "confirmed"
    assert c["O-1"]["suspected_defect"] and c["O-1"]["status_code"] == 422  # not the 500
    assert c["O-2"]["status"] == "confirmed"
    assert "C-3" not in c and spec["rejected"][0]["reasons"] == [
        "source_quote not found in the requirements"]  # fmt: skip
    assert [f["summary"] for f in spec["findings"]] == ["skip/limit have no bounds"]
    assert spec["unmapped_routes"] == [
        "POST /api/v1/private/users/  (backend/app/api/routes/private.py:5)"]  # fmt: skip
    assert spec["version"] == 2 and spec["history"][0]["changes"]
    assert c["C-1"]["sources"][0]["lines"] == "L2-L2"


def test_unattended_assumes_defaults_and_drops_unconfirmed_observed(model):
    run_id = _start(unattended=True)
    ex, spec = _spec(run_id)
    assert ex["status"] == "succeeded", ex["reason"]
    c = {x["id"]: x for x in spec["criteria"]}
    assert c["C-2"]["status"] == "assumed" and c["C-2"]["review_needed"]
    assert c["C-2"]["resolution"] == "100 items"  # the recommended default
    assert c["O-2"]["status"] == "out_of_scope"  # never confirmed -> never reaches cases
    assert all(d["assumed"] for d in spec["decisions"])


def test_same_answers_in_replay_give_the_same_spec_hash(model, tmp_path, monkeypatch):
    first = _start("record")
    _answer_all(first)
    spec_hash = hashing.sha256(_spec(first)[1])
    cache = {p.name: p.read_bytes() for p in (tmp_path / "llm_cache").iterdir()}

    monkeypatch.setattr(llm, "anthropic_provider", lambda *a: pytest.fail("replay went live"))
    replays = []
    for n in range(2):  # each in a clean data dir holding only the recorded calls, as in CI
        fresh = tmp_path / f"fresh{n}"
        monkeypatch.setenv("TP_DATA", str(fresh))
        (fresh / "llm_cache").mkdir(parents=True)
        for name, data in cache.items():
            (fresh / "llm_cache" / name).write_bytes(data)
        run_id = _start("replay")
        _answer_all(run_id)
        replays.append(_spec(run_id)[0]["outputs"]["spec"])
    # same body as the recorded run; the id differs only by its label (real vs recorded)
    assert hashing.sha256(artifacts.get(replays[0]).body) == spec_hash
    assert artifacts.get(replays[0]).labels == ["recorded"]
    assert replays[0] == replays[1]  # replay twice -> the identical artifact


def test_an_answer_from_an_earlier_run_is_reused_not_asked_again(model):
    _answer_all(_start())
    run_id = _start()
    asked = [i["payload"]["about"] for i in review.list_(status="open") if i["kind"] == "question"]
    assert asked == []  # both questions were answered in the first run
    _, _ = _spec(run_id)


def test_brief_path_and_rejecting_observed_behaviour_asks_what_should_happen(model):
    run_id = _start(text="Test paging through items.", kind="brief")
    co = next(i for i in review.list_(status="open") if i["kind"] == "confirm_observed")
    for item in review.list_(status="open"):
        if item["id"] == co["id"]:
            review.resolve(item["id"], "reject", "po")
        else:
            review.resolve(item["id"], "defer", "po")
    runner.resume(co["id"])
    follow = [i for i in review.list_(status="open") if i["kind"] == "question"]
    assert len(follow) == 1 and follow[0]["payload"]["round"] == 2
    review.resolve(follow[0]["id"], "defer", "po")
    runner.resume(follow[0]["id"])
    ex, spec = _spec(run_id)
    assert ex["status"] == "succeeded", ex["reason"]
    o2 = next(c for c in spec["criteria"] if c["id"] == "O-2")
    assert o2["status"] == "assumed" and o2["review_needed"]


def test_questions_filter_caps_and_requires_two_options():
    crit = {"C-1": {"id": "C-1", "title": "t", "given": "g", "when": "w", "then": "x",
                    "status": "draft"}}  # fmt: skip
    cands = [{"criterion": "C-1", "about": f"q{i}", "why": "", "options": ["a", "b"],
              "changes": "pass_fail", "kind": "ambiguity"} for i in range(7)]  # fmt: skip
    cands.append({"criterion": "C-1", "about": "one", "why": "", "options": ["a", "a"],
                  "changes": "pass_fail", "kind": "ambiguity"})  # fmt: skip
    ask, reused = questions.filter_(cands, crit, set())
    assert len(ask) == questions.PER_ROUND and reused == []
