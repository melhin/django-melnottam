"""Minimal Django app for end-to-end testing django-melnottam.

This app exists *only* so the test suite can boot a real ``run_application``
instance (web + task workers) and verify that:

* web workers come up and serve HTTP,
* task workers pick up jobs enqueued via the web endpoint,
* the whole pool shuts down gracefully on SIGINT.

It deliberately depends on nothing outside Django + django-tasks-db so it
stays usable in both the interpreter and process pool backends.
"""

default_app_config = "e2e_app.apps.E2eAppConfig"
