from typing import Any

from django_prodserver.backends.base import BaseServerBackend

from django_melnottam.manager import PoolType, run_application


class ProcessWorkerServer(BaseServerBackend):
    """
    A django-prodserver backend for running the application using process-pool
    workers (``ProcessPoolExecutor``) instead of subinterpreters.

    Each worker runs in its own OS process (forked), giving full memory
    isolation. This is the right choice when workers depend on C extensions
    or global state that does not play well with subinterpreters, or when
    you want the safety of process-level isolation at the cost of higher
    memory usage.

    Socket file descriptors are inherited via ``fork``; on platforms where
    ``fork`` is unavailable (e.g. Windows), web workers will not start
    correctly — use the subinterpreter backend there instead.

    Configuration example in settings.py:

        PRODUCTION_PROCESSES = {
            "web-with-task-worker": {
                "BACKEND": "django_melnottam.prodserver_backends.process.ProcessWorkerServer",
                "ARGS": {
                    "workers": 4,
                    "task_workers": 2,
                    "bind": "0.0.0.0:8000",
                },
            },
        }
    """

    def __init__(self, **server_args: Any) -> None:
        self.melnottam_args = server_args.get("ARGS", {})
        super().__init__(**server_args)

    def start_server(self, *args, **kwargs):
        """
        Start the application using process-pool workers.
        """
        # Set defaults and merge with provided args
        workers = self.melnottam_args.get("workers", 4)
        task_workers = self.melnottam_args.get("task_workers", 2)
        bind = self.melnottam_args.get("bind", "0.0.0.0:8000")
        app_path = self.melnottam_args.get("app_path")
        django_settings_module = self.melnottam_args.get("django_settings_module")

        if not app_path:
            raise ValueError("'app_path' is required in ARGS. Example: 'myapp.wsgi:application'")
        if not django_settings_module:
            raise ValueError(
                "'django_settings_module' is required in ARGS. Example: 'myapp.settings'"
            )

        run_application(
            app_path=app_path,
            workers=workers,
            task_workers=task_workers,
            bind=bind,
            django_settings_module=django_settings_module,
            pool_type=PoolType.PROCESS,
        )
