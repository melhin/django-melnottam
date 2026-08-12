"""End-to-end tests for django-melnottam.

These tests do NOT mock the executor. They boot a real ``run_application``
instance (web + task workers) in a subprocess for each pool backend, submit
a job through the HTTP endpoint, wait for the task worker to complete it,
then send SIGINT and assert the process exits cleanly.

This is the test the unit tests can't be: it verifies the full wiring of
manager.py -> tasks.py -> django_tasks_db worker -> Django view, for both
the interpreter and process backends.

Skipped automatically when:
  * not running on Python 3.14+ (InterpreterPoolExecutor unavailable), or
  * the E2E suite is not explicitly opted into via ``-m e2e`` (these tests
    are slow and spawn subprocesses, so they're off by default).

Run with::

    .venv/bin/pytest tests/test_e2e_backends.py -m e2e -s
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
TESTS_DIR = REPO_ROOT / "tests"


def _interpreter_pool_available() -> bool:
    try:
        from concurrent.futures.interpreter import InterpreterPoolExecutor  # noqa: F401
    except Exception:
        return False
    return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    """Poll /health/ until it returns 200, or raise after ``timeout``."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health/", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - connection refused while booting
            last_err = exc
            time.sleep(0.3)
    raise RuntimeError(
        f"web worker did not become healthy within {timeout}s (last error: {last_err})"
    )


def _enqueue_job(base_url: str) -> int:
    req = urllib.request.Request(f"{base_url}/enqueue/", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
    return int(data["job_id"])


def _get_status(base_url: str, job_id: int) -> str:
    with urllib.request.urlopen(f"{base_url}/status/{job_id}/", timeout=5) as r:
        return json.loads(r.read().decode())["status"]


def _wait_for_done(base_url: str, job_id: int, timeout: float = 30.0) -> None:
    """Poll /status/<id>/ until status == 'done', or raise after ``timeout``.

    This is the assertion that the task worker actually picked up the job
    enqueued by the web worker and ran the @task.
    """
    deadline = time.time() + timeout
    last_status = "?"
    while time.time() < deadline:
        try:
            last_status = _get_status(base_url, job_id)
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
            continue
        if last_status == "done":
            return
        time.sleep(0.5)
    raise AssertionError(
        f"job {job_id} did not reach 'done' within {timeout}s (last status: {last_status!r})"
    )


def _spawn_server(pool_type: str, port: int, db_path: Path) -> subprocess.Popen:
    """Spawn ``run_application`` in a subprocess with the given backend.

    We use a small inline driver script instead of the CLI so we control
    exactly which code path runs (and so the subprocess inherits a clean
    environment with the right DJANGO_SETTINGS_MODULE).
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{TESTS_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["DJANGO_SETTINGS_MODULE"] = "e2e_settings"
    env["E2E_DB_PATH"] = str(db_path)

    driver = textwrap.dedent(
        f"""
        import sys
        from django_melnottam.manager import PoolType, run_application

        run_application(
            app_path="e2e_app.wsgi:application",
            workers=1,
            task_workers=1,
            bind="127.0.0.1:{port}",
            django_settings_module="e2e_settings",
            pool_type=PoolType("{pool_type}"),
        )
        sys.exit(0)
        """
    )

    return subprocess.Popen(
        [sys.executable, "-c", driver],
        env=env,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _migrate(db_path: Path) -> None:
    """Run migrations on the e2e DB so web/task workers find a ready schema."""
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{TESTS_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["DJANGO_SETTINGS_MODULE"] = "e2e_settings"
    env["E2E_DB_PATH"] = str(db_path)
    subprocess.run(
        [sys.executable, "-m", "django", "migrate", "--run-syncdb"],
        env=env,
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "e2e.sqlite3"
    _migrate(p)
    return p


@pytest.mark.e2e
@pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="InterpreterPoolExecutor requires Python 3.14+",
)
@pytest.mark.parametrize("pool_type", ["interpreter", "process"])
def test_web_and_task_worker_lifecycle(pool_type: str, db_path: Path) -> None:
    """Boot the pool, submit a job via HTTP, confirm the task worker ran it,
    then SIGINT and confirm a clean (exit 0) shutdown."""
    if pool_type == "interpreter" and not _interpreter_pool_available():
        pytest.skip("InterpreterPoolExecutor not available on this build")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = _spawn_server(pool_type, port, db_path)
    try:
        try:
            _wait_for_health(base_url, timeout=2.0)
        except Exception:
            # Surface server logs on boot failure for easier debugging.
            out = proc.stdout.read() if proc.stdout else "<no stdout>"
            raise AssertionError(f"server ({pool_type}) failed to start. Output:\n{out}") from None

        job_id = _enqueue_job(base_url)
        _wait_for_done(base_url, job_id, timeout=2.0)
    finally:
        # Always shut the server down so we don't leak processes on failure.
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
        try:
            stdout, _ = proc.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            pytest.fail(f"server ({pool_type}) did not shut down within 5s after SIGINT.")

    assert proc.returncode == 0, (
        f"server ({pool_type}) exited with {proc.returncode} after SIGINT "
        f"(expected 0). Output:\n{stdout}"
    )
