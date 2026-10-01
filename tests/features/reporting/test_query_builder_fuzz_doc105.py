"""Doc 105 (ronda 3) — fuzz del CONSTRUCTOR de consultas (QuerySpec) del
Reporting, adaptado del `fuzz_query.py` del revisor R5.

Specs aleatorios (todas las entidades, UDP de varios tipos, filtros anidados,
agrupaciones, alias, órdenes y valores raros) contra `POST /query` (+ su página
2) y `POST /export`: nunca 500, y si responde 200 el SQL que el traductor de
Lakebase arma para cada lectura no tiene problemas (`LakebaseCheckingDb`: FakeDb
sola traga NotImplementedError del traductor, NaN/Infinity, enteros fuera de
int8…). NUL y surrogates del cuerpo los corta el guard global (fuera de aquí)."""
from __future__ import annotations

import json
import random

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

OPS = ["eq", "ne", "in", "nin", "contains", "startsWith", "gt", "gte", "lt", "lte", "between", "exists", "isnull"]
VALUES = [None, "", "A", "C1", "5", "1e3", "-2", "2026-01-03", "NaN", "Infinity", "-inf", 0, 1, -1, 2.5, -0.0,
          1e308, -1e-300, 10 ** 18, 10 ** 19, 2 ** 63, -(2 ** 63) - 1, True, False, [], [1, 2], ["A", None], [None],
          {"a": 1}, {"$ne": None}, "x" * 300, "ñ😀", "%", "_", ".*", "(", "[a-", "true", "yes", "0", 10 ** 400]
ALIASES = ["n", "value", "count", "_id", "$x", "a.b", "", " ", "x" * 70, "dataType", "ñ", "$", "a$b", "total_1"]
AGG = ["count", "countDistinct", "sum", "avg", "min", "max"]


@pytest.fixture
def engine(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    raw, on = fake.raw, {"projectId": "p1", "flgactive": True}
    raw["udp_definitions"].insert_many([
        {"_id": "u_num", **on, "name": "Num", "level": "column", "dataType": "number"},
        {"_id": "u_date", **on, "name": "Fecha", "level": "column", "dataType": "date"},
        {"_id": "u_list", **on, "name": "Lista", "level": "column", "dataType": "list", "allowedValues": ["A", "B"]},
        {"_id": "u_tab", **on, "name": "TabUdp", "level": "table", "dataType": "string"},
        {"_id": "u_cv", **on, "name": "CanvasUdp", "level": "canvas", "dataType": "string"}])
    raw["canonical_tables"].insert_many([
        {"_id": f"t{i}", **on, "physicalName": f"T{i}", "schema": f"s{i % 2}", "udpValues": {"u_tab": str(i)}}
        for i in range(4)])
    raw["canonical_columns"].insert_many([
        {"_id": f"c{i}", **on, "tableId": f"t{i % 4}", "physicalName": f"C{i}", "dataType": "INT", "ordinal": i,
         "isPrimaryKey": i % 3 == 0, "parentDomainId": "d1" if i % 2 else None,
         "udpValues": {"u_num": str(i), "u_date": f"2026-01-0{1 + i % 9}", "u_list": "AB"[i % 2]}}
        for i in range(12)])
    raw["parent_domains"].insert_one({"_id": "d1", **on, "name": "DOM"})
    raw["relationships"].insert_many([
        {"_id": f"r{i}", **on, "parentTableId": f"t{i}", "childTableId": f"t{(i + 1) % 4}",
         "identifying": bool(i % 2), "parentCardinality": "one", "childCardinality": "zero-many"} for i in range(4)])
    raw["views"].insert_many([
        {"_id": f"v{i}", **on, "name": f"V{i}", "schema": "s_vu", "tableId": f"t{i}",
         "sources": [{"tableId": f"t{i}", "column": f"C{i}", "outputAlias": f"A{i}"}]} for i in range(3)])
    raw["subject_areas"].insert_many([
        {"_id": f"sa{i}", **on, "name": f"M{i}", "tableIds": [f"t{i}"], "udpValues": {"u_cv": "x"}} for i in range(3)])
    return fake


def _catalogs(client: TestClient) -> dict[str, list[str]]:
    out = {}
    for frm in ("columns", "tables", "relationships", "views", "view_columns", "models"):
        res = client.get("/api/reporting/catalog", params={"projectId": "p1", "from": frm})
        out[frm] = [f["key"] for f in res.json()["data"]["fields"]]
    return out


def _spec(rnd: random.Random, catalogs: dict[str, list[str]]) -> dict:
    frm = rnd.choice(list(catalogs))
    keys = catalogs[frm]

    def cond():
        return {"field": rnd.choice(keys + ["nope", "udp.zz"]), "op": rnd.choice(OPS), "value": rnd.choice(VALUES)}

    def where(depth=0):
        conds = [where(depth + 1) if depth < 2 and rnd.random() < 0.2 else cond() for _ in range(rnd.randint(1, 3))]
        return {"op": rnd.choice(["and", "or", "not"]), "conditions": conds}

    s: dict = {"projectId": "p1", "from": frm, "limit": rnd.choice([1, 2, 5, 100])}
    if rnd.random() < 0.6:
        s["select"] = rnd.sample(keys, min(len(keys), rnd.randint(1, 3))) + (["nope"] if rnd.random() < 0.05 else [])
    if rnd.random() < 0.8:
        s["where"] = where()
    if rnd.random() < 0.3:
        s["groupBy"] = rnd.sample(keys, min(len(keys), rnd.randint(1, 2)))
    if rnd.random() < 0.35:
        s["aggregations"] = [{"fn": rnd.choice(AGG), "field": rnd.choice(keys + [None]), "as": rnd.choice(ALIASES)}
                             for _ in range(rnd.randint(1, 2))]
    if rnd.random() < 0.4:
        names = keys + ALIASES + [a["as"] for a in s.get("aggregations", [])]
        s["orderBy"] = [{"field": rnd.choice(names), "dir": rnd.choice(["asc", "desc"])}
                        for _ in range(rnd.choice([1, 1, 1, 2]))]
    return s


@pytest.mark.parametrize("seed", range(6))
def test_constructor_nunca_500_ni_sql_invalido_en_lakebase(engine, seed):
    rnd = random.Random(105_000 + seed)
    client = TestClient(app, raise_server_exceptions=False)
    catalogs = _catalogs(client)
    problems: list[str] = []
    for _ in range(120):
        spec = _spec(rnd, catalogs)
        body = json.dumps(spec)
        route = rnd.choice(["/api/reporting/query", "/api/reporting/query", "/api/reporting/export"])
        engine.problems.clear()
        res = client.post(route, content=body, headers={"content-type": "application/json"})
        if res.status_code == 200 and route.endswith("export"):
            _ = res.text                                   # consumir el stream (páginas siguientes)
        if res.status_code >= 500:
            problems.append(f"{route} {res.status_code}: {body[:300]}")
        elif res.status_code == 200 and engine.problems:
            problems.append(f"{route} 200 + Lakebase {engine.problems[:2]}: {body[:300]}")
        if res.status_code == 200 and route.endswith("query") and res.json()["data"].get("nextCursor"):
            engine.problems.clear()
            page2 = client.post(f"{route}?cursor={res.json()['data']['nextCursor']}", content=body,
                                headers={"content-type": "application/json"})
            if page2.status_code >= 500 or (page2.status_code == 200 and engine.problems):
                problems.append(f"página 2 {page2.status_code} {engine.problems[:2]}: {body[:300]}")
    assert not problems, "\n".join(problems[:15])
