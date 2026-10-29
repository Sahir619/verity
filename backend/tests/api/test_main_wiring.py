"""verity.main: the composition root builds the whole app from settings, and production refuses to start unsafe."""

import pytest
from fastapi.testclient import TestClient

from verity import main as main_module
from verity.config import AuthConfig, Settings
from verity.core import captions as cap
from verity.core.errors import PackIntegrityError
from verity.tools.local_docker import LocalDockerRunner
from verity_test_support import make_settings


async def _sandbox_ok(self) -> None:
    return None


def test_build_app_wires_every_builder_and_serves_health(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LocalDockerRunner, "_check_sandbox", _sandbox_ok)  # no docker call from this test
    settings = make_settings(tmp_path / "data")
    app = main_module.build_app(settings)  # packs, fake provider, local docker runner (never called), store, manager
    with TestClient(app) as client:  # the lifespan opens the store and starts the manager
        body = client.get("/health").json()
        assert body["liveEnabled"] is True and body["provider"] == "fake" and body["authRequired"] is False
        assert set(body["models"]) == {
            "planner", "rtl_gen", "repair", "optimizer", "router", "parser", "chat", "research", "memory"
        }
        assert "research" in body["features"] and "agent" in body["features"]  # fake provider: the fake search adapter, never Serper
        assert (tmp_path / "data" / "verity.db").is_file()
        assert client.get("/nope").status_code == 404
        assert client.options("/runs", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}).status_code in (200, 204)


def test_health_and_new_runs_report_sandbox_down_when_docker_cannot_run(tmp_path) -> None:
    settings = make_settings(tmp_path / "data")
    tools = settings.tools.model_copy(update={"docker_bin": "verity-no-such-docker-binary"})
    app = main_module.build_app(settings.model_copy(update={"tools": tools}))
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["liveEnabled"] is False and body["reason"] == "sandbox_down"
        res = client.post("/runs", json={"presetId": "add8", "priority": "area"})
        assert res.status_code == 503
        assert res.json() == {"error": cap.DOCKER_DOWN, "reason": "sandbox_down"}
        assert client.get("/runs").json() == {"runs": []}  # refused before a run row existed


def test_a_missing_task_pack_directory_still_builds_but_a_tampered_pack_refuses(tmp_path) -> None:
    empty = make_settings(tmp_path / "data", tasks_dir=tmp_path / "no-tasks")
    assert main_module.build_app(empty) is not None  # warns that no live preset exists

    import shutil
    from verity.config import AGENT_ROOT

    tasks = tmp_path / "tasks"
    shutil.copytree(AGENT_ROOT / "tasks" / "mac4", tasks / "mac4", ignore=shutil.ignore_patterns("__pycache__"))
    (tasks / "mac4" / "golden.py").write_text("# tampered\n", encoding="utf-8")
    with pytest.raises(PackIntegrityError):
        main_module.build_app(make_settings(tmp_path / "data2", tasks_dir=tasks))


def test_main_refuses_to_start_in_unsafe_production(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    unsafe: Settings = make_settings(tmp_path / "data").model_copy(update={"env": "production", "auth": AuthConfig(required=False)})
    monkeypatch.setattr(main_module, "load_settings", lambda: unsafe)
    started: list[bool] = []
    monkeypatch.setattr(main_module.uvicorn, "run", lambda *a, **k: started.append(True))
    with pytest.raises(SystemExit) as exit_info:
        main_module.main()
    assert exit_info.value.code == 1 and not started
    assert "refusing to start" in capsys.readouterr().err
