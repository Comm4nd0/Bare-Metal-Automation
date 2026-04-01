"""Django settings for Bare Metal Automation dashboard."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = os.environ.get(
    "BMA_SECRET_KEY",
    "django-insecure-dev-only-change-in-production",
)

DEBUG = os.environ.get("BMA_DEBUG", "true").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get("BMA_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "channels",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "bare_metal_automation.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "bare_metal_automation.dashboard.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Django Channels ────────────────────────────────────────────────────────

ASGI_APPLICATION = "bare_metal_automation.dashboard.routing.application"

# In-memory channel layer — no Redis required for single-server deployments.
# For multi-server or high-availability deployments swap this for:
#   channels_redis.core.RedisChannelLayer with HOSTS pointing to Redis.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# BMA specific
BMA_INVENTORY_PATH = os.environ.get(
    "BMA_INVENTORY_PATH",
    "configs/inventory/inventory.yaml",
)

# NetBox integration (empty = disabled, use manual YAML workflow)
BMA_NETBOX_URL = os.environ.get("BMA_NETBOX_URL", "")
BMA_NETBOX_TOKEN = os.environ.get("BMA_NETBOX_TOKEN", "")
BMA_NETBOX_TAG_PATTERN = os.environ.get(
    "BMA_NETBOX_TAG_PATTERN", r"^D\d+$",
)

# Git repo for templates and firmware
BMA_GIT_REPO_URL = os.environ.get("BMA_GIT_REPO_URL", "")
BMA_GIT_REPO_BRANCH = os.environ.get("BMA_GIT_REPO_BRANCH", "main")
BMA_GIT_REPO_PATH = os.environ.get("BMA_GIT_REPO_PATH", "configs")
