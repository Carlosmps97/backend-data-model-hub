"""Executor async del motor de reporting: carga el catálogo dinámico (UDP),
pre-resuelve filtros cross-entity (schema→tableId), compila, pagina por KEYSET,
ejecuta contra la BD e hidrata nombres (dominio, UDP, schema) en Python.

Reglas de escala: pushdown de todos los filtros al $match, keyset (no skip/limit
profundo), proyección mínima, `maxTimeMS` como circuit-breaker.

Doc 75: toda consulta es de UN proyecto (`spec.projectId`): el `$match` base,
el catálogo UDP y las hidrataciones (dominio, schema) van con `scoped`.
"""
from __future__ import annotations

import base64
import json
import math

from app.core.db.client import get_db
from app.core.scope import scoped

from .compiler import RESULT_COLUMNS_UNGROUPED, Compiled, QueryError, _field, build_match, compile_spec
from .schema import FieldDef, build_catalog
from .spec import MAX_ROWS, Condition, QuerySpec, WhereGroup

COLL_OF = {"columns": "canonical_columns", "tables": "canonical_tables",
           "relationships": "relationships", "views": "views",
           "view_columns": "views", "models": "subject_areas"}
# Campo de orden por defecto del row-query (keyset). `subject_areas` no tiene
# physicalName: la entidad `models` ordena por `name` (indexado en indexes.py).
# Doc 100 (7.2): relaciones y vistas tampoco — ordenar por un campo que no
# existe dejaba `null` en cada cursor y la página 2 no se podía pedir. Van por
# `_id` (la PK): el mismo orden que ya mostraba la página 1 (todos empataban
# en null y desempataba el `_id`).
DEFAULT_SORT_FIELD = {"models": "name", "relationships": "_id", "views": "_id"}
ACTIVE = {"flgactive": {"$ne": False}}
MAX_TIME_MS = 15000
# Tope del offset del cursor de `view_columns` (skip-paging), como el de
# `catalog/search`.
MAX_VIEW_COLUMNS_OFFSET = 1_000_000
# Doc 105: tope DURO de grupos del export CSV de un agrupado (una sola
# agregación, antes del stream). Alto y seguro: sólo se alcanza agrupando por un
# campo casi único; pasarlo es un 422, nunca un CSV cortado.
MAX_EXPORT_GROUPS = 100_000


async def _udp_defs(project_id: str) -> list[dict]:
    db = await get_db()
    docs = await db["udp_definitions"].find(scoped(project_id, ACTIVE)).to_list(None)
    return [{**d, "id": str(d.pop("_id"))} for d in docs]


async def get_catalog(from_: str, project_id: str) -> dict[str, FieldDef]:
    return build_catalog(from_, await _udp_defs(project_id))


def _encode_cursor(sort_val, _id, delivered: int | None = None) -> str:
    """`[valor de orden, _id]` (+ filas ya entregadas si hay tope total)."""
    value = [sort_val, str(_id)] + ([delivered] if delivered is not None else [])
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def _delivered_ok(v) -> bool:
    """Filas ya entregadas (3.er elemento del cursor, doc 105): entero ≥ 0. Puro."""
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_ROWS


def _page_size(spec: QuerySpec, delivered: int) -> int:
    """Filas de ESTA página: `limit`, sin pasar el tope total `maxRows` (doc
    105: `LIMIT n` del editor SQL; antes las páginas siguientes y el export
    seguían más allá). 0 = el tope ya se entregó. Puro."""
    if spec.maxRows is None:
        return spec.limit
    return max(min(spec.limit, spec.maxRows - delivered), 0)


def _cursor_text_ok(v) -> bool:
    """¿Texto que Postgres acepta como `text`? Doc 105 (revisión, hallazgo 6):
    un NUL (`\\u0000`) pasaba y Postgres rechaza 0x00 en `text` (500); ronda 3:
    un surrogate UTF-16 suelto tampoco — asyncpg no lo codifica (500). Puro."""
    if not isinstance(v, str) or "\x00" in v:
        return False
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _cursor_value_ok(v) -> bool:
    """¿Valor de orden que el keyset puede comparar? `None`, texto (sin NUL) o
    número FINITO. Doc 105 (P12): un booleano (en Python es `int`) o
    NaN/±Infinity pasaban y el traductor de Lakebase reventaba (500). Puro."""
    if v is None:
        return True
    if isinstance(v, str):
        return _cursor_text_ok(v)
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and math.isfinite(v))


def _decode_cursor(cur: str):
    try:
        v = json.loads(base64.urlsafe_b64decode(cur.encode()).decode())
    except Exception:
        raise QueryError("Invalid cursor", code=400)
    # El cursor va DIRECTO al $match; si sort_val fuese dict/list un atacante
    # inyectaría operadores Mongo ({"$ne":null} bypassea el keyset, {"$regex":…}
    # ReDoS). Solo se aceptan escalares comparables para el valor y string
    # (sin NUL) para el _id.
    if (not isinstance(v, list) or len(v) not in (2, 3)
            or not _cursor_value_ok(v[0]) or not _cursor_text_ok(v[1])
            or (len(v) == 3 and not _delivered_ok(v[2]))):
        raise QueryError("Invalid cursor", code=400)
    return v


def _after(path: str, direction: int, cv, cid: str) -> dict:
    """Filas DESPUÉS del cursor `(cv, cid)` en el orden `(path, direction),
    (_id, 1)`. Un registro sin el campo (`null`/ausente) va PRIMERO en orden
    ascendente y ÚLTIMO en descendente — como Mongo y como el ORDER BY del
    adaptador (NULLS FIRST / NULLS LAST) —, y comparar contra `null` no existe
    en Lakebase (el traductor lo rechaza): el tramo de nulos va aparte.
    Doc 100 (7.2). Puro."""
    if path == "_id":
        return {"_id": {"$gt": cid}}
    ties = {path: cv, "_id": {"$gt": cid}}
    if cv is None:
        # Dentro del tramo de nulos: en ascendente siguen todos los que SÍ
        # tienen valor; en descendente el tramo es el final.
        return {"$or": [ties, {path: {"$ne": None}}]} if direction == 1 else ties
    beyond = {path: {"$gt" if direction == 1 else "$lt": cv}}
    # En descendente los nulos vienen DESPUÉS de todos los valores.
    return {"$or": [beyond, ties] + ([] if direction == 1 else [{path: None}])}


async def _rewrite_cross_entity(node, catalog: dict[str, FieldDef], project_id: str):
    """Reemplaza condiciones de campos cross-entity (schema en `columns`, que vive
    en la tabla) por su equivalente NATIVO `tableId $in [...]`, pre-resolviendo
    contra canonical_tables del proyecto (barrido barato de 10k, índice de schema)."""
    if node is None:
        return None
    if isinstance(node, Condition):
        fd = catalog.get(node.field)
        if fd and fd.entity == "table" and node.field == "schema":
            db = await get_db()
            vals = node.value if isinstance(node.value, list) else [node.value]
            if node.op in ("eq", "in"):
                ids = [str(t["_id"]) async for t in db["canonical_tables"].find(
                    scoped(project_id, {**ACTIVE, "schema": {"$in": vals}}), {"_id": 1})]
                return Condition(field="tableId", op="in", value=ids or ["__none__"])
            # Otro operador lo rechaza el compilador (el mismo mensaje en `/validate`).
        return node
    subs = [await _rewrite_cross_entity(c, catalog, project_id) for c in node.conditions]
    return WhereGroup(op=node.op, conditions=[s for s in subs if s is not None])


def _ids_in(docs: list[dict], paths: list[str]) -> list[str]:
    return sorted({v for d in docs for p in paths if isinstance(v := d.get(p), str) and v})


async def _hydration_maps(select: list[FieldDef], from_: str, project_id: str, docs: list[dict]) -> dict:
    """Mapas para resolver nombres post-fetch, SÓLO de los ids de ESTA página
    (doc 105, ronda 4: releía todos los dominios y tablas del proyecto en cada
    página)."""
    db, maps = await get_db(), {}
    domain_ids = _ids_in(docs, [fd.path for fd in select if fd.hydrate == "domain"])
    if domain_ids:
        maps["domain"] = {str(d["_id"]): d.get("name") async for d in db["parent_domains"].find(
            scoped(project_id, {**ACTIVE, "_id": {"$in": domain_ids}}), {"name": 1})}
    table_ids = _ids_in(docs, ["tableId"]) if from_ == "columns" and any(fd.key == "schema" for fd in select) else []
    if table_ids:
        maps["schema"] = {str(t["_id"]): t.get("schema") async for t in db["canonical_tables"].find(
            scoped(project_id, {**ACTIVE, "_id": {"$in": table_ids}}), {"schema": 1})}
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


async def run_query(spec: QuerySpec, cursor: str | None = None, *, all_groups: bool = False) -> dict:
    """`all_groups` (el export CSV): un agrupado trae TODOS sus grupos —o hasta
    `maxRows`—, no una página; si se pasaría de `MAX_EXPORT_GROUPS`, 422 (doc 105)."""
    catalog = await get_catalog(spec.from_, spec.projectId)
    if spec.from_ == "view_columns":
        # Entidad virtual (F5): camino DEDICADO aggregate+unwind — no toca el
        # keyset genérico ni sus defensas de escala (ver _run_view_columns).
        return await _run_view_columns(spec, catalog, cursor)
    where = await _rewrite_cross_entity(spec.where, catalog, spec.projectId)
    spec2 = spec.model_copy(update={"where": where})
    compiled = compile_spec(spec2, catalog)
    db = await get_db()
    coll = db[COLL_OF[spec.from_]]
    base_match = {**scoped(spec.projectId, ACTIVE), **compiled.match}

    if compiled.grouped:
        pipe = [{"$match": base_match}, {"$group": compiled.group}, {"$project": compiled.project}]
        if compiled.sort:
            pipe.append({"$sort": {f: d for f, d in compiled.sort}})
        # Doc 105: el agrupado no pagina — a lo más `limit` grupos (el export:
        # todos, hasta el tope duro) y no más que el tope total `maxRows`; se
        # pide uno más para saber si se cortó.
        page = MAX_EXPORT_GROUPS if all_groups else spec.limit
        cap = min(page, spec.maxRows) if spec.maxRows else page
        pipe.append({"$limit": cap + 1})
        rows = await coll.aggregate(pipe, maxTimeMS=MAX_TIME_MS).to_list(cap + 1)
        warnings = list(compiled.warnings)
        if len(rows) > cap and (spec.maxRows is None or cap < spec.maxRows):
            # Cortado por la página (no por el LIMIT pedido): se AVISA; el export,
            # que no tiene por dónde avisar, falla ANTES de abrir el stream.
            if all_groups:
                raise QueryError(f"The grouped result has more than {cap} groups: filter, group by fewer "
                                 "fields or add LIMIT.", code=422)
            warnings.append(f"Only the first {cap} groups are shown: filter or group by fewer values.")
        # Claves internas del pipeline → nombres públicos (doc 105, ronda 3);
        # las auxiliares (orden de booleanos, el `$max` del SUM) no salen.
        rows = [{compiled.output[k]: v for k, v in r.items() if k in compiled.output} for r in rows[:cap]]
        if not rows and not spec.groupBy:
            # Agregado GLOBAL sin filas: una fila (conteos en 0, el resto vacío),
            # como SQL — el `$group` no devuelve ningún grupo (doc 105, ronda 3).
            rows = [{a.as_: 0 if a.fn in ("count", "countDistinct") else None for a in spec.aggregations}]
        # Ronda 5: las columnas del resultado, en su orden (`resultColumns`);
        # una dimensión agrupada que no está ahí no sale.
        meta = {gb: {"key": gb, "label": catalog[gb].label, "type": catalog[gb].type} for gb in spec.groupBy}
        meta.update({a.as_: {"key": a.as_, "label": a.as_, "type": "number"} for a in spec.aggregations})
        cols = [meta[name] for name in compiled.columns]
        rows = [{name: r.get(name) for name in compiled.columns} for r in rows]
        return {"rows": rows, "columns": cols, "nextCursor": None, "hasMore": False,
                "meta": {"grouped": True, "warnings": warnings}}

    # ── Filas: keyset sobre el primer campo de orden (+ _id de desempate) ──
    sort = list(compiled.sort) or [(DEFAULT_SORT_FIELD.get(spec.from_, "physicalName"), 1)]
    sort_path, sort_dir = sort[0]
    full_sort = [(sort_path, sort_dir)] + ([] if sort_path == "_id" else [("_id", 1)])
    delivered = 0
    if cursor:
        cv, cid, *rest = _decode_cursor(cursor)
        delivered = rest[0] if rest else 0
        base_match = {"$and": [base_match, _after(sort_path, sort_dir, cv, cid)]}
    columns = [{"key": fd.key, "label": fd.label, "type": fd.type, "udp": fd.udpDefId is not None}
               for fd in compiled.select]
    meta = {"grouped": False, "warnings": compiled.warnings, "scan": "index" if compiled.sort else "default"}
    page = _page_size(spec, delivered)                  # 0 = el tope ya se entregó: página vacía
    proj = {**compiled.project, sort_path: 1}
    # Proyectar las FUENTES de hidratación cross-entity (schema en columns viene
    # de la tabla → necesita tableId).
    if spec.from_ == "columns" and any(fd.key == "schema" for fd in compiled.select):
        proj["tableId"] = 1
    docs = await coll.find(base_match, proj).sort(full_sort).limit(page + 1).max_time_ms(MAX_TIME_MS).to_list(page + 1)
    # Hay más si sobró una fila Y no se llegó al tope total.
    has_more = len(docs) > page and (spec.maxRows is None or delivered + page < spec.maxRows)
    docs = docs[:page]
    maps = await _hydration_maps(compiled.select, spec.from_, spec.projectId, docs)
    rows = [_row(d, compiled.select, maps) for d in docs]
    next_cursor = None
    if has_more and docs:
        last = docs[-1]
        next_cursor = _encode_cursor(last.get(sort_path), last.get("_id"),
                                     delivered + len(docs) if spec.maxRows is not None else None)
    return {"rows": rows, "columns": columns, "nextCursor": next_cursor, "hasMore": has_more, "meta": meta}


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


def _check_view_columns(spec: QuerySpec, catalog: dict[str, FieldDef]) -> tuple[dict, list[FieldDef], str, int]:
    """Validación PURA de un spec de `view_columns` → (match, select, orden).
    Doc 105 (ronda 3): un campo desconocido en `select`/`orderBy` se DESCARTABA
    (y el orden caía a `name`) y un segundo orden se ignoraba: ahora, error."""
    if spec.is_grouped:
        raise QueryError("Grouping isn't supported for view_columns.", code=422)
    if spec.resultColumns is not None:                 # doc 105 (ronda 6): se ignoraba en silencio
        raise QueryError(RESULT_COLUMNS_UNGROUPED, code=422)
    match = build_match(spec.where, catalog)
    select = [_field(catalog, k) for k in (spec.select or list(catalog.keys()))]
    if len(spec.orderBy) > 1:
        raise QueryError("Sort by one field.", code=422)
    if spec.orderBy:
        return match, select, _field(catalog, spec.orderBy[0].field).path, 1 if spec.orderBy[0].dir == "asc" else -1
    return match, select, "name", 1


def _view_columns_sort(path: str, direction: int) -> dict:
    """Orden pedido + desempate ÚNICO (doc 105, ronda 4): con sólo `_id` (el de
    la VISTA), sus columnas empataban y el skip-paging podía repetir o saltar
    filas. El orden pedido conserva su dirección aunque sea un desempate. Puro."""
    spec = {path: direction}
    # Ronda 5: + tabla y expresión — la misma columna de dos tablas (vista
    # multi-fuente, sin alias) empataba.
    for key in ("_id", "sources.outputAlias", "sources.column", "sources.tableId", "sources.expression"):
        spec.setdefault(key, 1)
    return spec


def _view_column_id(view_id, source: dict) -> str:
    """`_id` de una fila de `view_columns`: vista + alias; sin alias, la columna
    (o expresión) + su tabla — la misma columna de dos tablas repetía el `_id`
    (doc 105, ronda 5). Puro."""
    label = source.get("outputAlias") or source.get("column") or source.get("expression") or ""
    if source.get("outputAlias"):
        return f"{view_id}#{label}"
    return f"{view_id}#{label}@{source.get('tableId') or ''}"


def check_spec(spec: QuerySpec, catalog: dict[str, FieldDef]) -> None:
    """Lo que `run_query` rechazaría con 4xx, SIN tocar la BD — para que
    `/query/validate` no diga «válido» a lo que `/query/sql` rechaza (doc 105,
    ronda 4). Levanta QueryError."""
    if spec.from_ == "view_columns":
        _check_view_columns(spec, catalog)
    else:
        compile_spec(spec, catalog)


async def _run_view_columns(spec: QuerySpec, catalog: dict[str, FieldDef],
                            cursor: str | None) -> dict:
    """1 fila por COLUMNA de cada vista (unwind de `views.sources`, F5).

    Camino DEDICADO: dataset acotado (columnas de vista, ~miles) → aggregate +
    unwind, sort en memoria y skip-paging (el keyset por _id no aplica a filas
    derivadas del $unwind). La `description` cae a la de la columna FÍSICA origen
    cuando la vista no la override (mismo fallback que el editor)."""
    match, select, sort_path, sort_dir = _check_view_columns(spec, catalog)
    db = await get_db()
    coll = db["views"]
    offset = 0
    if cursor:
        try:
            offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:
            raise QueryError("Invalid cursor", code=400)
        # Doc 105 (A2-o4): con tope (el de `catalog/search`) — un entero fuera
        # de int8 llegaba al OFFSET y asyncpg lo rechazaba (500).
        if not 0 <= offset <= MAX_VIEW_COLUMNS_OFFSET:
            raise QueryError("Invalid cursor", code=400)
    # El offset ES lo ya entregado: tope total `maxRows` (doc 105).
    page = _page_size(spec, offset)
    pipe: list[dict] = [{"$match": scoped(spec.projectId, ACTIVE)}, {"$unwind": "$sources"}]
    if match:
        pipe.append({"$match": match})
    pipe += [
        {"$project": {"name": 1, "schema": 1, "sources.outputAlias": 1,
                      "sources.column": 1, "sources.tableId": 1,
                      "sources.castType": 1, "sources.expression": 1,
                      "sources.description": 1}},
        {"$sort": _view_columns_sort(sort_path, sort_dir)},
        {"$skip": offset},
        {"$limit": page + 1},
    ]
    docs = await coll.aggregate(pipe, maxTimeMS=MAX_TIME_MS).to_list(page + 1)
    has_more = len(docs) > page and (spec.maxRows is None or offset + page < spec.maxRows)
    docs = docs[:page]
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
        row = {"_id": _view_column_id(d.get("_id"), s)}
        for fd in select:
            if fd.key == "description":
                row[fd.key] = s.get("description") or phys.get((s.get("tableId"), s.get("column")))
            else:
                row[fd.key] = _VC_GETTERS.get(fd.key, lambda d, s: None)(d, s)
        rows.append(row)
    next_cursor = (base64.urlsafe_b64encode(str(offset + page).encode()).decode()
                   if has_more else None)
    columns = [{"key": fd.key, "label": fd.label, "type": fd.type, "udp": False} for fd in select]
    return {"rows": rows, "columns": columns, "nextCursor": next_cursor, "hasMore": has_more,
            "meta": {"grouped": False, "warnings": [], "scan": "unwind"}}
