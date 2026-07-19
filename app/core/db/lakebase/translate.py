"""Traducción Mongo → SQL/JSONB (filtros, updates, proyección, orden).

Semántica validada contra el Lakebase real (doc 28 §2, sonda 20/20):
- Igualdad escalar → jsonpath **lax** `doc @? '$."a"."b" ? (@ == v)'`:
  cubre a la vez el valor escalar y el "array-contains" multikey de Mongo
  (lax des-anida arrays en cada paso). Indexable por el GIN jsonb_path_ops.
- `$ne` / `$nin` → NOT(...) e incluyen docs sin el campo (como Mongo).
- Comparaciones: strings → texto `COLLATE "C"` (orden por code points, igual
  que Mongo y que los índices); números → jsonpath (tipado, sin casts frágiles).
- `$regex` → `~` / `~*` sobre el texto extraído (el código de la app siempre
  pasa patrones `re.escape`ados o anclados — POSIX-compatibles).
- Todos los predicados devuelven boolean NO-NULL (COALESCE donde aplica) para
  que la lógica trivaluada de SQL no "trague" docs en `$ne`/`$nor`.

Dos modos:
- **table** (find/update/delete): `_id` opera sobre la COLUMNA `id` (PK).
- **doc** (pipelines de aggregate): `_id` es una key más dentro de `doc`.
"""

from __future__ import annotations

import json
from typing import Any

# ─── Builder de parámetros ──────────────────────────────────────────────


class Sql:
    """Acumula parámetros posicionales ($1, $2, …) para asyncpg."""

    def __init__(self) -> None:
        self.params: list[Any] = []

    def add(self, value: Any) -> str:
        self.params.append(value)
        return f"${len(self.params)}"

    def add_json(self, value: Any) -> str:
        """Parámetro JSON (texto) casteado a jsonb en el SQL."""
        return self.add(json.dumps(value, ensure_ascii=False, default=_json_default)) + "::jsonb"

    def add_jsonpath(self, path: str) -> str:
        return self.add(path) + "::jsonpath"

    def add_text_array(self, values: list[str]) -> str:
        return self.add(list(values)) + "::text[]"


def _json_default(value: Any) -> Any:
    """Red de seguridad del codec de escritura: la app guarda fechas como
    strings ISO; si algún día llega un datetime crudo, se canonicaliza."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Tipo no serializable a JSON: {type(value)!r}")


def dumps_canonical(value: Any) -> str:
    """JSON canónico compartido con la migración de datos (claves ordenadas)."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default
    )


# ─── Paths ──────────────────────────────────────────────────────────────


def _segs(field: str) -> list[str]:
    return field.split(".")


def jsonpath(field: str) -> str:
    """`a.b` → `$."a"."b"` (segmentos escapados como strings JSON)."""
    return "$" + "".join(f".{json.dumps(seg, ensure_ascii=False)}" for seg in _segs(field))


def _jp_value(value: Any) -> str:
    """Literal de valor dentro de un jsonpath (escape JSON)."""
    return json.dumps(value, ensure_ascii=False)


def pg_path(field: str, s: Sql) -> str:
    """Parámetro text[] para `#>` / `#>>` / `jsonb_set`."""
    return s.add_text_array(_segs(field))


# ─── Filtros ────────────────────────────────────────────────────────────

_LOGICAL = {"$or", "$and", "$nor"}


def filter_sql(flt: dict | None, s: Sql, doc: str = "doc", mode: str = "table") -> str:
    """Traduce un filtro Mongo a una expresión boolean SQL (no-NULL)."""
    if not flt:
        return "TRUE"
    parts: list[str] = []
    for key, value in flt.items():
        if key in _LOGICAL:
            if not isinstance(value, list) or not value:
                raise NotImplementedError(f"Filtro lógico inválido: {key}={value!r}")
            subs = [f"({filter_sql(f, s, doc, mode)})" for f in value]
            if key == "$and":
                parts.append("(" + " AND ".join(subs) + ")")
            elif key == "$or":
                parts.append("(" + " OR ".join(subs) + ")")
            else:  # $nor
                parts.append("NOT (" + " OR ".join(subs) + ")")
        elif key.startswith("$"):
            raise NotImplementedError(f"Operador top-level no soportado: {key}")
        else:
            parts.append(_field_clause(key, value, s, doc, mode))
    return " AND ".join(parts) if parts else "TRUE"


def _field_clause(field: str, cond: Any, s: Sql, doc: str, mode: str) -> str:
    is_id = mode == "table" and field == "_id"
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        clauses: list[str] = []
        if "$regex" in cond:
            clauses.append(
                regex_clause(field, cond["$regex"], cond.get("$options", ""), s, doc)
            )
        clauses.extend(
            _op_clause(field, op, val, s, doc, is_id)
            for op, val in cond.items()
            if op not in ("$regex", "$options")
        )
        return "(" + " AND ".join(clauses) + ")" if clauses else "TRUE"
    return _eq_clause(field, cond, s, doc, is_id)


def _op_clause(field: str, op: str, value: Any, s: Sql, doc: str, is_id: bool) -> str:
    if op == "$eq":
        return _eq_clause(field, value, s, doc, is_id)
    if op == "$ne":
        return f"NOT {_eq_clause(field, value, s, doc, is_id)}"
    if op == "$in":
        return _in_clause(field, value, s, doc, is_id)
    if op == "$nin":
        return f"NOT {_in_clause(field, value, s, doc, is_id)}"
    if op in ("$gt", "$gte", "$lt", "$lte"):
        return _cmp_clause(field, op, value, s, doc, is_id)
    if op == "$exists":
        exists = "TRUE" if is_id else f"({doc} @? {s.add_jsonpath(jsonpath(field))})"
        return exists if value else f"NOT {exists}"
    raise NotImplementedError(f"Operador de query no soportado: {op} en campo {field!r}")


def _eq_clause(field: str, value: Any, s: Sql, doc: str, is_id: bool) -> str:
    if is_id:
        return f"id = {s.add(_id_str(value))}"
    if value is None:
        pth = pg_path(field, s)
        exists = f"{doc} @? {s.add_jsonpath(jsonpath(field))}"
        return f"(COALESCE(({doc} #> {pth}) = 'null'::jsonb, false) OR NOT ({exists}))"
    if isinstance(value, (dict, list)):
        # Igualdad EXACTA de objeto/array (jsonb: insensible al orden de keys).
        pth = pg_path(field, s)
        return f"COALESCE(({doc} #> {pth}) = {s.add_json(value)}, false)"
    # Escalar: jsonpath lax == cubre escalar Y array-contains (multikey Mongo).
    path = f"{jsonpath(field)} ? (@ == {_jp_value(value)})"
    return f"{doc} @? {s.add_jsonpath(path)}"


def _in_clause(field: str, values: Any, s: Sql, doc: str, is_id: bool) -> str:
    if not isinstance(values, list):
        raise NotImplementedError(f"$in espera lista, llegó {type(values)!r}")
    if not values:
        return "FALSE"
    if is_id:
        return f"id = ANY({s.add_text_array([_id_str(v) for v in values])})"
    scalars = [v for v in values if not isinstance(v, (dict, list)) and v is not None]
    others = [v for v in values if isinstance(v, (dict, list)) or v is None]
    parts: list[str] = []
    if scalars:
        preds = " || ".join(f"@ == {_jp_value(v)}" for v in scalars)
        parts.append(f"{doc} @? {s.add_jsonpath(f'{jsonpath(field)} ? ({preds})')}")
    for v in others:
        parts.append(_eq_clause(field, v, s, doc, is_id=False))
    return "(" + " OR ".join(parts) + ")"


_CMP = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}


def _cmp_clause(field: str, op: str, value: Any, s: Sql, doc: str, is_id: bool) -> str:
    sql_op = _CMP[op]
    if is_id:
        return f'id COLLATE "C" {sql_op} {s.add(_id_str(value))}'
    if isinstance(value, str):
        # Bracketing de tipos de Mongo: solo compara strings; missing/null/no-string
        # quedan NULL → no matchean. Mismo orden (code points) que los índices.
        pth = pg_path(field, s)
        return f'COALESCE(({doc} #>> {pth}) COLLATE "C" {sql_op} {s.add(value)}, false)'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NotImplementedError(f"{op} sobre {type(value)!r} no soportado")
    path = f"{jsonpath(field)} ? (@ {sql_op} {_jp_value(value)})"
    return f"{doc} @? {s.add_jsonpath(path)}"


def _id_str(value: Any) -> str:
    if not isinstance(value, str):
        return str(value)
    return value


def regex_clause(field: str, pattern: str, options: str, s: Sql, doc: str) -> str:
    op = "~*" if "i" in (options or "") else "~"
    pth = pg_path(field, s)
    return f"COALESCE(({doc} #>> {pth}) {op} {s.add(pattern)}, false)"


# ─── Updates ────────────────────────────────────────────────────────────

_UPDATE_OPS = {"$set", "$setOnInsert", "$unset", "$inc", "$push"}


def is_update_doc(update: dict) -> bool:
    return any(k.startswith("$") for k in update)


def validate_update(update: dict) -> None:
    unknown = [k for k in update if k.startswith("$") and k not in _UPDATE_OPS]
    if unknown:
        raise NotImplementedError(f"Operadores de update no soportados: {unknown}")


def update_expr(update: dict, s: Sql, doc: str = "doc") -> str:
    """Expresión SQL que aplica `$set/$unset/$inc/$push` sobre `doc`.

    `$setOnInsert` NO participa acá (solo alimenta el doc del branch insert
    de un upsert). Los paths con punto crean padres objeto si faltan
    (jsonb_set anidado), leyendo SIEMPRE del doc original (los ops de un
    mismo update no se pisan entre sí, como en Mongo).
    """
    validate_update(update)
    expr = doc
    set_ops: dict = update.get("$set", {}) or {}
    simple = {k: v for k, v in set_ops.items() if "." not in k}
    dotted = {k: v for k, v in set_ops.items() if "." in k}
    if simple:
        expr = f"({expr} || {s.add_json(simple)})"
    for field, value in dotted.items():
        segs = _segs(field)
        for depth in range(1, len(segs)):
            parent = segs[:depth]
            expr = (
                f"jsonb_set({expr}, {s.add_text_array(parent)}, "
                f"COALESCE({doc} #> {s.add_text_array(parent)}, '{{}}'::jsonb), true)"
            )
        expr = f"jsonb_set({expr}, {s.add_text_array(segs)}, {s.add_json(value)}, true)"
    for field in (update.get("$unset", {}) or {}):
        if "." in field:
            expr = f"({expr} #- {s.add_text_array(_segs(field))})"
        else:
            expr = f"({expr} - {s.add(field)})"
    for field, delta in (update.get("$inc", {}) or {}).items():
        pth = pg_path(field, s)
        expr = (
            f"jsonb_set({expr}, {pth}, to_jsonb(COALESCE(({doc} #>> {s.add_text_array(_segs(field))})::numeric, 0)"
            f" + {s.add(delta)}::numeric), true)"
        )
    for field, item in (update.get("$push", {}) or {}).items():
        pth = pg_path(field, s)
        expr = (
            f"jsonb_set({expr}, {pth}, COALESCE({doc} #> {s.add_text_array(_segs(field))}, '[]'::jsonb)"
            f" || {s.add_json([item])}, true)"
        )
    return expr


def upsert_doc(flt: dict, update: dict) -> dict:
    """Doc que insertaría Mongo si el upsert no encuentra match: igualdades
    simples del filtro + operadores del update aplicados sobre vacío
    (`$setOnInsert`, `$set`, `$inc` desde 0, `$push` a lista nueva)."""
    base: dict = {}
    for key, value in (flt or {}).items():
        if key.startswith("$"):
            continue
        if isinstance(value, dict) and any(k.startswith("$") for k in value):
            if "$eq" in value:
                base[key] = value["$eq"]
            continue
        base[key] = value
    base.update(update.get("$setOnInsert", {}) or {})
    base.update(update.get("$set", {}) or {})
    for key, delta in (update.get("$inc", {}) or {}).items():
        base[key] = delta
    for key, item in (update.get("$push", {}) or {}).items():
        base[key] = [item]
    for key in list(base):
        if "." in key:
            raise NotImplementedError(f"Upsert con path anidado no soportado: {key}")
    return base


# ─── Proyección (top-level, como usa la app) ────────────────────────────


def projection_expr(projection: dict | None, s: Sql, doc: str = "doc") -> str:
    if not projection:
        return doc
    keys = {k: v for k, v in projection.items() if k != "_id"}
    include_mode = any(bool(v) for v in keys.values())
    if include_mode and any(not v for v in keys.values()):
        raise NotImplementedError("Proyección mixta inclusión/exclusión")
    if not include_mode:
        # Exclusión (solo top-level; no hay exclusiones anidadas en el código).
        if any("." in k for k in keys):
            raise NotImplementedError(f"Exclusión con paths anidados: {list(keys)}")
        excl = list(keys)
        if not projection.get("_id", 1):
            excl.append("_id")
        return f"({doc} - {s.add_text_array(excl)})"
    # Inclusión: top-level por lote + paths anidados (2 niveles, p. ej.
    # `udpValues.<defId>`) con la semántica de Mongo: raíz ausente/no-objeto →
    # se omite; raíz objeto sin la sub-key → queda `{raíz: {}}`.
    tops = [k for k in keys if "." not in k]
    nested: dict[str, list[str]] = {}
    for k in keys:
        if "." in k:
            root, rest = k.split(".", 1)
            if "." in rest:
                raise NotImplementedError(f"Proyección anidada >2 niveles: {k}")
            nested.setdefault(root, []).append(rest)
    parts: list[str] = []
    if projection.get("_id", 1):
        parts.append(f"jsonb_build_object('_id', {doc} -> '_id')")
    if tops:
        arr = s.add_text_array(tops)
        parts.append(
            f"(SELECT COALESCE(jsonb_object_agg(k, {doc} -> k), '{{}}'::jsonb) "
            f"FROM unnest({arr}) AS k WHERE {doc} ? k)"
        )
    for root, subs in nested.items():
        rk = s.add(root)
        inner: list[str] = []
        for sub in subs:
            pth = s.add_text_array([root, sub])
            inner.append(
                f"(CASE WHEN ({doc} #> {pth}) IS NOT NULL "
                f"THEN jsonb_build_object({s.add(sub)}::text, {doc} #> {pth}) "
                f"ELSE '{{}}'::jsonb END)"
            )
        parts.append(
            f"(CASE WHEN jsonb_typeof({doc} -> {rk}::text) = 'object' "
            f"THEN jsonb_build_object({rk}::text, ({' || '.join(inner)})) "
            f"ELSE '{{}}'::jsonb END)"
        )
    return "(" + " || ".join(parts) + ")" if parts else "'{}'::jsonb"


# ─── Orden (find) ───────────────────────────────────────────────────────


def order_by_find(sort_spec: list[tuple[str, int]], s: Sql, doc: str = "doc") -> str:
    """ORDER BY para find(): los campos ordenados en la app son strings
    (physicalName/name/at/updatedAt) o `_id`. Missing/null primero en ASC y
    último en DESC, como Mongo."""
    terms: list[str] = []
    for field, direction in sort_spec:
        asc = direction >= 0
        nulls = "NULLS FIRST" if asc else "NULLS LAST"
        dir_sql = "ASC" if asc else "DESC"
        if field == "_id":
            terms.append(f'id COLLATE "C" {dir_sql}')
        elif "." in field:
            terms.append(f'({doc} #>> {s.add_text_array(_segs(field))}) COLLATE "C" {dir_sql} {nulls}')
        else:
            terms.append(f'({doc} ->> {s.add(field)}) COLLATE "C" {dir_sql} {nulls}')
    return ("ORDER BY " + ", ".join(terms)) if terms else ""
