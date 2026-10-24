"""Pool backends for interpreter and process workers.

Each backend encapsulates the executor, queue creation, and cleanup details
that differ between subinterpreters and subprocesses. ``PoolManager`` only
deals with the common worker lifecycle.
"""

import logging
import multiprocessing
import signal
from concurrent.futures.interpreter import InterpreterPoolExecutor
from concurrent.futures.process import ProcessPoolExecutor
from concurrent.interpreters import Interpreter, create_queue
from enum import Enum
from typing import Any, List

logger = logging.getLogger(__name__)


def _reset_worker_signals() -> None:
    """Initializer for process-pool worker subprocesses.

    Resets SIGINT and SIGTERM to ``SIG_IGN`` so that Ctrl+C and termination
    signals are handled *only* by the parent process. Worker subprocesses are
    shut down via their shutdown queue (a ``"stop"`` message), not via OS
    signals.

    Why this is needed:
        ``ProcessPoolExecutor`` with the ``fork`` start method copies the
        parent's signal disposition *and* the ``_sigint_shutdown`` handler
        (which closes over the ``PoolManager``) into every child at fork time.
        Without this initializer, each child would inherit the handler and, on
        Ctrl+C, independently call ``pool.shutdown()`` on its own fork-copy of
        the ``PoolManager`` — producing duplicate shutdown sequences and broken
        futures (the ``Worker future N failed:`` messages).
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


class PoolType(Enum):
    """Which concurrency backend the manager should use."""

    INTERPRETER = "interpreter"
    PROCESS = "process"


class PoolBackend:
    """Backend-specific pool behavior (executor creation, queues, cleanup).

    Subclasses encapsulate everything that differs between the
    interpreter and process backends so that ``PoolManager`` stays free
    of ``if pool_type is ...`` branches.
    """

    def __init__(self, max_workers: int):
        self.max_workers = max_workers
        self.executor = self._create_executor()

    def _create_executor(self):
        raise NotImplementedError

    def create_queue(self) -> Any:
        raise NotImplementedError

    def shutdown(self, timeout: float = 5.0) -> None:
        """Shut down the executor, waiting for running workers to finish."""
        self.executor.shutdown(wait=True, cancel_futures=True)


class InterpreterBackend(PoolBackend):
    """Subinterpreter backend using ``InterpreterPoolExecutor``."""

    def __init__(self, max_workers: int):
        # Only meaningful for the interpreter backend.
        self._interpreters_to_close: List[Interpreter] = []
        super().__init__(max_workers)

    def _create_executor(self):
        return InterpreterPoolExecutor(max_workers=self.max_workers)

    def create_queue(self) -> Any:
        return create_queue()

    def close_interpreters(self) -> None:
        """Close tracked interpreters."""
        for i, interp in enumerate(self._interpreters_to_close):
            try:
                interp.close()
            except Exception as e:
                logger.debug("Error closing interpreter %d: %s", i + 1, e)

    def shutdown(self, timeout: float = 5.0) -> None:
        # Cooperative only: long-running commands must provide a factory.
        super().shutdown(timeout)
        self.close_interpreters()


class ProcessBackend(PoolBackend):
    """Subprocess backend using ``ProcessPoolExecutor`` with the fork start method.

    The process backend uses the ``fork`` start method so that duplicated
    socket file descriptors are inherited by worker subprocesses. On
    platforms where ``fork`` is unavailable (e.g. Windows, macOS 3.14+),
    socket-based web workers will not start correctly; task workers still
    work.
    """

    def _create_executor(self):
        # fork is required so duplicated socket FDs are inherited by
        # worker subprocesses. Task-only workloads could use spawn, but
        # we keep a single context for consistency.
        self._mp_context = multiprocessing.get_context("fork")
        # A Manager-backed queue is picklable (it's a proxy), so it can be
        # passed as a kwarg to executor.submit(). A plain mp_context.Queue()
        # is NOT picklable and raises "Queue objects should only be shared
        # between processes through inheritance" when the executor tries to
        # serialize the work item.
        self._mp_manager = multiprocessing.Manager()
        return ProcessPoolExecutor(
            max_workers=self.max_workers,
            mp_context=self._mp_context,
            # Reset signal handlers in each forked worker so SIGINT/SIGTERM
            # are handled only by the parent. Without this, children inherit
            # the parent's signal handler and run pool.shutdown() on their own
            # fork-copy of the PoolManager.
            initializer=_reset_worker_signals,
        )

    def create_queue(self) -> Any:
        return self._mp_manager.Queue()

    def shutdown(self, timeout: float = 5.0) -> None:
        # Terminate any worker still running after the graceful wait. This is
        # what lets the process backend stop long-running commands that do not
        # provide a worker_factory.
        try:
            self.executor.terminate_workers()
        except Exception as e:
            logger.debug("terminate_workers failed: %s", e)

        # Shut down the multiprocessing Manager so its helper process
        # doesn't leak.
        try:
            self._mp_manager.shutdown()
        except Exception as e:
            logger.debug("Manager shutdown error: %s", e)
