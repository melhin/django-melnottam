"""Unit tests for worker config construction."""

import os

from django_melnottam.workers import (
    CommandSpec,
    build_command_worker_configs,
    build_web_worker_configs,
)


def test_build_command_worker_configs():
    """Command specs expand into one config per instance."""
    specs = [
        CommandSpec("myapp.management.commands.send_emails", instances=2),
        CommandSpec("myapp.management.commands.cleanup", instances=1),
    ]
    configs = build_command_worker_configs(
        specs,
        log_level=20,
        django_settings_module="my.settings",
    )

    assert [c.worker_number for c in configs] == [1, 2, 3]
    assert [c.command for c in configs] == [
        "myapp.management.commands.send_emails",
        "myapp.management.commands.send_emails",
        "myapp.management.commands.cleanup",
    ]
    assert all(c.log_level == 20 for c in configs)
    assert all(c.django_settings_module == "my.settings" for c in configs)


def test_build_web_worker_configs_duplicates_sockets_per_worker():
    """Each web config gets its own duplicated socket FDs."""
    configs = build_web_worker_configs(
        app_path="myapp.wsgi:application",
        workers=2,
        bind="127.0.0.1:0",
        log_level=20,
        django_settings_module="my.settings",
    )

    try:
        assert len(configs) == 2
        assert configs[0].insecure_sockets
        assert configs[0].insecure_sockets is not configs[1].insecure_sockets

        for config in configs:
            assert config.application_path == "myapp.wsgi:application"
            assert config.workers == 2
            assert config.bind == "127.0.0.1:0"
            assert config.django_settings_module == "my.settings"
    finally:
        # Close the duplicated FDs so the test does not leak descriptors.
        for config in configs:
            for *_, fd in config.insecure_sockets:
                os.close(fd)
