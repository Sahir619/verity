"""Research requests for the seven fixed CPU block packs."""

from .queries import safe_label, text_sha256
from .service import ResearchRequest


_BLOCK_TITLES: dict[str, str] = {
    "rv32i_imm": "RISC-V immediate generator",
    "rv32i_dec": "RISC-V instruction decoder",
    "rv32i_alu": "RISC-V ALU",
    "rv32i_br": "RISC-V branch comparator",
    "rv32i_rf": "RISC-V register file",
    "rv32i_lsu": "RISC-V load store unit",
    "rv32i_ctl": "RISC-V control state machine",
}
_GOALS = ("area", "speed", "power")


def cpu_block_research(block_id: str, goal: str) -> ResearchRequest:
    """Build the same in-family request as RunManager, with empty knobs for a fixed CPU pack.

    Goal is validated and included in the request hash; queries and the cache key are shared across goals.
    references_for_run applies the run priority when ranking the checked technique ids. Pack and image hashes
    stay unset because this pure helper has no rendered pack or tool runner for comparable past-run counts.
    """
    if block_id not in _BLOCK_TITLES:
        raise ValueError(f"unknown CPU block: {block_id}")
    if goal not in _GOALS:
        raise ValueError(f"unknown CPU research goal: {goal}")
    title = _BLOCK_TITLES[block_id]
    return ResearchRequest(
        intent_class="in_family",
        label=safe_label(title),
        family=block_id,
        family_title=title,
        params={},
        in_family=True,
        text_sha256=text_sha256(f"{block_id} {goal}"),
        verdict_if_ok="buildable_now",
    )
