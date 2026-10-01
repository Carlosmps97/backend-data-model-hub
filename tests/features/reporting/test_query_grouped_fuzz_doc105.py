"""Doc 105 (ronda 5) — fuzz del SQL AGRUPADO del editor con SQLite de oráculo.

Base: el fuzz de agrupados del revisor R12, más lo que la ronda 5 soporta:
SELECT intercalado (agregados antes o entre las dimensiones), dimensiones
agrupadas que NO están en el SELECT (a veces también en el ORDER BY) y MIN/MAX
de booleanos. Ronda 6: alias «trampa» (`Count`, `MAX`, `count_2`…) que chocan
sin distinguir mayúsculas con el nombre AUTOMÁTICO de otro agregado sin alias
(R16, H1). El ORDER BY cubre TODAS las columnas de salida: el orden visible
queda definido (dos filas empatadas son iguales) y se compara fila a fila.
Además: `/query/validate` → `/export` da el mismo CSV (con `resultColumns`) y el
SQL de Lakebase no tiene problemas."""
from __future__ import annotations

import csv
import io
import logging
import random
import sqlite3

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

ON = {"projectId": "p1", "flgactive": True}
FIELDS = ["physicalName", "logicalName", "dataType", "ordinal", "isPrimaryKey", "isNullable", "description", "tableId"]
DIMS = ["dataType", "tableId", "isPrimaryKey", "isNullable"]
AGGS = ["COUNT(*)", "COUNT(DISTINCT dataType)", "SUM(ordinal)", "AVG(ordinal)", "MIN(ordinal)", "MAX(ordinal)",
        "MIN(physicalName)", "COUNT(DISTINCT tableId)", "MIN(isPrimaryKey)", "MAX(isNullable)"]
WHERES = ["ordinal BETWEEN 2 AND 7", "ordinal BETWEEN 3 AND 3", "isPrimaryKey IS NULL", "isPrimaryKey IS NOT NULL",
          "isNullable IS NULL", "isPrimaryKey = TRUE", "isPrimaryKey = FALSE", "dataType IN ('INT', 'DATE')",
          "tableId = 't2'", "ordinal IS NULL", "ordinal > 5 AND ordinal BETWEEN 0 AND 9",
          "physicalName LIKE 'C1%'", "(dataType = 'INT' OR tableId = 't3')"]


def _seed(raw) -> None:
    """260 columnas con nulos, ausentes y vacíos (datos del revisor R12)."""
    docs = []
    for i in range(260):
        d = {"_id": f"c{i:03d}", **ON, "physicalName": f"C{i:03d}", "logicalName": f"Col {i}",
             "tableId": ["t1", "t2", "t3", "t4", "t5", "t9"][i % 6],
             "dataType": ["INT", "STRING", "DATE", "DECIMAL"][i % 4]}
        if i % 10 == 0:
            d["isPrimaryKey"] = True
        elif i % 11 == 0:
            d["isPrimaryKey"] = None
        elif i % 7 != 0:
            d["isPrimaryKey"] = False                      # i % 7 == 0 → ausente
        if i % 3 == 0:
            d["isNullable"] = bool(i % 2)
        if i % 9:
            d["ordinal"] = i % 13
        if i % 5 == 0:
            d["description"] = f"desc {i}"
        elif i % 5 == 1:
            d["description"] = ""
        elif i % 5 == 2:
            d["description"] = None
        docs.append(d)
    raw["canonical_columns"].insert_many(docs)


@pytest.fixture
def qdb(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    _seed(fake.raw)
    return fake


@pytest.fixture
def oracle(qdb) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE columns (id TEXT, physicalName TEXT, logicalName TEXT, dataType TEXT, ordinal INTEGER, "
                "isPrimaryKey BOOLEAN, isNullable BOOLEAN, description TEXT, tableId TEXT)")
    for d in qdb.raw["canonical_columns"].find({"projectId": "p1", "flgactive": True}):
        con.execute("INSERT INTO columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [d["_id"]] + [(None if d.get(f) == "" and f == "description" else d.get(f)) for f in FIELDS])
    return con


def _cased(rnd: random.Random, name: str) -> str:
    return rnd.choice([name, name.lower(), name.upper()])


def _aliases(rnd: random.Random, aggs: list[str]) -> list[str]:
    """Alias explícitos, únicos sin distinguir mayúsculas: `a{x}` o una trampa —
    el nombre automático de algún agregado (`count`, `max_2`…) en otra caja."""
    bases = [a.split("(")[0].lower() for a in aggs]
    traps = bases + [f"{b}_2" for b in bases]
    names, used = [], set()
    for x in range(len(aggs)):
        name = rnd.choice(traps) if rnd.random() < 0.5 else f"a{x}"
        if name.lower() in used:
            name = f"a{x}"
        used.add(name.lower())
        names.append(rnd.choice([name, name.capitalize(), name.upper()]))
    return names


def _gen(rnd: random.Random) -> tuple[str, str, int]:
    """(SQL del motor, SQL de SQLite, columnas visibles)."""
    dims = rnd.sample(DIMS, rnd.randint(0, 3))
    hidden = {d for d in dims if rnd.random() < 0.3}
    shown = [d for d in dims if d not in hidden]
    aggs = rnd.sample(AGGS, rnd.randint(1 if not shown else 0, 3)) or ["COUNT(*)"]
    aliased = [rnd.random() < 0.5 for _ in aggs]
    names = _aliases(rnd, aggs)
    # Ítems visibles: ("d", dim) | ("a", j); intercalados o dimensiones primero.
    items = [("d", d) for d in shown] + [("a", j) for j in range(len(aggs))]
    if rnd.random() < 0.6:
        rnd.shuffle(items)
    sel_e, sel_l = [], []
    for kind, x in items:
        if kind == "d":
            sel_e.append(_cased(rnd, x))
            sel_l.append(x)
        else:
            sel_e.append(f"{aggs[x]} AS {names[x]}" if aliased[x] else aggs[x])
            sel_l.append(f"{aggs[x]} AS a{x}")
    position = {x: n + 1 for n, (kind, x) in enumerate(items) if kind == "d"}
    where = f" WHERE {rnd.choice(WHERES)}" if rnd.random() < 0.7 else ""
    group_e = group_l = ""
    if dims:
        order = rnd.sample(dims, len(dims))
        group_e = " GROUP BY " + ", ".join(
            str(position[d]) if d in position and rnd.random() < 0.4 else _cased(rnd, d) for d in order)
        group_l = " GROUP BY " + ", ".join(order)
    sort = list(range(len(items))) + [("h", d) for d in hidden if rnd.random() < 0.5]
    rnd.shuffle(sort)
    order_e, order_l = [], []
    for s in sort:
        desc = rnd.choice(["", " DESC"])
        if isinstance(s, tuple):                          # dimensión oculta: solo por nombre
            order_e.append(_cased(rnd, s[1]) + desc)
            order_l.append(s[1] + desc)
            continue
        kind, x = items[s]
        if kind == "d":
            order_e.append((str(s + 1) if rnd.random() < 0.4 else _cased(rnd, x)) + desc)
            order_l.append(x + desc)
            continue
        r = rnd.random()
        if r < 0.35:
            ref = str(s + 1)
        elif r < 0.7 or not aliased[x]:
            ref = aggs[x] if rnd.random() < 0.5 else aggs[x].lower()
        else:
            ref = _cased(rnd, names[x])
        order_e.append(ref + desc)
        order_l.append(f"a{x}" + desc)
    limit = f" LIMIT {rnd.choice([1, 2, 3, 5, 50])}" if rnd.random() < 0.3 else ""
    engine = f"SELECT {', '.join(sel_e)} FROM columns{where}{group_e} ORDER BY {', '.join(order_e)}{limit}"
    lite = f"SELECT {', '.join(sel_l)} FROM columns{where}{group_l} ORDER BY {', '.join(order_l)}{limit}"
    return engine, lite, len(items)


def _norm(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return round(v, 6)
    return v


def _cell(v) -> str:
    return "" if v is None else str(v)


@pytest.mark.parametrize("seed", range(6))
def test_agrupados_intercalados_y_ocultos_iguales_a_sqlite(qdb, oracle, seed):
    logging.disable(logging.CRITICAL)
    rnd = random.Random(105_500 + seed)
    client = TestClient(app, raise_server_exceptions=False)
    problems, shapes = [], {"interleaved": 0, "hidden": 0, "trap": 0}
    try:
        for _ in range(70):
            engine, lite, width = _gen(rnd)
            qdb.problems.clear()
            res = client.post("/api/reporting/query/sql", json={"text": engine, "projectId": "p1"})
            if res.status_code != 200:
                problems.append(f"{res.status_code} {res.text[:140]}: {engine}")
                continue
            data = res.json()["data"]
            if qdb.problems:
                problems.append(f"Lakebase {qdb.problems[:1]}: {engine}")
            keys = [c["key"] for c in data["columns"]]
            got = [tuple(_norm(r.get(k)) for k in keys) for r in data["rows"]]
            want = [tuple(_norm(v) for v in row) for row in oracle.execute(lite).fetchall()]
            if len(keys) != width or got != want or any(set(r) != set(keys) for r in data["rows"]):
                problems.append(f"filas: motor {keys} {got[:4]} ≠ SQLite {want[:4]}: {engine}")
                continue
            spec = client.post("/api/reporting/query/validate",
                               json={"text": engine, "projectId": "p1"}).json()["data"]["spec"]
            shapes["interleaved"] += bool(spec.get("resultColumns")) and \
                spec["resultColumns"] != sorted(spec["resultColumns"], key=lambda k: k not in spec["groupBy"])
            shapes["hidden"] += bool(spec.get("groupBy")) and \
                not set(spec["groupBy"]) <= set(spec.get("resultColumns") or spec["groupBy"])
            names = [a["as"] for a in spec["aggregations"]]
            shapes["trap"] += any(n != m and n.lower().startswith(m.lower().split("_")[0]) and
                                  m == m.lower() for n in names for m in names)
            lines = list(csv.reader(io.StringIO(client.post("/api/reporting/export", json=spec).text)))
            if len(lines[0]) != width or lines[1:] != [[_cell(r.get(k)) for k in keys] for r in data["rows"]]:
                problems.append(f"CSV ≠ /query/sql: {engine}")
    finally:
        logging.disable(logging.NOTSET)
    assert not problems, "\n".join(problems[:15])
    assert shapes["interleaved"] and shapes["hidden"] and shapes["trap"], shapes   # ejercitó las tres formas
