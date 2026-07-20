"""En local, X-Dev-User sobreescribe el usuario fake (para probar aprobación)."""
from __future__ import annotations

from starlette.requests import Request

from app.core.identity.provider import LocalIdentityProvider


def _req(headers: dict) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw})


def test_local_honra_x_dev_user():
    p = LocalIdentityProvider(email="dev@local").principal_from_request(_req({"X-Dev-User": "ana"}))
    assert p.username == "ana"
    assert p.email == "ana@local"


def test_local_sin_header_usa_el_fake():
    p = LocalIdentityProvider(email="dev@local").principal_from_request(_req({}))
    assert p.username == "dev"
