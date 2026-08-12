"""Settings for the e2e test app.

Kept intentionally minimal. The test driver points ``DJANGO_SETTINGS_MODULE``
at this module when it spawns ``run_application`` in a subprocess.
"""

import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "e2e-test-insecure-key"
DEBUG = False
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_tasks_db",
    "e2e_app",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "e2e_app.urls"

TEMPLATES: list = []

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # The test driver overrides this via the E2E_DB_PATH env var so each
        # test run gets a fresh, isolated database file.
        "NAME": os.environ.get(
            "E2E_DB_PATH",
            str(BASE_DIR / "e2e_test.sqlite3"),
        ),
    }
}

# File-based cache: subinterpreters can't share in-process memory, so a
# locmem cache would be invisible to workers running in other interpreters.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": tempfile.gettempdir(),
    }
}

TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default"],
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
