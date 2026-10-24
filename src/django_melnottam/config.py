from dataclasses import dataclass, field, fields
from typing import Any, Optional


def _task_kwargs(config: Any) -> dict[str, Any]:
    """Return the dataclass fields as kwargs, preserving object identity."""
    return {f.name: getattr(config, f.name) for f in fields(config)}


@dataclass
class WebWorkerConfig:
    """Configuration for WSGI application worker.

    Works with both subinterpreter and process pools: ``shutdown_queue``
    and ``shutdown_notification_queue`` accept either
    ``concurrent.interpreters.Queue`` or ``multiprocessing.Queue`` (both
    expose ``.get(timeout=)`` / ``.put()``).
    """

    worker_number: int
    log_level: int
    application_path: str
    workers: int = 1
    bind: str = "127.0.0.1:8000"
    insecure_sockets: tuple = ()
    shutdown_queue: Optional[Any] = None
    shutdown_notification_queue: Optional[Any] = None
    django_settings_module: Optional[str] = None

    def get_task_kwargs(self) -> dict[str, Any]:
        return _task_kwargs(self)


@dataclass
class CommandWorkerConfig:
    """Configuration for a management-command worker.

    ``worker_factory`` is optional. When omitted, the worker runs the command
    via ``call_command`` and the command must exit on its own. When provided,
    it must return an object with ``run()`` and ``shutdown()`` methods, which
    allows graceful shutdown via the worker's shutdown queue.
    """

    worker_number: int
    log_level: int
    command: str
    args: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    worker_factory: Optional[Any] = None
    shutdown_queue: Optional[Any] = None
    shutdown_notification_queue: Optional[Any] = None
    django_settings_module: Optional[str] = None

    def get_task_kwargs(self) -> dict[str, Any]:
        return _task_kwargs(self)
