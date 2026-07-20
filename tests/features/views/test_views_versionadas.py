"""Vistas VERSIONADAS (doc 20): slice de `views` en effective + overlay del diagrama."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import service as cs_service


def test_effective_views_slice_por_tabla(monkeypatch):
    """El slice por tabla de views filtra por tableId O sourceTableIds, y el
    _in_scope deja pasar payloads pendientes cuyas fuentes tocan la tabla."""
    seen: dict = {}

    async def _published(collection, flt=None, **kw):
        seen["flt"] = flt
        return [{"id": "v-pub", "name": "VU_PUB", "tableId": "t1",
                 "sourceTableIds": ["t1"], "showOnCanvas": True}]

    monkeypatch.setattr(cs_service.repository, "get", AsyncMock(return_value={"id": "cs1", "status": "draft"}))
    monkeypatch.setattr(cs_service.repository, "published", _published)
    monkeypatch.setattr(cs_service.repository, "changes_map", AsyncMock(return_value={"views": {
        # pendiente que TOCA t1 por sourceTableIds (sin estar en el slice publicado)
        "v-new": {"op": "upsert", "payload": {"name": "VU_NUEVA", "sourceTableIds": ["t1", "t9"],
                                              "showOnCanvas": True, "sql": ""}},
        # pendiente de OTRA tabla → fuera de scope
        "v-ajena": {"op": "upsert", "payload": {"name": "VU_AJENA", "sourceTableIds": ["t9"], "sql": ""}},
    }}))

    out = asyncio.run(cs_service.effective("cs1", "views", table_id="t1"))
    assert seen["flt"] == {"$or": [{"tableId": "t1"}, {"sourceTableIds": "t1"}]}
    names = {v.get("name") for v in out}
    assert names == {"VU_PUB", "VU_NUEVA"}


def test_diagram_overlay_de_vistas(monkeypatch):
    """El diagrama aplica el overlay de views del changeset: la pendiente del
    canvas entra, la borrada sale y la re-apuntada fuera del canvas también."""
    from app.features.projects import service as proj_service
    import app.features.catalog.repository as catalog_repo
    import app.features.changesets.repository as cs_repo
    import app.features.projects.repository as proj_repo
    import app.features.relationships.repository as rel_repo
    import app.features.views.repository as views_repo

    monkeypatch.setattr(proj_repo, "get_subject_area", AsyncMock(return_value={
        "id": "sa1", "name": "Canvas", "tableIds": ["t1"], "layout": {}}))
    monkeypatch.setattr(catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[
        {"id": "t1", "physicalName": "T1"}]))
    monkeypatch.setattr(catalog_repo, "list_columns_for_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr(rel_repo, "list_for_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr(views_repo, "list_for_canvas", AsyncMock(return_value=[
        {"id": "v-vieja", "name": "VU_VIEJA", "tableId": "t1", "sourceTableIds": ["t1"], "showOnCanvas": True},
    ]))
    monkeypatch.setattr(cs_repo, "changes_map", AsyncMock(side_effect=[
        {},  # 1ª llamada: subject_areas (sin cambios de canvas)
        {"views": {
            "v-vieja": {"op": "delete"},
            "v-nueva": {"op": "upsert", "payload": {
                "name": "VU_NUEVA", "sourceTableIds": ["t1"], "showOnCanvas": True, "sql": ""}},
        }},
    ]))

    dia = asyncio.run(proj_service.diagram("sa1", changeset_id="cs1"))
    names = {v.get("name") for v in dia["views"]}
    assert names == {"VU_NUEVA"}
