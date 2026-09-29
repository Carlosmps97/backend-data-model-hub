"""Reportes guardados y rutas del motor de consulta (doc 75): todo pertenece
a UN proyecto — `SavedReportBody.projectId` obligatorio, listado filtrado por
proyecto, `SqlBody.projectId` estampado en el spec, `/catalog` y los insights
exigen `projectId`."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.features.reporting.query import executor as ex
from app.features.reporting.query import reports


def test_saved_report_body_exige_project_id():
    with pytest.raises(ValidationError):
        reports.SavedReportBody.model_validate({"name": "R", "spec": {}})
    body = reports.SavedReportBody.model_validate({"name": "R", "spec": {}, "projectId": "p1"})
    assert body.projectId == "p1"


def test_list_reports_filtra_por_proyecto(monkeypatch):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[{"_id": "r1", "name": "b", "owner": "ana", "projectId": "p1"},
                                             {"_id": "r2", "name": "a", "owner": "x", "shared": True, "projectId": "p1"}])
    coll = MagicMock()
    coll.find = MagicMock(return_value=cursor)
    monkeypatch.setattr(reports, "get_db", AsyncMock(return_value={"saved_reports": coll}))
    out = asyncio.run(reports.list_reports("ana", "p1"))
    flt = coll.find.call_args.args[0]
    assert flt["projectId"] == "p1"
    assert flt["$or"] == [{"owner": "ana"}, {"shared": True}]
    assert [r["id"] for r in out] == ["r2", "r1"]


def test_validate_sql_estampa_el_proyecto(client, monkeypatch):
    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    resp = client.post("/api/reporting/query/validate",
                       json={"text": "SELECT physicalName FROM columns", "projectId": "p1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["spec"]["projectId"] == "p1"
    ex._udp_defs.assert_awaited_once_with("p1")


def test_sql_body_exige_project_id(client, monkeypatch):
    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    resp = client.post("/api/reporting/query/validate", json={"text": "SELECT physicalName FROM columns"})
    assert resp.status_code == 422


def test_catalog_exige_project_id(client, monkeypatch):
    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    assert client.get("/api/reporting/catalog", params={"from": "columns"}).status_code == 422
    resp = client.get("/api/reporting/catalog", params={"from": "columns", "projectId": "p1"})
    assert resp.status_code == 200
    ex._udp_defs.assert_awaited_once_with("p1")


def test_insights_exigen_project_id(client, monkeypatch):
    from app.features.reporting import views

    monkeypatch.setattr(views, "scorecard", AsyncMock(return_value={"tables": 1}))
    assert client.get("/api/reporting/insights/scorecard").status_code == 422
    resp = client.get("/api/reporting/insights/scorecard", params={"projectId": "p1"})
    assert resp.status_code == 200
    views.scorecard.assert_awaited_once_with("p1")


def test_reporting_tabular_exige_project_id(client, monkeypatch):
    from app.features.reporting import service

    monkeypatch.setattr(service, "filter_options", AsyncMock(return_value={"schemas": [], "subjectAreas": []}))
    assert client.get("/api/reporting/filters").status_code == 422
    resp = client.get("/api/reporting/filters", params={"projectId": "p1"})
    assert resp.status_code == 200
    service.filter_options.assert_awaited_once_with("p1", None)   # doc 102: sin versión = producción
