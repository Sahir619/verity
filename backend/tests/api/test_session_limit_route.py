"""The 429 reason session_budget_exhausted through the real app: POST /runs and POST /parse answer it with a plain
sentence that has no dollar figure, and the site itself (GET /health) stays open for everyone."""

import pytest
from fastapi.testclient import TestClient

from verity import main as main_module
from verity.tools.local_docker import LocalDockerRunner
from verity_test_support import make_settings

SENTENCE = "You have reached today's spend limit for this session. Try again tomorrow."


async def _sandbox_ok(self) -> None:
    return None


def test_runs_and_parse_answer_429_session_budget_exhausted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LocalDockerRunner, "_check_sandbox", _sandbox_ok)  # no docker call from this test
    base = make_settings(tmp_path / "data")
    settings = base.model_copy(update={"limits": base.limits.model_copy(update={"session_usd": 0.0})})
    with TestClient(main_module.build_app(settings)) as client:
        run = client.post("/runs", json={"presetId": "add8", "priority": "area"})
        assert run.status_code == 429
        assert run.json() == {"error": SENTENCE, "reason": "session_budget_exhausted"}

        parse = client.post("/parse", json={"text": "a 16-bit adder"})
        assert parse.status_code == 429
        assert parse.json() == {"error": SENTENCE, "reason": "session_budget_exhausted"}

        assert client.get("/health").json()["liveEnabled"] is True
        assert client.get("/runs").json() == {"runs": []}  # refused before a run row existed

