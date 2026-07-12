"""Vistas curadas enriquecidas del reporting (07 §3): UDP Coverage, Domain Usage,
Glossary Usage, Relationships resueltas y el Model Health Scorecard.

Regla de escala: las stats globales salen de POCAS agregaciones `$group` corridas
en PARALELO (asyncio.gather) — a 400k cada una es ~2-3s, concurrentes ≈ la más
lenta. La cobertura UDP usa `$objectToArray`+`$unwind` acotado por `$match` a las
entidades que TIENEN valores (barato: sólo esas se desenrollan).
"""
from __future__ import annotations

import asyncio
import re

from app.core.db.client import get_db

ACTIVE = {"flgactive": {"$ne": False}}
_MAXMS = 30000


async def _agg(coll: str, pipeline: list, limit=None):
    db = await get_db()
    return await db[coll].aggregate(pipeline, maxTimeMS=_MAXMS).to_list(limit)


async def _one(coll: str, pipeline: list) -> dict:
    r = await _agg(coll, pipeline, 1)
    return r[0] if r else {}


# ── Model Health Scorecard ───────────────────────────────────────────────────
async def scorecard() -> dict:
    NULLISH = [None, ""]
    col_stats_pipe = [{"$match": ACTIVE}, {"$group": {"_id": None,
        "columns": {"$sum": 1},
        "noDomain": {"$sum": {"$cond": [{"$not": ["$parentDomainId"]}, 1, 0]}},
        "noDesc": {"$sum": {"$cond": [{"$in": [{"$ifNull": ["$description", ""]}, NULLISH]}, 1, 0]}},
        "overridden": {"$sum": {"$cond": ["$typeOverridden", 1, 0]}},
        "withUdp": {"$sum": {"$cond": [{"$gt": [{"$size": {"$ifNull": [{"$objectToArray": "$udpValues"}, []]}}, 0]}, 1, 0]}},
        "pk": {"$sum": {"$cond": ["$isPrimaryKey", 1, 0]}},
        "fk": {"$sum": {"$cond": ["$isForeignKey", 1, 0]}}}}]
    tbl_pk_pipe = [{"$match": ACTIVE}, {"$group": {"_id": "$tableId", "pk": {"$sum": {"$cond": ["$isPrimaryKey", 1, 0]}}}},
                   {"$group": {"_id": None, "withCols": {"$sum": 1}, "withPk": {"$sum": {"$cond": [{"$gt": ["$pk", 0]}, 1, 0]}}}}]
    tbl_stats_pipe = [{"$match": ACTIVE}, {"$group": {"_id": None, "tables": {"$sum": 1},
        "noDesc": {"$sum": {"$cond": [{"$in": [{"$ifNull": ["$description", ""]}, NULLISH]}, 1, 0]}},
        "withUdp": {"$sum": {"$cond": [{"$gt": [{"$size": {"$ifNull": [{"$objectToArray": "$udpValues"}, []]}}, 0]}, 1, 0]}}}}]
    rel_pipe = [{"$match": ACTIVE}, {"$project": {"t": ["$sourceTableId", "$targetTableId"]}}, {"$unwind": "$t"},
                {"$group": {"_id": "$t"}}, {"$count": "involved"}]

    cols, tpk, tstats, rel = await asyncio.gather(
        _one("canonical_columns", col_stats_pipe), _one("canonical_columns", tbl_pk_pipe),
        _one("canonical_tables", tbl_stats_pipe), _one("relationships", rel_pipe))

    n_cols = cols.get("columns", 0) or 1
    n_tbl = tstats.get("tables", 0) or 1
    without_pk = n_tbl - tpk.get("withPk", 0)
    orphans = n_tbl - (rel.get("involved", 0))
    # completeness = promedio ponderado de señales "0..1 = mejor"
    parts = [
        1 - cols.get("noDomain", 0) / n_cols,
        1 - cols.get("noDesc", 0) / n_cols,
        1 - without_pk / n_tbl,
        1 - tstats.get("noDesc", 0) / n_tbl,
    ]
    return {
        "tables": tstats.get("tables", 0), "columns": cols.get("columns", 0),
        "relationships": None,
        "tablesWithoutPk": without_pk, "tablesWithoutDescription": tstats.get("noDesc", 0),
        "orphanTables": max(orphans, 0),
        "columnsWithoutDomain": cols.get("noDomain", 0), "columnsWithoutDescription": cols.get("noDesc", 0),
        "columnsTypeOverridden": cols.get("overridden", 0),
        "pkColumns": cols.get("pk", 0), "fkColumns": cols.get("fk", 0),
        "udpFillRateColumn": round(cols.get("withUdp", 0) / n_cols, 4),
        "udpFillRateTable": round(tstats.get("withUdp", 0) / n_tbl, 4),
        "completenessScore": round(sum(parts) / len(parts), 4),
    }


# ── UDP Coverage (por definición) ────────────────────────────────────────────
async def _coverage_pairs(coll: str) -> dict[str, dict[str, int]]:
    """{defId: {value: count}} de las entidades ACTIVAS con udpValues."""
    pipe = [{"$match": {**ACTIVE, "udpValues": {"$exists": True, "$ne": {}}}},
            {"$project": {"kv": {"$objectToArray": "$udpValues"}}}, {"$unwind": "$kv"},
            {"$group": {"_id": {"k": "$kv.k", "v": "$kv.v"}, "n": {"$sum": 1}}}]
    out: dict[str, dict[str, int]] = {}
    for r in await _agg(coll, pipe):
        out.setdefault(r["_id"]["k"], {})[r["_id"]["v"]] = r["n"]
    return out


async def udp_coverage() -> list[dict]:
    db = await get_db()
    defs = await db["udp_definitions"].find(ACTIVE).to_list(None)
    col_total = await db["canonical_columns"].count_documents(ACTIVE)
    tbl_total = await db["canonical_tables"].count_documents(ACTIVE)
    sa_total = await db["subject_areas"].count_documents(ACTIVE)
    col_pairs, tbl_pairs, sa_pairs = await asyncio.gather(
        _coverage_pairs("canonical_columns"), _coverage_pairs("canonical_tables"),
        _coverage_pairs("subject_areas"))
    # Fuente por nivel (F5: 'canvas' = subject_areas). Fallback: tablas.
    pairs_of = {"column": col_pairs, "table": tbl_pairs, "canvas": sa_pairs}
    total_of = {"column": col_total, "table": tbl_total, "canvas": sa_total}
    rows = []
    for d in defs:
        did, level = str(d["_id"]), d.get("level")
        pairs = pairs_of.get(level, tbl_pairs).get(did, {})
        total = total_of.get(level, tbl_total)
        set_count = sum(pairs.values())
        allowed = set(d.get("allowedValues") or [])
        invalid = sum(n for v, n in pairs.items() if allowed and v not in allowed)
        breakdown = sorted(({"value": v, "count": n, "pct": round(n / (set_count or 1), 3)}
                            for v, n in pairs.items()), key=lambda x: -x["count"])
        rows.append({
            "defId": did, "name": d.get("name"), "level": level, "dataType": d.get("dataType"),
            "allowedValues": d.get("allowedValues") or [], "defaultValue": d.get("defaultValue"),
            "totalEntities": total, "setCount": set_count, "missingCount": total - set_count,
            "coveragePct": round(set_count / (total or 1), 4),
            "distinctValueCount": len(pairs), "valueBreakdown": breakdown, "invalidCount": invalid,
        })
    rows.sort(key=lambda r: (r["level"], (r["name"] or "").lower()))
    return rows


# ── Domain Usage ─────────────────────────────────────────────────────────────
async def domain_usage() -> list[dict]:
    db = await get_db()
    domains = {str(d["_id"]): d async for d in db["parent_domains"].find(ACTIVE)}
    pipe = [{"$match": {**ACTIVE, "parentDomainId": {"$ne": None}}},
            {"$group": {"_id": "$parentDomainId", "columnCount": {"$sum": 1},
                        "tables": {"$addToSet": "$tableId"},
                        "overrideCount": {"$sum": {"$cond": ["$typeOverridden", 1, 0]}},
                        "types": {"$addToSet": "$dataType"}}}]
    used = {r["_id"]: r for r in await _agg("canonical_columns", pipe)}
    rows = []
    for did, d in domains.items():
        u = used.get(did, {})
        cc = u.get("columnCount", 0)
        rows.append({
            "domainId": did, "name": d.get("name"), "defaultDataType": d.get("defaultDataType"),
            "namingTerm": d.get("namingTerm"), "columnCount": cc, "tableCount": len(u.get("tables", []) or []),
            "overrideCount": u.get("overrideCount", 0),
            "overridePct": round(u.get("overrideCount", 0) / (cc or 1), 3),
            "distinctDataTypes": sorted(u.get("types", []) or []), "isUnused": cc == 0,
        })
    rows.sort(key=lambda r: -r["columnCount"])
    return rows


# ── Glossary Usage (best-effort, acotado) ────────────────────────────────────
async def glossary_usage(limit: int = 200) -> list[dict]:
    """Uso de cada término del glosario. `usage` = columnas cuyo physicalName
    contiene la abreviatura (heurística barata; el físico se deriva del glosario).
    Acotado por seguridad de RU (una regex por término escala mal a 400k)."""
    db = await get_db()
    terms = await db["glossary_terms"].find(ACTIVE).to_list(None)
    rows = []
    for t in terms[:limit]:
        ab = (t.get("abbrev") or "").upper()
        cnt = await db["canonical_columns"].count_documents(
            {**ACTIVE, "physicalName": {"$regex": f"(^|_){re.escape(ab)}(_|$)"}}, maxTimeMS=_MAXMS) if ab else 0
        rows.append({"term": t.get("term"), "abbrev": t.get("abbrev"), "scope": t.get("scope"),
                     "wordType": t.get("wordType"), "columnUsage": cnt, "isUnused": cnt == 0})
    rows.sort(key=lambda r: -r["columnUsage"])
    return rows


# ── Relationships resueltas ──────────────────────────────────────────────────
async def relationships_report(limit: int = 2000) -> list[dict]:
    db = await get_db()
    rels = await db["relationships"].find(ACTIVE).limit(limit).to_list(limit)
    tids = {t for r in rels for t in (r.get("sourceTableId"), r.get("targetTableId")) if t}
    cids = {c for r in rels for c in (r.get("sourceColumnId"), r.get("targetColumnId")) if c}
    tmap = {str(t["_id"]): t async for t in db["canonical_tables"].find({"_id": {"$in": list(tids)}}, {"physicalName": 1, "schema": 1})}
    cmap = {str(c["_id"]): c.get("physicalName") async for c in db["canonical_columns"].find({"_id": {"$in": list(cids)}}, {"physicalName": 1})}
    rows = []
    for r in rels:
        st, tt = tmap.get(r.get("sourceTableId"), {}), tmap.get(r.get("targetTableId"), {})
        sc, tc = cmap.get(r.get("sourceColumnId"), "?"), cmap.get(r.get("targetColumnId"), "?")
        card = f"{'N' if r.get('sourceCardinality') == 'many' else '1'}:{'N' if r.get('targetCardinality') == 'many' else '1'}"
        rows.append({
            "id": str(r["_id"]),
            "source": f"{st.get('physicalName', '?')}.{sc}", "sourceSchema": st.get("schema"),
            "target": f"{tt.get('physicalName', '?')}.{tc}", "targetSchema": tt.get("schema"),
            "cardinality": card, "identifying": bool(r.get("identifying")),
            "isSelfReferencing": r.get("sourceTableId") == r.get("targetTableId"),
            "crossSchema": st.get("schema") != tt.get("schema"),
            "label": f"{st.get('physicalName', '?')}.{sc} → {tt.get('physicalName', '?')}.{tc} ({card})",
        })
    return rows
