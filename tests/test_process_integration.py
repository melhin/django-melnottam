"""Integration tests for the process-pool backend.

Unlike test_pool_manager.py (which mocks the executor), these tests
actually start a real ProcessPoolExecutor and run a real task_worker_task
in a subprocess. They verify the end-to-end wiring of the PROCESS backend.

These tests require Django + django_tasks_db to be importable, so they
are skipped if the task worker's dependencies aren't installed.
"""

import logging
import time

import pytest

from django_melnottam.config import TaskWorkerConfig
from django_melnottam.manager import PoolManager, PoolType


def _task_deps_available() -> bool:
    """Return True if django_tasks_db is importable."""
    try:
        import django_tasks_db  # noqa: F401
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _task_deps_available(),
    reason="django_tasks_db not installed; process-pool task worker cannot run",
)


@pytest.fixture(autouse=True)
def setup_logging():
    """Reduce logging noise during tests."""
    logging.getLogger("django_melnottam.manager").setLevel(logging.WARNING)
    logging.getLogger("django_melnottam.tasks").setLevel(logging.WARNING)


@pytest.fixture
def cleanup_manager():
    """Force-shutdown any managers left over after a test."""
    managers = []
    yield managers
    time.sleep(0.1)
    for manager in managers:
        if manager and manager.executor:
            try:
                manager.executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass


def test_process_manager_starts_and_shuts_down_cleanly(cleanup_manager):
    """Process backend can start a task worker and shut it down gracefully."""
    manager = PoolManager(max_workers=1, pool_type=PoolType.PROCESS)
    cleanup_manager.append(manager)

    config = TaskWorkerConfig(worker_number=1, log_level=logging.WARNING)
    manager.start_worker(config)

    assert len(manager.futures) == 1
    assert len(manager.shutdown_queues) == 1

    time.sleep(0.5)
    manager.shutdown(timeout=5.0)

    assert manager._shutdown_requested


def test_process_manager_context_manager(cleanup_manager):
    """Process backend context manager cleans up workers on exit."""
    manager = PoolManager(max_workers=1, pool_type=PoolType.PROCESS)
    cleanup_manager.append(manager)

    with manager:
        assert manager.executor is not None
        assert manager.pool_type is PoolType.PROCESS

        config = TaskWorkerConfig(worker_number=1, log_level=logging.WARNING)
        manager.start_worker(config)

        assert len(manager.futures) == 1
        time.sleep(0.3)

    assert manager._shutdown_requested


def test_process_manager_rapid_shutdown(cleanup_manager):
    """Repeated shutdown() calls on the process backend are safe."""
    manager = PoolManager(max_workers=1, pool_type=PoolType.PROCESS)
    cleanup_manager.append(manager)

    config = TaskWorkerConfig(worker_number=1, log_level=logging.WARNING)
    manager.start_worker(config)

    time.sleep(0.3)

    manager.shutdown(timeout=3.0)
    manager.shutdown(timeout=1.0)
    manager.shutdown(timeout=0.5)

    assert manager._shutdown_requested
