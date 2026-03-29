"""WSGI config for ZTP-Forge dashboard."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ztp_forge.dashboard.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
