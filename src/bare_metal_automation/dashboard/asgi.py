"""ASGI entry point for Bare Metal Automation dashboard.

Use this instead of ``wsgi.py`` when serving with Daphne or another
ASGI-capable server that supports WebSockets::

    daphne bare_metal_automation.dashboard.asgi:application
    # or
    uvicorn bare_metal_automation.dashboard.asgi:application --host 0.0.0.0 --port 8000
"""

import os

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "bare_metal_automation.dashboard.settings",
)

from bare_metal_automation.dashboard.routing import application  # noqa: F401, E402
