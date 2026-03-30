"""WSGI config for Bare Metal Automation dashboard."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bare_metal_automation.dashboard.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
