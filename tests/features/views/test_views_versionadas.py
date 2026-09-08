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

    monkeypatch.setattr(cs_service.repository, "get", AsyncMock(return_value={"projectId": "p1", "id": "cs1", "status": "draft"}))
    monkeypatch.setattr(cs_service.repository, "published", _published)
    monkeypatch.setattr(cs_service.repository, "changes_map", AsyncMock(return_value={"views": {
        # pendiente que TOCA t1 por sourceTableIds (sin estar en el slice publicado)
        "v-new": {"op": "upsert", "payload": {"name": "VU_NUEVA", "sourceTableIds": ["t1", "t9"],
                                              "showOnCanvas": True, "sql": ""}},
        # pendiente de OTRA tabla → fuera de scope
        "v-ajena": {"op": "upsert", "payload": {"name": "VU_AJENA", "sourceTableIds": ["t9"], "sql": ""}},
    }}))

    out = asyncio.run(cs_service.effective("cs1", "views", table_id="t1"))
    assert seen["flt"].pop("projectId") == "p1"          # doc 75: slice acotado al proyecto
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


def test_create_view_estampa_proyecto_de_su_fuente(monkeypatch):
    """Doc 75 I1: la vista hereda el proyecto de su primera tabla fuente."""
    from app.features.views import service
    from app.features.views.schemas import ViewBody
    monkeypatch.setattr(service.catalog_repo, "project_of_table", AsyncMock(return_value="p9"))
    monkeypatch.setattr(service.repository, "create", AsyncMock(side_effect=lambda d: {**d, "id": "v1"}))
    out = asyncio.run(service.create(ViewBody(name="V", sourceTableIds=["t1"])))
    assert out["projectId"] == "p9"


def test_update_view_conserva_el_proyecto_del_doc_vivo(monkeypatch):
    from app.features.views import service
    from app.features.views.schemas import ViewBody
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"projectId": "p1", "id": "v1", "projectId": "p1"}))
    upd = AsyncMock(return_value={"id": "v1"})
    monkeypatch.setattr(service.repository, "update", upd)
    asyncio.run(service.update("v1", ViewBody.model_validate({"name": "V", "tableId": "t1", "projectId": "OTRO"})))
    assert upd.await_args.args[1]["projectId"] == "p1"
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=None))
    assert asyncio.run(service.update("nope", ViewBody(name="V", tableId="t1"))) is None


def test_create_relationship_deriva_proyecto_y_rechaza_cruce(monkeypatch):
    """Doc 75 I2: una relación jamás cruza proyectos."""
    import pytest
    from fastapi import HTTPException
    from app.features.relationships import service as rel_service
    from app.features.relationships.schemas import RelationshipBody
    owners = {"a": "p1", "b": "p1", "z": "p2"}
    monkeypatch.setattr(rel_service.catalog_repo, "project_of_table", AsyncMock(side_effect=lambda t: owners.get(t)))
    monkeypatch.setattr(rel_service.repository, "create", AsyncMock(side_effect=lambda d: {**d, "id": "r1"}))
    body = RelationshipBody.model_validate({"parentTableId": "a", "childTableId": "b",
                                            "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]})
    assert asyncio.run(rel_service.create(body))["projectId"] == "p1"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rel_service.create(RelationshipBody.model_validate(
            {"parentTableId": "a", "childTableId": "z", "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]})))
    assert exc.value.status_code == 409
