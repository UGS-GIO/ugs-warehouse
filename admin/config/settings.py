"""ugs-warehouse-admin — minimal Django + HTMX ops console behind Google Cloud IAP.

Stateless: no domain DB (SQLite is ephemeral, only for Django's own tables). AuthN is IAP
(the load balancer verifies the user before the request arrives); authZ is an email allowlist
(`ADMIN_EMAILS`). The app reads the STAC catalog and triggers Cloud Run jobs — it stores nothing.
"""
import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default=None) or __import__("secrets").token_urlsafe(50)
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Who may use the console — IAP-verified emails on this allowlist (lowercased). In DEBUG with
# DEV_IAP_EMAIL set, that email is treated as the logged-in user.
ADMIN_EMAILS = [e.strip().lower() for e in env.list("ADMIN_EMAILS", default=[]) if e.strip()]
DEV_IAP_EMAIL = env("DEV_IAP_EMAIL", default="")

# GCP — the project/region the warehouse Cloud Run jobs live in, and the STAC catalog base.
GCP_PROJECT = env("GCP_PROJECT", default="ut-dnr-ugs-backend-tools")
GCP_REGION = env("GCP_REGION", default="us-central1")
STAC_BASE = env("STAC_BASE", default="https://maps-assets.geology.utah.gov/warehouse/stac").rstrip("/")
# Dry-run: don't actually execute jobs (local dev / demo). Real runs need the SA + run.developer.
JOBS_DRY_RUN = env.bool("JOBS_DRY_RUN", default=DEBUG)

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "django.contrib.messages",
    "django_htmx",
    "core",
    "ops",
]

MIDDLEWARE = [
    "core.health.HealthCheckMiddleware",  # first — answer the probe before host validation
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "core.iap_auth.IAPAuthMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.messages.context_processors.messages",
        "core.iap_auth.context_processor",
    ]},
}]

# SQLite, ephemeral — only Django's own tables (no domain data). Survives a container restart? No,
# and that's fine: there's nothing to persist.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/db.sqlite3"}}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
TIME_ZONE = "America/Denver"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Prod hardening on Cloud Run (behind IAP + the LB's TLS).
if os.environ.get("K_SERVICE"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
