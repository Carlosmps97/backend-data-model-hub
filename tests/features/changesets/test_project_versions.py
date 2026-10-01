"""Doc 75 D2/I6/I7 — labels, producción, rollback y asof por proyecto; D5/D20
`deletesProject` en filas y diff."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.features.changesets import repository, service

ROWS = [
    {"id": "a1", "projectId": "A", "status": "approved", "appliedAt": "2026-09-01T00:00:00", "versionLabel": "v1"},
    {"id": "b1", "projectId": "B", "status": "approved", "appliedAt": "2026-09-02T00:00:00", "versionLabel": "v1"},
    {"id": "a2", "projectId": "A", "status": "approved", "appliedAt": "2026-09-03T00:00:00", "versionLabel": "v2"},
    {"id": "b2", "projectId": "B", "status": "draft", "appliedAt": None, "versionLabel": "v2"},
]


def _summaries(monkeypatch):
    async def _ls(project_id=None):
        return [r for r in ROWS if project_id is None or r["projectId"] == project_id]
    monkeypatch.setattr(service.repository, "list_summaries", _ls)
    monkeypatch.setattr(service.repository, "changesets_deleting_project", AsyncMock(return_value=set()))


def test_label_autoincremental_por_proyecto(monkeypatch):
    _summaries(monkeypatch)
    created = AsyncMock(side_effect=lambda title, owner, extra=None: {"id": "n", **(extra or {})})
    monkeypatch.setattr(service.repository, "create", created)
    out = asyncio.run(service.snapshot("ana", "A", None, None, None))
    assert out["versionLabel"] == "v3" and out["projectId"] == "A"
    out = asyncio.run(service.snapshot("ana", "B", None, None, None))
    assert out["versionLabel"] == "v3"     # B tiene v1 aplicada y v2 draft → v3


def test_produccion_por_proyecto(monkeypatch):
    _summaries(monkeypatch)
    assert asyncio.run(service.current_production("A"))["id"] == "a2"
    assert asyncio.run(service.current_production("B"))["id"] == "b1"


def test_rollback_solo_compone_versiones_del_proyecto(monkeypatch):
    after = AsyncMock(return_value=[{"id": "a2", "appliedAt": ROWS[2]["appliedAt"], "versionLabel": "v2"}])
    monkeypatch.setattr(service.repository, "applied_after", after)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={
        "canonical_tables": {"t1": {"op": "upsert", "payload": {"x": 1}, "beforeAt": "t", "before": None}}}))
    _summaries(monkeypatch)
    monkeypatch.setattr(service.repository, "create", AsyncMock(side_effect=lambda t, o, extra=None: {"id": "d", **(extra or {})}))
    # Doc 105: los inversos van en UN lote; la marca «incompleto» se quita al final.
    monkeypatch.setattr(service.repository, "set_changes_bulk", AsyncMock(return_value={"id": "d"}))
    monkeypatch.setattr(service.repository, "set_status", AsyncMock())
    monkeypatch.setattr(service.repository, "get", AsyncMock(side_effect=[ROWS[0], {"id": "d", "projectId": "A"}]))
    out = asyncio.run(service.rollback("a1", "ana"))
    after.assert_awaited_once_with("A", ROWS[0]["appliedAt"])
    assert out["projectId"] == "A"


def test_compare_entre_proyectos_409(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(side_effect=[ROWS[0], ROWS[1]]))
    assert asyncio.run(service.compare_versions("a1", "b1")) == "different-projects"


def test_applied_after_filtra_por_proyecto(monkeypatch):
    cursor = MagicMock(); cursor.to_list = AsyncMock(return_value=[])
    coll = MagicMock(); coll.find = MagicMock(return_value=cursor)
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"changesets": coll}))
    asyncio.run(repository.applied_after("A", "2026-09-01"))
    assert coll.find.call_args.args[0]["projectId"] == "A"


def test_version_rows_marcan_deletes_project(monkeypatch):
    _summaries(monkeypatch)
    monkeypatch.setattr(service.repository, "changesets_deleting_project", AsyncMock(return_value={"b2"}))
    rows = asyncio.run(service.list_versions())
    assert {r["id"]: r["deletesProject"] for r in rows} == {"a1": False, "b1": False, "a2": False, "b2": True}
    assert all(r["projectId"] for r in rows)


def test_diff_expone_deletes_project(monkeypatch):
    cs = {"id": "b2", "projectId": "B", "status": "submitted", "owner": "x", "createdAt": "t0"}
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=cs))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={"projects": {"B": {"op": "delete", "at": "t"}}}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(
        side_effect=lambda coll, flt=None, **kw: [{"id": "B", "name": "UDV INT FISICO"}] if coll == "projects" else []))
    counts = {"tables": 1, "columns": 2, "views": 0, "relationships": 0, "schemas": 1, "folders": 0, "canvases": 0}
    monkeypatch.setattr(service.projects_repo, "count_scope", AsyncMock(return_value=counts))
    out = asyncio.run(service.diff("b2"))
    assert out["impact"]["deletesProject"] == {"id": "B", "name": "UDV INT FISICO", "counts": counts}
    assert "projectsAffected" not in out["impact"]
