# Django Melnottam

⚠️ **EXPERIMENTAL** - This library is still in early development. APIs are subject to change without notice. Use at your own risk and with caution in production environments.


A Python package for managing Django application workers using `InterpreterPoolExecutor` from Python 3.14+.
This package provides a clean abstraction for running web and task workers in isolated Python interpreters.


## Intention

This library simplifies Django deployment by running web and task workers in a single process
Rather than managing separate services, you get a unified, straightforward deployment experience.
We've selected sensible defaults—including `django-tasks-db` for task queuing and `django-prodserver` for the production server—to create a plug-and-play system that integrates seamlessly with your existing Django applications.

## Overview

`django-melnottam` manages multiple worker processes using Python 3.14's `concurrent.futures.InterpreterPoolExecutor`. It provides:

- **Web Workers**: ASGI/WSGI application servers using Hypercorn
- **Task Workers**: Django task queue workers using `django-tasks-db`
- **Isolated Execution**: Each worker runs in its own Python interpreter, avoiding GIL contention
- **Graceful Shutdown**: Coordinated shutdown with timeout handling
- **Signal Management**: Proper SIGINT handling and cleanup

## Requirements

- Python 3.14+
- Django 6.0+
- `django-tasks-db >= 0.12.0`
- `django-prodserver>=2.4.0`
- `hypercorn >= 0.18.0`

## Installation

### From Pip

```bash
pip install django-melnottam
```

## Preferred Deployment

This library includes a built-in `django-prodserver` backend that provides the recommended way to deploy your Django application with web and task workers.

### Deployment Command

To use the preferred deployment method with `django-prodserver`, run the following command with environment variable substitution:

```bash
BIND_ADDRESS="0.0.0.0:$PORT" WORKERS="$WORKERS" TASK_WORKERS="$INSTANCE_TASK_WORKERS" python manage.py prodserver web-with-task-worker
```

### Configuration in Django Settings

Ensure your `settings.py` includes the production process configuration:

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
        "BACKEND": "django_melnottam.prodserver_backends.subinterpreter.SubinterpreterWorkerServer",
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

- `PORT`: The port to bind to (e.g., `8000`)
- `WORKERS`: Number of web workers (e.g., `4`)
- `INSTANCE_TASK_WORKERS`: Number of task workers (e.g., `2`)

## Usage
### Command Line Interface

```bash
django-melnottam-cli <application> [options]
```

#### Arguments

- `application`: Path to your WSGI application (e.g., `myapp.wsgi:application`)

#### Options

- `-w, --workers`: Number of web workers (default: 2)
- `-t, --task-workers`: Number of task workers (default: 1)
- `-b, --bind`: Bind address for web server (default: `0.0.0.0:9001`)
- `-s, --settings`: Django settings module (default: from `DJANGO_SETTINGS_MODULE` env var)
- `-v, --verbose`: Enable debug logging

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
from django_melnottam.manager import run_application

run_application(
    app_path="myapp.wsgi:application",
    workers=4,
    task_workers=2,
    bind="0.0.0.0:8000",
    django_settings_module="myapp.settings"
)
```
