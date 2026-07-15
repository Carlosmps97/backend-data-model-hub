"""Admin: helpers puros + create/update user + role sanitize (repos mockeados)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.admin import service
from app.features.admin.schemas import RoleBody, UserCreate, UserUpdate
from app.features.auth.models import PERMISSIONS


def test_initials():
    assert service.initials("María Rojas") == "MR"
    assert service.initials("Ana") == "AN"
    assert service.initials("") == "?"


def test_sanitize_permissions_solo_conocidos():
    out = service.sanitize_permissions({"model.view": True, "hack": True, "publish": "yes"})
    assert set(out) == set(PERMISSIONS)
    assert out["model.view"] is True and out["publish"] is True and "hack" not in out
    assert out["admin.manage"] is False


def test_create_user_hashea_password_y_audita(monkeypatch):
    captured = {}
    async def _upsert(username, fields):
        captured["username"] = username; captured["fields"] = fields
        return {"id": username, **{k: v for k, v in fields.items() if k != "passwordHash"}}
    monkeypatch.setattr(service.repository, "upsert_user", AsyncMock(side_effect=_upsert))
    aud = AsyncMock(); monkeypatch.setattr(service, "audit", aud)

    body = UserCreate(username="juan.castillo", email="j@e.com", name="Juan Castillo",
                      role="modelador", password="clave-segura-123")
    user = asyncio.run(service.create_user("maria.rojas", body))
    assert user["initials"] == "JC" and "passwordHash" not in user
    # el hash se guarda, nunca el password en claro
    from app.core.security import verify_password
    assert verify_password("clave-segura-123", captured["fields"]["passwordHash"])
    assert aud.await_args.args[1] == "admin.user.create"


def test_update_user_inexistente_es_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get_user", AsyncMock(return_value=None))
    body = UserUpdate(name="Nuevo")
    assert asyncio.run(service.update_user("mr", "fantasma", body)) is None


def test_update_user_solo_aplica_campos_enviados(monkeypatch):
    monkeypatch.setattr(service.repository, "get_user", AsyncMock(return_value={"id": "ana"}))
    monkeypatch.setattr(service, "_survives_admin", AsyncMock(return_value=True))  # guard fuera de foco
    captured = {}
    async def _upsert(username, fields):
        captured.update(fields); return {"id": username, **fields}
    monkeypatch.setattr(service.repository, "upsert_user", AsyncMock(side_effect=_upsert))
    monkeypatch.setattr(service, "audit", AsyncMock())
    asyncio.run(service.update_user("mr", "ana", UserUpdate(role="revisor")))
    assert captured == {"role": "revisor"}   # no toca email/name/password


def test_upsert_role_sanea_permisos(monkeypatch):
    monkeypatch.setattr(service, "_survives_admin", AsyncMock(return_value=True))  # guard fuera de foco
    captured = {}
    async def _upsert(key, fields):
        captured.update(fields); return {"id": key, **fields}
    monkeypatch.setattr(service.repository, "upsert_role", AsyncMock(side_effect=_upsert))
    monkeypatch.setattr(service, "audit", AsyncMock())
    body = RoleBody(name="Lector", permissions={"model.view": True, "export": True, "junk": True})
    asyncio.run(service.upsert_role("mr", "lector", body))
    assert set(captured["permissions"]) == set(PERMISSIONS)
    assert "junk" not in captured["permissions"]


# ── Guards anti-lockout (invariante: ≥1 admin activo) ─────────────────────

def _roles(admin_perm=True):
    return [{"id": "administrador", "name": "Admin", "permissions": {"admin.manage": admin_perm}},
            {"id": "lector", "name": "Lector", "permissions": {"model.view": True}}]


def test_no_se_puede_eliminar_al_ultimo_admin(monkeypatch):
    monkeypatch.setattr(service.repository, "list_users",
                        AsyncMock(return_value=[{"id": "admin", "role": "administrador", "status": "active"},
                                                {"id": "ana", "role": "lector", "status": "active"}]))
    monkeypatch.setattr(service.repository, "list_roles", AsyncMock(return_value=_roles()))
    monkeypatch.setattr(service, "audit", AsyncMock())
    import pytest
    with pytest.raises(service.AdminGuardError):
        asyncio.run(service.delete_user("admin", "admin"))
    # con OTRO admin, sí se puede
    monkeypatch.setattr(service.repository, "list_users",
                        AsyncMock(return_value=[{"id": "admin", "role": "administrador", "status": "active"},
                                                {"id": "mr", "role": "administrador", "status": "active"}]))
    monkeypatch.setattr(service.repository, "delete_user", AsyncMock(return_value=True))
    assert asyncio.run(service.delete_user("mr", "admin")) is True


def test_no_se_puede_quitar_admin_manage_del_ultimo_rol_admin(monkeypatch):
    monkeypatch.setattr(service.repository, "list_users",
                        AsyncMock(return_value=[{"id": "admin", "role": "administrador", "status": "active"}]))
    monkeypatch.setattr(service.repository, "list_roles", AsyncMock(return_value=_roles()))
    monkeypatch.setattr(service, "audit", AsyncMock())
    import pytest
    body = RoleBody(name="Admin", permissions={"model.view": True})  # sin admin.manage
    with pytest.raises(service.AdminGuardError):
        asyncio.run(service.upsert_role("admin", "administrador", body))


def test_no_se_puede_borrar_rol_con_usuarios(monkeypatch):
    monkeypatch.setattr(service.repository, "list_users",
                        AsyncMock(return_value=[{"id": "ana", "role": "modelador", "status": "active"}]))
    monkeypatch.setattr(service, "audit", AsyncMock())
    import pytest
    with pytest.raises(service.AdminGuardError):
        asyncio.run(service.delete_role("admin", "modelador"))
