"""A CPU job's own runs (its blocks and cores) never become runs of the chat the job was started from: the chat shows the
job as its CPU card and board, not one "A run" card per block. Fakes only, $0."""

from test_agent import ME, TID, h  # noqa: F401 (h is a fixture)
from test_ask import RUN_ID, seed

CPU_RUN_ID = "r_" + "c" * 32
GONE_RUN_ID = "r_" + "d" * 32


async def chip_and_cpu_runs(h):  # noqa: ANN001, ANN201
    chip = await seed(h.store, session_id=ME)
    cpu = chip.model_copy(update={"id": CPU_RUN_ID, "preset_id": "rv32i_imm"})
    await h.store.create_run(cpu)
    return chip, cpu


async def test_a_cpu_jobs_run_is_not_added_to_the_chat(h) -> None:  # noqa: F811
    chip, cpu = await chip_and_cpu_runs(h)
    await h.checked()
    await h.service.attach_run(TID, ME, cpu)
    assert (await h.thread()).facts["run_ids"] == []  # type: ignore[union-attr]
    await h.service.attach_run(TID, ME, chip)
    facts = (await h.thread()).facts  # type: ignore[union-attr]
    assert facts["run_ids"] == [RUN_ID] and facts["family"] == "cnt8"


async def test_a_cpu_jobs_run_linked_earlier_is_not_shown_in_the_chat(h) -> None:  # noqa: F811
    await chip_and_cpu_runs(h)
    await h.checked(run_ids=[CPU_RUN_ID, RUN_ID, GONE_RUN_ID])
    view = await h.service.read_thread(ME, TID)
    assert view.run_ids == [RUN_ID, GONE_RUN_ID]  # a run the store no longer has is shown as before
