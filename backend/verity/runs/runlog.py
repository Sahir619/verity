"""RunLog builder (CONTRACTS section 6, last paragraph). The recorded format equals frontend/public/runs/golden.json,
so a real run can be dropped in as the replay fixture with no conversion."""

from collections.abc import Sequence
from datetime import UTC, datetime

from ..config import CHAT_ROLES, RUN_ROLES, Settings
from ..contracts import CaptionEvent, Chapter, EventBase, RunLog, RunLogMeta, SpecEvent
from ..core.captions import CHAPTER_ID_BY_LABEL, CHAPTERS
from ..core.types import RunRecord

ROLES = RUN_ROLES + CHAT_ROLES  # the chat roles are listed only for a run that used research (see build_run_log)


def _iso_z(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_run_log(run: RunRecord, events: Sequence[EventBase], settings: Settings) -> RunLog:
    """RunLog from the stored events of one run. `settings` is part of the contract signature (reserved for
    formatting options); the log content comes only from the run record and the events."""
    ordered = sorted(events, key=lambda e: e.seq)

    title = run.preset_id
    for event in ordered:
        if isinstance(event, SpecEvent):
            title = event.spec.title
            break

    first_seen: dict[str, int] = {}
    for event in ordered:
        if isinstance(event, CaptionEvent) and event.chapter and event.chapter not in first_seen:
            first_seen[event.chapter] = event.t_ms
    chapters = [
        Chapter(id=CHAPTER_ID_BY_LABEL[label], label=label, t_ms=first_seen[label])
        for _, label in CHAPTERS
        if label in first_seen
    ]

    roles = ROLES if run.research_id else RUN_ROLES
    models = ", ".join(f"{role}={run.models[role]}" for role in roles if role in run.models)
    note = "Recorded live run." + (f" Models: {models}." if models else "")

    return RunLog(
        meta=RunLogMeta(
            run_id=run.id,
            sample=False,
            title=title,
            preset_id=run.preset_id,
            recorded_at=_iso_z(run.started_at or run.created_at),
            duration_ms=max((e.t_ms for e in ordered), default=0),
            chapters=chapters,
            note=note,
        ),
        events=list(ordered),
    )
