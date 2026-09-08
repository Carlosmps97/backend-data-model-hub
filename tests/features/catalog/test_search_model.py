"""Doc 70 §11 — buscador del Model: tablas · columnas · definiciones con sus
canvases; publicado + overlay del changeset. Repositorios mockeados."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.catalog import service
from app.features.catalog.service import overlay_search


def test_overlay_search_suma_hits_del_draft_y_saca_renombradas():
    pub = [{"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente"}]
    changes = {
        "t1": {"op": "upsert", "payload": {"physicalName": "M_PERSONA", "logicalName": "persona"}},  # ya no matchea
        "t9": {"op": "upsert", "payload": {"physicalName": "D_CLIENTE_HIST", "logicalName": "hist"}},  # nueva
        "t8": {"op": "upsert", "payload": {"physicalName": "OTRA", "logicalName": "otra"}},           # no matchea
    }
    out = overlay_search(pub, changes, ("physicalName", "logicalName"), "clien", 25)
    assert [d["id"] for d in out] == ["t9"]


def test_overlay_search_sin_cambios_conserva_publicado_y_capa():
    pub = [{"id": f"t{i}", "physicalName": f"T{i}", "logicalName": "x"} for i in range(5)]
    assert [d["id"] for d in overlay_search(pub, {}, ("physicalName",), "t", 3)] == ["t0", "t1", "t2"]


def _wire(monkeypatch, *, tables, cols_name, cols_def, owners, sas, changes=None):
    monkeypatch.setattr(service.repository, "list_tables", AsyncMock(return_value=tables))

    async def _cols(pid, q, limit, fields, extra=None, skip=0):
        return cols_def if fields == ("description",) else cols_name
    monkeypatch.setattr(service.repository, "search_columns_by", _cols)
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=owners))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=sas))
    monkeypatch.setattr("app.features.changesets.repository.changes_map", AsyncMock(return_value=changes or {}))


def test_search_model_resuelve_tablas_y_canvases_de_cada_hit(monkeypatch):
    _wire(
        monkeypatch,
        tables=[{"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"}],
        cols_name=[{"id": "c1", "tableId": "t1", "physicalName": "CODCLIENTE", "logicalName": "codigo cliente",
                    "dataType": "VARCHAR(30)", "description": None},
                   {"id": "c9", "tableId": "t9", "physicalName": "CLIENTE_X", "logicalName": "x", "dataType": "INT"}],  # tabla borrada
        cols_def=[{"id": "c2", "tableId": "t2", "physicalName": "NOMBRE", "logicalName": "nombre",
                   "dataType": "STRING", "description": "Nombre del cliente titular"}],
        owners=[{"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"},
                {"id": "t2", "physicalName": "M_CUENTA", "logicalName": "cuenta", "schema": "core"}],
        sas=[{"id": "saB", "name": "Banking", "projectId": "p1", "tableIds": ["t1", "t2"]},
             {"id": "saA", "name": "Analytics", "projectId": "p1", "tableIds": ["t1"]}],
    )
    out = asyncio.run(service.search_model("p1", "cliente", 25))
    assert [c["name"] for c in out["tables"][0]["canvases"]] == ["Analytics", "Banking"]     # ordenados
    assert [c["id"] for c in out["columns"]] == ["c1"]                                        # c9 sin tabla → fuera
    assert out["columns"][0]["tableName"] == "M_CLIENTE" and out["columns"][0]["schema"] == "core"
    assert out["definitions"][0]["id"] == "c2"
    assert [c["name"] for c in out["definitions"][0]["canvases"]] == ["Banking"]
    assert out["limit"] == 25


def test_search_model_con_changeset_aplica_overlay_de_tablas_columnas_y_canvases(monkeypatch):
    changes = {
        "canonical_tables": {"t7": {"op": "upsert", "payload": {"physicalName": "M_CLIENTE_NEW", "logicalName": "cliente nuevo", "schema": "core"}}},
        "canonical_columns": {"c7": {"op": "upsert", "payload": {"tableId": "t7", "physicalName": "IDCLIENTE", "logicalName": "id",
                                                                 "dataType": "INT", "description": "Id del cliente"}}},
        "subject_areas": {"saNew": {"op": "upsert", "payload": {"name": "Draft canvas", "projectId": "p1", "tableIds": ["t7"]}}},
    }
    _wire(monkeypatch, tables=[], cols_name=[], cols_def=[], owners=[], sas=[], changes=changes)
    out = asyncio.run(service.search_model("p1", "cliente", 25, "cs1"))
    assert [t["id"] for t in out["tables"]] == ["t7"]
    assert [c["name"] for c in out["tables"][0]["canvases"]] == ["Draft canvas"]
    assert [c["id"] for c in out["columns"]] == ["c7"] and out["columns"][0]["tableName"] == "M_CLIENTE_NEW"
    assert [c["id"] for c in out["definitions"]] == ["c7"]


def test_search_route_registered(client):
    assert "/api/projects/{project_id}/catalog/search" in {r.path for r in client.app.routes}


def test_overlay_search_respeta_where_de_dominio_y_alcance():
    pub = [{"id": "c1", "tableId": "t1", "parentDomainId": "d1", "physicalName": "CODCLI"}]
    changes = {
        "c2": {"op": "upsert", "payload": {"tableId": "t1", "parentDomainId": "d9", "physicalName": "CODCLI2"}},  # otro dominio
        "c3": {"op": "upsert", "payload": {"tableId": "t7", "parentDomainId": "d1", "physicalName": "CODCLI3"}},  # fuera del alcance
        "c4": {"op": "upsert", "payload": {"tableId": "t1", "parentDomainId": "d1", "physicalName": "CODCLI4"}},  # entra
    }
    out = overlay_search(pub, changes, ("physicalName",), "codcli", 25,
                         where={"parentDomainId": "d1", "tableId": {"$in": ["t1"]}})
    assert [d["id"] for d in out] == ["c1", "c4"]


def test_search_model_scope_tables_no_consulta_columnas(monkeypatch):
    calls: list[tuple] = []

    async def _cols(pid, q, limit, fields, extra=None, skip=0):
        calls.append((fields, extra)); return []
    monkeypatch.setattr(service.repository, "list_tables", AsyncMock(return_value=[
        {"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente", "schema": "core"}]))
    monkeypatch.setattr(service.repository, "search_columns_by", _cols)
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=[]))
    out = asyncio.run(service.search_model("p1", "cliente", 25, scope="tables"))
    assert [t["id"] for t in out["tables"]] == ["t1"] and out["columns"] == [] and out["definitions"] == []
    assert calls == [] and out["scope"] == "tables"


def test_search_model_alcance_por_proyecto_y_por_carpeta(monkeypatch):
    seen: dict = {}

    async def _tables(pid, q, limit, schema=None, ids=None, skip=0):
        seen["ids"] = ids; return []

    async def _cols(pid, q, limit, fields, extra=None, skip=0):
        seen.setdefault("extra", []).append(extra); return []
    monkeypatch.setattr(service.repository, "list_tables", _tables)
    monkeypatch.setattr(service.repository, "search_columns_by", _cols)
    monkeypatch.setattr(service.repository, "canvases_in_scope", AsyncMock(return_value=[
        {"id": "saA", "name": "A", "projectId": "p1", "folderId": "f1", "tableIds": ["t2", "t1"]},
        {"id": "saB", "name": "B", "projectId": "p1", "folderId": "f2", "tableIds": ["t3"]}]))
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=[]))
    # Doc 75 D6: el proyecto acota TODA lectura en el repositorio — sin carpeta ni
    # canvas no hay lista de ids permitidos ni consulta de canvases.
    asyncio.run(service.search_model("p1", "cli", 25))
    assert seen["ids"] is None
    assert seen["extra"] == [None, None]
    service.repository.canvases_in_scope.assert_not_awaited()
    # Con carpeta, el alcance son las tablas de los canvases del subárbol.
    monkeypatch.setattr(service.repository, "list_folders_lite", AsyncMock(return_value=[
        {"id": "f1", "projectId": "p1", "parentFolderId": None, "name": "F1"}]))
    asyncio.run(service.search_model("p1", "cli", 25, folder_id="f1"))
    assert seen["ids"] == ["t1", "t2", "t3"]
    assert seen["extra"][-1] == {"tableId": {"$in": ["t1", "t2", "t3"]}}
    service.repository.canvases_in_scope.assert_awaited_once_with("p1", ["f1"], None)


def test_folder_subtree_bfs():
    folders = [
        {"id": "sub", "parentFolderId": None},
        {"id": "dom1", "parentFolderId": "sub"}, {"id": "dom2", "parentFolderId": "sub"},
        {"id": "nested", "parentFolderId": "dom1"}, {"id": "other", "parentFolderId": None},
    ]
    assert service.folder_subtree(folders, "sub") == ["sub", "dom1", "dom2", "nested"]
    assert service.folder_subtree(folders, "dom2") == ["dom2"]
    assert service.folder_subtree(folders, "missing") == ["missing"]


def test_search_model_alcance_por_subproyecto_incluye_sus_dominios(monkeypatch):
    """Proyecto › Sub-proyecto (CPYBCA) › Dominio (Despriorizado) › Modelo: filtrar
    por el sub-proyecto alcanza los canvases que cuelgan de sus dominios."""
    seen: dict = {}

    async def _tables(pid, q, limit, schema=None, ids=None, skip=0):
        seen["ids"] = ids; return []

    async def _cols(pid, q, limit, fields, extra=None, skip=0):
        seen.setdefault("extra", []).append(extra); return []
    monkeypatch.setattr(service.repository, "list_tables", _tables)
    monkeypatch.setattr(service.repository, "search_columns_by", _cols)
    monkeypatch.setattr(service.repository, "list_folders_lite", AsyncMock(return_value=[
        {"id": "cpybca", "projectId": "p1", "parentFolderId": None, "name": "CPYBCA"},
        {"id": "despri", "projectId": "p1", "parentFolderId": "cpybca", "name": "Despriorizado"},
        {"id": "elim", "projectId": "p1", "parentFolderId": "cpybca", "name": "Eliminado"},
        {"id": "pym", "projectId": "p1", "parentFolderId": None, "name": "CPYBCAPYM"},
    ]))
    scope_mock = AsyncMock(return_value=[
        {"id": "fin", "name": "Finanzas", "projectId": "p1", "folderId": "despri", "tableIds": ["t1"]},
        {"id": "eli", "name": "Viejo", "projectId": "p1", "folderId": "elim", "tableIds": ["t2"]},
    ])
    monkeypatch.setattr(service.repository, "canvases_in_scope", scope_mock)
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.features.changesets.repository.changes_map", AsyncMock(return_value={}))
    asyncio.run(service.search_model("p1", "cli", 25, folder_id="cpybca"))
    # el subárbol de CPYBCA (sin CPYBCAPYM) viaja al repository y las tablas de sus canvases quedan permitidas
    scope_mock.assert_awaited_once_with("p1", ["cpybca", "despri", "elim"], None)
    assert seen["ids"] == ["t1", "t2"]
    assert seen["extra"] == [{"tableId": {"$in": ["t1", "t2"]}}] * 2


def test_search_model_alcance_por_dominio_solo_su_subarbol(monkeypatch):
    seen: dict = {}

    async def _tables(pid, q, limit, schema=None, ids=None, skip=0):
        seen["ids"] = ids; return []
    monkeypatch.setattr(service.repository, "list_tables", _tables)
    monkeypatch.setattr(service.repository, "search_columns_by", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "list_folders_lite", AsyncMock(return_value=[
        {"id": "cpybca", "projectId": "p1", "parentFolderId": None},
        {"id": "despri", "projectId": "p1", "parentFolderId": "cpybca"},
    ]))
    scope_mock = AsyncMock(return_value=[{"id": "fin", "name": "Finanzas", "projectId": "p1", "folderId": "despri", "tableIds": ["t1"]}])
    monkeypatch.setattr(service.repository, "canvases_in_scope", scope_mock)
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=[]))
    asyncio.run(service.search_model("p1", "cli", 25, folder_id="despri"))
    scope_mock.assert_awaited_once_with("p1", ["despri"], None)
    assert seen["ids"] == ["t1"]


# ── Doc 72 r2: navegación sin término + paginación por offset ─────────────────

def test_search_model_pagina_con_offset_y_devuelve_next_por_grupo(monkeypatch):
    seen: dict = {}

    async def _tables(pid, q, limit, schema=None, ids=None, skip=0):
        seen["tables"] = (q, limit, skip)
        return [{"id": f"t{i}", "physicalName": f"T{i}", "logicalName": "x"} for i in range(limit)]   # página llena

    async def _cols(pid, q, limit, fields, extra=None, skip=0):
        seen.setdefault("cols", []).append((fields, skip)); return []
    monkeypatch.setattr(service.repository, "list_tables", _tables)
    monkeypatch.setattr(service.repository, "search_columns_by", _cols)
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "canvases_with_tables", AsyncMock(return_value=[]))
    out = asyncio.run(service.search_model("p1", "", 50, offset=100))
    assert seen["tables"] == (None, 50, 100)                          # sin término → navega; skip = offset
    assert seen["cols"] == [(service.SEARCH_NAME_FIELDS, 100), (service.SEARCH_DEF_FIELDS, 100)]
    assert out["offset"] == 100 and out["next"] == {"tables": 150, "columns": None, "definitions": None}
    assert len(out["tables"]) == 50


def test_overlay_search_paginas_siguientes_no_repiten_altas_del_draft():
    pub = [{"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente"}]
    changes = {"t9": {"op": "upsert", "payload": {"physicalName": "D_CLIENTE_HIST", "logicalName": "hist"}},
               "t1": {"op": "delete"}}
    assert [d["id"] for d in overlay_search(pub, changes, ("physicalName",), "clien", 25)] == ["t9"]
    # página 2: la baja de t1 sigue aplicando, el alta t9 ya salió en la 1.
    assert overlay_search(pub, changes, ("physicalName",), "clien", 25, include_new=False) == []


def test_overlay_search_el_cap_deja_entrar_las_altas_sin_desplazar_publicados():
    pub = [{"id": f"t{i}", "physicalName": f"T{i}"} for i in range(3)]
    changes = {"n1": {"op": "upsert", "payload": {"physicalName": "T_NEW"}}}
    assert len(overlay_search(pub, changes, ("physicalName",), "t", 3)) == 4


def test_contains_sin_termino_exige_contenido_en_el_campo():
    assert service._contains({"description": "Nombre del cliente"}, ("description",), "") is True
    assert service._contains({"description": None}, ("description",), "") is False
    assert service._contains({"physicalName": "X", "logicalName": ""}, ("physicalName", "logicalName"), "") is True

