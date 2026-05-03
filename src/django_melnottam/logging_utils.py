"""Logging utilities for django-melnottam."""

import logging

from django.conf import settings


def get_log_level(explicit_level: int | None = None) -> int:
    """
    Determine the appropriate logging level.

    If an explicit level is provided, it will be used. Otherwise, the level
    is determined based on Django's DEBUG setting:
    - DEBUG=True -> logging.DEBUG
    - DEBUG=False -> logging.INFO

    Args:
        explicit_level: An explicit logging level to use (takes precedence)

    Returns:
        The logging level to use
    """
    if explicit_level is not None:
        return explicit_level

    if settings.configured and settings.DEBUG:
        return logging.DEBUG
    return logging.INFO
