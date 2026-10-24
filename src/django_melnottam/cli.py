import argparse
import json
import logging
import os
import sys

import django
from django.conf import settings
from rich.logging import RichHandler

from django_melnottam.application import run_application
from django_melnottam.backends import PoolType
from django_melnottam.workers import CommandSpec

logging.basicConfig(
    level=logging.INFO,
    format="[server_subinterpreter_worker] %(message)s",
    handlers=[RichHandler()],
)
logger = logging.getLogger(__name__)


def setup_django():
    """Initialize Django if not already configured."""
    if not settings.configured:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "parimitham.settings")
    django.setup(set_prefix=False)


def main():
    parser = argparse.ArgumentParser(
        description="Django application with InterpreterPoolExecutor worker management",
        prog="django-melnottam-cli",
    )
    parser.add_argument(
        "application",
        help="WSGI application path (e.g., 'myapp.wsgi:application')",
        type=str,
    )
    parser.add_argument(
        "-w",
        "--workers",
        dest="workers",
        help="The number of web workers to spawn and use",
        default=2,
        type=int,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Increase logging verbosity",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "-b",
        "--bind",
        help="Bind address for the web server",
        default="0.0.0.0:9001",
        type=str,
    )
    parser.add_argument(
        "-c",
        "--command-workers",
        help="JSON list of command worker specs, e.g. "
        '\'[{"command": "myapp.management.commands.send_emails", "instances": 1}]\'',
        default="[]",
        type=str,
    )
    parser.add_argument(
        "-s",
        "--settings",
        help="Django settings module",
        default=None,
        type=str,
    )
    parser.add_argument(
        "-p",
        "--pool",
        choices=[p.value for p in PoolType],
        default=PoolType.INTERPRETER.value,
        help="Concurrency backend: 'interpreter' (subinterpreters) or "
        "'process' (subprocesses). Defaults to interpreter.",
        type=str,
    )

    args = parser.parse_args()

    # Set up logging
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # Set Django settings module if provided
    if args.settings:
        os.environ["DJANGO_SETTINGS_MODULE"] = args.settings

    # Setup Django
    setup_django()

    command_workers = [CommandSpec.from_dict(spec) for spec in json.loads(args.command_workers)]

    # Run the application
    run_application(
        app_path=args.application,
        workers=args.workers,
        bind=args.bind,
        django_settings_module=args.settings,
        pool_type=PoolType(args.pool),
        command_workers=command_workers,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
