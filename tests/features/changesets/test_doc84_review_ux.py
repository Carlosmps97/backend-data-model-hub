"""Doc 84 — diffs legibles + avisos de publish específicos (backend).

D1/A1 · Errores del publish con detalle ESTRUCTURADO y en inglés:
  `PublishError.to_detail()` = {code, message, items, next}; los `raise X("msg")`
  existentes siguen valiendo. `_publish_duplicate_items` devuelve la lista
  COMPLETA de choques (tipo, nombre, esquema/tabla, dónde: producción o el propio
  request, y la referencia de producción); `_publish_duplicates` sigue devolviendo
  los mensajes (compat). `_production_refs` completa versión/owner/fecha desde el
  ledger (best-effort). El bloqueo NO cambia: mismo guard, mismo momento.
B1/B3 · `diffdetail`: `projectId`/`physicalNameOverridden`/`logicalTypeOverridden`
  son ruido; el detalle trae `at` (quién/cuándo lo pinta el front con el owner).
B2/B4 · `build_diff_tree`: carpeta/canvas/esquema/proyecto tocados llevan su
  `change` EN el árbol (+ `entityId` en esquemas) y la sección `structure` marca
  `inTree`; las columnas llevan `dataType`.
C1/C2 · Compare: `compose_versions_range(..., headers)` estampa `lastVersion` por
  entidad, los buckets lo exponen y `compare_versions` arma `tree/orphans/structure`.
A3 · `diff()` expone `lastChangeAt`.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service
from app.features.changesets.diffdetail import entity_detail
from app.features.changesets.validation import (
    CrossProjectError, DuplicateEntityError, InvalidPayloadError, PublishError,
    SchemaInUseError, duplicate_error, duplicate_hit)


# ── D1 · PublishError ──────────────────────────────────────────────────────


def test_publish_error_to_detail_and_compat():
    exc = DuplicateEntityError("Publish blocked: 1 duplicate name",
                               items=[{"name": "X"}], next_step="Fix it")
    assert isinstance(exc, PublishError) and isinstance(exc, ValueError)
    assert str(exc) == "Publish blocked: 1 duplicate name"
    assert exc.to_detail() == {"code": "duplicate_names", "message": "Publish blocked: 1 duplicate name",
                               "items": [{"name": "X"}], "next": "Fix it"}
    # Compat: los raise existentes sin items siguen funcionando.
    assert SchemaInUseError("in use").items == [] and SchemaInUseError("x").code == "schema_in_use"
    assert InvalidPayloadError("x").code == "invalid_changes"
    assert CrossProjectError("x").code == "cross_project"
    with pytest.raises(DuplicateEntityError):
        raise DuplicateEntityError("plain")


def test_duplicate_hit_expone_el_doc_que_choca_y_duplicate_error_sigue_igual():
    published = [{"id": "tx", "physicalName": "CLIENTE", "schema": "core"}]
    payload = {"physicalName": "cliente", "schema": "CORE"}
    hit = duplicate_hit("canonical_tables", "t9", payload, published, {})
    assert hit["hit"]["id"] == "tx"
    assert hit["message"] == "Table cliente already exists (schema core)"
    assert duplicate_error("canonical_tables", "t9", payload, published, {}) == hit["message"]
    assert duplicate_hit("canonical_tables", "t9", {"physicalName": "OTRA"}, published, {}) is None


# ── A1 · _publish_duplicate_items ──────────────────────────────────────────


def _pub_by_collection(monkeypatch, data: dict[str, list[dict]]):
    async def _pub(collection, flt=None, **kwargs):
        return data.get(collection, [])
    monkeypatch.setattr(service.repository, "published", _pub)


def test_duplicate_items_tabla_contra_produccion(monkeypatch):
    _pub_by_collection(monkeypatch, {
        "canonical_tables": [{"id": "tx", "physicalName": "AACARGATABLA01", "logicalName": "c", "schema": "A_DB"}],
    })
    changes = {"canonical_tables": {"t9": {"op": "upsert", "payload": {
        "physicalName": "aacargatabla01", "logicalName": "n", "schema": "A_DB"}}}}
    items = asyncio.run(service._publish_duplicate_items("p1", changes))
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "table" and it["collection"] == "canonical_tables" and it["entityId"] == "t9"
    assert it["name"] == "aacargatabla01" and it["schema"] == "A_DB" and it["where"] == "production"
    assert it["existing"] == {"id": "tx", "name": "AACARGATABLA01", "schema": "A_DB"}
    assert it["message"] == "Table aacargatabla01 already exists (schema A_DB)"
    # Compat: mismos mensajes de siempre.
    assert asyncio.run(service._publish_duplicates("p1", changes)) == [it["message"]]


def test_duplicate_items_dentro_del_propio_request(monkeypatch):
    _pub_by_collection(monkeypatch, {})
    changes = {"canonical_tables": {
        "t8": {"op": "upsert", "payload": {"physicalName": "M_X", "logicalName": "a", "schema": "S"}},
        "t9": {"op": "upsert", "payload": {"physicalName": "m_x", "logicalName": "b", "schema": "S"}},
    }}
    items = asyncio.run(service._publish_duplicate_items("p1", changes))
    # Una fila por cada tabla del request que choca con la otra (casing distinto
    # ⇒ mensajes distintos; misma semántica de siempre), ambas «request».
    assert {it["entityId"] for it in items} == {"t8", "t9"}
    assert all(it["where"] == "request" for it in items)
    assert {it["existing"]["id"] for it in items} == {"t8", "t9"}
    assert all(it["existing"]["id"] != it["entityId"] for it in items)


def test_duplicate_items_columna_con_nombre_de_tabla(monkeypatch):
    _pub_by_collection(monkeypatch, {
        "canonical_columns": [{"id": "c1", "tableId": "t1", "physicalName": "ID_CTA", "dataType": "BIGINT"}],
        "canonical_tables": [{"id": "t1", "physicalName": "M_CUENTA", "schema": "core"}],
    })
    changes = {"canonical_columns": {"c9": {"op": "upsert", "payload": {
        "tableId": "t1", "physicalName": "id_cta", "logicalName": "x", "dataType": "STRING"}}}}
    items = asyncio.run(service._publish_duplicate_items("p1", changes))
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "column" and it["name"] == "id_cta"
    assert it["tableId"] == "t1" and it["tableName"] == "M_CUENTA"
    assert it["existing"] == {"id": "c1", "name": "ID_CTA", "tableId": "t1", "tableName": "M_CUENTA"}
    assert it["message"] == "Column id_cta already exists in this table"


def test_duplicate_items_esquema(monkeypatch):
    _pub_by_collection(monkeypatch, {"schemas": [{"id": "s1", "name": "CORE"}]})
    changes = {"schemas": {"s9": {"op": "upsert", "payload": {"name": "core", "kind": "tables"}}}}
    items = asyncio.run(service._publish_duplicate_items("p1", changes))
    assert len(items) == 1
    assert items[0]["kind"] == "schema" and items[0]["existing"] == {"id": "s1", "name": "CORE"}


def test_production_refs_completa_version_owner_fecha(monkeypatch):
    items = [{"collection": "canonical_tables", "kind": "table", "entityId": "t9", "name": "X",
              "where": "production", "message": "m", "existing": {"id": "tx", "name": "X", "schema": "S"}},
             {"collection": "canonical_tables", "kind": "table", "entityId": "t8", "name": "Y",
              "where": "request", "message": "m2", "existing": {"id": "t7", "name": "Y", "schema": "S"}}]
    monkeypatch.setattr(service.repository, "entity_changes", AsyncMock(return_value=[
        {"csId": "cs-a", "collection": "canonical_tables", "entityId": "tx", "op": "upsert",
         "payload": {"physicalName": "X"}, "at": "2026-09-10T01:09:17+00:00", "before": None,
         "beforeAt": "2026-09-10T02:20:00+00:00"},
        {"csId": "cs-b", "collection": "canonical_tables", "entityId": "tx", "op": "upsert",
         "payload": {"physicalName": "X"}, "at": "2026-09-11T01:00:00+00:00",
         "before": {"physicalName": "X"}, "beforeAt": "2026-09-11T02:00:00+00:00"},
    ]))
    monkeypatch.setattr(service.repository, "changesets_by_ids", AsyncMock(return_value={
        "cs-a": {"id": "cs-a", "status": "approved", "appliedAt": "2026-09-10T02:20:00+00:00",
                 "owner": "admin", "versionLabel": "v2", "title": "ac", "approvals": {}},
        "cs-b": {"id": "cs-b", "status": "approved", "appliedAt": "2026-09-11T02:00:00+00:00",
                 "owner": "carla", "versionLabel": "v5", "title": "x", "approvals": {}},
    }))
    asyncio.run(service._production_refs(items))
    # La ÚLTIMA versión publicada que la tocó.
    assert items[0]["existing"] == {"id": "tx", "name": "X", "schema": "S",
                                    "version": "v5", "owner": "carla", "publishedAt": "2026-09-11T02:00:00+00:00"}
    # Un choque DENTRO del request no se enriquece (no está en producción).
    assert "version" not in items[1]["existing"]


def test_production_refs_es_best_effort(monkeypatch):
    items = [{"collection": "canonical_tables", "kind": "table", "entityId": "t9", "name": "X",
              "where": "production", "message": "m", "existing": {"id": "tx", "name": "X", "schema": "S"}}]
    monkeypatch.setattr(service.repository, "entity_changes", AsyncMock(side_effect=RuntimeError("sin BD")))
    asyncio.run(service._production_refs(items))
    assert items[0]["existing"]["version"] is None and items[0]["existing"]["publishedAt"] is None


def test_apply_and_finalize_levanta_duplicados_con_items_en_ingles(monkeypatch):
    transitions: list[tuple[str, str | None]] = []

    async def fake_transition(cs_id, from_status, fields, expect=None):
        transitions.append((from_status, fields.get("status")))
        return {"id": cs_id, **fields}

    monkeypatch.setattr(service.repository, "transition", fake_transition)
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={
        "canonical_tables": {"t9": {"op": "upsert", "payload": {
            "projectId": "p1", "physicalName": "cta", "logicalName": "cuenta", "schema": "CORE"}}},
    }))
    _pub_by_collection(monkeypatch, {
        "canonical_tables": [{"id": "t1", "projectId": "p1", "physicalName": "CTA", "logicalName": "cuenta", "schema": "core"}],
    })
    monkeypatch.setattr(service.repository, "entity_changes", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "changesets_by_ids", AsyncMock(return_value={}))
    apply_mock = AsyncMock()
    monkeypatch.setattr(service.repository, "apply_changes", apply_mock)

    with pytest.raises(DuplicateEntityError) as exc:
        asyncio.run(service._apply_and_finalize(
            {"id": "c1", "projectId": "p1"}, {"status": "approved"}, submitted_at="2026-01-01T00:00:00+00:00"))
    err = exc.value
    assert str(err).startswith("Publish blocked: 1 table name already exists in production")
    assert "Table cta already exists (schema core)" in str(err)
    assert err.items[0]["kind"] == "table" and err.items[0]["where"] == "production"
    assert err.items[0]["existing"]["version"] is None          # sin ledger: best-effort
    assert err.next and "withdraw" in err.next
    assert ("approved", "submitted") in transitions               # revert del claim
    apply_mock.assert_not_called()                                # producción intacta


def test_duplicates_message_cuenta_por_tipo():
    items = [
        {"kind": "table", "where": "production", "message": "Table A already exists"},
        {"kind": "table", "where": "production", "message": "Table B already exists"},
        {"kind": "schema", "where": "production", "message": "Schema S already exists"},
        {"kind": "column", "where": "request", "message": "Column C already exists in this table"},
    ]
    msg = service._duplicates_message(items)
    assert msg.startswith("Publish blocked: 2 table names and 1 schema name already exist in production")
    assert "1 name is repeated within the request" in msg
    assert "Table A already exists" in msg and "+1 more" in msg


# ── B1/B3 · diffdetail ─────────────────────────────────────────────────────

RES = {"udp": {}, "domains": {}, "tables": {}, "columns": {}, "projects": {}, "folders": {}}


def test_flags_internos_de_naming_y_projectId_son_ruido():
    change = {"op": "upsert", "at": "2026-09-10T06:00:07+00:00", "payload": {
        "physicalName": "COL", "logicalName": "col", "dataType": "VARCHAR(64)", "tableId": "t1",
        "projectId": "p1", "physicalNameOverridden": True, "logicalTypeOverridden": False,
        "typeOverridden": False, "ordinal": 0}}
    d = entity_detail("canonical_columns", "c1", change, None, RES)
    keys = {f["key"] for f in d["fields"]}
    assert "projectId" not in keys and "physicalNameOverridden" not in keys
    assert "logicalTypeOverridden" not in keys and "typeOverridden" not in keys
    assert {"physicalName", "logicalName", "dataType", "ordinal"} <= keys
    assert d["at"] == "2026-09-10T06:00:07+00:00"


# ── B2/B4 · build_diff_tree ────────────────────────────────────────────────


def test_build_diff_tree_marca_estructura_en_el_arbol_y_tipo_de_columna():
    collections = {
        "canonical_tables": {"added": [{"id": "t1", "name": "AACARGATABLA04"}], "edited": [], "deleted": []},
        "canonical_columns": {"added": [{"id": "c1", "name": "CODCLAVEPARTYCLI"}], "edited": [], "deleted": []},
        "folders": {"added": [{"id": "f-new", "name": "A_SA_CARGA_SUBJECT01"}], "edited": [], "deleted": []},
        "subject_areas": {"added": [], "edited": [{"id": "sa1", "name": "A_DG_CARGA_DIAGRAMA02"}], "deleted": []},
        "schemas": {"added": [{"id": "s-new", "name": "A_DB_CARGA_ESQUEMA01"}], "edited": [], "deleted": []},
        "projects": {"added": [], "edited": [], "deleted": []},
    }
    changes = {
        "canonical_tables": {"t1": {"op": "upsert", "payload": {"physicalName": "AACARGATABLA04", "schema": "A_DB_CARGA_ESQUEMA01"}}},
        "canonical_columns": {"c1": {"op": "upsert", "payload": {"physicalName": "CODCLAVEPARTYCLI", "tableId": "t1", "dataType": "VARCHAR(128)"}}},
        "folders": {"f-new": {"op": "upsert", "payload": {"name": "A_SA_CARGA_SUBJECT01"}}},
        "subject_areas": {"sa1": {"op": "upsert", "payload": {"name": "A_DG_CARGA_DIAGRAMA02", "tableIds": ["t1"]}}},
        "schemas": {"s-new": {"op": "upsert", "payload": {"name": "A_DB_CARGA_ESQUEMA01"}}},
    }
    published = {"canonical_tables": [], "canonical_columns": []}
    sas = [{"id": "sa1", "name": "A_DG_CARGA_DIAGRAMA02", "projectId": "p1", "folderId": "f-new", "tableIds": ["t1"]}]
    out = service.build_diff_tree(collections, changes, published, sas,
                                  [{"id": "p1", "name": "MODELO DDV"}],
                                  [{"id": "f-new", "name": "A_SA_CARGA_SUBJECT01"}])
    proj = out["tree"][0]
    assert proj.get("change") is None
    folder = proj["children"][0]
    assert folder["type"] == "folder" and folder["change"] == "added" and folder["entityId"] == "f-new"
    canvas = folder["children"][0]
    assert canvas["change"] == "edited" and canvas["entityId"] == "sa1"
    schema = canvas["children"][0]
    assert schema["change"] == "added" and schema["entityId"] == "s-new"
    col = schema["children"][0]["children"][0]
    assert col["type"] == "column" and col["dataType"] == "VARCHAR(128)"
    by = {(s["type"], s["id"]): s for s in out["structure"]}
    assert by[("folders", "f-new")]["inTree"] is True
    assert by[("subject_areas", "sa1")]["inTree"] is True
    assert by[("schemas", "s-new")]["inTree"] is True


def test_build_diff_tree_estructura_fuera_del_arbol_no_marca_inTree():
    collections = {"folders": {"added": [{"id": "f-solo", "name": "Vacía"}], "edited": [], "deleted": []}}
    changes = {"folders": {"f-solo": {"op": "upsert", "payload": {"name": "Vacía"}}}}
    out = service.build_diff_tree(collections, changes, {"canonical_tables": [], "canonical_columns": []}, [], [], [])
    assert out["tree"] == []
    assert out["structure"][0]["inTree"] is False


# ── A3 · diff().lastChangeAt ───────────────────────────────────────────────


def test_diff_expone_last_change_at(monkeypatch):
    cs = {"id": "cs1", "projectId": "P1", "status": "submitted", "owner": "ana", "createdAt": "t0"}
    changes = {"canonical_tables": {
        "t1": {"op": "upsert", "at": "2026-09-10T01:44:19+00:00", "payload": {"physicalName": "A", "projectId": "P1"}},
        "t2": {"op": "upsert", "at": "2026-09-10T06:05:59+00:00", "payload": {"physicalName": "B", "projectId": "P1"}},
    }}
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=cs))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "deleted_at", AsyncMock(return_value={}))   # doc 100
    out = asyncio.run(service.diff("cs1"))
    assert out["lastChangeAt"] == "2026-09-10T06:05:59+00:00"


# ── C1/C2 · Compare ────────────────────────────────────────────────────────


def _wire_catalogs(monkeypatch):
    async def _empty(project_id):
        return []
    monkeypatch.setattr("app.features.udp.repository.list_udp", _empty)
    monkeypatch.setattr("app.features.domains.repository.list_domains", _empty)


HDRS = {
    "v1": {"id": "v1", "projectId": "p1", "status": "approved", "appliedAt": "2026-01-01", "versionLabel": "v1"},
    "v3": {"id": "v3", "projectId": "p1", "status": "approved", "appliedAt": "2026-03-01", "versionLabel": "v3"},
}
CHANGES = {
    "v2": {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "x", "before": None,
                                       "payload": {"physicalName": "NUEVA", "schema": "core"}}},
           "canonical_columns": {"c1": {"op": "upsert", "beforeAt": "x", "before": None,
                                        "payload": {"physicalName": "ID", "tableId": "t1", "dataType": "BIGINT"}}}},
    "v3": {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "y",
                                       "before": {"id": "t1", "physicalName": "NUEVA", "schema": "core"},
                                       "payload": {"physicalName": "NUEVA2", "schema": "core"}}}},
}
AFTER_V1 = [
    {"id": "v3", "appliedAt": "2026-03-01", "versionLabel": "v3", "owner": "carla", "title": "tres"},
    {"id": "v2", "appliedAt": "2026-02-01", "versionLabel": "v2", "owner": "admin", "title": "dos"},
]


def test_compose_estampa_last_version_por_entidad():
    composed, _missing = service.compose_versions_range(
        [CHANGES["v2"], CHANGES["v3"]], headers=[AFTER_V1[1], AFTER_V1[0]])
    t1 = composed[("canonical_tables", "t1")]
    assert t1["lastVersion"] == {"id": "v3", "versionLabel": "v3", "owner": "carla", "appliedAt": "2026-03-01"}
    c1 = composed[("canonical_columns", "c1")]
    assert c1["lastVersion"]["versionLabel"] == "v2"
    # Sin headers (compat) no hay lastVersion.
    composed2, _ = service.compose_versions_range([CHANGES["v2"]])
    assert composed2[("canonical_tables", "t1")].get("lastVersion") is None


def test_compare_versions_trae_arbol_y_last_version(monkeypatch):
    _wire_catalogs(monkeypatch)
    monkeypatch.setattr(service.repository, "get", AsyncMock(side_effect=lambda cid: HDRS.get(cid)))
    monkeypatch.setattr(service.repository, "applied_after",
                        AsyncMock(side_effect=lambda pid, at: [v for v in AFTER_V1 if v["appliedAt"] > at]))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(side_effect=lambda cid, cols=None: CHANGES.get(cid, {})))
    data = {
        "subject_areas": [{"id": "sa1", "name": "Canvas A", "projectId": "p1", "folderId": None, "tableIds": ["t1"]}],
        "projects": [{"id": "p1", "name": "Proyecto"}],
        "folders": [],
    }

    async def _pub(collection, flt=None, **kwargs):
        return list(data.get(collection, []))
    monkeypatch.setattr(service.repository, "published", _pub)

    out = asyncio.run(service.compare_versions("v1", "v3"))
    added = out["collections"]["canonical_tables"]["added"]
    assert [e["id"] for e in added] == ["t1"]
    assert added[0]["lastVersion"]["versionLabel"] == "v3" and added[0]["lastVersion"]["owner"] == "carla"
    assert out["collections"]["canonical_columns"]["added"][0]["lastVersion"]["versionLabel"] == "v2"
    proj = out["tree"][0]
    assert proj["name"] == "Proyecto"
    canvas = proj["children"][0]["children"][0]
    assert canvas["name"] == "Canvas A"
    table = canvas["children"][0]["children"][0]
    assert table["name"] == "NUEVA2" and table["change"] == "added"
    assert table["children"][0]["dataType"] == "BIGINT"
    assert out["orphans"] == [] and out["structure"] == []
