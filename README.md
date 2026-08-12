# Django Melnottam

⚠️ **EXPERIMENTAL** — This library is still in early development. APIs may change without notice. Use with caution in production.

A Python package for running Django web and task workers from a single process, using either Python 3.14+ subinterpreters (`InterpreterPoolExecutor`) or OS processes (`ProcessPoolExecutor`). Pick the isolation model that fits your workload.

## Intention

Django deployment usually means juggling separate services for web and background tasks. `django-melnottam` collapses them into one process manager with sensible defaults — `django-tasks-db` for task queuing and `django-prodserver` for the production server — so you get a plug-and-play setup that drops into an existing Django project.

## Overview

`django-melnottam` runs workers through a pluggable pool backend (`PoolType.INTERPRETER` or `PoolType.PROCESS`) and gives you:

- **Web Workers** — ASGI/WSGI application servers powered by Hypercorn
- **Task Workers** — Django task queue workers backed by `django-tasks-db`
- **Two Isolation Models** — subinterpreters for low-overhead, in-process isolation; processes for full memory isolation and C-extension compatibility
- **Graceful Shutdown** — coordinated shutdown with timeout handling
- **Signal Management** — proper SIGINT handling and cleanup

## Requirements

- Python 3.14+
- Django 6.0+
- `django-tasks-db >= 0.12.0`
- `django-prodserver >= 2.4.0`
- `hypercorn >= 0.18.0`

> **Note on the process backend:** It uses the `fork` start method so duplicated socket file descriptors are inherited by worker subprocesses. On platforms where `fork` is unavailable (e.g. Windows, macOS 3.14+), web workers will not start correctly — task workers still work, or use the subinterpreter backend instead.

## Installation

### From Pip

```bash
pip install django-melnottam
```

## Preferred Deployment

The bundled `django-prodserver` backend is the recommended way to deploy web and task workers together.

### Deployment Command

```bash
BIND_ADDRESS="0.0.0.0:$PORT" WORKERS="$WORKERS" TASK_WORKERS="$INSTANCE_TASK_WORKERS" python manage.py prodserver web-with-task-worker
```

### Configuration in Django Settings

Choose the backend that matches your isolation needs. Both accept the same `ARGS`:

```python
from django_melnottam.config import PositiveIntegerValue, Value

INSTALLED_APPS = [
    # ...
    "django_tasks_db",
    "django_prodserver",
    # ...
]

TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default"],
    }
}

# Production process configuration with command-line parameter support
PRODUCTION_PROCESSES = {
    "web-with-task-worker": {
        # Subinterpreter backend (default, lower overhead):
        "BACKEND": "django_melnottam.prodserver_backends.subinterpreter.SubinterpreterWorkerServer",
        # Process backend (full process isolation):
        "BACKEND": "django_melnottam.prodserver_backends.process.ProcessWorkerServer",
        # Choose one from above.
        "ARGS": {
            "app_path": "myapp.wsgi:application",
            "django_settings_module": "myapp.settings",
            "workers": WORKERS,
            "task_workers": TASK_WORKERS,
            "bind": BIND_ADDRESS,
        },
    },
}
```

### Environment Variables

- `PORT` — port to bind to (e.g. `8000`)
- `WORKERS` — number of web workers (e.g. `4`)
- `INSTANCE_TASK_WORKERS` — number of task workers (e.g. `2`)

## Usage

### Command Line Interface

```bash
django-melnottam-cli <application> [options]
```

#### Arguments

- `application` — path to your WSGI application (e.g. `myapp.wsgi:application`)

#### Options

- `-w, --workers` — number of web workers (default: `2`)
- `-t, --task-workers` — number of task workers (default: `1`)
- `-b, --bind` — bind address for the web server (default: `0.0.0.0:9001`)
- `-s, --settings` — Django settings module (default: from `DJANGO_SETTINGS_MODULE`)
- `-v, --verbose` — enable debug logging

#### Example

```bash
django-melnottam-cli myapp.wsgi:application \
  --workers 4 \
  --task-workers 2 \
  --bind 0.0.0.0:8000 \
  --settings myapp.settings.production
```

### Programmatic Usage

```python
from django_melnottam.manager import run_application, PoolType

run_application(
    app_path="myapp.wsgi:application",
    workers=4,
    task_workers=2,
    bind="0.0.0.0:8000",
    django_settings_module="myapp.settings",
    pool_type=PoolType.INTERPRETER,  # or PoolType.PROCESS
)
```

## LLM Disclosure

LLMs were used for scaffolding boilerplate, drafting tests, shaping the `TASKS` config structure, and writing documentation (including this README). The design and idea were mostly not LLM suggestions. LLM output was reviewed and modified where needed.
