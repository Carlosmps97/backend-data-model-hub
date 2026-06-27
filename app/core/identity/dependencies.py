"""Selección del provider de identidad y dependencia FastAPI.

`get_identity_provider` lee `settings.AUTH_MODE` en cada llamada (los providers
son baratos y sin estado, no se cachean — así los tests pueden conmutar el modo
con monkeypatch sin invalidar caches).
"""
from __future__ import annotations

from starlette.requests import Request

from app.core.config import settings

from .models import Principal
from .provider import (
    DatabricksIdentityProvider,
    IdentityProvider,
    LocalIdentityProvider,
)


def get_identity_provider() -> IdentityProvider:
    if settings.AUTH_MODE == "databricks":
        return DatabricksIdentityProvider()
    return LocalIdentityProvider(
        email=settings.LOCAL_DEV_USER,
        username=settings.LOCAL_DEV_USERNAME or None,
        display_name=settings.LOCAL_DEV_DISPLAY_NAME or None,
    )


def current_principal(request: Request) -> Principal:
    """Dependencia FastAPI: el usuario en sesión, según el modo activo."""
    return get_identity_provider().principal_from_request(request)
