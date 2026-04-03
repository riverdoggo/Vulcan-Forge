#!/usr/bin/env python3
"""
Start FastAPI backend + React dev server and open the dashboard.
Stops child processes when this script exits (Ctrl+C or closing the console on Windows).
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"

_procs: list[subprocess.Popen] = []


def _npm_executable() -> str:
    if sys.platform == "win32":
        for name in ("npm.cmd", "npm"):
            p = shutil.which(name)
            if p:
                return p
    else:
        p = shutil.which("npm")
        if p:
            return p
    raise FileNotFoundError("npm not found in PATH; install Node.js or use npm from your environment.")


def _terminate_all() -> None:
    for p in _procs:
        if p.poll() is not None:
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                )
            else:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, AttributeError):
                    p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


atexit.register(_terminate_all)


def main() -> None:
    if not BACKEND_DIR.is_dir() or not FRONTEND_DIR.is_dir():
        print("Run this script from the project root (expects backend/ and frontend/).", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    npm = _npm_executable()

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    print("Starting backend (uvicorn) on http://127.0.0.1:8000 …")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(BACKEND_DIR),
        env=env,
        creationflags=creationflags,
        start_new_session=sys.platform != "win32",
    )
    _procs.append(backend)

    time.sleep(2)

    print("Starting frontend (React) on http://localhost:3000 …")
    # react-scripts start opens the browser by default; we open once below via webbrowser.
    fe_env = env.copy()
    fe_env["BROWSER"] = "none"
    fe_flags = creationflags
    frontend = subprocess.Popen(
        [npm, "start"],
        cwd=str(FRONTEND_DIR),
        env=fe_env,
        shell=False,
        creationflags=fe_flags,
        start_new_session=sys.platform != "win32",
    )
    _procs.append(frontend)

    time.sleep(4)
    print("Opening dashboard…")
    webbrowser.open("http://localhost:3000")

    print(
        "\nOrchestrator is running. Close this window or press Ctrl+C to stop backend and frontend.\n",
        flush=True,
    )

    try:
        backend.wait()
    except KeyboardInterrupt:
        print("\nShutting down…", flush=True)
    finally:
        _terminate_all()


if __name__ == "__main__":
    main()
