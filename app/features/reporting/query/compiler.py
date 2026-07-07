"""Compiler: `QuerySpec` → pipeline de Mongo. PURO (sin DB) y testeable.

Valida cada campo/op contra el Field Catalog, castea el value al tipo, y aplica
el PLANNER de escala: un orden por un campo sin índice se RECHAZA (Cosmos tira
500 en `.sort()` sin índice); `contains/startsWith` usan `re.escape` (nunca
regex arbitrario). El cliente manda keys públicas, jamás paths de Mongo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from .schema import FieldDef
from .spec import Aggregation, Condition, OrderBy, QuerySpec, WhereGroup


class QueryError(Exception):
    """Query inválida (campo/op/valor). El router la mapea a 400/422."""
    def __init__(self, message: str, code: int = 422):
        super().__init__(message)
        self.code = code


@dataclass
class Compiled:
    match: dict
    group: dict | None            # etapa $group (None si no agrupa)
    project: dict                 # $project final (levanta _id / select)
    sort: list[tuple[str, int]]   # [(mongoPath, 1|-1)]
    select: list[FieldDef]        # campos a devolver (para hidratar + columnas de salida)
    grouped: bool
    warnings: list[str] = dc_field(default_factory=list)


def _field(catalog: dict[str, FieldDef], key: str) -> FieldDef:
    fd = catalog.get(key)
    if fd is None:
        raise QueryError(f"Campo desconocido: {key!r}", code=400)
    return fd


def _coerce(fd: FieldDef, value):
    if value is None:
        return None
    if fd.udpDefId:                      # udpValues SIEMPRE string en storage
        return str(value)
    if fd.type == "number":
        try:
            f = float(value)
            return int(f) if f.is_integer() else f
        except (TypeError, ValueError):
            raise QueryError(f"Valor numérico inválido para {fd.key}: {value!r}", code=400)
    if fd.type == "boolean":
        return value in (True, "true", "True", 1, "1")
    return str(value)


def _predicate(fd: FieldDef, op: str, value) -> dict:
    p = fd.path
    if op not in fd.ops:
        raise QueryError(f"Op {op!r} no permitida para {fd.key} ({fd.type})", code=422)
    if op == "eq":
        return {p: _coerce(fd, value)}
    if op == "ne":
        return {p: {"$ne": _coerce(fd, value)}}
    if op in ("in", "nin"):
        vals = value if isinstance(value, list) else [value]
        return {p: {("$in" if op == "in" else "$nin"): [_coerce(fd, v) for v in vals]}}
    if op == "contains":
        return {p: {"$regex": re.escape(str(value)), "$options": "i"}}
    if op == "startsWith":
        return {p: {"$regex": "^" + re.escape(str(value)), "$options": "i"}}
    if op in ("gt", "gte", "lt", "lte"):
        return {p: {"$" + op: _coerce(fd, value)}}
    if op == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise QueryError(f"between requiere [min, max] en {fd.key}", code=400)
        return {p: {"$gte": _coerce(fd, value[0]), "$lte": _coerce(fd, value[1])}}
    if op == "exists":
        return {p: {"$exists": bool(value) if value is not None else True}}
    if op == "isnull":
        return {"$or": [{p: {"$exists": False}}, {p: {"$in": [None, ""]}}]}
    raise QueryError(f"Op no soportada: {op}", code=422)  # pragma: no cover


def build_match(node, catalog: dict[str, FieldDef]) -> dict:
    """WhereGroup/Condition → $match. Recursivo. Puro."""
    if node is None:
        return {}
    if isinstance(node, Condition):
        return _predicate(_field(catalog, node.field), node.op, node.value)
    # WhereGroup
    subs = [build_match(c, catalog) for c in node.conditions]
    subs = [s for s in subs if s]
    if not subs:
        return {}
    if node.op == "and":
        return subs[0] if len(subs) == 1 else {"$and": subs}
    if node.op == "or":
        return {"$or": subs}
    return {"$nor": subs}   # not


_AGG = {"count": None, "sum": "$sum", "avg": "$avg", "min": "$min", "max": "$max"}


def _accumulator(a: Aggregation, catalog: dict[str, FieldDef]) -> dict:
    if a.fn == "count":
        return {"$sum": 1}
    if a.fn == "countDistinct":
        return {"$addToSet": "$" + _field(catalog, a.field).path} if a.field else {"$addToSet": "$_id"}
    if a.field is None:
        raise QueryError(f"La agregación {a.fn} requiere un campo", code=400)
    return {_AGG[a.fn]: "$" + _field(catalog, a.field).path}


def compile_spec(spec: QuerySpec, catalog: dict[str, FieldDef]) -> Compiled:
    match = build_match(spec.where, catalog)
    warnings: list[str] = []

    if spec.is_grouped:
        # _id = dimensiones del groupBy (con $ifNull → "(sin valor)" para nulos).
        gid = {gb: {"$ifNull": ["$" + _field(catalog, gb).path, None]} for gb in spec.groupBy} or None
        accs, project = {}, {"_id": 0}
        for gb in spec.groupBy:
            project[gb] = f"$_id.{gb}"
        distinct_fields = []
        for a in spec.aggregations:
            accs[a.as_] = _accumulator(a, catalog)
            if a.fn == "countDistinct":
                distinct_fields.append(a.as_)
                project[a.as_] = {"$size": "$" + a.as_}
            else:
                project[a.as_] = "$" + a.as_
        group = {"_id": gid, **accs}
        # sort en memoria (el resultado agrupado es chico): cualquier key.
        sort = [(f, 1 if o.dir == "asc" else -1) for o in spec.orderBy for f in [o.field]]
        return Compiled(match=match, group=group, project=project, sort=sort,
                        select=[], grouped=True, warnings=warnings)

    # ── Filas (row query) ──
    select_keys = spec.select or [k for k, fd in catalog.items() if fd.udpDefId is None]
    select = [_field(catalog, k) for k in select_keys]
    project = {"_id": 1}
    for fd in select:
        if fd.entity is None:              # cross-entity (p.ej. schema en columns) no se proyecta directo
            project[fd.path] = 1
    # Planner de orden: un row-sort exige índice (sortable) o se rechaza.
    sort: list[tuple[str, int]] = []
    for o in spec.orderBy:
        fd = _field(catalog, o.field)
        if not fd.sortable:
            raise QueryError(
                f"No se puede ordenar por {o.field!r} (sin índice) a esta escala. "
                f"Ordená por un campo indexado (p.ej. physicalName).", code=422)
        sort.append((fd.path, 1 if o.dir == "asc" else -1))
    return Compiled(match=match, group=None, project=project, sort=sort,
                    select=select, grouped=False, warnings=warnings)
