# Django Melnottam

⚠️ **EXPERIMENTAL** — This library is in early development. APIs may change without notice.

Run Django web workers and management-command workers from one process, using either Python 3.14+ subinterpreters (`InterpreterPoolExecutor`) or OS processes (`ProcessPoolExecutor`).

## What it does

`django-melnottam` gives you one process manager for:

- **Web workers** — ASGI/WSGI application servers powered by Hypercorn.
- **Command workers** — any Django management command, run N times.
- **Two isolation models** — subinterpreters for low overhead, processes for full memory isolation.
- **Graceful shutdown** — queue-based stop signals, with process termination as a fallback.

## Requirements

- Python 3.14+
- Django 6.0+
- `django-tasks-db >= 0.12.0`
- `django-prodserver >= 2.4.0`
- `hypercorn >= 0.18.0`

> **Process backend note:** It uses the `fork` start method so duplicated socket FDs are inherited by workers. On platforms without `fork` (e.g. Windows), web workers will not start; use the subinterpreter backend there.

## Installation

```bash
pip install django-melnottam
```

## Deployment with django-prodserver

If you use `db_worker`, add the task backend first:

```python
INSTALLED_APPS = [
    # ...
    "django_tasks_db",
    "django_prodserver",
]

TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default"],
    }
}
```

Then configure the server:

For Multiprocessing
```python
PRODUCTION_PROCESSES = {
            "web-with-command-workers": {
                "BACKEND": "django_melnottam.prodserver_backends.process.ProcessWorkerServer",
                "ARGS": {
                    "workers": 2,
                    "bind": "0.0.0.0:8000",
                    "app_path": "myapp.asgi:application",
                    "django_settings_module": "myapp.settings",
                    "command_workers": [
                        {
                            # Provided you have the django_tasks backend installed.
                            "command": "db_worker",
                            # Not doing this causes duplicate workers being loaded,
                            # So might as well be careful about it.
                            "options": {"reload": False},
                            "instances":1,
                        },
                    ],
                },
            },
        }


```

For Subinterpreters (which is still experimental as the subinterpreter eosystem is still changing)
```python
PRODUCTION_PROCESSES = {
    "web-with-command-workers": {
        "BACKEND": "django_melnottam.prodserver_backends.subinterpreter.SubinterpreterWorkerServer",
        "ARGS": {
            "app_path": "myapp.wsgi:application",
            "django_settings_module": "myapp.settings",
            "workers": 4,
            "bind": "0.0.0.0:8000",
            "command_workers": [
                {
                    "command": "myapp.management.commands.send_emails",
                    "instances": 1,
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
```

Run it with:

```bash
python manage.py prodserver web-with-command-workers
```

Use `django_melnottam.prodserver_backends.process.ProcessWorkerServer` for the process backend.

## Command workers

Each entry in `command_workers` describes one management command:

- `command` — explicit dotted path to the command module.
- `args` — positional/CLI-style arguments (optional).
- `options` — keyword options passed to `call_command` (optional).
- `instances` — number of copies to run (default `1`).
- `worker_factory` — optional dotted path to a factory returning a stoppable object.

Without `worker_factory`, the worker runs `call_command(...)` and must exit on its own. This is ideal for one-shot commands.

For long-running commands like `db_worker`, provide a factory. The bundled `django_melnottam.tasks.db_worker_factory` returns a `django_tasks_db` worker that can be stopped through the shutdown queue.

The subinterpreter backend cannot force-stop a running command, so long-running commands there need a factory. The process backend does not require one: it terminates any remaining workers with `ProcessPoolExecutor.terminate_workers()`.

## CLI

```bash
django-melnottam-cli <application> [options]
```

- `-w, --workers` — number of web workers (default `2`)
- `-c, --command-workers` — JSON list of command specs (default `[]`)
- `-b, --bind` — bind address (default `0.0.0.0:9001`)
- `-s, --settings` — Django settings module
- `-p, --pool` — `interpreter` or `process` (default `interpreter`)
- `-v, --verbose` — debug logging

Example:

```bash
django-melnottam-cli myapp.wsgi:application \
  --workers 4 \
  --bind 0.0.0.0:8000 \
  --settings myapp.settings \
  --command-workers '[{"command": "myapp.management.commands.send_emails", "instances": 1}]'
```

## Programmatic usage

```python
from django_melnottam.application import run_application
from django_melnottam.backends import PoolType
from django_melnottam.workers import CommandSpec

run_application(
    app_path="myapp.wsgi:application",
    workers=4,
    bind="0.0.0.0:8000",
    django_settings_module="myapp.settings",
    pool_type=PoolType.INTERPRETER,  # or PoolType.PROCESS
    command_workers=[
        CommandSpec(
            command="myapp.management.commands.send_emails",
            instances=1,
        ),
        CommandSpec(
            command="django_tasks_db.management.commands.db_worker",
            options={"queue_names": ["default"]},
            instances=2,
            worker_factory="django_melnottam.tasks.db_worker_factory",
        ),
    ],
)
```

## LLM Disclosure

The project idea and design are the author's. LLMs assisted with
scaffolding boilerplate, drafting tests, and writing documentation
(including this README). All generated output was reviewed and edited.
