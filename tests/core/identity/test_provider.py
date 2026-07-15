"""Providers de identidad: local (fake) y databricks (headers SSO)."""
from __future__ import annotations

from starlette.requests import Request

from app.core.identity.provider import (
    DatabricksIdentityProvider,
    LocalIdentityProvider,
)


def _request_with_headers(headers: dict[str, str]) -> Request:
    # Headers are decoded by Starlette as latin-1 (HTTP spec); encode values
    # as latin-1 so non-ASCII characters round-trip correctly.
    raw = [(k.lower().encode(), v.encode("latin-1")) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw})


def test_local_provider_returns_configured_fake():
    p = LocalIdentityProvider(email="dev@local").principal_from_request(
        _request_with_headers({})
    )
    assert p.email == "dev@local"
    assert p.username == "dev"          # derivado del email
    assert p.display_name == "dev"
    assert p.source == "local"


def test_local_provider_respects_explicit_username_and_name():
    p = LocalIdentityProvider(
        email="dev@local", username="carlos", display_name="Carlos P."
    ).principal_from_request(_request_with_headers({}))
    assert p.username == "carlos"
    assert p.display_name == "Carlos P."


def test_databricks_provider_parses_forwarded_headers():
    req = _request_with_headers(
        {
            "X-Forwarded-Email": "ana@corp.com",
            "X-Forwarded-Preferred-Username": "ana",
            "X-Forwarded-User": "Ana Gómez",
        }
    )
    p = DatabricksIdentityProvider().principal_from_request(req)
    assert p.email == "ana@corp.com"
    assert p.username == "ana"
    assert p.display_name == "Ana Gómez"
    assert p.source == "databricks"


def test_databricks_provider_falls_back_to_email_prefix():
    req = _request_with_headers({"X-Forwarded-Email": "ana@corp.com"})
    p = DatabricksIdentityProvider().principal_from_request(req)
    assert p.username == "ana"          # sin preferred-username → prefijo del email
    assert p.display_name == "ana"      # sin X-Forwarded-User → username
