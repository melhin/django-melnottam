import logging
import signal
import time
from concurrent.futures import Future
from concurrent.futures.interpreter import InterpreterPoolExecutor
from concurrent.interpreters import Interpreter, Queue, QueueEmpty, create_queue
from socket import dup
from typing import List, Optional

from hypercorn.config import Config
from rich.logging import RichHandler

from .config import TaskWorkerConfig, WebWorkerConfig
from .exceptions import WorkerInitializationError

logging.basicConfig(
    level=logging.INFO, format="[pool_manager] %(message)s", handlers=[RichHandler()]
)
logger = logging.getLogger(__name__)


class InterpreterPoolManager:
    """
    Manages worker processes using InterpreterPoolExecutor.
    """

    def __init__(self, max_workers: int):
        self.max_workers = max_workers
        self.futures: List[Future] = []
        self.shutdown_queues = []
        self._shutdown_requested = False
        self._parent_shutdown_queue: Optional[Queue] = None
        self.django_settings_module: Optional[str] = None
        self.executor = InterpreterPoolExecutor(max_workers=self.max_workers)
        self._interpreters_to_close: List[Interpreter] = []

    def set_parent_shutdown_queue(self, queue: Queue):
        """Set the queue for parent communication."""
        self._parent_shutdown_queue = queue

    def start_worker(self, config):
        """
        Start a single worker based on the provided configuration.

        Args:
            config: Worker configuration (WebWorkerConfig or TaskWorkerConfig)
        """
        try:
            # Create shutdown queue for this worker
            shutdown_queue = create_queue()
            self.shutdown_queues.append(shutdown_queue)

            # Update the config with queues and parent reference
            config.shutdown_queue = shutdown_queue
            config.parent_shutdown_queue = self._parent_shutdown_queue

            # Get task function and kwargs from config
            task_func = config.get_task_function()
            task_kwargs = config.get_task_kwargs()

            logger.info(
                "Starting %s worker #%d",
                config.__class__.__name__,
                config.worker_number,
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
        logger.info("Starting web workers with InterpreterPoolExecutor")

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
                log_level=logger.level,
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
        logger.info("Starting %d task workers with InterpreterPoolExecutor", num_workers)

        configs = [
            TaskWorkerConfig(
                worker_number=i + 1,
                log_level=logger.level,
                django_settings_module=self.django_settings_module,
            )
            for i in range(num_workers)
        ]

        self.start_workers(configs)

    def shutdown(self, timeout: float = 5.0):
        """Shutdown all workers gracefully."""
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        logger.info("Shutting down worker pool with timeout %.1fs...", timeout)

        # Send stop signals to all workers
        logger.info("Sending stop signals to %d workers...", len(self.shutdown_queues))
        for i, queue in enumerate(self.shutdown_queues):
            try:
                queue.put("stop")
                logger.debug("Stop signal sent to worker %d", i + 1)
            except Exception as e:
                logger.warning("Failed to send stop signal to worker %d: %s", i + 1, e)

        # Give workers time to process shutdown signal
        logger.debug("Waiting 500ms for workers to process shutdown signal...")
        time.sleep(0.5)

        # Wait for futures to complete
        logger.info("Waiting for %d worker futures to complete...", len(self.futures))
        start_time = time.time()
        completed = 0
        for i, future in enumerate(self.futures):
            remaining_timeout = max(0, timeout - (time.time() - start_time))
            try:
                future.result(timeout=remaining_timeout)
                completed += 1
                logger.debug("Worker future %d completed successfully", i + 1)
            except Exception as e:
                logger.warning(f"Worker future {i + 1} error: {e}")

        logger.info("Completed %d/%d worker futures", completed, len(self.futures))

        # Shutdown the executor
        if self.executor:
            logger.info("Shutting down InterpreterPoolExecutor...")
            try:
                self.executor.shutdown(wait=True, cancel_futures=True)
                logger.info("Pool executor shutdown complete")
            except Exception as e:
                logger.warning(f"Error during executor shutdown: {e}")
        else:
            logger.warning("Executor was already None during shutdown")

        # Give subinterpreters time to clean up
        logger.debug("Waiting 500ms for subinterpreters to clean up...")
        time.sleep(0.5)

        # Close any tracked interpreters
        if self._interpreters_to_close:
            logger.info("Closing %d tracked interpreters...", len(self._interpreters_to_close))
            for i, interp in enumerate(self._interpreters_to_close):
                try:
                    interp.close()
                    logger.debug("Interpreter %d closed successfully", i + 1)
                except Exception as e:
                    logger.debug(f"Error closing interpreter {i + 1}: {e}")

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


class ErrorRaisedFromPoolException(Exception):
    pass


def run_application(
    app_path: str,
    workers: int,
    task_workers: int,
    bind: str = "127.0.0.1:8000",
    django_settings_module: Optional[str] = None,
):
    logger.info("Starting Django application and task workers with InterpreterPoolExecutor")
    logger.info("Web workers: %d, Task workers: %d, Bind: %s", workers, task_workers, bind)

    # Create parent shutdown queue
    parent_shutdown_queue = create_queue()

    # Calculate total workers needed
    total_workers = workers + task_workers

    # Store pool reference for signal handler
    pool_ref = None
    shutdown_in_progress = False

    def signal_handler(signum, frame):
        nonlocal shutdown_in_progress
        if shutdown_in_progress:
            logger.warning("Shutdown already in progress, forcing exit...")
            return
        shutdown_in_progress = True
        logger.info("Received signal, initiating graceful shutdown...")
        if pool_ref:
            try:
                pool_ref.shutdown(timeout=5.0)
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")

    # Register signal handler for SIGINT
    original_sigint_handler = signal.signal(signal.SIGINT, signal_handler)

    try:
        with InterpreterPoolManager(max_workers=total_workers) as pool:
            pool_ref = pool
            # Set parent shutdown queue and Django settings
            pool.set_parent_shutdown_queue(parent_shutdown_queue)
            pool.django_settings_module = django_settings_module

            try:
                # Start workers
                pool.start_web_workers(app_path, workers, bind)
                pool.start_task_workers(task_workers)

                logger.info("All workers started. Press Ctrl+C to stop.")

                # Wait for shutdown signal from parent queue or completion
                while not shutdown_in_progress:
                    # Check for shutdown message with a small timeout
                    try:
                        message = parent_shutdown_queue.get(timeout=2)
                        if message == "shutdown":
                            logger.info("Received shutdown request from worker")
                            raise ErrorRaisedFromPoolException()
                    except QueueEmpty:
                        continue

                # Wait for completion
                if not shutdown_in_progress:
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
