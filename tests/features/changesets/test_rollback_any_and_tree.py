"""Rollback a CUALQUIER versión (deshace las posteriores) + jerarquía del diff.

- `rollback(cs_id)`: crea un draft que restaura el modelo al estado de `cs_id`
  componiendo los inversos de todas las versiones publicadas DESPUÉS (el inverso
  de la más cercana a la objetivo gana por entidad).
- `build_diff_tree`: agrupa los cambios por Proyecto→Folder→Canvas→Esquema→Tabla
  →Columnas (+Vistas).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import service


# ── rollback a versión pasada ────────────────────────────────────────────

def test_rollback_a_version_pasada_deshace_posteriores(monkeypatch):
    target = {"id": "v1", "projectId": "p1", "status": "approved", "appliedAt": "2026-01-01", "versionLabel": "v1"}
    after = [  # latest→oldest (como los devuelve applied_after)
        {"id": "v3", "appliedAt": "2026-03-01", "versionLabel": "v3"},
        {"id": "v2", "appliedAt": "2026-02-01", "versionLabel": "v2"},
    ]
    changes_by_id = {
        # v3 creó t3 (before=None → el inverso la BORRA)
        "v3": {"canonical_tables": {"t3": {"op": "upsert", "before": None, "beforeAt": "x",
                                           "payload": {"physicalName": "NUEVA"}}}},
        # v2 modificó t1 (before=doc → el inverso la RESTAURA al doc previo)
        "v2": {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "x",
                                           "before": {"id": "t1", "physicalName": "VIEJO"},
                                           "payload": {"physicalName": "NUEVO"}}}},
    }
    gets = {"v1": target, "draft1": {"id": "draft1", "projectId": "p1", "title": "Restore to v1"}}
    monkeypatch.setattr(service.repository, "get", AsyncMock(side_effect=lambda cid: gets.get(cid)))
    monkeypatch.setattr(service.repository, "applied_after", AsyncMock(return_value=after))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(side_effect=lambda cid, cols=None: changes_by_id.get(cid, {})))
    monkeypatch.setattr(service.repository, "list_summaries",
                        AsyncMock(return_value=[{"versionLabel": "v1"}, {"versionLabel": "v2"},
                                                {"versionLabel": "v3"}]))
    create = AsyncMock(return_value={"id": "draft1"})
    monkeypatch.setattr(service.repository, "create", create)
    sets: list[tuple] = []
    monkeypatch.setattr(service.repository, "set_change",
                        AsyncMock(side_effect=lambda did, coll, eid, op, payload: sets.append((coll, eid, op))))

    res = asyncio.run(service.rollback("v1", "ana"))
    ops = {(c, e): o for c, e, o in sets}
    assert ops[("canonical_tables", "t3")] == "delete"    # creada en v3 → se borra
    assert ops[("canonical_tables", "t1")] == "upsert"     # modificada en v2 → se restaura
    assert res["id"] == "draft1"
    # Doc 65: el draft nace con etiqueta autoincremental, título en inglés y la
    # PROCEDENCIA estructurada de la restauración (chip "Restored from vN").
    assert create.await_args.args[0] == "Restore to v1"
    extra = create.await_args.kwargs["extra"]
    assert extra["versionLabel"] == "v4"
    assert extra["restoredFrom"] == {"csId": "v1", "versionLabel": "v1",
                                     "appliedAt": "2026-01-01"}


def test_rollback_version_actual_es_empty(monkeypatch):
    target = {"id": "v5", "projectId": "p1", "status": "approved", "appliedAt": "2026-05-01"}
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=target))
    monkeypatch.setattr(service.repository, "applied_after", AsyncMock(return_value=[]))
    assert asyncio.run(service.rollback("v5", "ana")) == "empty"


def test_rollback_no_publicada(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"projectId": "p1", "id": "d1", "status": "draft"}))
    assert asyncio.run(service.rollback("d1", "ana")) == "not-applied"


def test_rollback_falta_imagen_previa(monkeypatch):
    target = {"id": "v1", "projectId": "p1", "status": "approved", "appliedAt": "2026-01-01"}
    after = [{"id": "v2", "appliedAt": "2026-02-01"}]
    # cambio sin `beforeAt` (publicado antes de la feature) → no reconstruible.
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=target))
    monkeypatch.setattr(service.repository, "applied_after", AsyncMock(return_value=after))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(return_value={"canonical_tables": {"t1": {"op": "upsert", "payload": {}}}}))
    assert asyncio.run(service.rollback("v1", "ana")) == "no-before"


# ── jerarquía del diff ───────────────────────────────────────────────────

def test_build_diff_tree_agrupa_por_jerarquia():
    collections = {
        "canonical_tables": {"added": [{"id": "t1", "name": "CLIENTE", "collection": "canonical_tables"}],
                             "edited": [], "deleted": []},
        "canonical_columns": {"added": [{"id": "c1", "name": "ID", "collection": "canonical_columns"}],
                              "edited": [], "deleted": []},
    }
    changes = {
        "canonical_tables": {"t1": {"op": "upsert", "payload": {"physicalName": "CLIENTE", "schema": "core"}}},
        "canonical_columns": {"c1": {"op": "upsert", "payload": {"physicalName": "ID", "tableId": "t1"}}},
    }
    published = {"canonical_tables": [], "canonical_columns": []}
    sas = [{"id": "sa1", "name": "Clientes", "projectId": "p1", "folderId": "f1", "tableIds": ["t1"]}]
    out = service.build_diff_tree(collections, changes, published, sas,
                                  [{"id": "p1", "name": "Core Banking"}],
                                  [{"id": "f1", "name": "Comercial"}])
    proj = out["tree"][0]
    assert proj["type"] == "project" and proj["name"] == "Core Banking"
    canvas = proj["children"][0]["children"][0]
    assert canvas["type"] == "canvas" and canvas["name"] == "Clientes"
    schema = canvas["children"][0]
    assert schema["type"] == "schema" and schema["name"] == "core"
    table = schema["children"][0]
    assert table["type"] == "table" and table["change"] == "added"
    assert table["children"][0]["name"] == "ID" and table["children"][0]["type"] == "column"


def test_build_diff_tree_orphan_sin_canvas():
    collections = {"canonical_tables": {"added": [{"id": "t9", "name": "HUERFANA"}], "edited": [], "deleted": []}}
    changes = {"canonical_tables": {"t9": {"op": "upsert", "payload": {"physicalName": "HUERFANA", "schema": "core"}}}}
    out = service.build_diff_tree(collections, changes, {"canonical_tables": [], "canonical_columns": []}, [], [], [])
    assert out["tree"] == []
    assert len(out["orphans"]) == 1 and out["orphans"][0]["name"] == "HUERFANA"


def test_build_diff_tree_estructura_separada():
    collections = {"projects": {"added": [{"id": "p2", "name": "Nuevo"}], "edited": [], "deleted": []}}
    changes = {"projects": {"p2": {"op": "upsert", "payload": {"name": "Nuevo"}}}}
    out = service.build_diff_tree(collections, changes, {"canonical_tables": [], "canonical_columns": []}, [], [], [])
    assert len(out["structure"]) == 1
    assert out["structure"][0]["kind"] == "Project" and out["structure"][0]["change"] == "added"
