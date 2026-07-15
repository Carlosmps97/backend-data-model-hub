"""Validación F3a de create/update de vistas.

- POST sin ninguna fuente (ni sourceTableIds ni tableId legacy) → 409 (patrón
  del repo: HTTPException 409 con detalle en español para reglas de negocio).
- 2+ fuentes SIN joinOverride → se permite guardar (D2: el JOIN se infiere en
  el DDL del front; el backend NO valida joins).
- PUT normaliza tableId=sourceTableIds[0] ANTES del $set: repository.update
  escribe el dict tal cual (solo la respuesta se re-valida vía ViewDoc), sin
  normalizar el doc Mongo quedaría inconsistente.
Handlers/service llamados directo con service/repository mockeados
(AsyncMock + monkeypatch, patrón test_effective_search.py) — el write_guard es
dependency de router y no aplica al llamar la función.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.views import router as views_router
from app.features.views import service as views_service
from app.features.views.repository import build_canvas_query
from app.features.views.schemas import ViewBody


# ── POST /api/views ──────────────────────────────────────────────────────────

def test_create_sin_fuentes_409(monkeypatch):
    guard = AsyncMock()
    monkeypatch.setattr(views_router.service, "create", guard)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(views_router.create(ViewBody.model_validate({"name": "v"})))
    assert exc.value.status_code == 409
    assert "fuente" in exc.value.detail
    guard.assert_not_awaited()


def test_create_con_tableid_legacy_pasa(monkeypatch):
    monkeypatch.setattr(views_router.service, "create",
                        AsyncMock(return_value={"id": "v1"}))
    res = asyncio.run(views_router.create(
        ViewBody.model_validate({"name": "v", "tableId": "t1"})))
    assert res == {"success": True, "data": {"id": "v1"}}


def test_create_multifuente_sin_joinoverride_pasa(monkeypatch):
    # D2: con 2+ fuentes y sin joinOverride se guarda igual.
    monkeypatch.setattr(views_router.service, "create",
                        AsyncMock(return_value={"id": "v1"}))
    body = ViewBody.model_validate({"name": "v", "sourceTableIds": ["t1", "t2"]})
    res = asyncio.run(views_router.create(body))
    assert res["success"] is True


# ── PUT /api/views/{vid} (service.update normaliza) ─────────────────────────

def test_update_normaliza_antes_del_set(monkeypatch):
    mock_update = AsyncMock(return_value={"id": "v1"})
    monkeypatch.setattr(views_service.repository, "update", mock_update)
    body = ViewBody.model_validate(
        {"name": "v", "sourceTableIds": ["a", "b"], "tableId": "viejo"})
    asyncio.run(views_service.update("v1", body))
    sent = mock_update.await_args.args[1]
    assert sent["tableId"] == "a"
    assert sent["sourceTableIds"] == ["a", "b"]


def test_update_legacy_solo_tableid_materializa_sources(monkeypatch):
    mock_update = AsyncMock(return_value={"id": "v1"})
    monkeypatch.setattr(views_service.repository, "update", mock_update)
    asyncio.run(views_service.update(
        "v1", ViewBody.model_validate({"name": "v", "tableId": "t1"})))
    sent = mock_update.await_args.args[1]
    assert sent["sourceTableIds"] == ["t1"]
    assert sent["tableId"] == "t1"


# ── Regresión del hallazgo de la review de Task 5 ────────────────────────────
# Escenario reproducido por el reviewer: PUT de cuerpo completo con
# {name, tableId:"t1", showOnCanvas:true} y `sourceTableIds` OMITIDO. Antes del
# fix, service.update persistía el dump crudo → sourceTableIds=[] y
# build_canvas_query (que matchea SOLO sourceTableIds) nunca lo encontraba: la
# vista quedaba invisible en TODO canvas pese a showOnCanvas=true.

def test_update_tableid_only_showoncanvas_queda_visible_en_canvas(monkeypatch):
    mock_update = AsyncMock(return_value={"id": "v1"})
    monkeypatch.setattr(views_service.repository, "update", mock_update)
    # PUT de cuerpo completo: tableId puesto, sourceTableIds OMITIDO.
    body = ViewBody.model_validate(
        {"name": "v", "tableId": "t1", "showOnCanvas": True})
    asyncio.run(views_service.update("v1", body))

    persisted = mock_update.await_args.args[1]
    # (1) el doc que se escribe a Mongo materializa la fuente.
    assert persisted["sourceTableIds"] == ["t1"]
    assert persisted["showOnCanvas"] is True

    # (2) con ese doc persistido, la query del canvas SÍ lo matchea:
    # sourceTableIds ∩ tablas del canvas ≠ ∅ y showOnCanvas=True.
    q = build_canvas_query(["t1", "t2"])
    src = persisted["sourceTableIds"]
    assert persisted["showOnCanvas"] == q["showOnCanvas"]
    assert any(tid in q["sourceTableIds"]["$in"] for tid in src)
