"""Real DryRun on the reference stack (needs Docker, a published runner image and `tp env up
reference`). The bindings are hand-written (a scripted model), so no API key is needed."""

import json
from pathlib import Path

import pytest

from tp.core import contract
from tp.stages.generate import loop

pytestmark = pytest.mark.docker
DATA = Path(__file__).parents[4] / "data"

BINDINGS = {
    "TC-O6-2": 'api.get("/api/v1/items/", token=user["token"], params={"skip": data["skip"]})',
    "TC-C11-1": 'api.post("/api/v1/items/", token=user["token"], json={"title": data["title"]})',
    "TC-C8-1": """
other = make.user()
target = make.item(owner=other) if hints["item owner"] == "other user" else item
token = user["token"] if hints["caller"] == "logged-in user" else None
path = f"/api/v1/items/{target['id']}"
if hints["operation"] == "view":
    api.get(path, token=token)
elif hints["operation"] == "change":
    api.put(path, token=token, json={"title": "changed"})
else:
    api.delete(path, token=token)
""",
    "TC-C13-2": """
for _ in range(int(hints["total"]) - 1):
    make.item(owner=user)
r = api.get("/api/v1/items/", token=user["token"], params={"skip": data["skip"], "limit": data["limit"]})
observe("count", r.json()["count"])
observe("data_len", len(r.json()["data"]))
""",
}


class Scripted:
    def call(self, prompt, **kw):
        uid = json.loads(prompt.split("## The case\n\n")[1].split("\n\n## The routes")[0])["id"]
        return json.dumps({"binding": BINDINGS[uid].strip(), "cannot_bind": ""})


class Ctx:
    llm = Scripted()

    def __init__(self):
        contract.load()
        self.env = contract.SERVICES["env"]

    def emit(self, *a, **k):
        pass


@pytest.fixture
def real(monkeypatch, fx, tmp_path):
    monkeypatch.setenv("TP_DATA", str(DATA))
    ctx = Ctx()
    ctx.env.ensure("reference")
    units = [u for u in fx["cases"]["cases"] if u["id"] in BINDINGS]

    def go(variant="reference"):
        tests, ledger = loop.bind_all(ctx, units, fx["criteria"], ctx.env.openapi("reference"),
                                      tmp_path / variant.replace(":", "_"), "v2", variant)  # fmt: skip
        return tests, ledger

    return go


def test_bound_tests_on_the_reference_stack(real):  # G4
    tests, ledger = real()
    assert {u: t["status"] for u, t in tests.items()} == dict.fromkeys(BINDINGS, "BOUND"), ledger
    assert tests["TC-O6-2"]["dry_run"] == {"TC-O6-2.a": "PRODUCT_ERROR", "TC-O6-2.b": None,
                                           "TC-O6-2.c": None}  # the real pagination 500  # fmt: skip
    assert set(tests["TC-C11-1"]["dry_run"].values()) == {None}
    assert set(tests["TC-C8-1"]["dry_run"].values()) == {None}
    assert tests["TC-C13-2"]["dry_run"] == {"TC-C13-2": None}
    assert all(a["outcome"] == "bound" for a in ledger)


def test_the_seeded_title_bug_fails_as_an_assertion(real):
    tests, _ = real("bug:BUG_TITLE_MAX")
    assert tests["TC-C11-1"]["dry_run"] == {"TC-C11-1.a": None, "TC-C11-1.b": "ASSERTION",
                                            "TC-C11-1.c": None}  # fmt: skip
