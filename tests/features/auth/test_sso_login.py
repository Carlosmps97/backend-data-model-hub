"""Login SSO heredado de Databricks Apps (doc 38): resolución de la identidad
relevada (secreto compartido `x-dmh-proxy-secret`), whitelist por correo y
resolución best-effort del nombre. Estilo de la suite: services con repos
mockeados; el endpoint HTTP se prueba con el limiter ACTIVO (regresión slowapi
del doc 36)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import settings
from app.features.auth import service, sso
from app.features.auth.models import PERMISSIONS


def _req(headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw})


# ── resolve_sso_identity: el gate del relay ────────────────────────────────


def test_identidad_con_secreto_ok_normaliza_email(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "s3cr3t")
    ident = sso.resolve_sso_identity(_req({
        "x-dmh-proxy-secret": "s3cr3t",
        "x-dmh-sso-email": "  Carlos.Perez@Corp.COM ",
        "x-dmh-sso-username": "carlos.perez@corp.com",
        "x-dmh-sso-user-id": "12345",
    }))
    assert ident.email == "carlos.perez@corp.com"   # trim + lowercase
    assert ident.user_id == "12345"


def test_identidad_secreto_errado_o_ausente_es_401(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "s3cr3t")
    for headers in ({"x-dmh-proxy-secret": "otro", "x-dmh-sso-email": "a@b.co"},
                    {"x-dmh-sso-email": "a@b.co"}):
        with pytest.raises(HTTPException) as exc:
            sso.resolve_sso_identity(_req(headers))
        assert exc.value.status_code == 401


def test_identidad_con_secreto_sin_email_es_401(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "s3cr3t")
    with pytest.raises(HTTPException) as exc:
        sso.resolve_sso_identity(_req({"x-dmh-proxy-secret": "s3cr3t"}))
    assert exc.value.status_code == 401


def test_identidad_sin_secreto_en_produccion_es_503_fail_closed(monkeypatch):
    """Producción (`REQUIRE_AUTH=true`) sin `PROXY_SHARED_SECRET`: JAMÁS se
    aceptan headers a ciegas — 503 accionable (el login admin sigue vivo)."""
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    with pytest.raises(HTTPException) as exc:
        sso.resolve_sso_identity(_req({"x-dmh-sso-email": "a@b.co"}))
    assert exc.value.status_code == 503


def test_identidad_dev_acepta_relay_forwarded_o_simulacion(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "REQUIRE_AUTH", False)
    monkeypatch.setattr(settings, "LOCAL_DEV_USER", "dev@local")
    # 1) relay directo (curl / e2e)
    assert sso.resolve_sso_identity(
        _req({"x-dmh-sso-email": "A@B.co"})).email == "a@b.co"
    # 2) headers del proxy de Databricks (hipotético single-app)
    assert sso.resolve_sso_identity(
        _req({"X-Forwarded-Email": "c@d.co"})).email == "c@d.co"
    # 3) sin nada → simulación local (botón Databricks out-of-the-box en dev)
    assert sso.resolve_sso_identity(_req()).email == "dev@local"


# ── helpers puros ──────────────────────────────────────────────────────────


def test_is_email():
    assert sso.is_email("carlos.perez@corp.com")
    assert not sso.is_email("admin")
    assert not sso.is_email("a@b")          # sin TLD
    assert not sso.is_email("a b@c.com")    # espacios
    assert not sso.is_email("")


def test_derive_name_from_email():
    assert sso.derive_name_from_email("carlos.perez@corp.com") == "Carlos Perez"
    assert sso.derive_name_from_email("ana_maria-lopez@x.pe") == "Ana Maria Lopez"
    assert sso.derive_name_from_email("admin@x.pe") == "Admin"


# ── login_sso (repository + audit mockeados) ───────────────────────────────


def _ident(email="carlos.perez@corp.com", hint=None, user_id=None) -> sso.SsoIdentity:
    return sso.SsoIdentity(email=email, username_hint=hint, user_id=user_id)


def test_login_sso_correo_no_whitelisteado_es_none_y_audita(monkeypatch):
    monkeypatch.setattr(service.repository, "get_user", AsyncMock(return_value=None))
    aud = AsyncMock(); monkeypatch.setattr(service, "audit", aud)
    assert asyncio.run(service.login_sso(_ident())) is None
    assert aud.await_args.args[1] == "login_sso_denied"
    assert aud.await_args.kwargs["meta"]["reason"] == "not_whitelisted"


def test_login_sso_deshabilitado_es_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get_user",
                        AsyncMock(return_value={"id": "c@x.co", "status": "disabled",
                                                "role": "modelador"}))
    aud = AsyncMock(); monkeypatch.setattr(service, "audit", aud)
    assert asyncio.run(service.login_sso(_ident("c@x.co"))) is None
    assert aud.await_args.kwargs["meta"]["reason"] == "disabled"


def test_login_sso_ok_emite_token_con_shape_de_login(monkeypatch):
    user = {"id": "carlos.perez@corp.com", "email": "carlos.perez@corp.com",
            "name": "Carlos Perez", "role": "modelador", "status": "active"}
    monkeypatch.setattr(service.repository, "get_user", AsyncMock(return_value=user))
    monkeypatch.setattr(service.repository, "get_role",
                        AsyncMock(return_value={"id": "modelador", "name": "Modelador",
                                                "permissions": {"model.view": True, "model.edit": True}}))
    upsert = AsyncMock(); monkeypatch.setattr(service.repository, "upsert_user", upsert)
    aud = AsyncMock(); monkeypatch.setattr(service, "audit", aud)

    res = asyncio.run(service.login_sso(_ident(user_id="999")))
    assert res is not None and res["token"]
    assert res["user"]["username"] == "carlos.perez@corp.com"
    assert res["user"]["permissions"]["model.edit"] is True
    assert set(res["user"]["permissions"]) == set(PERMISSIONS)
    assert "passwordHash" not in res["user"]
    upsert.assert_not_awaited()            # nombre y email ya estaban → no toca
    assert aud.await_args.args[1] == "login_sso"
    assert aud.await_args.kwargs["meta"] == {"userId": "999"}


def test_login_sso_persiste_nombre_y_email_faltantes(monkeypatch):
    """Primer login de una entrada de whitelist (solo correo+rol): resuelve el
    nombre (SCIM/derivado), calcula iniciales y completa el email del doc."""
    user = {"id": "ana.lopez@corp.com", "email": "", "name": "",
            "role": "lector", "status": "active"}
    monkeypatch.setattr(service.repository, "get_user", AsyncMock(return_value=user))
    monkeypatch.setattr(service.repository, "get_role",
                        AsyncMock(return_value={"id": "lector", "name": "Lector",
                                                "permissions": {"model.view": True}}))
    upsert = AsyncMock(); monkeypatch.setattr(service.repository, "upsert_user", upsert)
    monkeypatch.setattr(service.sso, "resolve_display_name",
                        AsyncMock(return_value="Ana Lopez"))
    monkeypatch.setattr(service, "audit", AsyncMock())

    res = asyncio.run(service.login_sso(_ident("ana.lopez@corp.com")))
    assert res is not None
    upsert.assert_awaited_once()
    saved = upsert.await_args.args[1]
    assert saved == {"email": "ana.lopez@corp.com", "name": "Ana Lopez", "initials": "AL"}


def test_login_sso_no_pisa_nombre_existente(monkeypatch):
    user = {"id": "b@x.co", "email": "b@x.co", "name": "Nombre Manual",
            "role": "lector", "status": "active"}
    monkeypatch.setattr(service.repository, "get_user", AsyncMock(return_value=user))
    monkeypatch.setattr(service.repository, "get_role",
                        AsyncMock(return_value={"id": "lector", "permissions": {}}))
    upsert = AsyncMock(); monkeypatch.setattr(service.repository, "upsert_user", upsert)
    resolver = AsyncMock(); monkeypatch.setattr(service.sso, "resolve_display_name", resolver)
    monkeypatch.setattr(service, "audit", AsyncMock())
    res = asyncio.run(service.login_sso(_ident("b@x.co")))
    assert res is not None
    upsert.assert_not_awaited()
    resolver.assert_not_awaited()          # ni siquiera intenta SCIM


# ── resolve_display_name: SCIM best-effort con fallbacks ──────────────────


def test_nombre_scim_ok(monkeypatch):
    monkeypatch.setattr(sso, "_scim_display_name_sync", lambda email: "Carlos M. Perez")
    assert asyncio.run(sso.resolve_display_name("carlos@x.co", None)) == "Carlos M. Perez"


def test_nombre_scim_falla_usa_hint_no_correo(monkeypatch):
    def boom(email):
        raise RuntimeError("sin permisos SCIM")
    monkeypatch.setattr(sso, "_scim_display_name_sync", boom)
    assert asyncio.run(sso.resolve_display_name("c@x.co", "cperez")) == "cperez"


def test_nombre_hint_correo_se_descarta_y_deriva(monkeypatch):
    """En Entra ID `preferred_username` suele ser el mismo UPN/correo: no sirve
    como display name → se deriva del local-part."""
    monkeypatch.setattr(sso, "_scim_display_name_sync", lambda email: None)
    name = asyncio.run(sso.resolve_display_name("carlos.perez@x.co", "carlos.perez@x.co"))
    assert name == "Carlos Perez"


# ── Endpoint HTTP (limiter activo, mismo patrón que /login) ───────────────


def test_sso_login_http_con_rate_limiter_activo(monkeypatch):
    from starlette.testclient import TestClient

    from app.core.ratelimit import limiter
    from app.main import app

    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "REQUIRE_AUTH", False)   # dev → simulación
    monkeypatch.setattr(
        service, "login_sso",
        AsyncMock(return_value={"token": "tkn", "user": {"username": "dev@local"}}),
    )
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/auth/sso/login")
    assert r.status_code == 200, f"limiter activo rompió el SSO: {r.status_code} {r.text}"
    body = r.json()
    assert body["success"] is True and body["data"]["token"] == "tkn"


def test_sso_login_http_sin_whitelist_es_403(monkeypatch):
    from starlette.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "REQUIRE_AUTH", False)
    monkeypatch.setattr(service, "login_sso", AsyncMock(return_value=None))
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/auth/sso/login")
    assert r.status_code == 403
    assert "assign your email" in r.json()["detail"]
