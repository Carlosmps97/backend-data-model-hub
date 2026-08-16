"""Grabación EN LOTE de cambios (`add_changes_bulk` + `set_changes_bulk`).

La cascada de borrar tabla (y crear tabla desde fuentes) grababa N cambios con
N requests secuenciales — a 4k columnas eso son minutos de espera. El lote
graba todos los cambios del changeset en 1-2 round-trips, con la MISMA
semántica que `add_change` repetido: validación por ítem, unicidad evaluada en
orden (un delete del lote libera el nombre para un upsert posterior), owner y
draft guards, y protocolo de compensación si un submit gana la carrera.

Servicio con repository mockeado (patrón test_add_change_duplicates) y
repository con db fake (patrón test_repository_impact).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import repository, service
from app.features.changesets.repository import CHANGES_COLL, COLL, change_key
from app.features.changesets.validation import DuplicateEntityError, InvalidPayloadError, NameTooLongError


# ── Servicio: add_changes_bulk ─────────────────────────────────────────────


def _mock_repo(monkeypatch, published=None, changes_map=None, max_length=150):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "draft", "owner": "ana"}))
    published_mock = AsyncMock(return_value=published or [])
    monkeypatch.setattr(service.repository, "published", published_mock)
    changes_map_mock = AsyncMock(return_value=changes_map or {})
    monkeypatch.setattr(service.repository, "changes_map", changes_map_mock)
    bulk = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", bulk)
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"maxLength": max_length}))
    return bulk, published_mock, changes_map_mock


def _delete(collection: str, eid: str) -> dict:
    return {"collection": collection, "entityId": eid, "op": "delete", "payload": None}


def _col_upsert(eid: str, name: str, table_id: str = "t1", ordinal: int = 0) -> dict:
    return {"collection": "canonical_columns", "entityId": eid, "op": "upsert",
            "payload": {"tableId": table_id, "physicalName": name, "logicalName": name.lower(),
                        "dataType": "BIGINT", "ordinal": ordinal}}


def test_bulk_deletes_graba_todo_en_un_solo_lote(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch)
    items = [_delete("views", "v1"), _delete("relationships", "r1"),
             _delete("canonical_columns", "col1"), _delete("canonical_columns", "col2"),
             _delete("canonical_tables", "t1")]
    res = asyncio.run(service.add_changes_bulk("c1", "ana", items))
    assert isinstance(res, dict)
    bulk.assert_awaited_once()
    sent = bulk.await_args.args[1]
    assert [(i["collection"], i["entityId"], i["op"]) for i in sent] == [
        ("views", "v1", "delete"), ("relationships", "r1", "delete"),
        ("canonical_columns", "col1", "delete"), ("canonical_columns", "col2", "delete"),
        ("canonical_tables", "t1", "delete")]


def test_bulk_solo_deletes_no_consulta_publicado(monkeypatch):
    # El caso caliente (cascada de borrado) no debe pagar queries de unicidad:
    # los deletes no chequean duplicados ni longitud de nombre.
    bulk, published, changes_map = _mock_repo(monkeypatch)
    asyncio.run(service.add_changes_bulk(
        "c1", "ana", [_delete("canonical_columns", f"col{i}") for i in range(50)]))
    bulk.assert_awaited_once()
    published.assert_not_called()
    changes_map.assert_not_called()


def test_bulk_sin_items_devuelve_cs_sin_escribir(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch)
    res = asyncio.run(service.add_changes_bulk("c1", "ana", []))
    assert isinstance(res, dict)
    bulk.assert_not_called()


def test_bulk_forbidden_si_no_es_owner(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch)
    res = asyncio.run(service.add_changes_bulk("c1", "bob", [_delete("canonical_tables", "t1")]))
    assert res == "forbidden"
    bulk.assert_not_called()


def test_bulk_locked_si_ya_no_esta_en_draft(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch)
    bulk.return_value = None  # set_changes_bulk perdió el draft
    res = asyncio.run(service.add_changes_bulk("c1", "ana", [_delete("canonical_tables", "t1")]))
    assert res == "locked"


def test_bulk_changeset_inexistente_none(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch)
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=None))
    res = asyncio.run(service.add_changes_bulk("c9", "ana", [_delete("canonical_tables", "t1")]))
    assert res is None
    bulk.assert_not_called()


def test_bulk_payload_invalido_no_escribe_nada(monkeypatch):
    # Un solo ítem malformado tumba TODO el lote ANTES de escribir: sin esto,
    # el changeset quedaría con una cascada a medias (peor que el loop viejo).
    bulk, _, _ = _mock_repo(monkeypatch)
    items = [_col_upsert("c1", "ID_CTA"),
             {"collection": "canonical_columns", "entityId": "c2", "op": "upsert",
              "payload": {"logicalName": "sin tableId"}}]
    with pytest.raises(InvalidPayloadError, match="canonical_columns/c2"):
        asyncio.run(service.add_changes_bulk("c1", "ana", items))
    bulk.assert_not_called()


def test_bulk_upserts_tabla_mas_columnas_pasa(monkeypatch):
    # Forma de "crear tabla desde fuentes": 1 tabla + sus columnas en un lote.
    bulk, _, _ = _mock_repo(monkeypatch)
    items = [{"collection": "canonical_tables", "entityId": "t9", "op": "upsert",
              "payload": {"physicalName": "NUEVA", "logicalName": "nueva", "schema": "core"}},
             _col_upsert("c1", "ID_CTA", "t9", 0), _col_upsert("c2", "SALDO", "t9", 1)]
    res = asyncio.run(service.add_changes_bulk("c1", "ana", items))
    assert isinstance(res, dict)
    sent = bulk.await_args.args[1]
    assert [i["entityId"] for i in sent] == ["t9", "c1", "c2"]


def test_bulk_dup_intra_lote_no_escribe_nada(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch)
    items = [_col_upsert("c1", "ID_CTA"), _col_upsert("c2", "id_cta", ordinal=1)]
    with pytest.raises(DuplicateEntityError, match="already exists in this table"):
        asyncio.run(service.add_changes_bulk("c1", "ana", items))
    bulk.assert_not_called()


def test_bulk_dup_contra_publicado(monkeypatch):
    bulk, _, _ = _mock_repo(
        monkeypatch,
        published=[{"id": "pub1", "tableId": "t1", "physicalName": "ID_CTA",
                    "logicalName": "id", "dataType": "BIGINT"}])
    with pytest.raises(DuplicateEntityError):
        asyncio.run(service.add_changes_bulk("c1", "ana", [_col_upsert("c9", "id_cta")]))
    bulk.assert_not_called()


def test_bulk_delete_del_lote_libera_el_nombre(monkeypatch):
    # Semántica secuencial del loop viejo: borrar la columna publicada y crear
    # otra homónima EN EL MISMO LOTE es válido (el pending evoluciona en orden).
    bulk, _, _ = _mock_repo(
        monkeypatch,
        published=[{"id": "pub1", "tableId": "t1", "physicalName": "ID_CTA",
                    "logicalName": "id", "dataType": "BIGINT"}])
    items = [_delete("canonical_columns", "pub1"), _col_upsert("c9", "ID_CTA")]
    res = asyncio.run(service.add_changes_bulk("c1", "ana", items))
    assert isinstance(res, dict)
    bulk.assert_awaited_once()


def test_bulk_dedupe_ultimo_gana(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch)
    items = [_col_upsert("c1", "ID_CTA"), _delete("canonical_columns", "c1")]
    asyncio.run(service.add_changes_bulk("c1", "ana", items))
    sent = bulk.await_args.args[1]
    assert len(sent) == 1
    assert sent[0]["op"] == "delete" and sent[0]["entityId"] == "c1"


def test_bulk_nombre_muy_largo_bloquea(monkeypatch):
    bulk, _, _ = _mock_repo(monkeypatch, max_length=10)
    with pytest.raises(NameTooLongError, match="10-character"):
        asyncio.run(service.add_changes_bulk(
            "c1", "ana", [_col_upsert("c9", "NOMBREDEMASIADOLARGO")]))
    bulk.assert_not_called()


def test_bulk_relationship_se_persiste_normalizada_v2(monkeypatch):
    # Paridad con add_change: el payload de relationships se graba con el dump
    # v2 normalizado (parent/child + pairs), nunca el shape crudo del cliente.
    # Doc 47: el guard N=N exige que pA sea la llave COMPLETA del padre tA.
    bulk, _, _ = _mock_repo(monkeypatch, published=[
        {"id": "pA", "tableId": "tA", "physicalName": "PA", "logicalName": "pa",
         "dataType": "BIGINT", "ordinal": 0, "isPrimaryKey": True}])
    items = [{"collection": "relationships", "entityId": "r1", "op": "upsert",
              "payload": {"parentTableId": "tA", "childTableId": "tB",
                          "pairs": [{"parentColumnId": "pA", "childColumnId": "cB"}]}}]
    asyncio.run(service.add_changes_bulk("c1", "ana", items))
    sent = bulk.await_args.args[1]
    p = sent[0]["payload"]
    assert p["parentTableId"] == "tA" and p["childTableId"] == "tB"
    assert p["pairs"][0]["roleName"] is None          # dump completo del modelo
    assert p["parentCardinality"] == "one" and p["identifying"] is False


# ── Repository: set_changes_bulk (db fake) ─────────────────────────────────


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, n):
        return self._docs


class _FakeParentColl:
    def __init__(self, state):
        self.s = state

    async def find_one_and_update(self, flt, update, return_document=None):
        self.s["touch_calls"].append((flt, update))
        return dict(self.s["parent"]) if self.s["draft_at_touch"] else None

    async def find_one(self, flt, projection=None):
        self.s["recheck_calls"].append(flt)
        return {"_id": flt.get("_id")} if self.s["draft_at_recheck"] else None


class _FakeChangesColl:
    def __init__(self, state):
        self.s = state

    def find(self, flt, projection=None):
        ids = (flt.get("_id") or {}).get("$in") or []
        return _FakeCursor([dict(d) for d in self.s["prev_docs"] if d["_id"] in ids])

    async def bulk_write(self, ops, ordered=True):
        self.s["bulk_calls"].append(list(ops))
        return None


class _FakeDb:
    def __init__(self, state):
        self.s = state

    def __getitem__(self, name):
        if name == COLL:
            return _FakeParentColl(self.s)
        if name == CHANGES_COLL:
            return _FakeChangesColl(self.s)
        raise KeyError(name)


def _patch_db(monkeypatch, *, draft_at_touch=True, draft_at_recheck=True, prev_docs=()):
    state = {
        "parent": {"_id": "c1", "title": "t", "owner": "ana", "status": "draft",
                   "createdAt": "2026-08-13T00:00:00+00:00", "updatedAt": "2026-08-13T00:00:00+00:00"},
        "draft_at_touch": draft_at_touch, "draft_at_recheck": draft_at_recheck,
        "prev_docs": list(prev_docs),
        "touch_calls": [], "recheck_calls": [], "bulk_calls": [],
    }

    async def _get_db():
        return _FakeDb(state)

    monkeypatch.setattr(repository, "get_db", _get_db)
    return state


def _items(n=3, op="delete", collection="canonical_columns"):
    out = []
    for i in range(n):
        it = {"collection": collection, "entityId": f"e{i}", "op": op}
        if op != "delete":
            it["payload"] = {"tableId": "t1", "physicalName": f"C{i}", "logicalName": f"c{i}",
                             "dataType": "BIGINT", "ordinal": i}
        out.append(it)
    return out


def test_set_changes_bulk_graba_docs_y_toca_al_padre_una_vez(monkeypatch):
    state = _patch_db(monkeypatch)
    res = asyncio.run(repository.set_changes_bulk("c1", _items(3)))
    assert isinstance(res, dict) and res["id"] == "c1"
    # UN solo touch del padre, con guard de draft (mismo filtro que set_change).
    assert len(state["touch_calls"]) == 1
    assert state["touch_calls"][0][0] == {"_id": "c1", "status": "draft"}
    # UN solo lote de escritura: ReplaceOne upsert por _id determinista.
    assert len(state["bulk_calls"]) == 1
    ops = state["bulk_calls"][0]
    assert [type(o).__name__ for o in ops] == ["ReplaceOne"] * 3
    docs = [o._doc for o in ops]
    assert [o._filter["_id"] for o in ops] == [change_key("c1", "canonical_columns", f"e{i}") for i in range(3)]
    assert all(o._upsert for o in ops)
    assert all(d["csId"] == "c1" and d["op"] == "delete" and d["wtoken"] for d in docs)
    assert all("payload" not in d for d in docs)          # delete no lleva payload
    # Todos los cambios del lote comparten wtoken (identidad de ESTA escritura).
    assert len({d["wtoken"] for d in docs}) == 1


def test_set_changes_bulk_upsert_lleva_payload(monkeypatch):
    state = _patch_db(monkeypatch)
    asyncio.run(repository.set_changes_bulk("c1", _items(2, op="upsert")))
    docs = [o._doc for o in state["bulk_calls"][0]]
    assert all(d["op"] == "upsert" and d["payload"]["physicalName"] for d in docs)


def test_set_changes_bulk_padre_sin_draft_no_escribe(monkeypatch):
    state = _patch_db(monkeypatch, draft_at_touch=False)
    res = asyncio.run(repository.set_changes_bulk("c1", _items(3)))
    assert res is None
    assert state["bulk_calls"] == []


def test_set_changes_bulk_compensa_si_pierde_el_draft(monkeypatch):
    # Un submit gana la carrera entre el touch y el re-check: la escritura
    # propia se COMPENSA — el cambio con previo legítimo se RESTAURA y el
    # nuevo se borra, siempre filtrando por wtoken (no pisar re-ediciones).
    key0 = change_key("c1", "canonical_columns", "e0")
    prev = {"_id": key0, "csId": "c1", "collection": "canonical_columns",
            "entityId": "e0", "op": "upsert", "payload": {"x": 1},
            "at": "2026-08-13T00:00:00+00:00", "wtoken": "viejo"}
    state = _patch_db(monkeypatch, draft_at_recheck=False, prev_docs=[prev])
    res = asyncio.run(repository.set_changes_bulk("c1", _items(2)))
    assert res is None
    assert len(state["bulk_calls"]) == 2                  # escritura + compensación
    comp = state["bulk_calls"][1]
    token = state["bulk_calls"][0][0]._doc["wtoken"]
    by_kind = {type(o).__name__: o for o in comp}
    assert set(by_kind) == {"ReplaceOne", "DeleteOne"}
    restore = by_kind["ReplaceOne"]
    assert restore._filter == {"_id": key0, "wtoken": token}
    assert restore._doc["wtoken"] == "viejo"              # restaura el cambio previo
    assert by_kind["DeleteOne"]._filter == {"_id": change_key("c1", "canonical_columns", "e1"),
                                            "wtoken": token}


def test_set_changes_bulk_dedupe_por_entidad_ultimo_gana(monkeypatch):
    state = _patch_db(monkeypatch)
    items = [{"collection": "canonical_columns", "entityId": "e0", "op": "upsert",
              "payload": {"tableId": "t1", "physicalName": "A", "logicalName": "a",
                          "dataType": "BIGINT", "ordinal": 0}},
             {"collection": "canonical_columns", "entityId": "e0", "op": "delete"}]
    asyncio.run(repository.set_changes_bulk("c1", items))
    ops = state["bulk_calls"][0]
    assert len(ops) == 1 and ops[0]._doc["op"] == "delete"


def test_set_changes_bulk_trocea_lotes_grandes(monkeypatch):
    # El fast-path del adaptador manda arrays por unnest: lotes acotados a 1000
    # (mismo tamaño que la carga Erwin) para no armar statements desmedidos.
    state = _patch_db(monkeypatch)
    asyncio.run(repository.set_changes_bulk("c1", _items(2500)))
    assert [len(c) for c in state["bulk_calls"]] == [1000, 1000, 500]


def test_set_changes_bulk_items_vacios_none(monkeypatch):
    state = _patch_db(monkeypatch)
    res = asyncio.run(repository.set_changes_bulk("c1", []))
    assert res is None
    assert state["touch_calls"] == [] and state["bulk_calls"] == []
