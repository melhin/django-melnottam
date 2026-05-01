import logging
import time

import pytest
from django_melnottam.config import TaskWorkerConfig
from django_melnottam.manager import InterpreterPoolManager


@pytest.fixture(autouse=True)
def setup_logging():
    """Reduce logging noise during tests."""
    logging.getLogger("django_melnottam.manager").setLevel(logging.WARNING)


@pytest.fixture
def cleanup_manager():
    """Cleanup helper for managers created during tests."""
    managers = []
    yield managers

    # Cleanup all managers after test
    time.sleep(0.1)
    for manager in managers:
        if manager and manager.executor:
            try:
                manager.executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass


def test_manager_starts_and_shuts_down_cleanly(cleanup_manager):
    """Test that manager can start task workers and shutdown gracefully."""
    manager = InterpreterPoolManager(max_workers=1)
    cleanup_manager.append(manager)

    config = TaskWorkerConfig(worker_number=1, log_level=logging.WARNING)
    manager.start_worker(config)

    assert len(manager.futures) == 1
    assert len(manager.shutdown_queues) == 1

    time.sleep(0.5)
    manager.shutdown(timeout=3.0)

    assert manager._shutdown_requested


def test_manager_context_manager_with_workers(cleanup_manager):
    """Test context manager properly cleans up workers."""
    manager = InterpreterPoolManager(max_workers=1)
    cleanup_manager.append(manager)

    with manager:
        assert manager.executor is not None
        assert manager.max_workers == 1

        config = TaskWorkerConfig(worker_number=1, log_level=logging.WARNING)
        manager.start_worker(config)

        assert len(manager.futures) == 1

        time.sleep(0.3)

    # Verify context manager called shutdown
    assert manager._shutdown_requested


def test_rapid_shutdown(cleanup_manager):
    """Test that shutdown handles rapid calls gracefully."""
    manager = InterpreterPoolManager(max_workers=1)
    cleanup_manager.append(manager)

    config = TaskWorkerConfig(worker_number=1, log_level=logging.WARNING)
    manager.start_worker(config)

    time.sleep(0.3)

    manager.shutdown(timeout=2.0)
    manager.shutdown(timeout=1.0)
    manager.shutdown(timeout=0.5)

    assert manager._shutdown_requested
