"""Mutable working state of one candidate while the orchestrator builds it. `to_model()` gives the wire Candidate."""

from dataclasses import dataclass, field

from ..contracts import PPA, Attempt, Candidate, CandidateStatus, CreatedBy, Equivalence, Fitness, Verification
from ..core.types import MeasureResult, Rtl

FIRST_VERSION_REASON = "First version, written from the plan"


@dataclass(slots=True)
class Cand:
    id: str
    label: str
    round: int
    parent_id: str | None
    created_by: CreatedBy
    reason: str = ""
    rtl: str = ""  # text of the attempt in flight, or of the last attempt
    attempts: list[Attempt] = field(default_factory=list)
    verification: Verification | None = None
    ppa: PPA | None = None
    fitness: Fitness | None = None
    status: CandidateStatus = "drafting"
    fix_count: int = 0
    is_baseline: bool = False
    is_elite: bool = False
    eqy: Equivalence | None = None
    # Backend-only (never on the wire):
    rtl_obj: Rtl | None = None  # the last RTL that passed validation (what the sandbox saw)
    measure: MeasureResult | None = None
    signatures: list[str] = field(default_factory=list)  # failure signature per failed attempt (R5 stall detection)
    eqy_timed_out: bool = False  # the winner's prove EQY hit its time limit (human_ref then skips the reference EQY)
    auto_fixes: int = 0  # deterministic declaration fixes applied (R2), not counted in fix_count
    copy_of: str | None = None  # id of the earlier version this one synthesized to the same result as

    @property
    def number(self) -> int:
        return int(self.id[1:])

    def to_model(self) -> Candidate:
        return Candidate(
            id=self.id,
            label=self.label,
            round=self.round,
            parent_id=self.parent_id,
            created_by=self.created_by,
            reason=self.reason,
            rtl=self.rtl,
            attempts=list(self.attempts),
            verification=self.verification,
            ppa=self.ppa,
            fitness=self.fitness,
            status=self.status,
            fix_count=self.fix_count,
            is_baseline=self.is_baseline,
            is_elite=self.is_elite,
            eqy=self.eqy,
        )

    def reject(self) -> None:
        """Rejected candidates are never ranked and never elite."""
        self.status = "rejected"
        self.fitness = None
        self.is_elite = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "round": self.round,
            "parent_id": self.parent_id,
            "created_by": self.created_by,
            "reason": self.reason,
            "rtl": self.rtl,
            "attempts": [a.model_dump(mode="json") for a in self.attempts],
            "verification": self.verification.model_dump(mode="json") if self.verification else None,
            "ppa": self.ppa.model_dump(mode="json") if self.ppa else None,
            "fitness": self.fitness.model_dump(mode="json") if self.fitness else None,
            "status": self.status,
            "fix_count": self.fix_count,
            "is_baseline": self.is_baseline,
            "is_elite": self.is_elite,
            "eqy": self.eqy.model_dump(mode="json") if self.eqy else None,
            "auto_fixes": self.auto_fixes,
            "copy_of": self.copy_of,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cand":
        cand = cls(
            id=d["id"],
            label=d["label"],
            round=d["round"],
            parent_id=d.get("parent_id"),
            created_by=d["created_by"],
            reason=d.get("reason", ""),
            rtl=d.get("rtl", ""),
            attempts=[Attempt.model_validate(a) for a in d.get("attempts", [])],
            verification=Verification.model_validate(d["verification"]) if d.get("verification") else None,
            ppa=PPA.model_validate(d["ppa"]) if d.get("ppa") else None,
            fitness=Fitness.model_validate(d["fitness"]) if d.get("fitness") else None,
            status=d.get("status", "drafting"),
            fix_count=d.get("fix_count", 0),
            is_baseline=d.get("is_baseline", False),
            is_elite=d.get("is_elite", False),
            eqy=Equivalence.model_validate(d["eqy"]) if d.get("eqy") else None,
            auto_fixes=d.get("auto_fixes", 0),
            copy_of=d.get("copy_of"),
        )
        if cand.rtl:
            try:
                cand.rtl_obj = Rtl(cand.rtl)
            except Exception:
                pass
        return cand
