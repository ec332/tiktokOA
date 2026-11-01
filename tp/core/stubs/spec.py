from tp.core.contract import StageResult
from tp.core.stubs import Stub


class SpecStub(Stub):
    def run(self, ctx, inputs):
        # One LLM call so stub runs exercise record/replay end to end.
        summary = ctx.llm.call("stub: summarise requirements.md")
        return StageResult.succeeded({"spec": {"stub": "spec", "summary": summary}})


STAGE = SpecStub("spec", [], "spec")
