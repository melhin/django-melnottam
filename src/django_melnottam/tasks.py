import importlib
import logging
import signal
import threading
import time
from concurrent.interpreters import Queue, QueueEmpty
from queue import Empty
from socket import socket
from typing import Any, Callable, Optional

import django
from hypercorn.asyncio.run import asyncio_worker
from hypercorn.config import Config, Sockets

from .logging_utils import get_log_level


def setup_worker_logging(
    worker_number: int, log_level: Optional[int] = None, worker_type: str = "worker"
) -> logging.Logger:
    """
    Setup logging for a worker process.

    Args:
        worker_number: The worker number
        log_level: The logging level (defaults to DEBUG if Django DEBUG=True, INFO otherwise)
        worker_type: Descriptive name of the worker type

    Returns:
        Configured logger instance
    """
    # Determine log level from Django DEBUG setting if not provided
    log_level = get_log_level(log_level)

    # Create a logger specific to the worker, not the root logger
    logger = logging.getLogger(f"worker.{worker_type.lower()}.{worker_number}")

    # Only add a handler if one doesn't already exist
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(f"%(asctime)s {worker_type}-[{worker_number}] %(message)s")
        handler.setFormatter(formatter)
        handler.setLevel(log_level)
        logger.addHandler(handler)

    # Set the level on the logger itself
    logger.setLevel(log_level)

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = True

    return logger


def create_shutdown_monitor_thread(
    shutdown_queue: Queue,
    worker_number: int,
    shutdown_callback: Callable,
    worker_instance: Optional[Any] = None,
    worker_type: str = "worker",
    shutdown_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """
    Create and return (but don't start) a shutdown monitor thread.

    Args:
        shutdown_queue: Queue to monitor for shutdown signals
        worker_number: Worker number for logging
        shutdown_callback: Function to call on shutdown
        worker_instance: Optional worker instance to pass to callback
        worker_type: Descriptive name of the worker type
        shutdown_event: Optional event to signal shutdown completion

    Returns:
        Configured but not started Thread object
    """
    return threading.Thread(
        target=shutdown_monitor_task,
        args=(
            shutdown_queue,
            worker_number,
            shutdown_callback,
            worker_instance,
            worker_type,
            shutdown_event,
        ),
    )


def cleanup_thread(
    signal_thread: Optional[threading.Thread],
    logger: logging.Logger,
    timeout: Optional[float] = None,
) -> None:
    """
    Safely clean up a signal monitor thread.

    Args:
        signal_thread: The thread to clean up
        logger: Logger for debug messages
        timeout: Timeout for thread join (None for blocking wait)
    """
    if signal_thread and signal_thread.is_alive():
        try:
            signal_thread.join(timeout=timeout)
        except Exception as e:
            logger.debug("Error joining signal thread: %s", e)


def close_sockets(
    sockets_list: list,
    logger: logging.Logger,
) -> None:
    """
    Helper to safely close a list of sockets.

    Args:
        sockets_list: List of socket objects to close
        logger: Logger instance for debug messages
    """
    for sock in sockets_list:
        try:
            sock.close()
        except Exception as e:
            logger.debug("Error closing socket: %s", e)


def shutdown_monitor_task(
    shutdown_queue: Any,
    worker_number: int,
    shutdown_callback: Callable,
    worker_instance: Optional[Any] = None,
    worker_type: str = "worker",
    shutdown_event: Optional[threading.Event] = None,
) -> None:
    """
    Common shutdown monitoring task that waits for shutdown signals.

    Works with both ``concurrent.interpreters.Queue`` (raises ``QueueEmpty``)
    and ``multiprocessing.Queue`` (raises ``queue.Empty``) on timeout.
    """
    logger = logging.getLogger(__name__)
    try:
        while True:
            try:
                msg = shutdown_queue.get(timeout=10)
                if msg == "stop":
                    logger.info(
                        "Received stop signal - %s %d shutting down", worker_type, worker_number
                    )
                    if worker_instance:
                        shutdown_callback(worker_instance)
                    else:
                        shutdown_callback()
                    break
            except QueueEmpty, Empty:
                if shutdown_event and shutdown_event.is_set():
                    logger.info(
                        "Shutdown event set for %s %d, exiting monitor", worker_type, worker_number
                    )
                    break
                continue
            except OSError, RuntimeError:
                time.sleep(0.1)
    except Exception as e:
        logger.debug("Shutdown monitor error: %s", e)


def web_worker_task(
    worker_number: int,
    log_level: int,
    application_path: str,
    workers: int,
    bind: str,
    insecure_sockets: tuple,
    shutdown_queue: Any,
    shutdown_notification_queue: Optional[Any] = None,
    django_settings_module: Optional[str] = None,
) -> None:
    """
    Web worker task executable in either a subinterpreter (InterpreterPoolExecutor)
    or a subprocess (ProcessPoolExecutor). The queue arguments accept either
    ``concurrent.interpreters.Queue`` or ``multiprocessing.Queue``.
    """
    logger = setup_worker_logging(worker_number, log_level, "Web")
    logger.info("Starting web worker: %d", worker_number)

    # Setup Django if settings module provided
    if django_settings_module:
        import os

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", django_settings_module)
        django.setup(set_prefix=False)

    worker_shutdown_event = threading.Event()
    worker_shutdown_event.clear()
    thread_shutdown_event = threading.Event()
    signal_thread = None

    def web_worker_shutdown_callback():
        """Callback to handle web worker shutdown."""
        logger.info("Shutdown signal received")
        worker_shutdown_event.set()

        if shutdown_notification_queue:
            shutdown_notification_queue.put("shutdown")

    logger.info("Starting hypercorn worker")
    _insecure_sockets = []
    try:
        for sock_data in insecure_sockets:
            _insecure_sockets.append(socket(*sock_data))
        worker_hypercorn_sockets = Sockets([], _insecure_sockets, [])
        worker_config = Config()
        worker_config.application_path = application_path
        worker_config.workers = workers
        worker_config.debug = log_level == logging.DEBUG
        worker_config.accesslog = logger
        worker_config.bind = bind

        signal_thread = create_shutdown_monitor_thread(
            shutdown_queue,
            worker_number,
            web_worker_shutdown_callback,
            None,
            "Web App worker",
            thread_shutdown_event,
        )
        signal_thread.start()

        asyncio_worker(
            worker_config,
            worker_hypercorn_sockets,
            shutdown_event=worker_shutdown_event,  # type: ignore
        )

    except (OSError, RuntimeError) as e:
        logger.exception("Error in web worker: %s", e)
        if shutdown_notification_queue:
            try:
                shutdown_notification_queue.put("shutdown")
            except Exception:
                pass
    except Exception as e:
        logger.exception("Unexpected error in web worker: %s", e)
        if shutdown_notification_queue:
            shutdown_notification_queue.put("shutdown")
    finally:
        logger.debug("Starting worker cleanup")
        worker_shutdown_event.set()
        thread_shutdown_event.set()
        cleanup_thread(signal_thread, logger, timeout=None)
        close_sockets(_insecure_sockets, logger)

        time.sleep(0.1)
        logger.debug("Worker cleanup complete")


def _resolve_factory(factory: Any) -> Callable:
    """Resolve a dotted path to a callable, or return the callable as-is."""
    if isinstance(factory, str):
        module_path, _, attr = factory.rpartition(".")
        module = importlib.import_module(module_path)
        return getattr(module, attr)
    return factory


def db_worker_factory(
    *,
    command: str,
    args: tuple[str, ...],
    options: dict[str, Any],
    worker_number: int,
) -> Any:
    """Build a stoppable ``django_tasks_db`` worker."""
    from django_tasks_db.management.commands import db_worker

    return db_worker.Worker(
        queue_names=options.get("queue_names", ["default"]),
        interval=options.get("interval", 1),
        batch=options.get("batch", False),
        backend_name=options.get("backend_name", "default"),
        startup_delay=options.get("startup_delay", True),
        max_tasks=options.get("max_tasks"),
        worker_id=f"task_worker_{worker_number}",
        excluded_queue_names=[],
    )


def command_worker_task(
    worker_number: int,
    log_level: int,
    command: str,
    args: tuple[str, ...],
    options: dict[str, Any],
    worker_factory: Optional[Any] = None,
    shutdown_queue: Any = None,
    shutdown_notification_queue: Optional[Any] = None,
    django_settings_module: Optional[str] = None,
) -> None:
    """Run a management command or a stoppable command worker.

    Works with both subinterpreter and process pools. The queue arguments
    accept either ``concurrent.interpreters.Queue`` or
    ``multiprocessing.Queue``.
    """
    logger = setup_worker_logging(worker_number, log_level, "Command")
    logger.info("Starting command worker: %d (%s)", worker_number, command)

    if django_settings_module:
        import os

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", django_settings_module)
    django.setup(set_prefix=False)

    # Raw mode: run the management command to completion.
    if worker_factory is None:
        try:
            from django.core.management import call_command

            call_command(command, *args, **options)
        except Exception as exc:
            logger.exception("Command %s failed: %s", command, exc)
            if shutdown_notification_queue:
                shutdown_notification_queue.put("shutdown")
        return

    # Factory mode: build a stoppable worker and run it.
    factory = _resolve_factory(worker_factory)
    worker = factory(
        command=command,
        args=args,
        options=options,
        worker_number=worker_number,
    )

    thread_shutdown_event = threading.Event()
    signal_thread = None

    def shutdown_callback(w):
        w.shutdown(signal.SIGINT, None)
        thread_shutdown_event.set()

    try:
        signal_thread = create_shutdown_monitor_thread(
            shutdown_queue,
            worker_number,
            shutdown_callback,
            worker,
            "Command worker",
            thread_shutdown_event,
        )
        signal_thread.start()
        worker.run()
    except (OSError, RuntimeError) as e:
        logger.exception("Error in command worker: %s", e)
    except Exception as exc:
        logger.exception("Command worker errored: %s", exc)
        if shutdown_notification_queue:
            shutdown_notification_queue.put("shutdown")
    finally:
        logger.info("Command worker finished")
        thread_shutdown_event.set()
        cleanup_thread(signal_thread, logger, timeout=None)
        time.sleep(0.1)
        logger.debug("Command worker cleanup complete")
