"""Signal handling and shutdown waiting for the worker pool.

The SIGINT handler only sets an event. The actual pool shutdown happens in
the caller's main loop, outside the signal handler, so the handler never
acquires locks or joins threads.
"""

import logging
import signal
import threading
from concurrent.interpreters import QueueEmpty
from contextlib import contextmanager
from queue import Empty
from typing import Iterator

logger = logging.getLogger(__name__)


@contextmanager
def sigint_shutdown() -> Iterator[threading.Event]:
    """Install a SIGINT handler that requests shutdown exactly once.

    Yields an event that is set when SIGINT is received. The caller is
    responsible for performing the actual shutdown after the event is set.
    """
    shutdown_requested = threading.Event()

    def handle(signum, frame):
        if shutdown_requested.is_set():
            logger.warning("Shutdown already in progress, forcing exit...")
            return
        shutdown_requested.set()
        logger.info("Received signal, initiating graceful shutdown...")

    previous_handlers = {sig: signal.signal(sig, handle) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield shutdown_requested
    finally:
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)


def wait_for_shutdown_signal(parent_queue, shutdown_requested: threading.Event) -> bool:
    """Block until a 'shutdown' message arrives on the parent queue or
    ``shutdown_requested`` is set.

    Returns True if a worker-initiated shutdown was requested, False if the
    loop exited because shutdown was already requested.

    Note:
        For the process backend, ``parent_queue`` is a proxy backed by a
        ``multiprocessing.Manager`` helper process. If the parent's signal
        handler triggers shutdown while this loop is blocked, the manager is
        torn down and subsequent ``get()`` calls raise ``BrokenPipeError`` /
        ``EOFError`` because the underlying connection is gone. We treat those
        as "the pool is already shutting down" and exit the loop cleanly
        instead of propagating the exception.
    """
    while not shutdown_requested.is_set():
        try:
            message = parent_queue.get(timeout=2)
            if message == "shutdown":
                logger.info("Received shutdown request from worker")
                return True
        except QueueEmpty, Empty:
            continue
        except (BrokenPipeError, EOFError, ConnectionError) as e:
            logger.debug("Parent shutdown queue closed while waiting for signal: %s", e)
            return False
    return False
