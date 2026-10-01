"""Vistas curadas enriquecidas del reporting (07 §3): UDP Coverage, Domain Usage,
Glossary Usage, Relationships resueltas y el Model Health Scorecard.

Doc 75: cada insight es de UN proyecto (`scoped(project_id, ACTIVE)` en todo
`$match`/`find`/`count`).

Regla de escala: las stats del proyecto salen de POCAS agregaciones `$group`
corridas en PARALELO (asyncio.gather) — a 400k cada una es ~2-3s, concurrentes ≈ la más
lenta. La cobertura UDP usa `$objectToArray`+`$unwind` acotado por `$match` a las
entidades que TIENEN valores (barato: sólo esas se desenrollan).
"""
from __future__ import annotations

import asyncio
import re

from app.core.db.client import get_db
from app.core.facets import udp_view
from app.core.scope import scoped
from app.features.changesets import repository as cs_repo

from . import repository
from .draft import REL_ENDS, changes_of, overlay_named, overlay_project, overlay_relationships

ACTIVE = {"flgactive": {"$ne": False}}
_MAXMS = 30000


async def _agg(coll: str, pipeline: list, limit=None):
    db = await get_db()
    return await db[coll].aggregate(pipeline, maxTimeMS=_MAXMS).to_list(limit)


async def _one(coll: str, pipeline: list) -> dict:
    r = await _agg(coll, pipeline, 1)
    return r[0] if r else {}


# ── Model Health Scorecard ───────────────────────────────────────────────────
_COLUMN_METRICS = ("columns", "noDomain", "noDesc", "overridden", "withUdp", "pk", "fk")


async def _active_table_ids(project_id: str) -> set[str]:
    db = await get_db()
    return {str(t["_id"]) async for t in db["canonical_tables"].find(scoped(project_id, ACTIVE), {"_id": 1})}


async def scorecard(project_id: str) -> dict:
    NULLISH = [None, ""]
    active = scoped(project_id, ACTIVE)
    # Doc 105 (ronda 4): las métricas de columnas POR TABLA y se suman sólo las
    # de tablas ACTIVAS — una columna activa de una tabla borrada contaba en
    # `columns`, `pkColumns`, `fkColumns`… y como «tabla con PK» (ronda 3).
    col_by_table_pipe = [{"$match": active}, {"$group": {"_id": "$tableId",
        "columns": {"$sum": 1},
        "noDomain": {"$sum": {"$cond": [{"$not": ["$parentDomainId"]}, 1, 0]}},
        "noDesc": {"$sum": {"$cond": [{"$in": [{"$ifNull": ["$description", ""]}, NULLISH]}, 1, 0]}},
        "overridden": {"$sum": {"$cond": ["$typeOverridden", 1, 0]}},
        "withUdp": {"$sum": {"$cond": [{"$gt": [{"$size": {"$ifNull": [{"$objectToArray": "$udpValues"}, []]}}, 0]}, 1, 0]}},
        "pk": {"$sum": {"$cond": ["$isPrimaryKey", 1, 0]}},
        "fk": {"$sum": {"$cond": ["$isForeignKey", 1, 0]}}}}]
    tbl_stats_pipe = [{"$match": active}, {"$group": {"_id": None, "tables": {"$sum": 1},
        "noDesc": {"$sum": {"$cond": [{"$in": [{"$ifNull": ["$description", ""]}, NULLISH]}, 1, 0]}},
        "withUdp": {"$sum": {"$cond": [{"$gt": [{"$size": {"$ifNull": [{"$objectToArray": "$udpValues"}, []]}}, 0]}, 1, 0]}}}}]
    # Doc 105 (revisión, hallazgo 7): extremos v2 (parent/child) con fallback a
    # los legacy (target/source) — la regla del conteo por tabla del reporte
    # (`repository._relationships`). Antes sólo source/target: con relaciones
    # v2 las huérfanas salían de más. `$addToSet` (y no un `$project` a un
    # array): mongomock no evalúa expresiones dentro de un array literal.
    rel_pipe = [{"$match": active}, {"$group": {"_id": None,
        "parents": {"$addToSet": {"$ifNull": ["$parentTableId", "$targetTableId"]}},
        "children": {"$addToSet": {"$ifNull": ["$childTableId", "$sourceTableId"]}}}}]

    by_table, tstats, rel, table_ids = await asyncio.gather(
        _agg("canonical_columns", col_by_table_pipe), _one("canonical_tables", tbl_stats_pipe),
        _one("relationships", rel_pipe), _active_table_ids(project_id))

    live = [g for g in by_table if g.get("_id") in table_ids]
    cols = {k: sum(g.get(k) or 0 for g in live) for k in _COLUMN_METRICS}
    tables = tstats.get("tables", 0)
    n_cols = cols["columns"] or 1
    n_tbl = tables or 1
    # Conteo REAL (doc 105): el divisor `n_tbl` (≥ 1) no es un conteo — un
    # proyecto vacío tenía «1 tabla sin PK» y su completitud salía 0.75.
    without_pk = max(tables - sum(1 for g in live if (g.get("pk") or 0) > 0), 0)
    # Huérfana = tabla ACTIVA sin ninguna relación (una relación hacia una tabla
    # inactiva no cuenta), con el conteo real (un proyecto vacío no tiene 1).
    involved = {t for t in (*(rel.get("parents") or []), *(rel.get("children") or [])) if t}
    orphans = tables - len(involved & table_ids)
    # completeness = promedio ponderado de señales "0..1 = mejor"
    parts = [
        1 - cols["noDomain"] / n_cols,
        1 - cols["noDesc"] / n_cols,
        1 - without_pk / n_tbl,
        1 - tstats.get("noDesc", 0) / n_tbl,
    ]
    return {
        "tables": tables, "columns": cols["columns"],
        "relationships": None,
        "tablesWithoutPk": without_pk, "tablesWithoutDescription": tstats.get("noDesc", 0),
        "orphanTables": max(orphans, 0),
        "columnsWithoutDomain": cols["noDomain"], "columnsWithoutDescription": cols["noDesc"],
        "columnsTypeOverridden": cols["overridden"],
        "pkColumns": cols["pk"], "fkColumns": cols["fk"],
        "udpFillRateColumn": round(cols["withUdp"] / n_cols, 4),
        "udpFillRateTable": round(tstats.get("withUdp", 0) / n_tbl, 4),
        "completenessScore": round(sum(parts) / len(parts), 4),
    }


# ── UDP Coverage (por definición) ────────────────────────────────────────────
async def _coverage_pairs(project_id: str, coll: str) -> dict[str, dict[str, int]]:
    """{defId: {value: count}} de las entidades ACTIVAS del proyecto con udpValues."""
    pipe = [{"$match": scoped(project_id, {**ACTIVE, "udpValues": {"$exists": True, "$ne": {}}})},
            {"$project": {"kv": {"$objectToArray": "$udpValues"}}}, {"$unwind": "$kv"},
            {"$group": {"_id": {"k": "$kv.k", "v": "$kv.v"}, "n": {"$sum": 1}}}]
    out: dict[str, dict[str, int]] = {}
    for r in await _agg(coll, pipe):
        out.setdefault(r["_id"]["k"], {})[r["_id"]["v"]] = r["n"]
    return out


async def udp_coverage(project_id: str) -> list[dict]:
    db = await get_db()
    active = scoped(project_id, ACTIVE)
    defs = await db["udp_definitions"].find(active).to_list(None)
    col_total = await db["canonical_columns"].count_documents(active)
    tbl_total = await db["canonical_tables"].count_documents(active)
    sa_total = await db["subject_areas"].count_documents(active)
    vw_total = await db["views"].count_documents(active)
    col_pairs, tbl_pairs, sa_pairs, vw_pairs = await asyncio.gather(
        _coverage_pairs(project_id, "canonical_columns"), _coverage_pairs(project_id, "canonical_tables"),
        _coverage_pairs(project_id, "subject_areas"), _coverage_pairs(project_id, "views"))
    # Fuente por nivel (F5: 'canvas' = subject_areas; doc 61: 'view' = views).
    # Fallback: tablas.
    pairs_of = {"column": col_pairs, "table": tbl_pairs, "canvas": sa_pairs,
                "view": vw_pairs}
    total_of = {"column": col_total, "table": tbl_total, "canvas": sa_total,
                "view": vw_total}
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
            "defId": did, "name": d.get("name"), "level": level, "view": udp_view(d),
            "dataType": d.get("dataType"),
            "allowedValues": d.get("allowedValues") or [], "defaultValue": d.get("defaultValue"),
            "totalEntities": total, "setCount": set_count, "missingCount": total - set_count,
            "coveragePct": round(set_count / (total or 1), 4),
            "distinctValueCount": len(pairs), "valueBreakdown": breakdown, "invalidCount": invalid,
        })
    # `level or ""`: los defs salen de Mongo CRUDOS (sin validar por modelo) —
    # uno sin `level` (script/consola) no debe tirar 500 a todo Insights.
    rows.sort(key=lambda r: (r["level"] or "", (r["name"] or "").lower()))
    return rows


# ── Domain Usage ─────────────────────────────────────────────────────────────
async def domain_usage(project_id: str) -> list[dict]:
    db = await get_db()
    domains = {str(d["_id"]): d async for d in db["parent_domains"].find(scoped(project_id, ACTIVE))}
    pipe = [{"$match": scoped(project_id, {**ACTIVE, "parentDomainId": {"$ne": None}})},
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
async def glossary_usage(project_id: str, limit: int = 200) -> list[dict]:
    """Uso de cada término del glosario del proyecto. `usage` = columnas cuyo
    physicalName contiene la abreviatura (heurística barata; el físico se
    deriva del glosario). Acotado por seguridad de RU (una regex por término
    escala mal a 400k)."""
    db = await get_db()
    terms = await db["glossary_terms"].find(scoped(project_id, ACTIVE)).to_list(None)
    rows = []
    for t in terms[:limit]:
        ab = (t.get("abbrev") or "").upper()
        cnt = await db["canonical_columns"].count_documents(
            scoped(project_id, {**ACTIVE, "physicalName": {"$regex": f"(^|_){re.escape(ab)}(_|$)"}}),
            maxTimeMS=_MAXMS) if ab else 0
        rows.append({"term": t.get("term"), "abbrev": t.get("abbrev"), "scope": t.get("scope"),
                     "columnUsage": cnt, "isUnused": cnt == 0})
    rows.sort(key=lambda r: -r["columnUsage"])
    return rows


# ── Relationships resueltas ──────────────────────────────────────────────────
# Cardinalidad de plataforma → etiqueta legible por lado. El mapeo viejo
# ('N' si == 'many', si no '1') aplanaba zero-many/one-many/zero-one a "1".
_CARD_LABEL = {"one": "1", "zero-one": "0..1", "one-many": "1..N",
               "zero-many": "0..N", "many": "N"}


def _rel_docs(raw: list[dict]) -> list[dict]:
    """`_id` → `id` (el overlay de la versión trabaja por id). Puro."""
    return [{**{k: v for k, v in r.items() if k != "_id"}, "id": str(r["_id"])} for r in raw]


async def _first_relationships(db, project_id: str, limit: int, rel_changes: dict) -> list[dict]:
    """Las primeras `limit` relaciones del proyecto (con la versión, si hay)."""
    cur = db["relationships"].find(scoped(project_id, ACTIVE))
    read = limit
    if rel_changes:
        # Doc 105 (A4): con la versión, el tope va DESPUÉS del overlay y sobre
        # un orden estable (`_id`): se leen `limit` + una por cada baja (pueden
        # caer dentro de la ventana) y se corta ya superpuesto. Antes salían
        # más filas que el tope (altas) o faltaban las que sí entraban (bajas).
        cur = cur.sort("_id", 1)
        read = limit + sum(1 for ch in rel_changes.values() if ch.get("op") == "delete")
    docs = _rel_docs(await cur.limit(read).to_list(read))
    if rel_changes:
        docs = sorted(overlay_project(docs, rel_changes, project_id), key=lambda d: d["id"])[:limit]
    return docs


async def _lot_relationships(db, project_id: str, table_ids: list[str],
                             changeset_id: str | None) -> list[dict]:
    """Doc 105 (A3-o1): TODAS las relaciones con el padre o el hijo en el lote
    (sin tope global: el lote ya acota), con la versión si hay. Orden por id.
    De la versión se leen SÓLO los cambios que tocan el lote (revisión del doc
    105, hallazgo 1: cada lote releía el ledger entero del draft)."""
    query = scoped(project_id, {**ACTIVE, "$or": [{k: {"$in": table_ids}} for k in REL_ENDS]})
    docs = _rel_docs(await db["relationships"].find(query).to_list(None))
    if changeset_id:
        rel_changes = await repository.relationship_changes_touching(
            changeset_id, [d["id"] for d in docs], table_ids)
        if rel_changes:
            docs = overlay_relationships(docs, rel_changes, project_id, set(table_ids))
    return sorted(docs, key=lambda d: d["id"])


def _validated(docs: list[dict]) -> tuple[list[dict], set[str], set[str]]:
    """Relaciones validadas (una legacy sale en forma v2) + los ids de las
    tablas y columnas de sus pares. Puro."""
    from app.features.relationships.models import RelationshipDoc

    rels = [RelationshipDoc.model_validate(d).model_dump() for d in docs]
    tids = {t for r in rels for t in (r["parentTableId"], r["childTableId"]) if t}
    cids = {c for r in rels for p in r["pairs"]
            for c in (p.get("parentColumnId"), p.get("childColumnId")) if c}
    return rels, tids, cids


async def _published_names(db, tids: set[str], cids: set[str]) -> tuple[dict, dict]:
    """{tableId: {physicalName, schema}} y {columnId: physicalName} publicados."""
    tmap = {str(t["_id"]): t async for t in db["canonical_tables"].find({"_id": {"$in": list(tids)}}, {"physicalName": 1, "schema": 1})}
    cmap = {str(c["_id"]): c.get("physicalName") async for c in db["canonical_columns"].find({"_id": {"$in": list(cids)}}, {"physicalName": 1})}
    return tmap, cmap


def _version_names(tmap: dict, cmap: dict, tids: set[str], cids: set[str],
                   table_changes: dict, column_changes: dict) -> tuple[dict, dict]:
    """Los nombres con los renombres, altas y bajas de la versión. Puro."""
    tmap = overlay_named(tmap, table_changes, tids)
    named = overlay_named({k: {"physicalName": v} for k, v in cmap.items()}, column_changes, cids)
    return tmap, {k: d.get("physicalName") for k, d in named.items()}


async def relationships_report(project_id: str, limit: int = 2000,
                               changes: dict | None = None) -> list[dict]:
    """Relaciones del proyecto resueltas, UNA FILA POR PAR de columnas (v2,
    doc 19): una FK compuesta de 3 columnas emite 3 filas con el mismo `id` y
    `pairIndex` incremental — conserva el espíritu "1 fila por columna FK" del
    export. Doc 102: con una versión propia (`changes`), sus relaciones y los
    nombres de tablas y columnas de esa versión. Las primeras `limit` (GET de
    compatibilidad); el export pide LOTES (`relationships_lot_report`)."""
    db = await get_db()
    docs = await _first_relationships(db, project_id, limit, changes_of(changes, "relationships"))
    rels, tids, cids = _validated(docs)
    tmap, cmap = await _published_names(db, tids, cids)
    if changes:
        tmap, cmap = _version_names(tmap, cmap, tids, cids, changes_of(changes, "canonical_tables"),
                                    changes_of(changes, "canonical_columns"))
    return _rows(rels, tmap, cmap)


async def relationships_lot_report(project_id: str, table_ids: list[str],
                                   changeset_id: str | None = None) -> list[dict]:
    """Doc 105 (A3-o1): las MISMAS filas que `relationships_report`, pero sólo
    de las relaciones que tocan el LOTE `table_ids` (padre o hijo en él) y sin
    `limit`. Con `changeset_id` (versión propia ya validada por
    `versions.open_version`), la versión — leyendo del ledger SÓLO lo que toca
    el lote: sus relaciones y los nombres de las tablas y columnas de SUS pares
    (revisión del doc 105, hallazgo 1: con un draft de carga Excel, releer el
    ledger entero en cada uno de los ~50 lotes no escala)."""
    db = await get_db()
    docs = await _lot_relationships(db, project_id, table_ids, changeset_id)
    rels, tids, cids = _validated(docs)
    tmap, cmap = await _published_names(db, tids, cids)
    if changeset_id:
        tmap, cmap = _version_names(
            tmap, cmap, tids, cids,
            await repository.version_changes_by_ids(changeset_id, "canonical_tables", sorted(tids)),
            await cs_repo.column_changes_for_tables(changeset_id, sorted(cids), []))
    return _rows(rels, tmap, cmap)


def _rows(rels: list[dict], tmap: dict, cmap: dict) -> list[dict]:
    """Una fila por PAR de columnas, con los extremos resueltos. Puro."""
    rows = []
    for r in rels:
        pt, ct = tmap.get(r["parentTableId"], {}), tmap.get(r["childTableId"], {})
        p_card = r.get("parentCardinality") or "one"
        c_card = r.get("childCardinality") or "zero-many"
        card = f"{_CARD_LABEL.get(p_card, p_card)} → {_CARD_LABEL.get(c_card, c_card)}"
        pt_name, ct_name = pt.get("physicalName", "?"), ct.get("physicalName", "?")
        for i, p in enumerate(r["pairs"]):
            pc = cmap.get(p.get("parentColumnId"), "?")
            cc = cmap.get(p.get("childColumnId"), "?")
            rows.append({
                "id": r["id"], "pairIndex": i, "pairCount": len(r["pairs"]),
                # Extremos SEPARADOS (ids + tabla + columna) para el export por
                # niveles del Reporting; `parent`/`child` combinados se mantienen.
                "parentTableId": r["parentTableId"], "childTableId": r["childTableId"],
                "parentTable": pt_name, "parentColumn": pc,
                "childTable": ct_name, "childColumn": cc,
                "parent": f"{pt_name}.{pc}", "parentSchema": pt.get("schema"),
                "child": f"{ct_name}.{cc}", "childSchema": ct.get("schema"),
                "roleName": p.get("roleName"),
                # Doc 98: frases de la RELACIÓN (se repiten en cada fila de par).
                "parentToChildPhrase": r.get("parentToChildPhrase"),
                "childToParentPhrase": r.get("childToParentPhrase"),
                "cardinality": card, "identifying": bool(r.get("identifying")),
                "subcategory": bool(r.get("subcategory")),
                "isSelfReferencing": r["parentTableId"] == r["childTableId"],
                "crossSchema": pt.get("schema") != ct.get("schema"),
                "label": f"{pt_name}.{pc} → {ct_name}.{cc} ({card})",
            })
    return rows
