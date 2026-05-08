from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "outputs" / "gui_launch"
BACKEND_LOG = LOG_DIR / "backend.log"
FRONTEND_LOG = LOG_DIR / "frontend.log"
FRONTEND_ROOT = ROOT / "frontend"
FRONTEND_DIST = FRONTEND_ROOT / "dist"
FRONTEND_SRC = FRONTEND_ROOT / "src"
FRONTEND_SCRIPTS = FRONTEND_ROOT / "scripts"
PROMOTED_BACKEND_ENV = {
    "BOXBOX_INFER_ACCELERATOR": "torch",
    "BOXBOX_INFER_CANDIDATE_STRATEGY": "core4_adaptive_plus",
    "BOXBOX_HYBRID_SEARCH_STRATEGY": "core4",
}


def _python() -> Path:
    for candidate in (
        ROOT / "backend" / ".venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _is_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def _resolved_backend_env(
    *,
    infer_accelerator: str | None = None,
    inference_candidate_strategy: str | None = None,
    hybrid_search_strategy: str | None = None,
) -> dict[str, str]:
    env = dict(PROMOTED_BACKEND_ENV)
    env["BOXBOX_INFER_ACCELERATOR"] = str(
        infer_accelerator or os.environ.get("BOXBOX_INFER_ACCELERATOR", env["BOXBOX_INFER_ACCELERATOR"])
    )
    env["BOXBOX_INFER_CANDIDATE_STRATEGY"] = str(
        inference_candidate_strategy
        or os.environ.get("BOXBOX_INFER_CANDIDATE_STRATEGY", env["BOXBOX_INFER_CANDIDATE_STRATEGY"])
    )
    env["BOXBOX_HYBRID_SEARCH_STRATEGY"] = str(
        hybrid_search_strategy
        or os.environ.get("BOXBOX_HYBRID_SEARCH_STRATEGY", env["BOXBOX_HYBRID_SEARCH_STRATEGY"])
    )
    return env


def _launch(name: str, command: list[str], log_path: Path, env_overrides: dict[str, str] | None = None) -> int:
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    with log_path.open("ab", buffering=0) as log:
        log.write(f"\n\n--- launching {name} at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
        if env_overrides:
            for key, value in sorted(env_overrides.items()):
                log.write(f"{key}={value}\n".encode())
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
            close_fds=True,
        )
    return process.pid


def _run_frontend_build() -> None:
    env = os.environ.copy()
    env["npm_config_cache"] = str(ROOT / ".npm-cache")
    for command in (["npm", "run", "build:wasm"], ["npm", "run", "verify:dist"]):
        subprocess.run(command, cwd=FRONTEND_ROOT, env=env, check=True)


def _latest_mtime(paths: list[Path]) -> float:
    latest = 0.0
    for path in paths:
        if path.exists():
            latest = max(latest, path.stat().st_mtime)
    return latest


def _frontend_source_paths() -> list[Path]:
    paths: list[Path] = []
    for root in (FRONTEND_SRC, FRONTEND_SCRIPTS):
        if root.exists():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    for name in ("index.html", "package.json", "package-lock.json"):
        path = FRONTEND_ROOT / name
        if path.exists():
            paths.append(path)
    return paths


def _frontend_dist_paths() -> list[Path]:
    if not FRONTEND_DIST.exists():
        return []
    return [path for path in FRONTEND_DIST.rglob("*") if path.is_file()]


def _frontend_dist_freshness() -> dict[str, object]:
    source_paths = _frontend_source_paths()
    dist_paths = _frontend_dist_paths()
    source_latest = _latest_mtime(source_paths)
    dist_latest = _latest_mtime(dist_paths)
    return {
        "fresh": bool(dist_paths) and dist_latest >= source_latest,
        "source_file_count": len(source_paths),
        "dist_file_count": len(dist_paths),
        "source_latest": source_latest,
        "dist_latest": dist_latest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch BoxBox backend and static frontend.")
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Serve the existing frontend/dist without rebuilding/verifying it first.",
    )
    parser.add_argument(
        "--allow-stale-frontend",
        action="store_true",
        help="Allow serving frontend/dist even if source files are newer. Only use for deliberate stale-bundle debugging.",
    )
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--infer-accelerator", type=str, default=None)
    parser.add_argument("--inference-candidate-strategy", type=str, default=None)
    parser.add_argument("--hybrid-search-strategy", type=str, default=None)
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    python = _python()
    backend_env = _resolved_backend_env(
        infer_accelerator=args.infer_accelerator,
        inference_candidate_strategy=args.inference_candidate_strategy,
        hybrid_search_strategy=args.hybrid_search_strategy,
    )

    backend_url = f"http://127.0.0.1:{args.backend_port}/docs"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"

    backend_pid = None
    frontend_pid = None
    frontend_build = "skipped"
    frontend_freshness = {"fresh": False}

    if not args.skip_frontend_build:
        _run_frontend_build()
        frontend_build = "built_and_verified"
        frontend_freshness = _frontend_dist_freshness()
    else:
        frontend_freshness = _frontend_dist_freshness()
        if not frontend_freshness["fresh"] and not args.allow_stale_frontend:
            print(f"backend_url={backend_url}")
            print(f"frontend_url={frontend_url}")
            print("backend_pid=not_started")
            print("frontend_pid=not_started")
            print("frontend_build=stale_dist_blocked")
            print("frontend_dist_fresh=False")
            print("backend_up=False")
            print("frontend_up=False")
            print(f"backend_log={BACKEND_LOG}")
            print(f"frontend_log={FRONTEND_LOG}")
            print(f"backend_accelerator={backend_env['BOXBOX_INFER_ACCELERATOR']}")
            print(f"backend_infer_strategy={backend_env['BOXBOX_INFER_CANDIDATE_STRATEGY']}")
            print(f"backend_hybrid_strategy={backend_env['BOXBOX_HYBRID_SEARCH_STRATEGY']}")
            print("stale_frontend_hint=run without --skip-frontend-build or pass --allow-stale-frontend intentionally")
            return 1

    if not _is_up(backend_url):
        backend_pid = _launch(
            "backend",
            [
                str(python),
                "-m",
                "uvicorn",
                "backend.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.backend_port),
            ],
            BACKEND_LOG,
            env_overrides=backend_env,
        )

    if not _is_up(frontend_url):
        frontend_pid = _launch(
            "frontend",
            [
                str(python),
                "-m",
                "http.server",
                str(args.frontend_port),
                "--bind",
                "127.0.0.1",
                "--directory",
                str(FRONTEND_DIST),
            ],
            FRONTEND_LOG,
        )

    deadline = time.time() + 20
    while time.time() < deadline:
        if _is_up(backend_url) and _is_up(frontend_url):
            break
        time.sleep(0.5)

    print(f"backend_url={backend_url}")
    print(f"frontend_url={frontend_url}")
    print(f"backend_pid={backend_pid or 'already_running'}")
    print(f"frontend_pid={frontend_pid or 'already_running'}")
    print(f"frontend_build={frontend_build}")
    print(f"backend_up={_is_up(backend_url)}")
    print(f"frontend_up={_is_up(frontend_url)}")
    print(f"frontend_dist_fresh={bool(frontend_freshness['fresh'])}")
    print(f"backend_log={BACKEND_LOG}")
    print(f"frontend_log={FRONTEND_LOG}")
    print(f"backend_accelerator={backend_env['BOXBOX_INFER_ACCELERATOR']}")
    print(f"backend_infer_strategy={backend_env['BOXBOX_INFER_CANDIDATE_STRATEGY']}")
    print(f"backend_hybrid_strategy={backend_env['BOXBOX_HYBRID_SEARCH_STRATEGY']}")
    return 0 if _is_up(backend_url) and _is_up(frontend_url) else 1


if __name__ == "__main__":
    raise SystemExit(main())
