"""Doc 105 (ronda 3) — fuzz del editor SQL del Reporting con SQLite de oráculo.

Cada consulta generada (soportada o no) va a `POST /query/sql` y a
`POST /query/validate`:
- nunca 500; si `/query/sql` responde 200, el SQL que el traductor de Lakebase
  arma para ella no tiene problemas (`LakebaseCheckingDb`);
- si responde 200, las columnas y las filas son las MISMAS que devuelve SQLite
  para el mismo texto sobre los mismos datos: un 200 con otro resultado es una
  cláusula que el parser ignoró o tradujo mal EN SILENCIO. Lo no soportado debe
  ser un 400/422 legible.

Diferencias DOCUMENTADAS del motor, que el generador evita a propósito: en
LIKE, `_` es literal; LIKE sin `%` es igualdad exacta (con `%`, sin distinguir
mayúsculas, como SQLite); y `<>`/`NOT` incluyen los registros con el campo
vacío (semántica del motor), por eso sólo se niegan campos que nunca están
vacíos. `LIMIT n` es el TOTAL de filas: se siguen todas las páginas (como
«Load more») y se compara el resultado completo."""
from __future__ import annotations

import random
import sqlite3

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

ROWS = [
    # id, physicalName (único), logicalName, dataType, ordinal, isPrimaryKey, description (nullable), tableId
    ("c00", "CLIENTE_ID", "Cliente Id", "INT", 0, True, "Llave del cliente", "t1"),
    ("c01", "NOMBRE", "Nombre", "STRING", 1, False, None, "t1"),
    ("c02", "APELLIDO", "Apellido", "STRING", 2, False, "Apellido paterno", "t1"),
    ("c03", "CUENTA_ID", "Cuenta Id", "INT", 0, True, "Llave", "t2"),
    ("c04", "SALDO", "Saldo", "DECIMAL", 1, False, None, "t2"),
    ("c05", "MONEDA", "Moneda", "STRING", 2, False, "ISO 4217", "t2"),
    ("c06", "FEC_ALTA", "Fecha Alta", "DATE", 3, False, None, "t2"),
    ("c07", "RIESGO_ID", "Riesgo Id", "INT", 0, True, "Llave", "t3"),
    ("c08", "SCORE", "Score", "DECIMAL", 1, False, "Puntaje", "t3"),
    ("c09", "NIVEL", "Nivel", "STRING", 2, False, None, "t3"),
    ("c10", "CLIENTE_ID_REF", "Cliente Ref", "INT", 3, False, "FK", "t3"),
    ("c11", "ESTADO", "Estado", "STRING", 12, False, "Activo o no", "t3"),
]
NAMES = [r[1] for r in ROWS]
NONNULL_TEXT = ["physicalName", "logicalName", "dataType", "tableId"]


@pytest.fixture(scope="module")
def oracle() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE columns (id TEXT, physicalName TEXT, logicalName TEXT, dataType TEXT, "
                "ordinal INTEGER, isPrimaryKey BOOLEAN, description TEXT, tableId TEXT)")
    con.executemany("INSERT INTO columns VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ROWS)
    return con


@pytest.fixture
def engine(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["canonical_columns"].insert_many([
        {"_id": i, "projectId": "p1", "flgactive": True, "physicalName": p, "logicalName": lg, "dataType": dt,
         "ordinal": o, "isPrimaryKey": pk, "tableId": t,
         # El vacío, a veces `null` guardado y a veces ausente (Lakebase los trata distinto).
         **({"description": d} if d is not None or int(i[1:]) % 2 else {})}
        for i, p, lg, dt, o, pk, d, t in ROWS])
    return fake


# ── Generador ────────────────────────────────────────────────────────────────
def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _cased(rnd: random.Random, text: str) -> str:
    return rnd.choice([text, text.lower(), text.title()])


def _text_value(rnd: random.Random, field: str) -> str:
    idx = {"physicalName": 1, "logicalName": 2, "dataType": 3, "tableId": 7, "description": 6}[field]
    values = [r[idx] for r in ROWS if r[idx] is not None]
    return rnd.choice(values + ["NO_EXISTE"])


def _like(rnd: random.Random, field: str) -> str:
    """Patrones que el motor soporta: prefijo `x%`, contiene `%x%` (sin
    distinguir mayúsculas) y exacto `x` (mismo caso); sin `_`."""
    value = _text_value(rnd, field).replace("_", "")
    cut = value[:rnd.randint(1, max(1, len(value)))]
    kind = rnd.random()
    if kind < 0.4:
        return _q(_cased(rnd, cut) + "%")
    if kind < 0.8:
        return _q("%" + _cased(rnd, cut[len(cut) // 2:] or cut) + "%")
    return _q(value)


def _supported(rnd: random.Random, depth: int = 0, negatable: bool = True, under_not: bool = False) -> str:
    """Condición del subconjunto soportado (y con la misma semántica en SQLite).
    `under_not`: va dentro de un NOT — nada sobre `description` (puede estar
    vacío: la negación del motor incluye los vacíos, la de SQL no)."""
    k = rnd.random()
    if depth < 2 and k < 0.15:
        op = rnd.choice(["AND", "OR"])
        return (f"({_supported(rnd, depth + 1, negatable, under_not)} {op} "
                f"{_supported(rnd, depth + 1, negatable, under_not)})")
    if depth < 2 and negatable and k < 0.22:
        return f"NOT ({_supported(rnd, depth + 1, True, True)})"
    kinds = ["text_eq", "text_ne", "num", "in", "not_in", "like", "not_like", "null", "bool"]
    kind = rnd.choice(kinds if under_not else kinds + ["desc"])
    if kind == "text_eq":
        f = rnd.choice(NONNULL_TEXT)
        return f"{f} = {_q(_text_value(rnd, f))}"
    if kind == "text_ne" and negatable:
        f = rnd.choice(NONNULL_TEXT)
        return f"{f} {rnd.choice(['<>', '!='])} {_q(_text_value(rnd, f))}"
    if kind == "num" or (kind == "text_ne" and not negatable):
        op = rnd.choice(["=", "<>", ">", ">=", "<", "<="] if negatable else ["=", ">", ">=", "<", "<="])
        n = rnd.choice(["0", "1", "2", "3", "12", "-1", "1.5", "1e1", "'2'"])
        return f"ordinal {op} {n}"
    if kind in ("in", "not_in"):
        f = rnd.choice(NONNULL_TEXT + ["ordinal"])
        vals = ([str(rnd.randint(0, 4)) for _ in range(rnd.randint(1, 3))] if f == "ordinal"
                else [_q(_text_value(rnd, f)) for _ in range(rnd.randint(1, 3))])
        neg = "NOT " if kind == "not_in" and negatable else ""
        return f"{f} {neg}IN ({', '.join(vals)})"
    if kind in ("like", "not_like"):
        f = rnd.choice(NONNULL_TEXT)
        neg = "NOT " if kind == "not_like" and negatable else ""
        return f"{f} {neg}LIKE {_like(rnd, f)}"
    if kind == "null":
        return f"description IS {rnd.choice(['', 'NOT '])}NULL"
    if kind == "bool":
        return f"isPrimaryKey {rnd.choice(['=', '<>'] if negatable else ['='])} {rnd.choice(['TRUE', 'FALSE'])}"
    return f"description {rnd.choice(['=', 'LIKE'])} {_q(_text_value(rnd, 'description') + ('%' if rnd.random() < 0.5 else ''))}"


# Cosas que el motor NO soporta (o soporta con otra semántica): deben ser 400/422
# — o, si responde 200, dar lo mismo que SQLite.
_ODD_WHERE = [
    "ordinal BETWEEN 1 AND 2", "ordinal NOT BETWEEN 1 AND 2", "UPPER(physicalName) = 'NOMBRE'", "ordinal + 1 > 2",
    "physicalName ILIKE 'nom%'", "description IS TRUE", "description IS NOT TRUE", "isPrimaryKey IS FALSE",
    "ordinal = NULL", "ordinal <> NULL", "description = NULL", "ordinal IN (NULL, 1)", "physicalName LIKE '%ID'",
    "physicalName LIKE 'C%ID'", "physicalName LIKE 'NOMBRE%%'", "physicalName LIKE '%%'", "physicalName LIKE ''",
    "physicalName LIKE 'N' ESCAPE '!'", "1 = ordinal", "isPrimaryKey = 'yes'", "isPrimaryKey = 2",
    "CAST(ordinal AS TEXT) = '1'", "CASE WHEN ordinal > 1 THEN 1 ELSE 0 END = 1", "physicalName = logicalName",
    "ordinal IN (SELECT ordinal FROM columns)", "physicalName IS NOT DISTINCT FROM 'NOMBRE'", "TRUE",
    "NOT physicalName", "physicalName GLOB 'N*'", "ordinal > ALL (SELECT 1)", "COALESCE(description, 'x') = 'x'",
]
_ODD_TAIL = [
    " LIMIT 0", " LIMIT -1", " LIMIT 1, 2", " LIMIT 2 OFFSET 1", " OFFSET 1", " FETCH FIRST 2 ROWS ONLY",
    " ORDER BY physicalName NULLS LAST", " ORDER BY physicalName DESC NULLS FIRST", " ORDER BY 1",
    " ORDER BY physicalName, ordinal", " ORDER BY ordinal", " ORDER BY UPPER(physicalName)", " LIMIT 1e1",
    " ORDER BY physicalName ASC NULLS FIRST", " ORDER BY physicalName DESC NULLS LAST", " LIMIT 5 PERCENT",
    " TABLESAMPLE (10 PERCENT)",
]
_ODD_FROM = ["dmh.columns", "main.columns", "columns AS c", "columns c"]
_ODD_SELECT = [
    "DISTINCT physicalName", "physicalName AS nombre", "*, physicalName", "physicalName, *", "COUNT(physicalName) AS n",
    "COUNT(DISTINCT dataType) AS n", "SUM(DISTINCT ordinal) AS s", "COUNT(*) AS n, physicalName",
    "UPPER(physicalName)", "1", "ordinal + 1 AS x", "COUNT(*) AS n, *", "MAX(ordinal) AS m, MIN(physicalName) AS p",
    "COUNT(1) AS n", "COUNT(*) AS _id", "COUNT(*) AS \"a.b\"", "COUNT(description) AS n",
    "COUNT(DISTINCT description) AS n",
]


def _query(rnd: random.Random) -> str:
    grouped = rnd.random() < 0.3
    if grouped:
        gb = rnd.choice(["dataType", "tableId", "isPrimaryKey", "dataType, tableId"])
        aggs = rnd.sample(["COUNT(*) AS n", "SUM(ordinal) AS s", "MIN(physicalName) AS lo", "MAX(ordinal) AS hi",
                           "AVG(ordinal) AS av", "COUNT(DISTINCT dataType) AS d",
                           "COUNT(DISTINCT description) AS dd"], rnd.randint(1, 2))
        text = f"SELECT {gb}, {', '.join(aggs)} FROM columns"
    else:
        text = "SELECT " + rnd.choice(["*", "physicalName", "physicalName, ordinal", "logicalName, dataType, isPrimaryKey",
                                       "c.physicalName"]) + " FROM columns" + (" AS c" if rnd.random() < 0.2 else "")
        if "c." in text and " AS c" not in text:
            text += " AS c"
    if rnd.random() < 0.3:
        text = text.replace("SELECT ", "SELECT ", 1)
        text = f"SELECT {rnd.choice(_ODD_SELECT)} FROM columns"
        grouped = "COUNT" in text or "SUM" in text or "MAX" in text
    if rnd.random() < 0.85:
        cond = _supported(rnd) if rnd.random() < 0.8 else rnd.choice(_ODD_WHERE)
        text += f" WHERE {cond}"
    if grouped and "GROUP BY" not in text and rnd.random() < 0.7 and text.startswith("SELECT dataType"):
        pass
    if text.startswith(("SELECT dataType", "SELECT tableId", "SELECT isPrimaryKey")) and grouped:
        text += f" GROUP BY {text.split('SELECT ', 1)[1].split(', ')[0] if 'dataType, tableId' not in text else 'dataType, tableId'}"
        if rnd.random() < 0.5:
            text += " ORDER BY " + rnd.choice(["n", "s", "lo", "hi", "av", "d", "dataType", "tableId"]).split()[0] \
                + rnd.choice(["", " DESC"])
    elif rnd.random() < 0.5 and not grouped:
        text += rnd.choice([" ORDER BY physicalName", " ORDER BY physicalName DESC", ""])
    if rnd.random() < 0.35:
        text += rnd.choice([" LIMIT 1", " LIMIT 3", " LIMIT 100"] + _ODD_TAIL)
    return text


# ── Comparación con SQLite ───────────────────────────────────────────────────
def _norm(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return round(v, 6)
    return v


def _sqlite(con: sqlite3.Connection, text: str):
    """(nombres, filas) de SQLite; None si SQLite no acepta el texto."""
    sql = text
    if " LIMIT " in sql and " ORDER BY " not in sql and "GROUP BY" not in sql:
        # El motor ordena por `physicalName` por defecto (keyset).
        head, tail = sql.split(" LIMIT ", 1)
        sql = f"{head} ORDER BY physicalName LIMIT {tail}"
    try:
        cur = con.execute(sql)
    except sqlite3.Error:
        return None
    names = [d[0] for d in cur.description]
    return names, [tuple(_norm(v) for v in row) for row in cur.fetchall()]


def _differs(text: str, data: dict, expected) -> str | None:
    if expected is None:
        return "el motor aceptó un texto que SQLite rechaza"
    names, rows = expected
    star = "*" in text.split(" FROM ", 1)[0] and "COUNT(*)" not in text
    keys = [c["key"] for c in data["columns"]]
    ours = data["rows"]
    if star:
        # `*` del motor = su catálogo (más campos que la tabla de SQLite): cada
        # columna de SQLite que el catálogo conoce debe salir.
        missing = [n for n in names if n != "id" and n not in keys]
        if missing:
            return f"columnas: al `*` del motor le faltan {missing}"
        got = [(r["_id"],) for r in ours]
        want = [(row[names.index("id")],) for row in rows]
    else:
        if keys != names:
            return f"columnas: motor {keys} ≠ SQLite {names}"
        got = [tuple(_norm(r.get(k)) for k in keys) for r in ours]
        want = rows
    limited = " LIMIT " in text
    if not limited and sorted(got, key=repr) != sorted(want, key=repr):
        return f"filas: motor {sorted(got, key=repr)[:6]} ≠ SQLite {sorted(want, key=repr)[:6]}"
    if " ORDER BY " in text and not star:
        # Con empates el orden entre ellos no está definido (ni aquí ni en
        # SQLite): se compara la SECUENCIA de la clave de orden.
        key = text.rsplit(" ORDER BY ", 1)[1].split()[0].split(".")[-1]
        at = names.index(key) if key in names else None
        if at is not None and [g[at] for g in got] != [w[at] for w in want]:
            return f"orden: motor {[g[at] for g in got][:8]} ≠ SQLite {[w[at] for w in want][:8]}"
        if at is None and got != want:
            return f"orden/filas: motor {got[:6]} ≠ SQLite {want[:6]}"
    elif limited and "GROUP BY" not in text and got != want:
        return f"página: motor {got[:6]} ≠ SQLite {want[:6]}"       # orden por defecto: physicalName
    elif limited and len(got) != len(want):
        return f"LIMIT: motor {len(got)} filas ≠ SQLite {len(want)}"
    return None


def _check_all(engine, oracle, texts) -> list[str]:
    client = TestClient(app, raise_server_exceptions=False)
    problems: list[str] = []
    for text in texts:
        engine.problems.clear()
        res = client.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
        val = client.post("/api/reporting/query/validate", json={"text": text, "projectId": "p1"})
        if res.status_code >= 500 or val.status_code >= 500:
            problems.append(f"500 ({res.status_code}/{val.status_code}): {text}")
            continue
        if res.status_code != 200:
            assert res.status_code in (400, 422), (res.status_code, text)
            continue
        if engine.problems:
            problems.append(f"Lakebase {engine.problems[:2]}: {text}")
        data = res.json()["data"]
        # Como «Load more»: se siguen TODAS las páginas — `LIMIT n` es el total.
        cursor, pages = data["nextCursor"], 1
        while cursor and pages < 30:
            nxt = client.post(f"/api/reporting/query/sql?cursor={cursor}", json={"text": text, "projectId": "p1"})
            assert nxt.status_code == 200, (nxt.status_code, text)
            data = {**data, "rows": data["rows"] + nxt.json()["data"]["rows"]}
            cursor, pages = nxt.json()["data"]["nextCursor"], pages + 1
        diff = _differs(text, data, _sqlite(oracle, text))
        if diff:
            problems.append(f"{diff}: {text}")
    return problems


@pytest.mark.parametrize("seed", range(8))
def test_editor_sql_nunca_500_ni_resultado_distinto_de_sqlite(engine, oracle, seed):
    rnd = random.Random(10_500 + seed)
    problems = _check_all(engine, oracle, [_query(rnd) for _ in range(60)])
    assert not problems, "\n".join(problems)


def test_editor_sql_cada_forma_rara_al_menos_una_vez(engine, oracle):
    """Recorrido DETERMINISTA de todas las formas raras (el azar puede no tocar
    alguna): cada una es 400/422 o da lo mismo que SQLite."""
    base = "SELECT physicalName FROM columns"
    texts = [f"{base} WHERE {w}" for w in _ODD_WHERE] + [f"{base}{t}" for t in _ODD_TAIL]
    texts += [f"SELECT {sel} FROM columns" for sel in _ODD_SELECT]
    texts += [f"SELECT {sel} FROM columns WHERE physicalName = 'NO_EXISTE'" for sel in _ODD_SELECT]
    texts += [f"SELECT physicalName FROM {src} WHERE ordinal > 1" for src in _ODD_FROM]
    problems = _check_all(engine, oracle, texts)
    assert not problems, "\n".join(problems)
