"""Doc 70 §12 — visibilidad de versiones en el visor del canvas: publicadas y
propias para todos; ajenas no publicadas sólo con `versions.view_all`
(o `admin.manage`); el revisor asignado ve el request que le toca."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.identity import Principal
from app.features.changesets import access

PUB = {"id": "v3", "status": "approved", "appliedAt": "2026-09-01", "owner": "ana"}
DRAFT = {"id": "d1", "status": "draft", "owner": "ana", "reviewers": []}
REQ = {"id": "r1", "status": "submitted", "owner": "ana", "reviewers": ["qa"]}


def _p(username: str) -> Principal:
    return Principal(email=f"{username}@x", username=username, display_name=username, source="local")


def test_can_view_reglas_puras():
    assert access.can_view(PUB, "beto", {})                       # publicada → todos
    assert access.can_view(DRAFT, "ana", {})                      # propia
    assert not access.can_view(DRAFT, "beto", {})                 # ajena sin permiso
    assert access.can_view(DRAFT, "beto", {"versions.view_all": True})
    assert access.can_view(DRAFT, "beto", {"admin.manage": True})  # admin implícito (matrices pre-doc 70)
    assert access.can_view(REQ, "qa", {})                         # revisor asignado
    assert not access.can_view(REQ, "beto", {})
    assert access.can_view(None, "beto", {})                      # inexistente → 404 del endpoint


def test_ensure_pasa_sin_changeset_y_con_asof(monkeypatch):
    get = AsyncMock()
    monkeypatch.setattr(access.repository, "get", get)
    asyncio.run(access.ensure_changeset_visible(None, _p("beto")))
    asyncio.run(access.ensure_changeset_visible("asof:v1", _p("beto")))
    get.assert_not_awaited()


def test_ensure_publicada_no_consulta_permisos(monkeypatch):
    monkeypatch.setattr(access.repository, "get", AsyncMock(return_value=PUB))
    resolve = AsyncMock()
    monkeypatch.setattr(access.auth_service, "resolve_session_user", resolve)
    asyncio.run(access.ensure_changeset_visible("v3", _p("beto")))
    resolve.assert_not_awaited()


def test_ensure_draft_ajeno_403_salvo_view_all(monkeypatch):
    monkeypatch.setattr(access.repository, "get", AsyncMock(return_value=DRAFT))
    monkeypatch.setattr(access.auth_service, "resolve_session_user",
                        AsyncMock(return_value={"username": "beto", "permissions": {"model.view": True}}))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(access.ensure_changeset_visible("d1", _p("beto")))
    assert exc.value.status_code == 403
    monkeypatch.setattr(access.auth_service, "resolve_session_user",
                        AsyncMock(return_value={"username": "root", "permissions": {"versions.view_all": True}}))
    asyncio.run(access.ensure_changeset_visible("d1", _p("root")))   # admin → pasa


def test_ensure_owner_pasa_aunque_no_este_registrado(monkeypatch):
    monkeypatch.setattr(access.repository, "get", AsyncMock(return_value=DRAFT))
    monkeypatch.setattr(access.auth_service, "resolve_session_user", AsyncMock(return_value=None))
    asyncio.run(access.ensure_changeset_visible("d1", _p("ana")))


def test_permiso_en_catalogo_y_label_seed():
    from app.features.auth.models import PERMISSIONS
    from scripts.create_admin import build_roles
    assert "versions.view_all" in PERMISSIONS
    roles = {r["_id"]: r["permissions"] for r in build_roles()}
    assert roles["administrador"]["versions.view_all"] is True
    assert roles["modelador"]["versions.view_all"] is False
    assert roles["modelador"]["rollback"] is True          # doc 70 §12: el modelador puede pedir rollback
    assert roles["revisor"]["versions.view_all"] is False
