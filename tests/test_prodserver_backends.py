"""Unit tests for the django-prodserver backends.

Both backends are thin adapters over ``run_application``: they read config
from the prodserver ``ARGS`` dict, validate required fields, and forward
the rest. These tests mock ``run_application`` so no real workers spawn.
"""

from unittest.mock import patch

import pytest

from django_melnottam.manager import PoolType
from django_melnottam.prodserver_backends.process import ProcessWorkerServer
from django_melnottam.prodserver_backends.subinterpreter import SubinterpreterWorkerServer


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
def _server_args(**overrides):
    """Build a minimal valid ARGS dict for a backend."""
    base = {
        "workers": 3,
        "task_workers": 1,
        "bind": "0.0.0.0:9000",
        "app_path": "myapp.wsgi:application",
        "django_settings_module": "myapp.settings",
    }
    base.update(overrides)
    return {"ARGS": base}


# ----------------------------------------------------------------------
# SubinterpreterWorkerServer
# ----------------------------------------------------------------------
@patch("django_melnottam.prodserver_backends.subinterpreter.run_application")
def test_subinterpreter_backend_forwards_args(mock_run):
    """Subinterpreter backend forwards all ARGS to run_application and uses
    the default (interpreter) pool_type."""
    server = SubinterpreterWorkerServer(**_server_args())
    server.start_server()

    mock_run.assert_called_once_with(
        app_path="myapp.wsgi:application",
        workers=3,
        task_workers=1,
        bind="0.0.0.0:9000",
        django_settings_module="myapp.settings",
    )
    # Default pool_type must be INTERPRETER (not passed explicitly by this backend)
    assert "pool_type" not in mock_run.call_args.kwargs


@patch("django_melnottam.prodserver_backends.subinterpreter.run_application")
def test_subinterpreter_backend_applies_defaults(mock_run):
    """Missing workers/task_workers/bind fall back to the documented defaults."""
    server = SubinterpreterWorkerServer(
        **_server_args(
            workers=None,
            task_workers=None,
            bind=None,
        )
    )
    # Remove the None entries so .get() falls back to defaults
    server.melnottam_args = {
        "app_path": "myapp.wsgi:application",
        "django_settings_module": "myapp.settings",
    }
    server.start_server()

    mock_run.assert_called_once_with(
        app_path="myapp.wsgi:application",
        workers=4,
        task_workers=2,
        bind="0.0.0.0:8000",
        django_settings_module="myapp.settings",
    )


def test_subinterpreter_backend_requires_app_path():
    """Missing app_path raises ValueError before touching run_application."""
    server = SubinterpreterWorkerServer(**_server_args(app_path=None))
    server.melnottam_args.pop("app_path")
    with pytest.raises(ValueError, match="app_path"):
        server.start_server()


def test_subinterpreter_backend_requires_django_settings_module():
    """Missing django_settings_module raises ValueError."""
    server = SubinterpreterWorkerServer(**_server_args(django_settings_module=None))
    server.melnottam_args.pop("django_settings_module")
    with pytest.raises(ValueError, match="django_settings_module"):
        server.start_server()


# ----------------------------------------------------------------------
# ProcessWorkerServer
# ----------------------------------------------------------------------
@patch("django_melnottam.prodserver_backends.process.run_application")
def test_process_backend_forwards_args_and_process_pool_type(mock_run):
    """Process backend forwards all ARGS AND explicitly selects PoolType.PROCESS."""
    server = ProcessWorkerServer(**_server_args())
    server.start_server()

    mock_run.assert_called_once_with(
        app_path="myapp.wsgi:application",
        workers=3,
        task_workers=1,
        bind="0.0.0.0:9000",
        django_settings_module="myapp.settings",
        pool_type=PoolType.PROCESS,
    )


@patch("django_melnottam.prodserver_backends.process.run_application")
def test_process_backend_applies_defaults(mock_run):
    """Missing workers/task_workers/bind fall back to the documented defaults."""
    server = ProcessWorkerServer(**_server_args())
    server.melnottam_args = {
        "app_path": "myapp.wsgi:application",
        "django_settings_module": "myapp.settings",
    }
    server.start_server()

    mock_run.assert_called_once_with(
        app_path="myapp.wsgi:application",
        workers=4,
        task_workers=2,
        bind="0.0.0.0:8000",
        django_settings_module="myapp.settings",
        pool_type=PoolType.PROCESS,
    )


def test_process_backend_requires_app_path():
    """Missing app_path raises ValueError before touching run_application."""
    server = ProcessWorkerServer(**_server_args())
    server.melnottam_args.pop("app_path")
    with pytest.raises(ValueError, match="app_path"):
        server.start_server()


def test_process_backend_requires_django_settings_module():
    """Missing django_settings_module raises ValueError."""
    server = ProcessWorkerServer(**_server_args())
    server.melnottam_args.pop("django_settings_module")
    with pytest.raises(ValueError, match="django_settings_module"):
        server.start_server()


# ----------------------------------------------------------------------
# Backend parity
# ----------------------------------------------------------------------
@patch("django_melnottam.prodserver_backends.subinterpreter.run_application")
@patch("django_melnottam.prodserver_backends.process.run_application")
def test_backends_differ_only_in_pool_type(mock_proc_run, mock_interp_run):
    """Given identical ARGS, the two backends call run_application with the
    same kwargs except for pool_type — the single axis of difference."""
    args = _server_args()

    SubinterpreterWorkerServer(**args).start_server()
    ProcessWorkerServer(**args).start_server()

    interp_kwargs = mock_interp_run.call_args.kwargs
    proc_kwargs = mock_proc_run.call_args.kwargs

    # Everything except pool_type must match
    interp_without_pool = {k: v for k, v in interp_kwargs.items() if k != "pool_type"}
    assert interp_without_pool == {k: v for k, v in proc_kwargs.items() if k != "pool_type"}

    # And pool_type is the one real difference
    assert "pool_type" not in interp_kwargs
    assert proc_kwargs["pool_type"] is PoolType.PROCESS
