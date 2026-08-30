"""Suite VIVA del adaptador Lakebase (doc 28 F2).

Pega al Postgres real de Lakebase en un schema efímero `dmh_test_<rand>` que
se dropea al final. Por eso NO corre en el pytest normal: activar con

    LAKEBASE_TESTS=1 .venv/bin/python -m pytest tests/lakebase -q

Cubre la superficie pymongo del inventario (doc 28 §5) y los 8 shapes
de pipeline reales del reporting con fixtures sintéticas.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("LAKEBASE_TESTS") != "1",
    reason="Suite viva contra Lakebase: correr con LAKEBASE_TESTS=1",
)

_LOOP = asyncio.new_event_loop()
threading.Thread(target=_LOOP.run_forever, daemon=True).start()


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, _LOOP).result(timeout=120)


@pytest.fixture(scope="module")
def db():
    from app.core.db.lakebase import LakebaseDatabase, create_pool

    schema = f"dmh_test_{uuid.uuid4().hex[:8]}"
    pool = run(create_pool())
    database = LakebaseDatabase(pool, schema)

    async def _setup():
        async with pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    run(_setup())
    yield database

    async def _teardown():
        async with pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await pool.close()

    run(_teardown())


@pytest.fixture()
def coll(db):
    name = f"c{uuid.uuid4().hex[:10]}"
    c = db[name]
    yield c
    run(db.drop_table(name))


def seed(coll, docs):
    run(coll.insert_many(docs))


# ─── CRUD y filtros ─────────────────────────────────────────────────────


def test_insert_find_roundtrip(coll):
    run(coll.insert_one({"_id": "a1", "physicalName": "ZETA", "n": 3, "flgactive": True}))
    doc = run(coll.find_one({"_id": "a1"}))
    assert doc == {"_id": "a1", "physicalName": "ZETA", "n": 3, "flgactive": True}


def test_insert_autogenerates_objectid_style_id(coll):
    res = run(coll.insert_one({"at": "2026-07-19T00:00:00", "actor": "admin"}))
    assert isinstance(res.inserted_id, str) and len(res.inserted_id) == 24
    assert run(coll.find_one({"_id": res.inserted_id}))["actor"] == "admin"


def test_duplicate_id_raises_duplicatekeyerror(coll):
    from pymongo.errors import DuplicateKeyError

    run(coll.insert_one({"_id": "dup"}))
    with pytest.raises(DuplicateKeyError):
        run(coll.insert_one({"_id": "dup"}))


def test_flgactive_ne_false_includes_missing(coll):
    seed(coll, [
        {"_id": "t1", "flgactive": True},
        {"_id": "t2", "flgactive": False},
        {"_id": "t3"},
    ])
    ids = {d["_id"] for d in run(coll.find({"flgactive": {"$ne": False}}).to_list(None))}
    assert ids == {"t1", "t3"}


def test_in_nin_on_id_and_fields(coll):
    seed(coll, [
        {"_id": "x1", "tableId": "T1"},
        {"_id": "x2", "tableId": "T2"},
        {"_id": "x3", "tableId": "T3"},
    ])
    assert {d["_id"] for d in run(coll.find({"_id": {"$in": ["x1", "x3"]}}).to_list(None))} == {"x1", "x3"}
    assert {d["_id"] for d in run(coll.find({"tableId": {"$in": ["T2"]}}).to_list(None))} == {"x2"}
    assert {d["_id"] for d in run(coll.find({"_id": {"$nin": ["x1"]}}).to_list(None))} == {"x2", "x3"}
    assert run(coll.find({"tableId": {"$in": []}}).to_list(None)) == []


def test_array_contains_scalar_equality(coll):
    seed(coll, [
        {"_id": "sa1", "tableIds": ["a", "b"]},
        {"_id": "sa2", "tableIds": []},
        {"_id": "sa3", "tableIds": ["b", "c"]},
        {"_id": "sa4"},
    ])
    ids = {d["_id"] for d in run(coll.find({"tableIds": "b"}).to_list(None))}
    assert ids == {"sa1", "sa3"}
    ids = {d["_id"] for d in run(coll.find({"tableIds": {"$in": ["c", "zz"]}}).to_list(None))}
    assert ids == {"sa3"}


def test_dotted_path_across_array_of_objects(coll):
    seed(coll, [
        {"_id": "r1", "pairs": [{"parentColumnId": "p1", "childColumnId": "c1"},
                                  {"parentColumnId": "p2", "childColumnId": "c2"}]},
        {"_id": "r2", "pairs": [{"parentColumnId": "p9", "childColumnId": "c1"}]},
    ])
    ids = {d["_id"] for d in run(coll.find({"pairs.parentColumnId": "p2"}).to_list(None))}
    assert ids == {"r1"}
    ids = {d["_id"] for d in run(coll.find(
        {"$or": [{"pairs.parentColumnId": "c1"}, {"pairs.childColumnId": "c1"}]}
    ).to_list(None))}
    assert ids == {"r1", "r2"}


def test_exists_and_null_equality(coll):
    seed(coll, [
        {"_id": "e1", "beforeAt": "2026"},
        {"_id": "e2", "beforeAt": None},
        {"_id": "e3"},
    ])
    assert {d["_id"] for d in run(coll.find({"beforeAt": {"$exists": False}}).to_list(None))} == {"e3"}
    assert {d["_id"] for d in run(coll.find({"beforeAt": {"$exists": True}}).to_list(None))} == {"e1", "e2"}
    assert {d["_id"] for d in run(coll.find({"beforeAt": None}).to_list(None))} == {"e2", "e3"}


def test_regex_escaped_case_insensitive_and_anchored(coll):
    import re

    seed(coll, [
        {"_id": "n1", "physicalName": "CLIENTE_NATURAL"},
        {"_id": "n2", "physicalName": "cliente"},
        {"_id": "n3", "physicalName": "PRODUCTO"},
    ])
    rx = {"$regex": re.escape("cliente"), "$options": "i"}
    assert {d["_id"] for d in run(coll.find({"physicalName": rx}).to_list(None))} == {"n1", "n2"}
    rx = {"$regex": "^" + re.escape("CLIENTE") + "$"}
    assert run(coll.find({"physicalName": rx}).to_list(None)) == []
    rx = {"$regex": f"^{re.escape('cliente')}$", "$options": "i"}
    assert {d["_id"] for d in run(coll.find({"physicalName": rx}).to_list(None))} == {"n2"}


def test_string_gt_comparison_iso_dates(coll):
    seed(coll, [
        {"_id": "d1", "appliedAt": "2026-07-01T00:00:00"},
        {"_id": "d2", "appliedAt": "2026-07-15T00:00:00"},
        {"_id": "d3"},
    ])
    ids = {d["_id"] for d in run(coll.find({"appliedAt": {"$gt": "2026-07-10"}}).to_list(None))}
    assert ids == {"d2"}


def test_sort_collation_code_points_and_nulls(coll):
    seed(coll, [
        {"_id": "s1", "physicalName": "alfa"},
        {"_id": "s2", "physicalName": "MEDIA"},
        {"_id": "s3", "physicalName": "ZETA"},
        {"_id": "s4"},
    ])
    docs = run(coll.find({}).sort("physicalName", 1).to_list(None))
    assert [d["_id"] for d in docs] == ["s4", "s2", "s3", "s1"]  # missing, M, Z, a
    docs = run(coll.find({}).sort([("physicalName", -1), ("_id", 1)]).to_list(None))
    assert [d["_id"] for d in docs] == ["s1", "s3", "s2", "s4"]


def test_keyset_pagination_shape(coll):
    seed(coll, [{"_id": f"k{i}", "physicalName": f"N{i:02d}"} for i in range(5)])
    page1 = run(coll.find({}).sort([("physicalName", 1), ("_id", 1)]).limit(3).to_list(3))
    cv, cid = page1[-1]["physicalName"], page1[-1]["_id"]
    flt = {"$and": [{}, {"$or": [
        {"physicalName": {"$gt": cv}},
        {"physicalName": cv, "_id": {"$gt": cid}},
    ]}]}
    page2 = run(coll.find(flt).sort([("physicalName", 1), ("_id", 1)]).to_list(None))
    assert [d["_id"] for d in page1 + page2] == [f"k{i}" for i in range(5)]


def test_projection_inclusion_and_exclusion(coll):
    seed(coll, [{"_id": "p1", "name": "A", "folderId": "f", "big": ["x"] * 3}])
    doc = run(coll.find_one({"_id": "p1"}, {"name": 1, "folderId": 1}))
    assert doc == {"_id": "p1", "name": "A", "folderId": "f"}
    doc = run(coll.find_one({"_id": "p1"}, {"big": 0}))
    assert doc == {"_id": "p1", "name": "A", "folderId": "f"}
    doc = run(coll.find_one({"_id": "p1"}, {"name": 1, "_id": 0}))
    assert doc == {"name": "A"}
    doc = run(coll.find_one({"_id": "p1"}, {"name": 1, "missing": 1}))
    assert doc == {"_id": "p1", "name": "A"}  # campo ausente NO aparece


def test_projection_dotted_inclusion(coll):
    seed(coll, [
        {"_id": "pd1", "physicalName": "A", "udpValues": {"u-1": "DAC", "u-2": "X"}},
        {"_id": "pd2", "physicalName": "B", "udpValues": {}},
        {"_id": "pd3", "physicalName": "C"},
    ])
    docs = run(coll.find({}, {"physicalName": 1, "udpValues.u-1": 1})
               .sort("physicalName", 1).to_list(None))
    assert docs[0] == {"_id": "pd1", "physicalName": "A", "udpValues": {"u-1": "DAC"}}
    assert docs[1] == {"_id": "pd2", "physicalName": "B", "udpValues": {}}
    assert docs[2] == {"_id": "pd3", "physicalName": "C"}  # raíz ausente → se omite


def test_async_for_iteration(coll):
    seed(coll, [{"_id": "i1"}, {"_id": "i2"}])

    async def gather():
        return {d["_id"] async for d in coll.find({})}

    assert run(gather()) == {"i1", "i2"}


# ─── Updates ────────────────────────────────────────────────────────────


def test_update_one_set_unset_inc_push(coll):
    seed(coll, [{"_id": "u1", "failedAttempts": 1, "lockedUntil": "x", "comments": []}])
    run(coll.update_one({"_id": "u1"}, {"$inc": {"failedAttempts": 1}}))
    run(coll.update_one({"_id": "u1"}, {"$unset": {"lockedUntil": ""}}))
    run(coll.update_one({"_id": "u1"}, {"$push": {"comments": {"author": "a", "text": "hola"}}}))
    run(coll.update_one({"_id": "u1"}, {"$set": {"status": "ok"}}))
    doc = run(coll.find_one({"_id": "u1"}))
    assert doc["failedAttempts"] == 2
    assert "lockedUntil" not in doc
    assert doc["comments"] == [{"author": "a", "text": "hola"}]
    assert doc["status"] == "ok"


def test_update_one_dotted_dynamic_path(coll):
    seed(coll, [{"_id": "cs1", "approvals": {}}, {"_id": "cs2"}])
    run(coll.update_one({"_id": "cs1"}, {"$set": {"approvals.beto": {"status": "approved"}}}))
    run(coll.update_one({"_id": "cs2"}, {"$set": {"approvals.ana": {"status": "rejected"}}}))
    assert run(coll.find_one({"_id": "cs1"}))["approvals"] == {"beto": {"status": "approved"}}
    assert run(coll.find_one({"_id": "cs2"}))["approvals"] == {"ana": {"status": "rejected"}}


def test_merge_objects_approvals_con_correo(coll):
    """`$mergeObjects` (dialecto propio): keys dinámicas como DATO jsonb — un
    correo con puntos queda como UNA key literal (el dot-path de `$set` lo
    splitearía por los puntos). Es la escritura de `set_approval`."""
    seed(coll, [{"_id": "cs1", "status": "submitted", "title": "bb"}])
    doc = run(coll.find_one_and_update(
        {"_id": "cs1", "status": "submitted"},
        {"$mergeObjects": {"approvals": {"carlosmps97@hotmail.com": {"status": "approved"}}},
         "$set": {"updatedAt": "T1"}},
        return_document=True,
    ))
    assert doc["approvals"] == {"carlosmps97@hotmail.com": {"status": "approved"}}
    # Segundo revisor: mergea sobre el campo ya existente sin pisar al primero.
    doc = run(coll.find_one_and_update(
        {"_id": "cs1", "status": "submitted"},
        {"$mergeObjects": {"approvals": {"ana": {"status": "rejected"}}}},
        return_document=True,
    ))
    assert doc["approvals"] == {
        "carlosmps97@hotmail.com": {"status": "approved"},
        "ana": {"status": "rejected"},
    }
    assert doc["updatedAt"] == "T1"


def test_update_one_upsert_with_setoninsert(coll):
    res = run(coll.update_one(
        {"_id": "cfg"},
        {"$set": {"separator": "_"}, "$setOnInsert": {"createdAt": "T0"}},
        upsert=True,
    ))
    assert res.upserted_id == "cfg"
    run(coll.update_one(
        {"_id": "cfg"},
        {"$set": {"separator": "-"}, "$setOnInsert": {"createdAt": "T9"}},
        upsert=True,
    ))
    doc = run(coll.find_one({"_id": "cfg"}))
    assert doc["separator"] == "-" and doc["createdAt"] == "T0"


def test_update_many_soft_delete(coll):
    seed(coll, [
        {"_id": "m1", "flgactive": True},
        {"_id": "m2"},
        {"_id": "m3", "flgactive": False},
    ])
    res = run(coll.update_many(
        {"flgactive": {"$ne": False}},
        {"$set": {"flgactive": False, "deletedAt": "T"}},
    ))
    assert res.modified_count == 2
    assert run(coll.count_documents({"flgactive": {"$ne": False}})) == 0


def test_find_one_and_update_state_claim(coll):
    seed(coll, [{"_id": "cs9", "status": "draft", "title": "x"}])
    doc = run(coll.find_one_and_update(
        {"_id": "cs9", "status": "draft"}, {"$set": {"status": "submitted"}},
        return_document=True,
    ))
    assert doc["status"] == "submitted"
    doc = run(coll.find_one_and_update(
        {"_id": "cs9", "status": "draft"}, {"$set": {"status": "submitted"}},
        return_document=True,
    ))
    assert doc is None  # guard: ya no está en draft


def test_find_one_and_update_projection_and_upsert(coll):
    doc = run(coll.find_one_and_update(
        {"_id": "au1"}, {"$inc": {"failedAttempts": 1}},
        return_document=True, projection={"failedAttempts": 1}, upsert=True,
    ))
    assert doc == {"_id": "au1", "failedAttempts": 1}


def test_replace_one_wtoken_compensation_pattern(coll):
    seed(coll, [{"_id": "ch1", "payload": {"v": 1}, "wtoken": "tok-A"}])
    res = run(coll.replace_one({"_id": "ch1", "wtoken": "tok-OTRO"}, {"payload": {"v": 9}}))
    assert res.matched_count == 0  # el token no coincide: no pisa
    res = run(coll.replace_one({"_id": "ch1", "wtoken": "tok-A"},
                               {"payload": {"v": 2}, "wtoken": "tok-B"}))
    assert res.matched_count == 1
    doc = run(coll.find_one({"_id": "ch1"}))
    assert doc == {"_id": "ch1", "payload": {"v": 2}, "wtoken": "tok-B"}
    res = run(coll.delete_one({"_id": "ch1", "wtoken": "tok-ZZ"}))
    assert res.deleted_count == 0
    res = run(coll.delete_one({"_id": "ch1", "wtoken": "tok-B"}))
    assert res.deleted_count == 1


def test_replace_one_upsert(coll):
    res = run(coll.replace_one({"_id": "rp1"}, {"name": "nuevo"}, upsert=True))
    assert res.upserted_id == "rp1"
    assert run(coll.find_one({"_id": "rp1"})) == {"_id": "rp1", "name": "nuevo"}


def test_delete_many(coll):
    seed(coll, [{"_id": "z1", "csId": "c"}, {"_id": "z2", "csId": "c"}, {"_id": "z3", "csId": "o"}])
    res = run(coll.delete_many({"csId": "c"}))
    assert res.deleted_count == 2
    assert run(coll.count_documents({})) == 1


def test_distinct_flattens_arrays(coll):
    seed(coll, [
        {"_id": "v1", "schema": "SCH_A", "sourceTableIds": ["t1", "t2"]},
        {"_id": "v2", "schema": "SCH_B", "sourceTableIds": ["t2"]},
        {"_id": "v3"},
    ])
    assert sorted(run(coll.distinct("schema"))) == ["SCH_A", "SCH_B"]
    assert sorted(run(coll.distinct("sourceTableIds"))) == ["t1", "t2"]
    assert run(coll.distinct("schema", {"_id": "v2"})) == ["SCH_B"]


# ─── bulk_write ─────────────────────────────────────────────────────────


def test_bulk_write_updateone_fast_path(coll):
    from pymongo import UpdateOne

    seed(coll, [{"_id": "b1", "v": 1, "createdAt": "T0"}])
    ops = [
        UpdateOne({"_id": "b1"}, {"$set": {"v": 2}, "$setOnInsert": {"createdAt": "T9"}}, upsert=True),
        UpdateOne({"_id": "b2"}, {"$set": {"v": 5}, "$setOnInsert": {"createdAt": "T1"}}, upsert=True),
    ]
    run(coll.bulk_write(ops, ordered=False))
    d1, d2 = run(coll.find_one({"_id": "b1"})), run(coll.find_one({"_id": "b2"}))
    assert d1["v"] == 2 and d1["createdAt"] == "T0"
    assert d2["v"] == 5 and d2["createdAt"] == "T1"


def test_bulk_write_replaceone_fast_path(coll):
    from pymongo import ReplaceOne

    ops = [ReplaceOne({"_id": f"rb{i}"}, {"n": i}, upsert=True) for i in range(50)]
    run(coll.bulk_write(ops, ordered=False))
    assert run(coll.count_documents({})) == 50
    ops = [ReplaceOne({"_id": "rb0"}, {"n": 99}, upsert=True)]
    run(coll.bulk_write(ops))
    assert run(coll.find_one({"_id": "rb0"}))["n"] == 99


def test_bulk_write_general_path(coll):
    from pymongo import DeleteMany, InsertOne, UpdateOne

    seed(coll, [{"_id": "g1", "flgactive": True}, {"_id": "g2", "flgactive": True}])
    ops = [
        InsertOne({"_id": "g3"}),
        UpdateOne({"_id": {"$nin": ["g1"]}}, {"$set": {"flgactive": False}}),
        DeleteMany({"_id": "g1"}),
    ]
    run(coll.bulk_write(ops, ordered=False))
    assert run(coll.find_one({"_id": "g1"})) is None
    inactive = {d["_id"] for d in run(coll.find({"flgactive": False}).to_list(None))}
    assert inactive in ({"g2"}, {"g3"})  # UpdateOne toca UNA sola (como Mongo)


# ─── Índices ────────────────────────────────────────────────────────────


def test_create_index_variants_and_unique_seq(coll, db):
    from pymongo.errors import DuplicateKeyError

    run(coll.create_index([("flgactive", 1)]))
    run(coll.create_index([("updatedAt", -1)]))
    run(coll.create_index([("schema", 1), ("physicalName", 1)]))
    run(coll.create_index([("udpValues.$**", 1)]))  # wildcard → lo cubre el GIN
    run(coll.create_index([("pairs.parentColumnId", 1)]))
    run(coll.create_index([("seq", 1)], unique=True))
    run(coll.insert_one({"_id": "sv1", "seq": 1}))
    with pytest.raises(DuplicateKeyError):
        run(coll.insert_one({"_id": "sv2", "seq": 1}))
    run(coll.insert_one({"_id": "sv3", "seq": 2}))


# ─── Aggregations (los 8 shapes reales) ─────────────────────────────────

ACTIVE = {"flgactive": {"$ne": False}}


def test_agg_column_counts_group_by_tableid(coll):
    seed(coll, [
        {"_id": "c1", "tableId": "T1"},
        {"_id": "c2", "tableId": "T1"},
        {"_id": "c3", "tableId": "T2"},
        {"_id": "c4", "tableId": "T2", "flgactive": False},
    ])
    rows = run(coll.aggregate([
        {"$match": ACTIVE},
        {"$group": {"_id": "$tableId", "n": {"$sum": 1}}},
    ]).to_list(None))
    assert {r["_id"]: r["n"] for r in rows} == {"T1": 2, "T2": 1}


def test_agg_arrange_all_max_de_suma_de_longitudes(coll):
    """Shape de scripts/arrange_all.py (ancho de nodo): $max de
    $add($strLenCP(physicalName), $strLenCP(dataType)) por tabla — destapado
    2026-07-24 al correr arrange_all por primera vez contra Lakebase."""
    seed(coll, [
        {"_id": "a1", "tableId": "T1", "physicalName": "COD", "dataType": "INT",
         "logicalName": "Codigo"},
        {"_id": "a2", "tableId": "T1", "physicalName": "NOMBRELARGO",
         "dataType": "VARCHAR(120)", "logicalName": "N"},
        {"_id": "a3", "tableId": "T2", "physicalName": "X"},   # sin dataType
    ])
    rows = run(coll.aggregate([{"$group": {"_id": "$tableId", "n": {"$sum": 1},
        "maxphys": {"$max": {"$add": [
            {"$strLenCP": {"$ifNull": ["$physicalName", ""]}},
            {"$strLenCP": {"$ifNull": ["$dataType", ""]}}]}},
        "maxlog": {"$max": {"$add": [
            {"$strLenCP": {"$ifNull": ["$logicalName", ""]}},
            {"$strLenCP": {"$ifNull": ["$dataType", ""]}}]}}}}]).to_list(None))
    by = {r["_id"]: r for r in rows}
    assert by["T1"]["n"] == 2
    assert by["T1"]["maxphys"] == len("NOMBRELARGO") + len("VARCHAR(120)")
    assert by["T1"]["maxlog"] == 1 + len("VARCHAR(120)")
    assert by["T2"]["maxphys"] == 1                     # dataType ausente → ""


def test_agg_scorecard_col_stats(coll):
    NULLISH = [None, ""]
    seed(coll, [
        {"_id": "c1", "parentDomainId": "d1", "description": "ok", "typeOverridden": True,
         "udpValues": {"u1": "x"}, "isPrimaryKey": True, "isForeignKey": False},
        {"_id": "c2", "parentDomainId": None, "description": "", "udpValues": {}},
        {"_id": "c3"},
    ])
    rows = run(coll.aggregate([{"$match": ACTIVE}, {"$group": {"_id": None,
        "columns": {"$sum": 1},
        "noDomain": {"$sum": {"$cond": [{"$not": ["$parentDomainId"]}, 1, 0]}},
        "noDesc": {"$sum": {"$cond": [{"$in": [{"$ifNull": ["$description", ""]}, NULLISH]}, 1, 0]}},
        "overridden": {"$sum": {"$cond": ["$typeOverridden", 1, 0]}},
        "withUdp": {"$sum": {"$cond": [{"$gt": [{"$size": {"$ifNull": [{"$objectToArray": "$udpValues"}, []]}}, 0]}, 1, 0]}},
        "pk": {"$sum": {"$cond": ["$isPrimaryKey", 1, 0]}},
        "fk": {"$sum": {"$cond": ["$isForeignKey", 1, 0]}}}}]).to_list(None))
    assert len(rows) == 1
    r = rows[0]
    assert r["columns"] == 3 and r["noDomain"] == 2 and r["noDesc"] == 2
    assert r["overridden"] == 1 and r["withUdp"] == 1 and r["pk"] == 1 and r["fk"] == 0


def test_agg_two_stage_group_tbl_pk(coll):
    seed(coll, [
        {"_id": "c1", "tableId": "T1", "isPrimaryKey": True},
        {"_id": "c2", "tableId": "T1"},
        {"_id": "c3", "tableId": "T2"},
    ])
    rows = run(coll.aggregate([
        {"$match": ACTIVE},
        {"$group": {"_id": "$tableId", "pk": {"$sum": {"$cond": ["$isPrimaryKey", 1, 0]}}}},
        {"$group": {"_id": None, "withCols": {"$sum": 1},
                     "withPk": {"$sum": {"$cond": [{"$gt": ["$pk", 0]}, 1, 0]}}}},
    ]).to_list(None))
    assert rows == [{"_id": None, "withCols": 2, "withPk": 1}]


def test_agg_rel_pipe_project_array_unwind_count(coll):
    seed(coll, [
        {"_id": "r1", "sourceTableId": "T1", "targetTableId": "T2"},
        {"_id": "r2", "sourceTableId": "T2", "targetTableId": "T3"},
    ])
    rows = run(coll.aggregate([
        {"$match": ACTIVE},
        {"$project": {"t": ["$sourceTableId", "$targetTableId"]}},
        {"$unwind": "$t"},
        {"$group": {"_id": "$t"}},
        {"$count": "involved"},
    ]).to_list(None))
    assert rows == [{"involved": 3}]


def test_agg_count_empty_returns_no_rows(coll):
    rows = run(coll.aggregate([
        {"$match": ACTIVE}, {"$group": {"_id": "$x"}}, {"$count": "involved"},
    ]).to_list(None))
    assert rows == []


def test_agg_coverage_objecttoarray_unwind_composite_id(coll):
    seed(coll, [
        {"_id": "c1", "udpValues": {"u1": "DAC", "u2": "SI"}},
        {"_id": "c2", "udpValues": {"u1": "DAC"}},
        {"_id": "c3", "udpValues": {}},
        {"_id": "c4"},
    ])
    rows = run(coll.aggregate([
        {"$match": {**ACTIVE, "udpValues": {"$exists": True, "$ne": {}}}},
        {"$project": {"kv": {"$objectToArray": "$udpValues"}}},
        {"$unwind": "$kv"},
        {"$group": {"_id": {"k": "$kv.k", "v": "$kv.v"}, "n": {"$sum": 1}}},
    ]).to_list(None))
    out: dict = {}
    for r in rows:
        out.setdefault(r["_id"]["k"], {})[r["_id"]["v"]] = r["n"]
    assert out == {"u1": {"DAC": 2}, "u2": {"SI": 1}}


def test_agg_domain_usage_addtoset(coll):
    seed(coll, [
        {"_id": "c1", "parentDomainId": "d1", "tableId": "T1", "dataType": "INT", "typeOverridden": True},
        {"_id": "c2", "parentDomainId": "d1", "tableId": "T2", "dataType": "INT"},
        {"_id": "c3", "parentDomainId": None, "tableId": "T3", "dataType": "STR"},
    ])
    rows = run(coll.aggregate([
        {"$match": {**ACTIVE, "parentDomainId": {"$ne": None}}},
        {"$group": {"_id": "$parentDomainId", "columnCount": {"$sum": 1},
                     "tables": {"$addToSet": "$tableId"},
                     "overrideCount": {"$sum": {"$cond": ["$typeOverridden", 1, 0]}},
                     "types": {"$addToSet": "$dataType"}}},
    ]).to_list(None))
    assert len(rows) == 1
    r = rows[0]
    assert r["_id"] == "d1" and r["columnCount"] == 2 and r["overrideCount"] == 1
    assert sorted(r["tables"]) == ["T1", "T2"] and r["types"] == ["INT"]


def test_agg_facets_group_sort_limit(coll):
    seed(coll, [
        {"_id": "f1", "dataType": "VARCHAR"},
        {"_id": "f2", "dataType": "INT"},
        {"_id": "f3", "dataType": "VARCHAR"},
        {"_id": "f4"},
    ])
    rows = run(coll.aggregate([
        {"$match": ACTIVE},
        {"$group": {"_id": "$dataType"}},
        {"$sort": {"_id": 1}},
        {"$limit": 10},
    ]).to_list(None))
    assert [r["_id"] for r in rows] == [None, "INT", "VARCHAR"]


def test_agg_view_columns_unwind_match_project_sort_skip_limit(coll):
    seed(coll, [
        {"_id": "v1", "name": "V_A", "schema": "S1", "flgactive": True, "sources": [
            {"column": "COL_B", "outputAlias": "B", "tableId": "t1", "description": "db"},
            {"column": "COL_A", "tableId": "t1"},
        ]},
        {"_id": "v2", "name": "V_B", "schema": "S2", "sources": [
            {"column": "COL_C", "castType": "INT", "tableId": "t2"},
        ]},
        {"_id": "v3", "name": "V_C", "sources": []},
    ])
    pipe = [
        {"$match": ACTIVE},
        {"$unwind": "$sources"},
        {"$match": {"sources.tableId": "t1"}},
        {"$project": {"name": 1, "schema": 1, "sources.outputAlias": 1,
                      "sources.column": 1, "sources.tableId": 1,
                      "sources.castType": 1, "sources.expression": 1,
                      "sources.description": 1}},
        {"$sort": {"sources.column": 1, "_id": 1}},
        {"$skip": 0},
        {"$limit": 5},
    ]
    docs = run(coll.aggregate(pipe).to_list(None))
    assert [d["sources"]["column"] for d in docs] == ["COL_A", "COL_B"]
    assert docs[0]["name"] == "V_A" and docs[0]["_id"] == "v1"
    assert "outputAlias" not in docs[0]["sources"]  # ausente NO aparece
    assert docs[1]["sources"]["outputAlias"] == "B"


def test_agg_grouped_query_engine_shape(coll):
    seed(coll, [
        {"_id": "q1", "dataType": "INT", "ordinal": 1},
        {"_id": "q2", "dataType": "INT", "ordinal": 5},
        {"_id": "q3", "dataType": "STR", "ordinal": 2},
    ])
    rows = run(coll.aggregate([
        {"$match": ACTIVE},
        {"$group": {"_id": {"dt": {"$ifNull": ["$dataType", None]}},
                     "cnt": {"$sum": 1},
                     "mx": {"$max": "$ordinal"}, "mn": {"$min": "$ordinal"},
                     "avg_o": {"$avg": "$ordinal"},
                     "dts": {"$addToSet": "$dataType"}}},
        {"$project": {"_id": 0, "dt": "$_id.dt", "cnt": 1, "mx": 1, "mn": 1,
                      "avg_o": 1, "nd": {"$size": {"$ifNull": ["$dts", []]}}}},
        {"$sort": {"dt": 1}},
        {"$limit": 100},
    ]).to_list(None))
    assert rows == [
        {"dt": "INT", "cnt": 2, "mx": 5, "mn": 1, "avg_o": 3.0, "nd": 1},
        {"dt": "STR", "cnt": 1, "mx": 2, "mn": 2, "avg_o": 2.0, "nd": 1},
    ]


def test_agg_maxtimems_accepted(coll):
    seed(coll, [{"_id": "t1"}])
    rows = run(coll.aggregate([{"$match": {}}], maxTimeMS=30000).to_list(None))
    assert len(rows) == 1


# ─── Database-level ─────────────────────────────────────────────────────


def test_db_command_ping_and_list_collections(coll, db):
    assert run(db.command("ping")) == {"ok": 1}
    seed(coll, [{"_id": "x"}])  # la tabla se crea lazy: recién existe al operar
    names = run(db.list_collection_names())
    assert coll.name in names


def test_drop_and_recreate(coll):
    seed(coll, [{"_id": "d1"}])
    run(coll.drop())
    assert run(coll.count_documents({})) == 0  # re-ensure lazy: tabla vacía
