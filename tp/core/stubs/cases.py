from tp.core.contract import StageResult
from tp.core.stubs import Stub

LINES = [
    {"id": "TC-1", "text": "title of 255 chars is accepted"},
    {"id": "TC-2", "text": "title of 256 chars is rejected with 422"},
    {"id": "TC-3", "text": "non-owner cannot update an item"},
]


class CasesStub(Stub):
    """Raises the skeleton's one fake review: a line-level sign-off.

    A rejected line re-opens alone in a new item; the other lines keep their approval.
    """

    def run(self, ctx, inputs):
        items = ctx.review.items()
        if not items:
            return StageResult.awaiting_review([ctx.review.create("signoff", {"lines": LINES})])
        if any(i["status"] == "open" for i in items):
            return StageResult.awaiting_review([i["id"] for i in items if i["status"] == "open"])
        last = items[-1]
        if last["decision"] == "defer":
            return self._done(inputs, signed=False)
        rejected = [
            {**line, "comment": last["detail"]["lines"][line["id"]]["comment"]}
            for line in last["payload"]["lines"]
            if last["detail"]["lines"][line["id"]]["decision"] == "reject"
        ]
        if rejected:
            return StageResult.awaiting_review([ctx.review.create("signoff", {"lines": rejected})])
        return self._done(inputs, signed=True)

    def _done(self, inputs, signed: bool):
        body = {"stub": "cases", "spec": inputs["spec"].id, "signed": signed}
        return StageResult.succeeded({"cases": body}, labels=[] if signed else ["unsigned"])


STAGE = CasesStub("cases", ["spec"], "cases")
