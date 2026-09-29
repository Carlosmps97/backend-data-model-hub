"""Doc 92 D3/D4: `offset` en el fast path del reporte y conteo total de tablas."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.reporting import service
from app.features.reporting.router import report_tables_count


def test_list_table_rows_pasa_el_offset_al_fast_path(monkeypatch):
    calls: dict = {}

    async def _page(project_id, limit, offset=0):
        calls["page"] = (project_id, limit, offset)
        return {"tables": [{"id": "t51", "physicalName": "M_Z", "schema": "core"}],
                "columnCounts": {}, "relationships": [], "subjectAreas": []}

    monkeypatch.setattr(service.repository, "report_inputs_page", _page)
    monkeypatch.setattr(service.repository, "report_inputs", AsyncMock(side_effect=AssertionError("no full scan")))
    monkeypatch.setattr(service.repository, "udp_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.projects_repo, "get_project", AsyncMock(return_value={"id": "p1", "name": "P"}))
    rows = asyncio.run(service.list_table_rows("p1", {}, 50, 50))
    assert calls["page"] == ("p1", 50, 50)
    assert rows[0]["id"] == "t51"


def test_count_tables_endpoint(monkeypatch):
    monkeypatch.setattr(service.repository, "count_tables", AsyncMock(return_value=1932))
    out = asyncio.run(report_tables_count(projectId="p1", changesetId=None, principal=None))
    assert out["data"] == {"total": 1932}
    service.repository.count_tables.assert_awaited_once_with("p1")
