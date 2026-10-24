from typing import Any

from django_prodserver.backends.base import BaseServerBackend

from django_melnottam.application import run_application
from django_melnottam.workers import CommandSpec


class SubinterpreterWorkerServer(BaseServerBackend):
    """
    A django-prodserver backend for running the application using
    subinterpreter workers.

    Configuration example in settings.py:

        PRODUCTION_PROCESSES = {
            "web-with-command-workers": {
                "BACKEND": "django_melnottam.prodserver_backends.subinterpreter.SubinterpreterWorkerServer",
                "ARGS": {
                    "workers": 4,
                    "bind": "0.0.0.0:8000",
                    "app_path": "myapp.wsgi:application",
                    "django_settings_module": "myapp.settings",
                    "command_workers": [
                        {
                            "command": "myapp.management.commands.send_emails",
                            "instances": 1,
                            "worker_factory": "django_melnottam.tasks.send_emails_factory",
                        },
                        {
                            "command": "django_tasks_db.management.commands.db_worker",
                            "options": {"queue_names": ["default"]},
                            "instances": 2,
                            "worker_factory": "django_melnottam.tasks.db_worker_factory",
                        },
                    ],
                },
            },
        }
    """

    def __init__(self, **server_args: Any) -> None:
        self.melnottam_args = server_args.get("ARGS", {})
        super().__init__(**server_args)

    def start_server(self, *args, **kwargs):
        """Start the application using subinterpreter workers."""
        workers = self.melnottam_args.get("workers", 4)
        bind = self.melnottam_args.get("bind", "0.0.0.0:8000")
        app_path = self.melnottam_args.get("app_path")
        django_settings_module = self.melnottam_args.get("django_settings_module")
        command_workers = [
            CommandSpec.from_dict(spec) for spec in self.melnottam_args.get("command_workers", [])
        ]

        if not app_path:
            raise ValueError("'app_path' is required in ARGS. Example: 'myapp.wsgi:application'")
        if not django_settings_module:
            raise ValueError(
                "'django_settings_module' is required in ARGS. Example: 'myapp.settings'"
            )

        run_application(
            app_path=app_path,
            workers=workers,
            bind=bind,
            django_settings_module=django_settings_module,
            command_workers=command_workers,
        )
