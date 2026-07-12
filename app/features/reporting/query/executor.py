"""Executor async del motor de reporting: carga el catálogo dinámico (UDP),
pre-resuelve filtros cross-entity (schema→tableId), compila, pagina por KEYSET,
ejecuta contra Cosmos e hidrata nombres (dominio, UDP, schema) en Python.

Reglas de escala: pushdown de todos los filtros al $match, keyset (no skip/limit
profundo), proyección mínima, `maxTimeMS` como circuit-breaker.
"""
from __future__ import annotations

import base64
import json

from app.core.db.client import get_db

from .compiler import Compiled, QueryError, build_match, compile_spec
from .schema import FieldDef, build_catalog
from .spec import Condition, QuerySpec, WhereGroup

COLL_OF = {"columns": "canonical_columns", "tables": "canonical_tables",
           "relationships": "relationships", "views": "views",
           "models": "subject_areas"}
# Campo de orden por defecto del row-query (keyset). `subject_areas` no tiene
# physicalName: la entidad `models` ordena por `name` (indexado en indexes.py).
DEFAULT_SORT_FIELD = {"models": "name"}
ACTIVE = {"flgactive": {"$ne": False}}
MAX_TIME_MS = 15000


async def _udp_defs() -> list[dict]:
    db = await get_db()
    docs = await db["udp_definitions"].find(ACTIVE).to_list(None)
    return [{**d, "id": str(d.pop("_id"))} for d in docs]


async def get_catalog(from_: str) -> dict[str, FieldDef]:
    return build_catalog(from_, await _udp_defs())


def _encode_cursor(sort_val, _id) -> str:
    return base64.urlsafe_b64encode(json.dumps([sort_val, str(_id)]).encode()).decode()


def _decode_cursor(cur: str):
    try:
        v = json.loads(base64.urlsafe_b64decode(cur.encode()).decode())
    except Exception:
        raise QueryError("Cursor inválido", code=400)
    # El cursor va DIRECTO al $match; si sort_val fuese dict/list un atacante
    # inyectaría operadores Mongo ({"$ne":null} bypassea el keyset, {"$regex":…}
    # ReDoS). Solo se aceptan escalares para el valor y string para el _id.
    if (not isinstance(v, list) or len(v) != 2
            or isinstance(v[0], (dict, list)) or not isinstance(v[1], str)):
        raise QueryError("Cursor inválido", code=400)
    return v


async def _rewrite_cross_entity(node, catalog: dict[str, FieldDef]):
    """Reemplaza condiciones de campos cross-entity (schema en `columns`, que vive
    en la tabla) por su equivalente NATIVO `tableId $in [...]`, pre-resolviendo
    contra canonical_tables (barrido barato de 10k, índice de schema)."""
    if node is None:
        return None
    if isinstance(node, Condition):
        fd = catalog.get(node.field)
        if fd and fd.entity == "table" and node.field == "schema":
            db = await get_db()
            vals = node.value if isinstance(node.value, list) else [node.value]
            if node.op in ("eq", "in"):
                ids = [str(t["_id"]) async for t in
                       db["canonical_tables"].find({**ACTIVE, "schema": {"$in": vals}}, {"_id": 1})]
                return Condition(field="tableId", op="in", value=ids or ["__none__"])
            raise QueryError("Filtro por schema en columns sólo soporta = / in", code=422)
        return node
    subs = [await _rewrite_cross_entity(c, catalog) for c in node.conditions]
    return WhereGroup(op=node.op, conditions=[s for s in subs if s is not None])


async def _hydration_maps(select: list[FieldDef], from_: str) -> dict:
    """Mapas para resolver nombres post-fetch (una sola lectura c/u)."""
    db, maps = await get_db(), {}
    if any(fd.hydrate == "domain" for fd in select):
        maps["domain"] = {str(d["_id"]): d.get("name")
                          async for d in db["parent_domains"].find(ACTIVE, {"name": 1})}
    if from_ == "columns" and any(fd.key == "schema" for fd in select):
        maps["schema"] = {str(t["_id"]): t.get("schema")
                          async for t in db["canonical_tables"].find(ACTIVE, {"schema": 1})}
    return maps


def _row(doc: dict, select: list[FieldDef], maps: dict) -> dict:
    out = {"_id": str(doc.get("_id"))}
    for fd in select:
        if fd.hydrate == "domain":
            out[fd.key] = maps.get("domain", {}).get(doc.get(fd.path))
        elif fd.hydrate == "derived":
            # Campo calculado post-fetch (F5: tableCount = len(tableIds)). No
            # se computa en Mongo: $size en projection de find() no es portable
            # a Cosmos RU; el path proyecta la fuente (array) y acá se cuenta.
            out[fd.key] = len(doc.get(fd.path) or [])
        elif fd.udpDefId:
            out[fd.key] = (doc.get("udpValues") or {}).get(fd.udpDefId)
        elif fd.key == "schema" and fd.entity == "table":
            out[fd.key] = maps.get("schema", {}).get(doc.get("tableId"))
        else:
            out[fd.key] = doc.get(fd.path)
    return out


async def run_query(spec: QuerySpec, cursor: str | None = None) -> dict:
    catalog = await get_catalog(spec.from_)
    where = await _rewrite_cross_entity(spec.where, catalog)
    spec2 = spec.model_copy(update={"where": where})
    compiled = compile_spec(spec2, catalog)
    db = await get_db()
    coll = db[COLL_OF[spec.from_]]
    base_match = {**ACTIVE, **compiled.match}

    if compiled.grouped:
        pipe = [{"$match": base_match}, {"$group": compiled.group}, {"$project": compiled.project}]
        if compiled.sort:
            pipe.append({"$sort": {f: d for f, d in compiled.sort}})
        pipe.append({"$limit": spec.limit})
        rows = await coll.aggregate(pipe, maxTimeMS=MAX_TIME_MS).to_list(spec.limit)
        for r in rows:
            r.pop("_id", None)
        cols = [{"key": gb, "label": catalog[gb].label, "type": catalog[gb].type} for gb in spec.groupBy]
        cols += [{"key": a.as_, "label": a.as_, "type": "number"} for a in spec.aggregations]
        return {"rows": rows, "columns": cols, "nextCursor": None, "hasMore": False,
                "meta": {"grouped": True, "warnings": compiled.warnings}}

    # ── Filas: keyset sobre el primer campo de orden (+ _id de desempate) ──
    sort = list(compiled.sort) or [(DEFAULT_SORT_FIELD.get(spec.from_, "physicalName"), 1)]
    sort_path, sort_dir = sort[0]
    full_sort = [(sort_path, sort_dir), ("_id", 1)]
    if cursor:
        cv, cid = _decode_cursor(cursor)
        gt = "$gt" if sort_dir == 1 else "$lt"
        base_match = {"$and": [base_match, {"$or": [
            {sort_path: {gt: cv}},
            {sort_path: cv, "_id": {"$gt": cid}}]}]}
    maps = await _hydration_maps(compiled.select, spec.from_)
    proj = {**compiled.project, sort_path: 1}
    # Proyectar las FUENTES de hidratación cross-entity (schema en columns viene
    # de la tabla → necesita tableId).
    if spec.from_ == "columns" and any(fd.key == "schema" for fd in compiled.select):
        proj["tableId"] = 1
    docs = await coll.find(base_match, proj).sort(full_sort).limit(spec.limit + 1).max_time_ms(MAX_TIME_MS).to_list(spec.limit + 1)
    has_more = len(docs) > spec.limit
    docs = docs[:spec.limit]
    rows = [_row(d, compiled.select, maps) for d in docs]
    next_cursor = None
    if has_more and docs:
        last = docs[-1]
        next_cursor = _encode_cursor(last.get(sort_path), last.get("_id"))
    columns = [{"key": fd.key, "label": fd.label, "type": fd.type, "udp": fd.udpDefId is not None}
               for fd in compiled.select]
    return {"rows": rows, "columns": columns, "nextCursor": next_cursor, "hasMore": has_more,
            "meta": {"grouped": False, "warnings": compiled.warnings,
                     "scan": "index" if compiled.sort else "default"}}
