"""Stage 5 — Env: bring up the reference stack and publish its env_spec."""

from tp.core.contract import StageResult, register
from tp.stages.env.cli import app as cli
from tp.stages.env.service import SERVICE


class EnvStage:
    name = "env"
    version = "1.3.0"  # 1.1: E6 harness, network; 1.2: fixed:<patch>; 1.3: runner in fingerprint
    consumes: dict[str, int] = {}  # noqa: RUF012 — the contract's shape, never mutated
    produces: dict[str, int] = {"env_spec": 1}  # noqa: RUF012
    variant = "reference"  # the other variants are brought up through ctx.env by later stages

    def run(self, ctx, inputs):
        spec = ctx.env.up(self.variant)
        ctx.emit("env_up", variant=spec["variant"], spec_hash=spec["spec_hash"])
        return StageResult.succeeded({"env_spec": spec})


STAGE = register(EnvStage(), service=SERVICE, cli=cli)
