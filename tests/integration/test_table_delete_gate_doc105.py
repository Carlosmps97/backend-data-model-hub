"""Doc 105 (A5 del doc 102): un draft que borra una tabla dejando columnas,
relaciones o vistas vivas que la referencian publicaba referencias huérfanas
(el Reporting mostraba «?» y nombres de tablas borradas). El front las borra en
la misma operación; por API (o con una cascada que se cortó) no. El publish lo
bloquea como el esquema en uso: 409, producción intacta, sigue en revisión."""
from __future__ import annotations

from tests.integration.conftest import publish


def _submit(api, cs):
    api("ana").post(f"/api/changesets/{cs}/submit", {"title": "t", "reviewers": ["beto"]})


def test_borrar_una_tabla_con_dependencias_vivas_no_publica(api, world, fake_db):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_tables", world["t1"], None, op="delete")      # sólo la tabla (por API)
    _submit(api, cs)
    status, body = api("beto").call("POST", f"/api/changesets/{cs}/review", {"decision": "approve"})
    assert status == 409, body
    detail = body["detail"]
    assert detail["code"] == "table_in_use"
    text = " ".join(i["message"] for i in detail["items"])
    assert "M_CLIENTE" in text and "column" in text and "relationship" in text and "view" in text
    assert fake_db.raw["canonical_tables"].find_one({"_id": world["t1"]})["flgactive"] is not False
    assert fake_db.raw["changesets"].find_one({"_id": cs})["status"] == "submitted"


def test_borrar_la_tabla_con_toda_su_cascada_publica(api, world, fake_db):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.bulk(cs, [
        {"collection": "views", "entityId": world["view"], "op": "delete"},
        {"collection": "relationships", "entityId": world["rel"], "op": "delete"},
        {"collection": "canonical_columns", "entityId": world["c_a"], "op": "delete"},
        {"collection": "canonical_columns", "entityId": world["c_b"], "op": "delete"},
    ])
    ana.change(cs, "canonical_tables", world["t1"], None, op="delete")
    assert publish(api, cs)["appliedAt"]
    assert fake_db.raw["canonical_tables"].find_one({"_id": world["t1"]})["flgactive"] is False


def test_una_columna_mudada_a_otra_tabla_no_bloquea(api, world):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    col = next(c for c in ana.get(f"/api/changesets/{cs}/effective/canonical_columns?tableId={world['t2']}"))
    ana.bulk(cs, [
        {"collection": "canonical_columns", "entityId": col["id"], "op": "upsert",
         "payload": {**{k: v for k, v in col.items() if k != "id"}, "tableId": world["t1"], "ordinal": 5,
                     "physicalName": "CODCLIENTE_CTA", "logicalName": "Codigo Cliente Cuenta",
                     "isForeignKey": False}},
        {"collection": "relationships", "entityId": world["rel"], "op": "delete"},
    ])
    ana.change(cs, "canonical_tables", world["t2"], None, op="delete")
    assert publish(api, cs)["appliedAt"]


def test_una_relacion_legacy_hacia_la_tabla_borrada_tambien_bloquea(api, world, fake_db):
    """Revisión R1: la forma legacy v1 (`sourceTableId`/`targetTableId`, sin
    parent/child) que `_effective_docs` y el Reporting siguen contemplando no
    la veía el gate: el publish pasaba y dejaba la relación huérfana."""
    fake_db.raw["relationships"].insert_one({
        "_id": "rel-legacy", "projectId": world["pid"], "flgactive": True,
        "sourceTableId": world["t2"], "sourceColumnId": world["c_c"],
        "targetTableId": world["t1"], "targetColumnId": world["c_a"],
        "sourceCardinality": "many", "targetCardinality": "one"})
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.bulk(cs, [
        {"collection": "views", "entityId": world["view"], "op": "delete"},
        {"collection": "relationships", "entityId": world["rel"], "op": "delete"},
        {"collection": "canonical_columns", "entityId": world["c_a"], "op": "delete"},
        {"collection": "canonical_columns", "entityId": world["c_b"], "op": "delete"},
    ])
    ana.change(cs, "canonical_tables", world["t1"], None, op="delete")
    _submit(api, cs)
    status, body = api("beto").call("POST", f"/api/changesets/{cs}/review", {"decision": "approve"})
    assert status == 409, body
    assert body["detail"]["code"] == "table_in_use"
    assert "1 relationship(s)" in body["detail"]["items"][0]["message"]
    assert fake_db.raw["relationships"].find_one({"_id": "rel-legacy"})["flgactive"] is not False


def test_el_gate_lee_en_lotes_acotados_un_borrado_masivo(api, world, fake_db, monkeypatch):
    """Revisión R1: un restore/borrado de miles de tablas armaba un `$in` sin
    tope sobre CAMPOS (en Lakebase, un jsonpath con N alternativas). Ahora va
    en lotes. Los `$in` por `_id` no cuentan: van por `id = ANY(...)`."""
    from app.features.changesets import repository

    n = 650
    fake_db.raw["canonical_tables"].insert_many([
        {"_id": f"tm-{i}", "projectId": world["pid"], "flgactive": True, "physicalName": f"M_TM_{i}",
         "logicalName": f"TM {i}", "schema": "ddv"} for i in range(n)])
    seen: list[int] = []
    real = repository.published

    async def spy(coll, flt=None, *a, **kw):
        def sizes(f):
            if isinstance(f, dict):
                for k, v in f.items():
                    if k == "_id":
                        continue
                    if k == "$in" and isinstance(v, list):
                        seen.append(len(v))
                    else:
                        sizes(v)
            elif isinstance(f, list):
                for x in f:
                    sizes(x)
        sizes(flt)
        return await real(coll, flt, *a, **kw)

    monkeypatch.setattr(repository, "published", spy)
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.bulk(cs, [{"collection": "canonical_tables", "entityId": f"tm-{i}", "op": "delete"} for i in range(n)])
    assert publish(api, cs)["appliedAt"]
    assert seen and max(seen) <= 300, sorted(seen)[-5:]
