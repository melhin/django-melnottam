import logging
import multiprocessing
import signal
import threading
import time
from concurrent.futures import Future
from concurrent.futures.interpreter import InterpreterPoolExecutor
from concurrent.futures.process import ProcessPoolExecutor
from concurrent.interpreters import Interpreter, Queue, QueueEmpty, create_queue
from enum import Enum
from queue import Empty
from socket import dup
from typing import Any, List, Optional, Union

from hypercorn.config import Config
from rich.logging import RichHandler

from .config import TaskWorkerConfig, WebWorkerConfig
from .exceptions import WorkerInitializationError
from .logging_utils import get_log_level

logging.basicConfig(
    level=logging.INFO, format="[pool_manager] %(message)s", handlers=[RichHandler()]
)
logger = logging.getLogger(__name__)


def _reset_worker_signals() -> None:
    """Initializer for process-pool worker subprocesses.

    Resets SIGINT and SIGTERM to ``SIG_IGN`` so that Ctrl+C and termination
    signals are handled *only* by the parent process. Worker subprocesses are
    shut down via their shutdown queue (a ``"stop"`` message), not via OS
    signals.

    Why this is needed:
        ``ProcessPoolExecutor`` with the ``fork`` start method copies the
        parent's signal disposition *and* the ``run_application`` closure
        (including ``pool_ref``) into every child at fork time. Without this
        initializer, each child would inherit ``signal_handler`` and, on Ctrl+C,
        independently call ``pool_ref.shutdown()`` on its own fork-copy of the
        ``PoolManager`` — producing duplicate shutdown sequences and broken
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

    def shutdown(self) -> None:
        """Backend-specific cleanup that runs after the executor is shut down."""


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

    def shutdown(self) -> None:
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
            # the parent's signal_handler and run pool_ref.shutdown() on their
            # own fork-copy of the PoolManager.
            initializer=_reset_worker_signals,
        )

    def create_queue(self) -> Any:
        return self._mp_manager.Queue()

    def shutdown(self) -> None:
        # Shut down the multiprocessing Manager so its helper process
        # doesn't leak.
        try:
            self._mp_manager.shutdown()
        except Exception as e:
            logger.debug("Manager shutdown error: %s", e)


class PoolManager:
    """
    Manages worker execution using either an InterpreterPoolExecutor
    (subinterpreters) or a ProcessPoolExecutor (subprocesses).

    The two backends share the same worker task functions and configs because
    ``concurrent.interpreters.Queue`` and ``multiprocessing.Queue`` expose the
    same ``.get(timeout=)`` / ``.put()`` API used for shutdown signalling.

    Backend-specific behavior (executor construction, queue creation, and
    cleanup) is delegated to a :class:`PoolBackend` subclass selected by
    ``pool_type``.
    """

    def __init__(self, max_workers: int, pool_type: PoolType = PoolType.INTERPRETER):
        self.max_workers = max_workers
        self.pool_type = pool_type
        self.futures: List[Future] = []
        self.shutdown_queues: List[Queue] = []
        self._shutdown_requested = False
        self._parent_shutdown_queue: Optional[Queue] = None
        self.django_settings_module: Optional[str] = None

        if pool_type is PoolType.INTERPRETER:
            self.backend: PoolBackend = InterpreterBackend(max_workers)
        else:
            self.backend = ProcessBackend(max_workers)
        self.executor = self.backend.executor

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------
    def create_queue(self) -> Any:
        """Create a shutdown queue appropriate for the active backend.

        Delegates to the backend; returns either a
        ``concurrent.interpreters.Queue`` (interpreter backend) or a
        manager-backed ``multiprocessing`` queue proxy (process backend).
        The manager proxy is picklable, so it can be passed through
        ``ProcessPoolExecutor.submit``.
        """
        return self.backend.create_queue()

    def set_parent_shutdown_queue(self, queue: Queue):
        """Set the queue for parent communication."""
        self._parent_shutdown_queue = queue

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def start_worker(self, config: Union[WebWorkerConfig, TaskWorkerConfig]):
        """
        Start a single worker based on the provided configuration.

        Args:
            config: Worker configuration (WebWorkerConfig or TaskWorkerConfig)
        """
        try:
            # Create shutdown queue for this worker
            shutdown_queue = self.create_queue()
            self.shutdown_queues.append(shutdown_queue)

            # Update the config with queues and parent reference
            config.shutdown_queue = shutdown_queue
            config.parent_shutdown_queue = self._parent_shutdown_queue

            # Get task function and kwargs from config
            task_func = config.get_task_function()
            task_kwargs = config.get_task_kwargs()

            logger.info(
                "Starting %s worker #%d (%s pool)",
                config.__class__.__name__,
                config.worker_number,
                self.pool_type.value,
            )

            # Submit to executor
            future = self.executor.submit(task_func, **task_kwargs)
            self.futures.append(future)

        except Exception as e:
            logger.error(f"Failed to start worker: {e}", exc_info=True)
            raise WorkerInitializationError(
                f"Failed to start worker #{config.worker_number}: {e}"
            ) from e

    def start_workers(self, configs):
        """
        Start multiple workers from a list of configurations.

        Args:
            configs: List of worker configurations
        """
        for config in configs:
            self.start_worker(config)

        logger.debug("All workers submitted to pool executor")

    def start_web_workers(self, app_path: str, workers: int, bind: str = "127.0.0.1:8000"):
        """
        Start web workers using the pool executor.
        """
        logger.info("Starting web workers with %s pool", self.pool_type.value)

        # Setup socket configuration
        config = Config()
        config.bind = bind
        config.workers = workers
        sockets = config.create_sockets()

        # Store socket metadata (not the actual duplicated FDs yet)
        socket_metadata = tuple(
            [(int(s.family), int(s.type), s.proto) for s in sockets.insecure_sockets]
        )

        configs = []
        for i in range(workers):
            # Duplicate sockets for EACH worker to avoid sharing file descriptors
            insecure_sockets = tuple(
                [
                    (family, sock_type, proto, dup(s.fileno()))
                    for (family, sock_type, proto), s in zip(
                        socket_metadata, sockets.insecure_sockets, strict=True
                    )
                ]
            )

            # Create web worker config
            web_config = WebWorkerConfig(
                worker_number=i + 1,
                log_level=get_log_level(),
                application_path=app_path,
                workers=workers,
                bind=bind,
                insecure_sockets=insecure_sockets,
                django_settings_module=self.django_settings_module,
            )
            configs.append(web_config)

        # Start all web workers
        self.start_workers(configs)

        # Close the original sockets in the parent process after duplication
        for s in sockets.insecure_sockets:
            try:
                s.close()
            except Exception as e:
                logger.warning("Failed to close original socket: %s", e)

    def start_task_workers(self, num_workers: int):
        """
        Start task workers using the pool executor.
        """
        logger.info("Starting %d task workers with %s pool", num_workers, self.pool_type.value)

        configs = [
            TaskWorkerConfig(
                worker_number=i + 1,
                log_level=get_log_level(),
                django_settings_module=self.django_settings_module,
            )
            for i in range(num_workers)
        ]

        self.start_workers(configs)

    def _send_stop_signals(self) -> None:
        """Send 'stop' to every worker shutdown queue."""
        for i, queue in enumerate(self.shutdown_queues):
            try:
                queue.put("stop")
            except Exception as e:
                logger.warning("Failed to send stop signal to worker %d: %s", i + 1, e)

    def _wait_for_futures(self, timeout: float) -> None:
        """Wait for all tracked futures to complete within the remaining timeout."""
        start_time = time.time()
        for i, future in enumerate(self.futures):
            remaining = max(0, timeout - (time.time() - start_time))
            try:
                future.result(timeout=remaining)
            except Exception as e:
                logger.warning("Worker future %d failed: %s", i + 1, e)

    def shutdown(self, timeout: float = 5.0):
        """Shutdown all workers gracefully."""
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        logger.info("Shutting down worker pool (timeout: %.1fs)", timeout)

        self._send_stop_signals()
        time.sleep(0.5)  # Give workers time to process

        self._wait_for_futures(timeout)

        # Shutdown executor
        if self.executor:
            try:
                self.executor.shutdown(wait=True, cancel_futures=True)
            except Exception as e:
                logger.warning("Executor shutdown error: %s", e)

        time.sleep(0.5)  # Cleanup time

        # Backend-specific cleanup (e.g. closing tracked interpreters for the
        # interpreter backend, or shutting down the multiprocessing Manager
        # for the process backend so its helper process doesn't leak).
        self.backend.shutdown()

        logger.info("Worker pool shutdown complete")

    def join(self):
        """Wait for all workers to complete."""
        for future in self.futures:
            try:
                future.result()
            except Exception as e:
                logger.error("Worker task failed: %s", e)
                raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(timeout=10.0)


# Backwards-compatible alias for existing importers.
InterpreterPoolManager = PoolManager


class ErrorRaisedFromPoolException(Exception):
    pass


def _wait_for_shutdown_signal(parent_shutdown_queue, shutdown_in_progress: threading.Event) -> bool:
    """Block until a 'shutdown' message arrives on the parent queue or
    ``shutdown_in_progress`` is set.

    Returns True if a worker-initiated shutdown was requested, False if the
    loop exited because shutdown was already in progress.

    Note:
        For the process backend, ``parent_shutdown_queue`` is a proxy backed
        by a ``multiprocessing.Manager`` helper process. If the parent's
        signal handler runs ``pool.shutdown()`` while this loop is blocked,
        the manager is torn down and subsequent ``get()`` calls raise
        ``BrokenPipeError`` / ``EOFError`` because the underlying connection is
        gone. We treat those as "the pool is already shutting down" and exit
        the loop cleanly instead of propagating the exception.
    """
    while not shutdown_in_progress.is_set():
        try:
            message = parent_shutdown_queue.get(timeout=2)
            if message == "shutdown":
                logger.info("Received shutdown request from worker")
                return True
        except QueueEmpty, Empty:
            continue
        except (BrokenPipeError, EOFError, ConnectionError) as e:
            # The backing manager has been shut down (typically because the
            # parent signal handler already triggered pool.shutdown()). The
            # queue is no longer usable, so stop waiting.
            logger.debug("Parent shutdown queue closed while waiting for signal: %s", e)
            return False
    return False


def run_application(
    app_path: str,
    workers: int,
    task_workers: int,
    bind: str = "127.0.0.1:8000",
    django_settings_module: Optional[str] = None,
    pool_type: PoolType = PoolType.INTERPRETER,
):
    """Run the Django application and task workers.

    Args:
        app_path: WSGI application path (e.g. ``myapp.wsgi:application``).
        workers: Number of web workers.
        task_workers: Number of task workers.
        bind: Bind address for the web server.
        django_settings_module: Django settings module path.
        pool_type: Which concurrency backend to use.
    """
    logger.info("Starting Django application and task workers with %s pool", pool_type.value)
    logger.info("Web workers: %d, Task workers: %d, Bind: %s", workers, task_workers, bind)

    # Calculate total workers needed
    total_workers = workers + task_workers

    # Store pool reference for signal handler
    pool_ref: Optional[PoolManager] = None
    shutdown_in_progress = threading.Event()

    def signal_handler(signum, frame):
        if shutdown_in_progress.is_set():
            logger.warning("Shutdown already in progress, forcing exit...")
            return
        shutdown_in_progress.set()
        logger.info("Received signal, initiating graceful shutdown...")
        if pool_ref:
            try:
                pool_ref.shutdown(timeout=5.0)
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")

    # Register signal handler for SIGINT
    original_sigint_handler = signal.signal(signal.SIGINT, signal_handler)

    try:
        with PoolManager(max_workers=total_workers, pool_type=pool_type) as pool:
            pool_ref = pool
            # Set parent shutdown queue and Django settings
            parent_shutdown_queue = pool.create_queue()
            pool.set_parent_shutdown_queue(parent_shutdown_queue)
            pool.django_settings_module = django_settings_module

            try:
                # Start workers
                pool.start_web_workers(app_path, workers, bind)
                pool.start_task_workers(task_workers)

                logger.info("All workers started. Press Ctrl+C to stop.")

                worker_requested_shutdown = _wait_for_shutdown_signal(
                    parent_shutdown_queue, shutdown_in_progress
                )
                if worker_requested_shutdown:
                    raise ErrorRaisedFromPoolException()

                # Wait for completion
                if not shutdown_in_progress.is_set():
                    pool.join()
            except ErrorRaisedFromPoolException:
                logger.error("Error Raised from pool exception")
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
            finally:
                logger.info("Application shutdown complete")
    finally:
        # Restore original signal handler
        signal.signal(signal.SIGINT, original_sigint_handler)
