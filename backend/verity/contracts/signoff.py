"""Sign-off, waveform and spec-rules artifacts (backend/CONTRACTS.md section 6.3). Each one is delivered as an
`artifact` event whose content is this model as JSON:

  signoff/<candidateId>.json   Signoff      after the winner and the human reference proof, on every run
  waves/<candidateId>.json     Waves        a failing version before its repair, and the winner
  spec/rules.json              SpecRules    at the spec stage

Every field added after the first version is optional, so older clients and older run logs keep working.
"""

from typing import Literal

from pydantic import Field

from .base import WireModel, opt

EqyVsV1 = Literal["proven", "inconclusive", "not_equivalent", "not_run", "v1_is_winner"]
EqyVsHuman = Literal["proven", "inconclusive", "not_equivalent", "not_run"]
StrengthStatus = Literal["ok", "skipped", "failed"]


class SeedTally(WireModel):
    seed: int
    passed: int
    total: int


class SignoffTests(WireModel):
    seeds_run: int
    seeds_passed: int
    per_seed: list[SeedTally] = Field(default_factory=list)


class SignoffLint(WireModel):
    errors: int
    warnings: int
    by_tag: dict[str, int] = Field(default_factory=dict)
    latches: int


class SignoffStrength(WireModel):
    """Planted bugs: planted = caught + harmless + undecided + missed (missed = proven different, the tests let it
    through). Survivors beyond the proof limit count as undecided."""

    planted: int
    caught: int
    harmless: int
    undecided: int
    status: StrengthStatus
    note: str | None = opt()


class Signoff(WireModel):
    candidate_id: str
    tests: SignoffTests
    lint: SignoffLint
    eqy_vs_v1: EqyVsV1
    eqy_vs_human: EqyVsHuman
    strength: SignoffStrength | None
    duration_ms: int


class WaveSignal(WireModel):
    name: str
    width: int
    got: list[str]  # one value per cycle; "x" for unknown
    expected: list[str] | None = opt()  # same length; "" where the test expects nothing


class Waves(WireModel):
    candidate_id: str
    case: str
    fail_cycle: int | None
    start_cycle: int
    signals: list[WaveSignal] = Field(default_factory=list)
    note: str | None = opt()


class SpecRules(WireModel):
    rules: list[str]
