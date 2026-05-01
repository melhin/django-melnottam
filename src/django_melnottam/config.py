from concurrent.interpreters import Queue
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class WebWorkerConfig:
    """Configuration for WSGI application worker."""

    worker_number: int
    log_level: int
    application_path: str
    workers: int = 1
    bind: str = "127.0.0.1:8000"
    insecure_sockets: tuple = ()
    shutdown_queue: Optional[Queue] = None
    worker_queue: Optional[Queue] = None
    parent_shutdown_queue: Optional[Queue] = None
    django_settings_module: Optional[str] = None

    def get_task_function(self) -> Callable:
        from .tasks import web_worker_task

        return web_worker_task

    def get_task_kwargs(self) -> dict:
        return {
            "worker_number": self.worker_number,
            "log_level": self.log_level,
            "application_path": self.application_path,
            "workers": self.workers,
            "bind": self.bind,
            "insecure_sockets": self.insecure_sockets,
            "shutdown_queue": self.shutdown_queue,
            "worker_queue": self.worker_queue,
            "parent_shutdown_queue": self.parent_shutdown_queue,
            "django_settings_module": self.django_settings_module,
        }


@dataclass
class TaskWorkerConfig:
    """Configuration for task queue worker."""

    worker_number: int
    log_level: int
    shutdown_queue: Optional[Queue] = None
    worker_queue: Optional[Queue] = None
    parent_shutdown_queue: Optional[Queue] = None
    django_settings_module: Optional[str] = None

    def get_task_function(self) -> Callable:
        from .tasks import task_worker_task

        return task_worker_task

    def get_task_kwargs(self) -> dict:
        return {
            "worker_number": self.worker_number,
            "log_level": self.log_level,
            "shutdown_queue": self.shutdown_queue,
            "worker_queue": self.worker_queue,
            "parent_shutdown_queue": self.parent_shutdown_queue,
            "django_settings_module": self.django_settings_module,
        }
