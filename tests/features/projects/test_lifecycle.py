"""Doc 75 D5/D15 — proyecto nuevo (vacío o con copia) + marcador v1; sin PUT/DELETE
directos (rename/description/delete van por draft); conteos y cascada."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.projects import repository, service
from app.features.projects.schemas import ProjectCreateBody
from app.main import app


def _mock(monkeypatch, *, existing=None):
    monkeypatch.setattr(service.repository, "find_by_name", AsyncMock(return_value=existing))
    monkeypatch.setattr(service.repository, "create_project", AsyncMock(side_effect=lambda d: {**d, "id": "pNEW"}))
    monkeypatch.setattr(service.repository, "get_project", AsyncMock(return_value={"id": "pSRC", "name": "DDV"}))
    monkeypatch.setattr(service.repository, "discard_project", AsyncMock())
    monkeypatch.setattr(service.std_service, "bootstrap_project", AsyncMock(return_value={"seq": 1}))
    monkeypatch.setattr(service.cs_service, "create_base_marker", AsyncMock(return_value={"versionLabel": "v1"}))
    monkeypatch.setattr(service, "audit", AsyncMock())


def test_create_vacio_crea_baseline_y_marcador(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.create_project("ana", ProjectCreateBody(name="UDV INT INTERNO")))
    assert out["id"] == "pNEW" and out["name"] == "UDV INT INTERNO"
    service.std_service.bootstrap_project.assert_awaited_once_with("ana", "pNEW", None)
    service.cs_service.create_base_marker.assert_awaited_once_with("pNEW")


def test_create_con_copia_pasa_bloques_y_nombre_fuente(monkeypatch):
    _mock(monkeypatch)
    body = ProjectCreateBody(name="X", copyFrom={"projectId": "pSRC", "blocks": ["glossary", "udp"]})
    asyncio.run(service.create_project("ana", body))
    service.std_service.bootstrap_project.assert_awaited_once_with(
        "ana", "pNEW", {"projectId": "pSRC", "blocks": ["glossary", "udp"], "projectName": "DDV"})


def test_create_nombre_duplicado_409(monkeypatch):
    _mock(monkeypatch, existing={"id": "p1", "name": "X"})
    with pytest.raises(service.DuplicateProjectError):
        asyncio.run(service.create_project("ana", ProjectCreateBody(name="x ")))


def test_create_falla_a_mitad_no_deja_proyecto_visible(monkeypatch):
    _mock(monkeypatch)
    service.cs_service.create_base_marker.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        asyncio.run(service.create_project("ana", ProjectCreateBody(name="Y")))
    service.repository.discard_project.assert_awaited_once_with("pNEW")


def test_no_existen_put_ni_delete_directos():
    paths = {(r.path, m) for r in app.routes for m in getattr(r, "methods", ())}
    assert ("/api/projects/{pid}", "PUT") not in paths and ("/api/projects/{pid}", "DELETE") not in paths
    assert ("/api/projects", "POST") in paths and ("/api/projects/{project_id}/counts", "GET") in paths


def test_count_scope_y_cascade_delete_filtran_por_proyecto(monkeypatch):
    calls = []

    class _Res:
        modified_count = 2

    class _Coll:
        def __init__(self, name): self.name = name
        async def count_documents(self, flt): calls.append(("count", self.name, flt)); return 3
        async def update_many(self, flt, upd): calls.append(("update", self.name, flt, upd)); return _Res()
        def find(self, flt, proj=None):
            calls.append(("find", self.name, flt))
            class _C:
                async def to_list(self_inner, n): return [{"_id": "u1", "projectIds": ["p1", "p2"]}]
            return _C()
        async def update_one(self, flt, upd): calls.append(("update_one", self.name, flt, upd))

    class _Db:
        def __getitem__(self, name): return _Coll(name)
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value=_Db()))
    counts = asyncio.run(repository.count_scope("p1"))
    assert counts == {"tables": 3, "columns": 3, "views": 3, "relationships": 3, "schemas": 3, "folders": 3, "canvases": 3}
    assert all(c[2]["projectId"] == "p1" for c in calls if c[0] == "count")
    calls.clear()
    out = asyncio.run(repository.cascade_delete("p1", "cs9"))
    touched = {c[1] for c in calls if c[0] == "update"}
    assert {"canonical_tables", "canonical_columns", "views", "relationships", "schemas", "folders", "subject_areas",
            "parent_domains", "glossary_terms", "udp_definitions", "ddl_rules", "ddl_ruleset_config",
            "naming_config", "saved_reports"} <= touched
    assert "changesets" not in touched and "standards_versions" not in touched
    assert all(c[2]["projectId"] == "p1" and c[3]["$set"]["deletedIn"] == "cs9" for c in calls if c[0] == "update")
    pull = [c for c in calls if c[0] == "update_one" and c[1] == "users"]
    assert pull and pull[0][3] == {"$set": {"projectIds": ["p2"]}}
    assert out["canonical_tables"] == 2
