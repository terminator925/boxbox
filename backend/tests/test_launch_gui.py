from __future__ import annotations

import importlib.util
import sys
import os
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "launch_gui.py"


def _load_launch_gui():
    spec = importlib.util.spec_from_file_location("launch_gui_script", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_rebuilds_frontend_by_default(monkeypatch, tmp_path, capsys):
    launch_gui = _load_launch_gui()
    build_calls = []
    launch_calls = []

    def fake_launch(name, command, log_path, env_overrides=None):
        launch_calls.append(
            {
                "name": name,
                "command": command,
                "log_path": log_path,
                "env_overrides": env_overrides,
            }
        )
        return 1234

    monkeypatch.setattr(launch_gui, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(launch_gui, "BACKEND_LOG", tmp_path / "logs" / "backend.log")
    monkeypatch.setattr(launch_gui, "FRONTEND_LOG", tmp_path / "logs" / "frontend.log")
    monkeypatch.setattr(launch_gui, "_run_frontend_build", lambda: build_calls.append("build"))
    monkeypatch.setattr(launch_gui, "_python", lambda: Path(sys.executable))
    monkeypatch.setattr(launch_gui, "_is_up", lambda url: True)
    monkeypatch.setattr(launch_gui, "_launch", fake_launch)
    monkeypatch.setattr(launch_gui, "_frontend_dist_freshness", lambda: {"fresh": True})
    monkeypatch.setattr(sys, "argv", ["launch_gui.py"])

    assert launch_gui.main() == 0

    captured = capsys.readouterr().out
    assert build_calls == ["build"]
    assert "frontend_build=built_and_verified" in captured
    assert "backend_pid=already_running" in captured
    assert "frontend_pid=already_running" in captured
    assert "backend_accelerator=torch" in captured
    assert "backend_infer_strategy=core4_adaptive_plus" in captured
    assert "backend_hybrid_strategy=core4" in captured
    assert launch_calls == []


def test_main_can_skip_frontend_build(monkeypatch, tmp_path, capsys):
    launch_gui = _load_launch_gui()

    def fail_build():
        raise AssertionError("frontend build should be skipped")

    monkeypatch.setattr(launch_gui, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(launch_gui, "BACKEND_LOG", tmp_path / "logs" / "backend.log")
    monkeypatch.setattr(launch_gui, "FRONTEND_LOG", tmp_path / "logs" / "frontend.log")
    monkeypatch.setattr(launch_gui, "_run_frontend_build", fail_build)
    monkeypatch.setattr(launch_gui, "_python", lambda: Path(sys.executable))
    monkeypatch.setattr(launch_gui, "_is_up", lambda url: True)
    monkeypatch.setattr(launch_gui, "_launch", lambda name, command, log_path, env_overrides=None: 1234)
    monkeypatch.setattr(launch_gui, "_frontend_dist_freshness", lambda: {"fresh": True})
    monkeypatch.setattr(sys, "argv", ["launch_gui.py", "--skip-frontend-build"])

    assert launch_gui.main() == 0

    captured = capsys.readouterr().out
    assert "frontend_build=skipped" in captured
    assert "frontend_dist_fresh=True" in captured


def test_main_blocks_stale_frontend_when_build_is_skipped(monkeypatch, tmp_path, capsys):
    launch_gui = _load_launch_gui()

    def fail_launch(name, command, log_path, env_overrides=None):
        raise AssertionError("launcher should not start services when stale frontend is blocked")

    monkeypatch.setattr(launch_gui, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(launch_gui, "BACKEND_LOG", tmp_path / "logs" / "backend.log")
    monkeypatch.setattr(launch_gui, "FRONTEND_LOG", tmp_path / "logs" / "frontend.log")
    monkeypatch.setattr(launch_gui, "_run_frontend_build", lambda: None)
    monkeypatch.setattr(launch_gui, "_python", lambda: Path(sys.executable))
    monkeypatch.setattr(launch_gui, "_is_up", lambda url: False)
    monkeypatch.setattr(launch_gui, "_launch", fail_launch)
    monkeypatch.setattr(launch_gui, "_frontend_dist_freshness", lambda: {"fresh": False})
    monkeypatch.setattr(sys, "argv", ["launch_gui.py", "--skip-frontend-build"])

    assert launch_gui.main() == 1

    captured = capsys.readouterr().out
    assert "frontend_build=stale_dist_blocked" in captured
    assert "frontend_dist_fresh=False" in captured
    assert "stale_frontend_hint=run without --skip-frontend-build or pass --allow-stale-frontend intentionally" in captured


def test_main_allows_stale_frontend_when_explicitly_requested(monkeypatch, tmp_path, capsys):
    launch_gui = _load_launch_gui()

    monkeypatch.setattr(launch_gui, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(launch_gui, "BACKEND_LOG", tmp_path / "logs" / "backend.log")
    monkeypatch.setattr(launch_gui, "FRONTEND_LOG", tmp_path / "logs" / "frontend.log")
    monkeypatch.setattr(launch_gui, "_run_frontend_build", lambda: None)
    monkeypatch.setattr(launch_gui, "_python", lambda: Path(sys.executable))
    monkeypatch.setattr(launch_gui, "_is_up", lambda url: True)
    monkeypatch.setattr(launch_gui, "_launch", lambda name, command, log_path, env_overrides=None: 1234)
    monkeypatch.setattr(launch_gui, "_frontend_dist_freshness", lambda: {"fresh": False})
    monkeypatch.setattr(sys, "argv", ["launch_gui.py", "--skip-frontend-build", "--allow-stale-frontend"])

    assert launch_gui.main() == 0

    captured = capsys.readouterr().out
    assert "frontend_build=skipped" in captured
    assert "frontend_dist_fresh=False" in captured


def test_main_launches_backend_with_promoted_env(monkeypatch, tmp_path):
    launch_gui = _load_launch_gui()
    launch_calls = []
    status = {"backend": False, "frontend": False}

    def fake_is_up(url: str) -> bool:
        if url.endswith("/docs"):
            return status["backend"]
        return status["frontend"]

    def fake_launch(name, command, log_path, env_overrides=None):
        launch_calls.append(
            {
                "name": name,
                "command": command,
                "log_path": log_path,
                "env_overrides": dict(env_overrides or {}),
            }
        )
        status[name] = True
        return 1234

    monkeypatch.setattr(launch_gui, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(launch_gui, "BACKEND_LOG", tmp_path / "logs" / "backend.log")
    monkeypatch.setattr(launch_gui, "FRONTEND_LOG", tmp_path / "logs" / "frontend.log")
    monkeypatch.setattr(launch_gui, "_run_frontend_build", lambda: None)
    monkeypatch.setattr(launch_gui, "_python", lambda: Path(sys.executable))
    monkeypatch.setattr(launch_gui, "_is_up", fake_is_up)
    monkeypatch.setattr(launch_gui, "_launch", fake_launch)
    monkeypatch.setattr(launch_gui, "_frontend_dist_freshness", lambda: {"fresh": True})
    monkeypatch.setattr(sys, "argv", ["launch_gui.py"])

    assert launch_gui.main() == 0

    backend_call = next(call for call in launch_calls if call["name"] == "backend")
    assert backend_call["env_overrides"] == launch_gui.PROMOTED_BACKEND_ENV


def test_resolved_backend_env_prefers_existing_environment(monkeypatch):
    launch_gui = _load_launch_gui()
    monkeypatch.setenv("BOXBOX_INFER_ACCELERATOR", "cuda")
    monkeypatch.setenv("BOXBOX_INFER_CANDIDATE_STRATEGY", "all")
    monkeypatch.setenv("BOXBOX_HYBRID_SEARCH_STRATEGY", "all")

    resolved = launch_gui._resolved_backend_env()

    assert resolved == {
        "BOXBOX_INFER_ACCELERATOR": "cuda",
        "BOXBOX_INFER_CANDIDATE_STRATEGY": "all",
        "BOXBOX_HYBRID_SEARCH_STRATEGY": "all",
    }


def test_resolved_backend_env_prefers_cli_overrides(monkeypatch):
    launch_gui = _load_launch_gui()
    monkeypatch.setenv("BOXBOX_INFER_ACCELERATOR", "cuda")
    monkeypatch.setenv("BOXBOX_INFER_CANDIDATE_STRATEGY", "all")
    monkeypatch.setenv("BOXBOX_HYBRID_SEARCH_STRATEGY", "all")

    resolved = launch_gui._resolved_backend_env(
        infer_accelerator="torch",
        inference_candidate_strategy="core4_adaptive_plus",
        hybrid_search_strategy="core4",
    )

    assert resolved == launch_gui.PROMOTED_BACKEND_ENV


def test_run_frontend_build_uses_wasm_build_and_repo_cache(monkeypatch, tmp_path):
    launch_gui = _load_launch_gui()
    commands = []

    def fake_run(command, cwd, env, check):
        commands.append(
            {
                "command": command,
                "cwd": cwd,
                "cache": env.get("npm_config_cache"),
                "check": check,
            }
        )

    monkeypatch.setattr(launch_gui, "ROOT", tmp_path)
    monkeypatch.setattr(launch_gui, "FRONTEND_ROOT", tmp_path / "frontend")
    monkeypatch.setattr(launch_gui.subprocess, "run", fake_run)

    launch_gui._run_frontend_build()

    assert commands == [
        {
            "command": ["npm", "run", "build:wasm"],
            "cwd": tmp_path / "frontend",
            "cache": str(tmp_path / ".npm-cache"),
            "check": True,
        },
        {
            "command": ["npm", "run", "verify:dist"],
            "cwd": tmp_path / "frontend",
            "cache": str(tmp_path / ".npm-cache"),
            "check": True,
        },
    ]


def test_frontend_dist_freshness_compares_source_and_dist_mtimes(monkeypatch, tmp_path):
    launch_gui = _load_launch_gui()
    frontend_root = tmp_path / "frontend"
    src = frontend_root / "src"
    scripts = frontend_root / "scripts"
    dist = frontend_root / "dist"
    src.mkdir(parents=True)
    scripts.mkdir(parents=True)
    dist.mkdir(parents=True)

    source_file = src / "App.jsx"
    helper_file = scripts / "verify_dist.mjs"
    dist_file = dist / "assets" / "index.js"
    dist_file.parent.mkdir(parents=True)
    source_file.write_text("source", encoding="utf8")
    helper_file.write_text("helper", encoding="utf8")
    dist_file.write_text("dist", encoding="utf8")

    monkeypatch.setattr(launch_gui, "FRONTEND_ROOT", frontend_root)
    monkeypatch.setattr(launch_gui, "FRONTEND_SRC", src)
    monkeypatch.setattr(launch_gui, "FRONTEND_SCRIPTS", scripts)
    monkeypatch.setattr(launch_gui, "FRONTEND_DIST", dist)

    os.utime(source_file, (100.0, 100.0))
    os.utime(helper_file, (120.0, 120.0))
    os.utime(dist_file, (200.0, 200.0))
    assert launch_gui._frontend_dist_freshness()["fresh"] is True

    os.utime(source_file, (300.0, 300.0))
    assert launch_gui._frontend_dist_freshness()["fresh"] is False
