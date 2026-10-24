"""Application entrypoint: run web and command workers."""

import logging
from typing import Optional, Sequence

from .backends import PoolType
from .lifecycle import sigint_shutdown, wait_for_shutdown_signal
from .manager import PoolManager
from .workers import CommandSpec

logger = logging.getLogger(__name__)


def run_application(
    app_path: str,
    workers: int,
    bind: str = "127.0.0.1:8000",
    django_settings_module: Optional[str] = None,
    pool_type: PoolType = PoolType.INTERPRETER,
    command_workers: Sequence[CommandSpec] = (),
):
    """Run the Django application and command workers.

    Args:
        app_path: WSGI application path (e.g. ``myapp.wsgi:application``).
        workers: Number of web workers.
        bind: Bind address for the web server.
        django_settings_module: Django settings module path.
        pool_type: Which concurrency backend to use.
        command_workers: Management-command worker specs.
    """
    command_worker_count = sum(spec.instances for spec in command_workers)
    logger.info("Starting Django application and command workers with %s pool", pool_type.value)
    logger.info(
        "Web workers: %d, Command workers: %d, Bind: %s",
        workers,
        command_worker_count,
        bind,
    )

    with PoolManager(
        max_workers=workers + command_worker_count,
        pool_type=pool_type,
        django_settings_module=django_settings_module,
    ) as pool:
        with sigint_shutdown() as shutdown_requested:
            pool.start_web_workers(app_path, workers, bind)
            pool.start_command_workers(command_workers)
            logger.info("All workers started. Press Ctrl+C to stop.")

            worker_requested_shutdown = wait_for_shutdown_signal(
                pool.parent_queue, shutdown_requested
            )
            if worker_requested_shutdown or shutdown_requested.is_set():
                pool.shutdown(timeout=5.0)
            else:
                pool.join()

        logger.info("Application shutdown complete")
