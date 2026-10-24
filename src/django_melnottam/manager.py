"""Worker pool orchestration.

The lifecycle is deliberately linear:

    1. Build a PoolManager (interpreter or process backend).
    2. Start web workers and command workers.
    3. Wait until a worker or the user requests shutdown.
    4. Shut the pool down (idempotent).

Backend details live in ``backends.py``; worker configuration lives in
``workers.py``; signal handling lives in ``lifecycle.py``; the application
entrypoint lives in ``application.py``.
"""

import logging
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Union

from .backends import InterpreterBackend, PoolBackend, PoolType, ProcessBackend
from .config import CommandWorkerConfig, WebWorkerConfig
from .exceptions import WorkerInitializationError
from .logging_utils import get_log_level
from .tasks import command_worker_task, web_worker_task
from .workers import CommandSpec, build_command_worker_configs, build_web_worker_configs

logger = logging.getLogger(__name__)


@dataclass
class Worker:
    """A submitted worker and the queue used to ask it to stop."""

    config: Union[WebWorkerConfig, CommandWorkerConfig]
    future: Future
    shutdown_queue: Any


_TASK_FUNCTIONS = {
    WebWorkerConfig: web_worker_task,
    CommandWorkerConfig: command_worker_task,
}


def _task_function(config: Union[WebWorkerConfig, CommandWorkerConfig]):
    try:
        return _TASK_FUNCTIONS[type(config)]
    except KeyError:
        raise TypeError(f"Unsupported worker config: {type(config).__name__}") from None


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

    def __init__(
        self,
        max_workers: int,
        pool_type: PoolType,
        django_settings_module: Optional[str] = None,
    ):
        self.max_workers = max_workers
        self.pool_type = pool_type
        self.django_settings_module = django_settings_module
        self.workers: List[Worker] = []
        self._shutdown_requested = False

        if pool_type is PoolType.INTERPRETER:
            self.backend: PoolBackend = InterpreterBackend(max_workers)
        else:
            self.backend = ProcessBackend(max_workers)

        self.parent_queue = self.backend.create_queue()

    @property
    def executor(self):
        return self.backend.executor

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

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def start_worker(self, config: Union[WebWorkerConfig, CommandWorkerConfig]):
        """
        Start a single worker based on the provided configuration.

        Args:
            config: Worker configuration (WebWorkerConfig or CommandWorkerConfig)
        """
        try:
            shutdown_queue = self.create_queue()
            config.shutdown_queue = shutdown_queue
            config.shutdown_notification_queue = self.parent_queue

            task_func = _task_function(config)
            task_kwargs = config.get_task_kwargs()

            logger.info(
                "Starting %s worker #%d (%s pool)",
                config.__class__.__name__,
                config.worker_number,
                self.pool_type.value,
            )

            future = self.executor.submit(task_func, **task_kwargs)
            self.workers.append(Worker(config, future, shutdown_queue))

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
        """Start web workers using the pool executor."""
        logger.info("Starting web workers with %s pool", self.pool_type.value)
        self.start_workers(
            build_web_worker_configs(
                app_path=app_path,
                workers=workers,
                bind=bind,
                log_level=get_log_level(),
                django_settings_module=self.django_settings_module,
            )
        )

    def start_command_workers(self, specs: Sequence[CommandSpec]):
        """Start command workers from a list of specs."""
        total = sum(spec.instances for spec in specs)
        logger.info("Starting %d command worker(s) with %s pool", total, self.pool_type.value)
        self.start_workers(
            build_command_worker_configs(
                specs,
                log_level=get_log_level(),
                django_settings_module=self.django_settings_module,
            )
        )

    def _request_worker_shutdown(self) -> None:
        """Send 'stop' to every worker shutdown queue."""
        for worker in self.workers:
            try:
                worker.shutdown_queue.put("stop")
            except Exception as e:
                logger.warning(
                    "Failed to send stop signal to worker %d: %s",
                    worker.config.worker_number,
                    e,
                )

    def _wait_for_workers(self, timeout: float) -> None:
        """Wait for all tracked workers to complete within the remaining timeout."""
        start_time = time.monotonic()
        for worker in self.workers:
            remaining = max(0, timeout - (time.monotonic() - start_time))
            try:
                worker.future.result(timeout=remaining)
            except Exception as e:
                logger.warning("Worker %d failed: %s", worker.config.worker_number, e)

    def shutdown(self, timeout: float = 5.0):
        """Shutdown all workers gracefully."""
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        logger.info("Shutting down worker pool (timeout: %.1fs)", timeout)

        self._request_worker_shutdown()
        time.sleep(0.5)  # Give workers time to process

        self._wait_for_workers(timeout)

        time.sleep(0.5)  # Cleanup time

        # Backend-specific executor shutdown and cleanup.
        self.backend.shutdown(timeout)

        logger.info("Worker pool shutdown complete")

    def join(self):
        """Wait for all workers to complete."""
        for worker in self.workers:
            try:
                worker.future.result()
            except Exception as e:
                logger.error("Worker task failed: %s", e)
                raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(timeout=10.0)
