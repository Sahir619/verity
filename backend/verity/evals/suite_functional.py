"""`functional` suite: spec to first RTL, pass@1 (no repair). Costs LLM credits with --provider nebius.

Per task and repeat: plan, feasibility gate, baseline RTL, rules check, one sandbox verify. `initial_pass` is the pass@1
signal. Nothing is repaired and nothing is measured here.
"""

import logging

from ..core.errors import BudgetExhausted, LLMError, RoleOutputError, RtlRejected, ToolInfraError
from ..core.pack import TaskPack
from ..orchestrator.feasibility import check_plan
from ..orchestrator.plan_ops import normalize_plan
from ..roles import default_roles
from .harness import EvalEnv
from .record import EvalRecord

log = logging.getLogger("verity.evals")


async def run(env: EvalEnv, packs: list[TaskPack], repeats: int) -> None:
    for pack in packs:
        for repeat in range(1, repeats + 1):
            if not env.budget_left():
                log.warning("eval budget reached, stopping the functional suite")
                return
            record = await _one(env, pack, repeat)
            await env.write(record)


async def _one(env: EvalEnv, pack: TaskPack, repeat: int) -> EvalRecord:
    record = env.new_record("functional", pack.id, repeat)
    roles = default_roles()
    llm, ledger = env.new_llm(pack)
    versions: dict[str, str] = {}
    try:
        with record.stage("plan"):
            planned = await roles.plan(llm, pack, pack.default_priority)
        versions[planned.meta.role] = planned.meta.prompt_version
        plan = normalize_plan(planned.plan)
        reasons = check_plan(plan, pack, env.settings)
        if reasons:
            record.initial_pass = False
            record.final_pass = False
            record.note = "plan rejected: " + "; ".join(reasons)[:300]
            return record

        with record.stage("rtl"):
            out = await roles.generate_rtl(llm, pack, plan, pack.default_priority)
        versions[out.meta.role] = out.meta.prompt_version
        try:
            rtl = pack.validate_rtl(out.text, max_bytes=env.settings.rtl.max_bytes)
        except RtlRejected as exc:
            record.initial_pass = False
            record.final_pass = False
            record.note = "rejected by the coding rules: " + "; ".join(exc.problems)[:300]
            return record

        if env.skip_sandbox:
            record.note = "sandbox skipped (--skip-sandbox): rules check only"
            return record
        with record.stage("sim"):
            result = await env.tools.verify(rtl, pack)
        record.initial_pass = result.passed_all(pack.test_count)
        record.final_pass = record.initial_pass
        record.repairs_used = 0
        record.extra["verify_status"] = result.status
        record.extra["tests_passed"] = result.sim.passed if result.sim else 0
    except (BudgetExhausted, LLMError, RoleOutputError, ToolInfraError) as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        record.initial_pass = False
        record.final_pass = False
    finally:
        snap = ledger.snapshot()
        record.llm_calls = snap.llm_calls
        record.input_tokens = snap.input_tokens
        record.output_tokens = snap.output_tokens
        record.est_usd = round(snap.usd, 6)
        record.prompt_versions = versions
    return record
