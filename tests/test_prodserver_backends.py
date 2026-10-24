"""Unit tests for the django-prodserver backends.

Both backends are thin adapters over ``run_application``: they read config
from the prodserver ``ARGS`` dict, validate required fields, and forward
the rest. These tests mock ``run_application`` so no real workers spawn.
"""

from unittest.mock import patch

import pytest

from django_melnottam.backends import PoolType
from django_melnottam.prodserver_backends.process import ProcessWorkerServer
from django_melnottam.prodserver_backends.subinterpreter import SubinterpreterWorkerServer
from django_melnottam.workers import CommandSpec


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
def _server_args(**overrides):
    """Build a minimal valid ARGS dict for a backend."""
    base = {
        "workers": 3,
        "command_workers": [{"command": "myapp.management.commands.send_emails", "instances": 1}],
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
def test_subinterpreter_backend_applies_defaults(mock_run):
    """Missing workers/command_workers/bind fall back to the documented defaults."""
    server = SubinterpreterWorkerServer(
        **_server_args(
            workers=None,
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
        bind="0.0.0.0:8000",
        django_settings_module="myapp.settings",
        command_workers=[],
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
        bind="0.0.0.0:9000",
        django_settings_module="myapp.settings",
        command_workers=[
            CommandSpec(
                command="myapp.management.commands.send_emails",
                instances=1,
            )
        ],
        pool_type=PoolType.PROCESS,
    )


@patch("django_melnottam.prodserver_backends.process.run_application")
def test_process_backend_applies_defaults(mock_run):
    """Missing workers/command_workers/bind fall back to the documented defaults."""
    server = ProcessWorkerServer(**_server_args())
    server.melnottam_args = {
        "app_path": "myapp.wsgi:application",
        "django_settings_module": "myapp.settings",
    }
    server.start_server()

    mock_run.assert_called_once_with(
        app_path="myapp.wsgi:application",
        workers=4,
        bind="0.0.0.0:8000",
        django_settings_module="myapp.settings",
        command_workers=[],
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
