# Django Melnottam

A Python package for managing Django application workers using `InterpreterPoolExecutor` from Python 3.14+. This package provides a clean abstraction for running web and task workers in isolated Python interpreters.

## Overview

`django-subinterpreter-workers` manages multiple worker processes using Python 3.14's `concurrent.futures.InterpreterPoolExecutor`. It provides:

- **Web Workers**: ASGI/WSGI application servers using Hypercorn
- **Task Workers**: Django task queue workers using `django-tasks-db`
- **Isolated Execution**: Each worker runs in its own Python interpreter, avoiding GIL contention
- **Graceful Shutdown**: Coordinated shutdown with timeout handling
- **Signal Management**: Proper SIGINT handling and cleanup

## Features

- ✅ Multiple web and task workers managed in a single pool
- ✅ Automatic socket duplication for web worker binding
- ✅ Shared shutdown coordination via inter-interpreter queues
- ✅ Configurable worker counts and binding addresses
- ✅ Rich logging with timestamps and worker identification
- ✅ Django settings module support
- ✅ Graceful shutdown with configurable timeouts

## Requirements

- Python 3.14+
- Django 6.0+
- `django-tasks-db >= 0.12.0`
- `hypercorn >= 0.18.0`
- `rich >= 15.0.0`

## Installation

### From GitHub

```bash
pip install git+https://github.com/yourusername/django-subinterpreter-workers.git
```


## Usage

### Command Line Interface

```bash
server-subinterpreter-worker <application> [options]
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
server-subinterpreter-worker myapp.wsgi:application \
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

### Using the InterpreterPoolManager Directly

```python
from django_melnottam.manager import InterpreterPoolManager
from django_melnottam.config import WebWorkerConfig, TaskWorkerConfig

# Create manager with total worker count
pool = InterpreterPoolManager(max_workers=6)

# Configure and start web workers
web_config = WebWorkerConfig(
    worker_number=1,
    log_level=logging.INFO,
    application_path="myapp.wsgi:application",
    workers=4,
    bind="0.0.0.0:8000"
)
pool.start_worker(web_config)

# Configure and start task workers
task_config = TaskWorkerConfig(
    worker_number=2,
    log_level=logging.INFO
)
pool.start_worker(task_config)

# Gracefully shutdown
pool.shutdown(timeout=10.0)
```

## Configuration

### Django Settings

Ensure your Django settings module is properly configured with:

```python
# settings.py
TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.InMemoryBackend",
        # or use your preferred backend
    }
}
```

### Environment Variables

- `DJANGO_SETTINGS_MODULE`: Path to your Django settings module (required if not provided via CLI)

## Architecture

### Worker Types

#### Web Workers
- Run Hypercorn ASGI server
- Each worker binds to the same port (handled via socket duplication)
- Manages HTTP requests
- Shutdown triggered by stop signal or exception

#### Task Workers
- Run `django-tasks-db` worker
- Process background tasks from the task queue
- Single worker processes tasks sequentially

### Communication

Workers communicate via `concurrent.interpreters.Queue`:

- **Shutdown Queue**: Each worker monitors its shutdown queue for stop signals
- **Parent Shutdown Queue**: Workers can signal the parent to shutdown
- **Worker Queue**: Reserved for future inter-worker communication

### Interpreter Pool

The `InterpreterPoolExecutor` manages a pool of Python interpreters:

- Each submitted task runs in its own interpreter
- Interpreters are created on-demand up to `max_workers`
- No shared state between workers (strong isolation)
- GIL is not contended between workers

## Shutdown Behavior

1. SIGINT signal received → initiates graceful shutdown
2. Stop signal sent to all workers via shutdown queues
3. Workers begin cleanup (close sockets, flush logs)
4. Parent waits for worker futures with timeout
5. InterpreterPoolExecutor shuts down
6. Subinterpreters are cleaned up

Timeout is configurable (default: 5 seconds per worker).

## Exceptions

The package provides custom exceptions for error handling:

- `WorkerException`: Base exception
- `WorkerInitializationError`: Worker failed to start
- `WorkerShutdownError`: Worker shutdown failed
- `ConfigurationError`: Invalid configuration
- `HookExecutionError`: Hook execution failed
- `TaskExecutionError`: Task execution failed
- `PoolExecutionError`: Pool-level error

## Development

### Setup Development Environment

```bash
uv sync --all-extras
```


## Running Tests

### Run All Tests
```bash
uv run pytest
```


### Code Quality

```bash
# Format code
ruff format .

# Lint and fix
ruff check . --fix
```

## Troubleshooting

### Workers Not Starting
- Check Django settings module is valid
- Verify application path is correct
- Check logs for detailed error messages
- Use `--verbose` flag for debug output

### Memory Usage High
- Reduce `max_workers` count
- Check for memory leaks in application code
- Monitor with `ps aux | grep python`

### Slow Task Processing
- Increase `--task-workers`
- Check task queue backend is properly configured
