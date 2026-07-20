"""Auth propia: permisos efectivos (puro), login/resolución (repo mockeado) y
el token-first de `current_principal`."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from starlette.requests import Request

from app.core.config import settings
from app.core.identity.dependencies import current_principal
from app.features.auth import service
from app.features.auth.models import PERMISSIONS


def _req(headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw})


# ── effective_permissions / access_level (puro) ───────────────────────────

def test_effective_permissions_fills_all_keys_and_filters_unknown():
    role = {"permissions": {"model.view": True, "bogus.perm": True}}
    perms = service.effective_permissions(role)
    assert set(perms) == set(PERMISSIONS)          # todas las keys presentes
    assert perms["model.view"] is True
    assert perms["model.edit"] is False            # no declarada → False
    assert "bogus.perm" not in perms               # key desconocida descartada


def test_effective_permissions_none_role_all_false():
    perms = service.effective_permissions(None)
    assert all(v is False for v in perms.values())


def test_access_level_derivation():
    assert service.access_level({"admin.manage": True}) == "full"
    assert service.access_level({"standards.edit": True}) == "edit"
    assert service.access_level({"model.edit": True}) == "edit"
    assert service.access_level({"export": True, "model.view": True}) == "read"


# ── login (repository + security + audit mockeados) ───────────────────────
# login() lee `get_login_record` (doc crudo: hash + status + lockout) y resuelve
# el usuario enriquecido vía `get_user` (para /me). El lockout usa
# register/clear_failed_login.

def test_login_ok_devuelve_token_y_usuario(monkeypatch):
    from app.core.security import hash_password
    h = hash_password("123456789")
    monkeypatch.setattr(service.repository, "get_login_record",
                        AsyncMock(return_value={"passwordHash": h, "status": "active"}))
    monkeypatch.setattr(service.repository, "clear_failed_login", AsyncMock())
    monkeypatch.setattr(service.repository, "get_user",
                        AsyncMock(return_value={"id": "maria.rojas", "email": "m@e.com", "name": "Maria Rojas",
                                                "role": "administrador", "status": "active"}))
    monkeypatch.setattr(service.repository, "get_role",
                        AsyncMock(return_value={"id": "administrador", "name": "Administrador",
                                                "permissions": {p: True for p in PERMISSIONS}}))
    monkeypatch.setattr(service, "audit", AsyncMock())

    res = asyncio.run(service.login("maria.rojas", "123456789"))
    assert res is not None and res["token"]
    assert res["user"]["role"] == "administrador"
    assert res["user"]["permissions"]["admin.manage"] is True
    assert "passwordHash" not in res["user"]


def test_login_http_con_rate_limiter_activo(monkeypatch):
    """Regresión Apps 2026-07-20: con el limiter HABILITADO (postura de
    producción; en dev está OFF y por eso nunca se vio) slowapi intenta
    inyectar los headers X-RateLimit-* en la respuesta del endpoint, y si este
    devuelve un dict explota con "parameter `response` must be an instance of
    starlette.responses.Response" → 500 en el PRIMER login real. El endpoint
    declara `response: Response` para darle a slowapi dónde inyectarlos."""
    from unittest.mock import AsyncMock as _AsyncMock

    from starlette.testclient import TestClient

    from app.core.ratelimit import limiter
    from app.main import app

    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(
        service, "login", _AsyncMock(return_value={"token": "tkn", "user": {"id": "maria"}})
    )
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/auth/login", json={"username": "maria", "password": "x"})
    assert r.status_code == 200, f"limiter activo rompió el login: {r.status_code} {r.text}"
    body = r.json()
    assert body["success"] is True and body["data"]["token"] == "tkn"


def test_login_password_incorrecta_es_none_y_audita_fallo(monkeypatch):
    from app.core.security import hash_password
    monkeypatch.setattr(service.repository, "get_login_record",
                        AsyncMock(return_value={"status": "active", "passwordHash": hash_password("correcta")}))
    reg = AsyncMock(); monkeypatch.setattr(service.repository, "register_failed_login", reg)
    aud = AsyncMock(); monkeypatch.setattr(service, "audit", aud)
    assert asyncio.run(service.login("ana", "incorrecta")) is None
    assert aud.await_args.args[1] == "login_failed"
    reg.assert_awaited()   # cuenta el intento fallido (lockout)


def test_login_usuario_inexistente_es_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get_login_record", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "audit", AsyncMock())
    assert asyncio.run(service.login("fantasma", "x")) is None


def test_login_usuario_deshabilitado_no_entra(monkeypatch):
    from app.core.security import hash_password
    monkeypatch.setattr(service.repository, "get_login_record",
                        AsyncMock(return_value={"status": "disabled", "passwordHash": hash_password("x")}))
    monkeypatch.setattr(service, "audit", AsyncMock())
    assert asyncio.run(service.login("ana", "x")) is None


def test_login_cuenta_bloqueada_no_entra_ni_con_pass_correcta(monkeypatch):
    """Con lockedUntil en el futuro, la cuenta no entra AUN con la contraseña
    correcta (rechaza sin verificar) y audita 'login_locked'."""
    from datetime import datetime, timedelta, timezone
    from app.core.security import hash_password
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(service.repository, "get_login_record",
                        AsyncMock(return_value={"status": "active", "passwordHash": hash_password("correcta"),
                                                "lockedUntil": future}))
    aud = AsyncMock(); monkeypatch.setattr(service, "audit", aud)
    assert asyncio.run(service.login("ana", "correcta")) is None
    assert aud.await_args.args[1] == "login_locked"


def test_resolve_session_user_deshabilitado_es_none(monkeypatch):
    """Deshabilitar un usuario revoca su SESIÓN activa (no solo el login): las
    dependencias de permiso resuelven None → 403, aunque el token siga vigente."""
    monkeypatch.setattr(service.repository, "get_user",
                        AsyncMock(return_value={"id": "ana", "status": "disabled", "role": "administrador"}))
    monkeypatch.setattr(service.repository, "get_role",
                        AsyncMock(return_value={"id": "administrador", "permissions": {p: True for p in PERMISSIONS}}))
    assert asyncio.run(service.resolve_session_user("ana")) is None


# ── Falla-cerrado de la config de sesión ──────────────────────────────────

def test_assert_secure_config_falla_con_default_en_prod(monkeypatch):
    import pytest

    from app.core import config
    monkeypatch.setattr(config.settings, "REQUIRE_AUTH", True)
    monkeypatch.setattr(config.settings, "SECRET_KEY", config.INSECURE_DEFAULT_SECRET_KEY)
    with pytest.raises(RuntimeError):
        config.assert_secure_config()
    # con un SECRET_KEY propio no falla
    monkeypatch.setattr(config.settings, "SECRET_KEY", "una-clave-fuerte-aleatoria")
    config.assert_secure_config()

    # en dev (REQUIRE_AUTH=false) el default solo advierte, no impide arrancar
    monkeypatch.setattr(config.settings, "REQUIRE_AUTH", False)
    monkeypatch.setattr(config.settings, "SECRET_KEY", config.INSECURE_DEFAULT_SECRET_KEY)
    config.assert_secure_config()


# ── current_principal token-first ─────────────────────────────────────────

def test_current_principal_usa_el_token(monkeypatch):
    from app.core.security import create_access_token
    tok = create_access_token("beto", extra={"email": "beto@e.com", "name": "Beto Diaz"})
    p = current_principal(_req({"Authorization": f"Bearer {tok}"}))
    assert p.username == "beto" and p.email == "beto@e.com" and p.source == "session"


def test_current_principal_token_invalido_es_401(monkeypatch):
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        current_principal(_req({"Authorization": "Bearer no-es-un-token"}))
    assert exc.value.status_code == 401


def test_current_principal_sin_token_cae_al_seam_en_local(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "REQUIRE_AUTH", False)
    monkeypatch.setattr(settings, "LOCAL_DEV_USER", "carlos@local")
    p = current_principal(_req())
    assert p.source == "local" and p.email == "carlos@local"


def test_current_principal_sin_token_con_require_auth_es_401(monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    with pytest.raises(HTTPException) as exc:
        current_principal(_req())
    assert exc.value.status_code == 401
