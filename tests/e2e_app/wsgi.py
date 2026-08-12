"""WSGI application for the e2e test app.

All views in ``e2e_app.views`` are synchronous, so WSGI is the natural fit.
Hypercorn serves WSGI apps by wrapping them in an async adapter, which works
fine for these tests.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "e2e_settings")

application = get_wsgi_application()
