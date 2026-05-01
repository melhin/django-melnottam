import argparse
import logging
import os
import sys

import django
from django.conf import settings
from rich.logging import RichHandler

from django_melnottam.manager import run_application

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
        prog="server-subinterpreter-worker",
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
        "-t",
        "--task-workers",
        help="Number of task workers",
        default=1,
        type=int,
    )
    parser.add_argument(
        "-s",
        "--settings",
        help="Django settings module",
        default=None,
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

    # Run the application
    run_application(
        app_path=args.application,
        workers=args.workers,
        task_workers=args.task_workers,
        bind=args.bind,
        django_settings_module=args.settings,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
