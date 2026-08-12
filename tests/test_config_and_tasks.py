"""Unit tests for config dataclasses and tasks.py helpers.

These verify that the generalized configs work for both queue types and
that the shared shutdown monitor handles both QueueEmpty and Empty.
"""

import logging
import threading
from queue import Empty
from unittest.mock import MagicMock

from django_melnottam.config import TaskWorkerConfig, WebWorkerConfig
from django_melnottam.tasks import (
    cleanup_thread,
    close_sockets,
    create_shutdown_monitor_thread,
    setup_worker_logging,
    shutdown_monitor_task,
)


# ----------------------------------------------------------------------
# Configs
# ----------------------------------------------------------------------
def test_task_worker_config_get_task_function():
    """TaskWorkerConfig resolves to the task_worker_task function."""
    from django_melnottam.tasks import task_worker_task

    cfg = TaskWorkerConfig(worker_number=1, log_level=20)
    assert cfg.get_task_function() is task_worker_task


def test_task_worker_config_get_task_kwargs():
    """get_task_kwargs surfaces every field the task function expects."""
    cfg = TaskWorkerConfig(
        worker_number=3,
        log_level=logging.DEBUG,
        django_settings_module="my.settings",
    )
    kw = cfg.get_task_kwargs()
    assert kw == {
        "worker_number": 3,
        "log_level": logging.DEBUG,
        "shutdown_queue": None,
        "parent_shutdown_queue": None,
        "django_settings_module": "my.settings",
    }


def test_web_worker_config_get_task_function():
    """WebWorkerConfig resolves to the web_worker_task function."""
    from django_melnottam.tasks import web_worker_task

    cfg = WebWorkerConfig(worker_number=1, log_level=20, application_path="app.wsgi:application")
    assert cfg.get_task_function() is web_worker_task


def test_web_worker_config_get_task_kwargs_includes_sockets():
    """get_task_kwargs forwards insecure_sockets and bind to the task."""
    sockets = (1, 2, 3, 4)
    cfg = WebWorkerConfig(
        worker_number=2,
        log_level=logging.INFO,
        application_path="app.wsgi:application",
        workers=4,
        bind="0.0.0.0:8000",
        insecure_sockets=sockets,
    )
    kw = cfg.get_task_kwargs()
    assert kw["insecure_sockets"] == sockets
    assert kw["bind"] == "0.0.0.0:8000"
    assert kw["workers"] == 4
    assert kw["application_path"] == "app.wsgi:application"


def test_configs_accept_either_queue_type():
    """The generalized queue fields accept any object (interpreter or mp queue)."""
    fake_q = MagicMock()
    cfg = TaskWorkerConfig(
        worker_number=1,
        log_level=20,
        shutdown_queue=fake_q,
        parent_shutdown_queue=fake_q,
    )
    assert cfg.shutdown_queue is fake_q
    assert cfg.parent_shutdown_queue is fake_q


# ----------------------------------------------------------------------
# setup_worker_logging
# ----------------------------------------------------------------------
def test_setup_worker_logging_returns_named_logger():
    """The logger name encodes worker type and number."""
    logger = setup_worker_logging(5, logging.WARNING, "Web")
    assert logger.name == "worker.web.5"
    assert logger.level == logging.WARNING
    assert logger.handlers, "expected a handler attached"


# ----------------------------------------------------------------------
# shutdown_monitor_task with both queue types
# ----------------------------------------------------------------------
def test_shutdown_monitor_handles_interpreter_queue_empty():
    """Monitor exits cleanly when an interpreter-style queue raises QueueEmpty
    and the shutdown_event is set."""
    from concurrent.interpreters import QueueEmpty

    class FakeInterpQueue:
        def get(self, timeout=None):
            raise QueueEmpty()

    shutdown_event = threading.Event()
    shutdown_event.set()

    callback = MagicMock()
    # Runs in this thread; should return promptly because the event is set.
    shutdown_monitor_task(
        FakeInterpQueue(),
        worker_number=1,
        shutdown_callback=callback,
        worker_type="Test",
        shutdown_event=shutdown_event,
    )
    callback.assert_not_called()  # event-driven exit, no "stop" received


def test_shutdown_monitor_handles_multiprocessing_queue_empty():
    """Monitor exits cleanly when an mp-style queue raises queue.Empty
    and the shutdown_event is set."""

    class FakeMpQueue:
        def get(self, timeout=None):
            raise Empty()

    shutdown_event = threading.Event()
    shutdown_event.set()

    callback = MagicMock()
    shutdown_monitor_task(
        FakeMpQueue(),
        worker_number=1,
        shutdown_callback=callback,
        worker_type="Test",
        shutdown_event=shutdown_event,
    )
    callback.assert_not_called()


def test_shutdown_monitor_invokes_callback_on_stop():
    """When the queue yields 'stop', the callback fires exactly once."""

    class StopQueue:
        def get(self, timeout=None):
            return "stop"

    callback = MagicMock()
    shutdown_monitor_task(
        StopQueue(),
        worker_number=2,
        shutdown_callback=callback,
        worker_type="Test",
    )
    callback.assert_called_once_with()


def test_shutdown_monitor_passes_worker_instance_to_callback():
    """When a worker_instance is supplied it is forwarded to the callback."""

    class StopQueue:
        def get(self, timeout=None):
            return "stop"

    worker_instance = MagicMock()
    callback = MagicMock()
    shutdown_monitor_task(
        StopQueue(),
        worker_number=3,
        shutdown_callback=callback,
        worker_instance=worker_instance,
        worker_type="Test",
    )
    callback.assert_called_once_with(worker_instance)


def test_create_shutdown_monitor_thread_returns_unstarted_thread():
    """Helper returns a Thread that has not been started yet."""
    q = MagicMock()
    t = create_shutdown_monitor_thread(q, 1, lambda: None)
    assert isinstance(t, threading.Thread)
    assert not t.is_alive()


# ----------------------------------------------------------------------
# close_sockets / cleanup_thread
# ----------------------------------------------------------------------
def test_close_sockets_closes_each_socket():
    """close_sockets calls .close() on every socket and swallows errors."""
    s1, s2 = MagicMock(), MagicMock()
    s2.close.side_effect = OSError("already closed")
    close_sockets([s1, s2], logging.getLogger("test"))
    s1.close.assert_called_once()
    s2.close.assert_called_once()


def test_cleanup_thread_joins_alive_thread():
    """cleanup_thread joins a live thread; tolerates join errors."""
    t = MagicMock()
    t.is_alive.return_value = True
    cleanup_thread(t, logging.getLogger("test"), timeout=0.1)
    t.join.assert_called_once_with(timeout=0.1)


def test_cleanup_thread_skips_dead_thread():
    """cleanup_thread does not call join on an already-dead thread."""
    t = MagicMock()
    t.is_alive.return_value = False
    cleanup_thread(t, logging.getLogger("test"), timeout=0.1)
    t.join.assert_not_called()
