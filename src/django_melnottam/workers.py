"""Build worker configurations.

This module owns the details of turning high-level options (app path, bind
address, worker counts, command specs) into the config objects that
``PoolManager`` submits to an executor. Keeping it separate from
``manager.py`` means the manager only has to deal with worker lifecycle.
"""

import logging
from dataclasses import dataclass, field
from socket import dup
from typing import Any, Callable, Optional, Sequence

from hypercorn.config import Config, Sockets

from .config import CommandWorkerConfig, WebWorkerConfig

logger = logging.getLogger(__name__)


@dataclass
class CommandSpec:
    """Declarative spec for a management-command worker.

    ``command`` is an explicit dotted path to the command module, e.g.
    ``"myapp.management.commands.send_emails"``. ``worker_factory`` is
    optional; when provided it must be a callable (or dotted path to one)
    that returns an object with ``run()`` and ``shutdown()`` methods.
    """

    command: str
    args: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    instances: int = 1
    worker_factory: Optional[Callable[..., Any] | str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandSpec":
        return cls(
            command=data["command"],
            args=tuple(data.get("args", ())),
            options=dict(data.get("options", {})),
            instances=int(data.get("instances", 1)),
            worker_factory=data.get("worker_factory"),
        )


def build_web_worker_configs(
    *,
    app_path: str,
    workers: int,
    bind: str,
    log_level: int,
    django_settings_module: Optional[str] = None,
) -> list[WebWorkerConfig]:
    """Build one ``WebWorkerConfig`` per web worker.

    Each worker receives its own duplicated socket file descriptors so no two
    workers share an FD. The originals are closed once duplication is done.
    """
    sockets = _create_sockets(bind, workers)
    try:
        return [
            WebWorkerConfig(
                worker_number=i + 1,
                log_level=log_level,
                application_path=app_path,
                workers=workers,
                bind=bind,
                insecure_sockets=_duplicate_sockets(sockets),
                django_settings_module=django_settings_module,
            )
            for i in range(workers)
        ]
    finally:
        _close_sockets(sockets)


def build_command_worker_configs(
    specs: Sequence[CommandSpec],
    *,
    log_level: int,
    django_settings_module: Optional[str] = None,
) -> list[CommandWorkerConfig]:
    """Expand command specs into one config per instance."""
    configs = []
    worker_number = 1
    for spec in specs:
        for _ in range(spec.instances):
            configs.append(
                CommandWorkerConfig(
                    worker_number=worker_number,
                    log_level=log_level,
                    command=spec.command,
                    args=spec.args,
                    options=spec.options,
                    worker_factory=spec.worker_factory,
                    django_settings_module=django_settings_module,
                )
            )
            worker_number += 1
    return configs


def _create_sockets(bind: str, workers: int) -> Sockets:
    config = Config()
    config.bind = bind
    config.workers = workers
    return config.create_sockets()


def _duplicate_sockets(sockets: Sockets) -> tuple:
    return tuple(
        (int(s.family), int(s.type), s.proto, dup(s.fileno())) for s in sockets.insecure_sockets
    )


def _close_sockets(sockets: Sockets) -> None:
    for sock in sockets.insecure_sockets:
        try:
            sock.close()
        except Exception as e:
            logger.warning("Failed to close original socket: %s", e)
