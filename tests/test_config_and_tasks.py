"""Unit tests for config dataclasses and tasks.py helpers.

These verify that the generalized configs work for both queue types and
that the shared shutdown monitor handles both QueueEmpty and Empty.
"""

import logging
import threading
from queue import Empty
from unittest.mock import MagicMock

from django_melnottam.tasks import (
    cleanup_thread,
    close_sockets,
    create_shutdown_monitor_thread,
    shutdown_monitor_task,
)


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
