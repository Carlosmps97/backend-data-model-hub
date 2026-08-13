"""Executor async del motor de reporting: carga el catálogo dinámico (UDP),
pre-resuelve filtros cross-entity (schema→tableId), compila, pagina por KEYSET,
ejecuta contra la BD e hidrata nombres (dominio, UDP, schema) en Python.

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
           "view_columns": "views", "models": "subject_areas"}
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
            # se computa en la BD: $size en projection de find() no lo soporta
            # el adaptador; el path proyecta la fuente (array) y acá se cuenta.
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
    if spec.from_ == "view_columns":
        # Entidad virtual (F5): camino DEDICADO aggregate+unwind — no toca el
        # keyset genérico ni sus defensas de escala (ver _run_view_columns).
        return await _run_view_columns(spec, catalog, cursor)
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


# ── Entidad virtual `view_columns` (F5) ──────────────────────────────────────
_VC_GETTERS = {
    "viewName": lambda d, s: d.get("name"),
    "schema": lambda d, s: d.get("schema"),
    "outputName": lambda d, s: s.get("outputAlias") or s.get("column"),
    "sourceColumn": lambda d, s: s.get("column"),
    "sourceTableId": lambda d, s: s.get("tableId"),
    "castType": lambda d, s: s.get("castType"),
    "expression": lambda d, s: s.get("expression"),
}


async def _run_view_columns(spec: QuerySpec, catalog: dict[str, FieldDef],
                            cursor: str | None) -> dict:
    """1 fila por COLUMNA de cada vista (unwind de `views.sources`, F5).

    Camino DEDICADO: dataset acotado (columnas de vista, ~miles) → aggregate +
    unwind, sort en memoria y skip-paging (el keyset por _id no aplica a filas
    derivadas del $unwind). La `description` cae a la de la columna FÍSICA origen
    cuando la vista no la override (mismo fallback que el editor)."""
    if spec.is_grouped:
        raise QueryError("El agrupado no está soportado para view_columns.", code=422)
    db = await get_db()
    coll = db["views"]
    match = build_match(spec.where, catalog)
    select_keys = spec.select or list(catalog.keys())
    select = [catalog[k] for k in select_keys if k in catalog]
    if spec.orderBy:
        fd0 = catalog.get(spec.orderBy[0].field)
        sort_path = fd0.path if fd0 else "name"
        sort_dir = 1 if spec.orderBy[0].dir == "asc" else -1
    else:
        sort_path, sort_dir = "name", 1
    offset = 0
    if cursor:
        try:
            offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:
            raise QueryError("Cursor inválido", code=400)
        if offset < 0:
            raise QueryError("Cursor inválido", code=400)
    pipe: list[dict] = [{"$match": {**ACTIVE}}, {"$unwind": "$sources"}]
    if match:
        pipe.append({"$match": match})
    pipe += [
        {"$project": {"name": 1, "schema": 1, "sources.outputAlias": 1,
                      "sources.column": 1, "sources.tableId": 1,
                      "sources.castType": 1, "sources.expression": 1,
                      "sources.description": 1}},
        {"$sort": {sort_path: sort_dir, "_id": 1}},
        {"$skip": offset},
        {"$limit": spec.limit + 1},
    ]
    docs = await coll.aggregate(pipe, maxTimeMS=MAX_TIME_MS).to_list(spec.limit + 1)
    has_more = len(docs) > spec.limit
    docs = docs[:spec.limit]
    # Fallback de `description`: def de la columna física origen (una lectura,
    # acotada a las tablas presentes en la página).
    phys: dict = {}
    if any(fd.key == "description" for fd in select):
        tids = {(d.get("sources") or {}).get("tableId") for d in docs}
        tids.discard(None)
        if tids:
            async for c in db["canonical_columns"].find(
                    {"tableId": {"$in": list(tids)}, **ACTIVE},
                    {"tableId": 1, "physicalName": 1, "description": 1}):
                phys[(c.get("tableId"), c.get("physicalName"))] = c.get("description")
    rows = []
    for d in docs:
        s = d.get("sources") or {}
        row = {"_id": f"{d.get('_id')}#{s.get('outputAlias') or s.get('column')}"}
        for fd in select:
            if fd.key == "description":
                row[fd.key] = s.get("description") or phys.get((s.get("tableId"), s.get("column")))
            else:
                row[fd.key] = _VC_GETTERS.get(fd.key, lambda d, s: None)(d, s)
        rows.append(row)
    next_cursor = (base64.urlsafe_b64encode(str(offset + spec.limit).encode()).decode()
                   if has_more else None)
    columns = [{"key": fd.key, "label": fd.label, "type": fd.type, "udp": False} for fd in select]
    return {"rows": rows, "columns": columns, "nextCursor": next_cursor, "hasMore": has_more,
            "meta": {"grouped": False, "warnings": [], "scan": "unwind"}}
