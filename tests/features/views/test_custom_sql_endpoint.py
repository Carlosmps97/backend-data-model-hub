"""Doc 61: endpoint de parse del sandbox + derivación server-side en el camino
directo de /api/views (create/update). Patrón test_view_validation.py:
handlers/service directos con AsyncMock + monkeypatch."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.views import router as views_router
from app.features.views import service as views_service
from app.features.views.schemas import SqlParseBody, ViewBody


# ── POST /api/views/sql/parse ───────────────────────────────────────────────

def test_parse_endpoint_ok():
    res = asyncio.run(views_router.parse_sql(
        SqlParseBody.model_validate({"sql": "SELECT id, nombre AS n FROM core.t1"})))
    assert res["success"] is True
    assert res["data"]["columns"] == [{"name": "id"}, {"name": "n"}]
    assert res["data"]["tables"] == ["core.t1"]


def test_parse_endpoint_invalid_422():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(views_router.parse_sql(
            SqlParseBody.model_validate({"sql": "SELECT * FROM t"})))
    assert exc.value.status_code == 422
    assert "SELECT *" in exc.value.detail["message"]
    assert exc.value.detail["line"] >= 1


# ── create/update derivan customColumns (no se confía en el cliente) ────────

def test_create_deriva_custom_columns(monkeypatch):
    mock_create = AsyncMock(return_value={"id": "v1"})
    monkeypatch.setattr(views_service.repository, "create", mock_create)
    monkeypatch.setattr(views_service.catalog_repo, "project_of_table", AsyncMock(return_value="p1"))
    body = ViewBody.model_validate({
        "name": "v", "tableId": "t1",
        "customSql": "SELECT id, UPPER(n) AS n_up FROM t1",
        "customColumns": [{"name": "MENTIRA"}],  # el back la RE-deriva
    })
    asyncio.run(views_service.create(body))
    sent = mock_create.await_args.args[0]
    assert sent["customColumns"] == [
        {"name": "id"}, {"name": "n_up", "expression": "UPPER(n)"}]


def test_update_limpia_custom_columns_al_volver_a_regular(monkeypatch):
    mock_update = AsyncMock(return_value={"id": "v1"})
    monkeypatch.setattr(views_service.repository, "update", mock_update)
    monkeypatch.setattr(views_service.repository, "get", AsyncMock(return_value={"id": "v1", "projectId": "p1"}))
    body = ViewBody.model_validate({
        "name": "v", "tableId": "t1", "customSql": "   ",
        "customColumns": [{"name": "zombi"}],
    })
    asyncio.run(views_service.update("v1", body))
    sent = mock_update.await_args.args[1]
    assert sent["customSql"] is None
    assert sent["customColumns"] == []


def test_update_custom_sql_invalido_422(monkeypatch):
    # La validación vive en el SERVICE (punto único, _with_custom_sql); el
    # router traduce CustomSqlError → 422. El repo NUNCA se toca.
    guard = AsyncMock()
    monkeypatch.setattr(views_service.repository, "update", guard)
    body = ViewBody.model_validate(
        {"name": "v", "tableId": "t1", "customSql": "DELETE FROM t"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(views_router.update("v1", body))
    assert exc.value.status_code == 422
    assert "SELECT" in exc.value.detail["message"]
    guard.assert_not_awaited()


def test_create_custom_sql_invalido_422(monkeypatch):
    guard = AsyncMock()
    monkeypatch.setattr(views_service.repository, "create", guard)
    body = ViewBody.model_validate(
        {"name": "v", "tableId": "t1", "customSql": "SELECT SUM(x) FROM t"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(views_router.create(body))
    assert exc.value.status_code == 422
    assert "alias" in exc.value.detail["message"].lower()
    guard.assert_not_awaited()
