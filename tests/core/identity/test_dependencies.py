"""La factory elige el provider según AUTH_MODE; current_principal resuelve."""
from __future__ import annotations

from starlette.requests import Request

from app.core.config import settings
from app.core.identity.dependencies import current_principal, get_identity_provider
from app.core.identity.provider import (
    DatabricksIdentityProvider,
    LocalIdentityProvider,
)


def _bare_request() -> Request:
    return Request({"type": "http", "headers": []})


def test_factory_default_is_local():
    assert isinstance(get_identity_provider(), LocalIdentityProvider)


def test_factory_databricks_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "databricks")
    assert isinstance(get_identity_provider(), DatabricksIdentityProvider)


def test_current_principal_local(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "LOCAL_DEV_USER", "carlos@local")
    p = current_principal(_bare_request())
    assert p.email == "carlos@local"
    assert p.source == "local"
