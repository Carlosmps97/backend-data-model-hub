"""Compilador de pipelines de aggregation Mongo → un SELECT de Postgres.

Alcance CERRADO al inventario real del backend (doc 28 §5): stages `$match`,
`$group`, `$project`, `$sort`, `$skip`, `$limit`, `$count`, `$unwind`; exprs
`$sum/$avg/$min/$max/$addToSet` (acumuladores) y `$cond/$ifNull/$eq/$gt/$in/
$size/$not/$objectToArray`, refs `"$campo"`, literales y arrays literales.
Cualquier cosa fuera del alcance → `NotImplementedError` con el detalle
(fail-fast: jamás un resultado silenciosamente distinto).

Cada stage envuelve al anterior como subquery de una sola columna `d jsonb`.
`$sort` se fusiona con los `$skip`/`$limit` contiguos (mismo nivel SQL, para
que el orden se preserve). El orden de `$sort` replica el de tipos de Mongo
(null < number < string) con la terna rank/numérico/texto·COLLATE "C".
"""

from __future__ import annotations

from typing import Any

from app.core.db.lakebase.translate import Sql, filter_sql

_JS_FALSY = "ARRAY['null'::jsonb, 'false'::jsonb, '0'::jsonb]"


def compile_pipeline(pipeline: list[dict], base_from: str, s: Sql) -> str:
    """`base_from` = SELECT que produce la columna `doc` (p. ej. la tabla)."""
    cur = f"SELECT doc AS d FROM {base_from}"
    i = 0
    n = 0
    while i < len(pipeline):
        stage = pipeline[i]
        if not isinstance(stage, dict) or len(stage) != 1:
            raise NotImplementedError(f"Stage inválida: {stage!r}")
        op, arg = next(iter(stage.items()))
        n += 1
        alias = f"q{n}"
        if op == "$match":
            cond = filter_sql(arg, s, doc="d", mode="doc")
            cur = f"SELECT d FROM ({cur}) {alias} WHERE {cond}"
        elif op == "$unwind":
            cur = _unwind(arg, cur, alias, s)
        elif op == "$group":
            cur = _group(arg, cur, alias, s)
        elif op == "$project":
            cur = _project(arg, cur, alias, s)
        elif op == "$count":
            name = s.add(str(arg)) + "::text"
            cur = (
                f"SELECT jsonb_build_object({name}, count(*)) AS d "
                f"FROM ({cur}) {alias} HAVING count(*) > 0"
            )
        elif op == "$sort":
            offset = limit = None
            j = i + 1
            while j < len(pipeline) and len(pipeline[j]) == 1:
                nxt, nval = next(iter(pipeline[j].items()))
                if nxt == "$skip" and offset is None:
                    offset = int(nval)
                elif nxt == "$limit" and limit is None:
                    limit = int(nval)
                else:
                    break
                j += 1
            i = j - 1
            order = _order_by(arg, s)
            tail = ""
            if offset is not None:
                tail += f" OFFSET {s.add(offset)}"
            if limit is not None:
                tail += f" LIMIT {s.add(limit)}"
            cur = f"SELECT d FROM ({cur}) {alias} {order}{tail}"
        elif op == "$skip":
            cur = f"SELECT d FROM ({cur}) {alias} OFFSET {s.add(int(arg))}"
        elif op == "$limit":
            cur = f"SELECT d FROM ({cur}) {alias} LIMIT {s.add(int(arg))}"
        else:
            raise NotImplementedError(f"Stage de aggregate no soportada: {op}")
        i += 1
    return cur


# ─── Stages ─────────────────────────────────────────────────────────────


def _unwind(arg: Any, cur: str, alias: str, s: Sql) -> str:
    if not isinstance(arg, str) or not arg.startswith("$"):
        raise NotImplementedError(f"$unwind solo soporta '$campo': {arg!r}")
    pth = s.add(arg[1:].split(".")) + "::text[]"
    return (
        f"SELECT jsonb_set({alias}.d, {pth}, e.value) AS d FROM ({cur}) {alias} "
        f"CROSS JOIN LATERAL jsonb_array_elements("
        f"CASE WHEN jsonb_typeof({alias}.d #> {pth}) = 'array' "
        f"THEN {alias}.d #> {pth} ELSE '[]'::jsonb END) AS e(value)"
    )


def _group(spec: dict, cur: str, alias: str, s: Sql) -> str:
    gid = spec.get("_id", "__missing__")
    if gid == "__missing__":
        raise NotImplementedError("$group sin _id")
    accs = {k: v for k, v in spec.items() if k != "_id"}
    fields: list[str] = []
    group_by: list[str] = []
    if gid is None:
        fields.append(f"{s.add('_id')}::text, 'null'::jsonb")
    elif isinstance(gid, dict) and not _is_op(gid):
        pairs: list[str] = []
        for k, sub in gid.items():
            esql = f"COALESCE({_expr(sub, s)}, 'null'::jsonb)"
            group_by.append(esql)
            pairs.append(f"{s.add(k)}::text, {esql}")
        fields.append(f"{s.add('_id')}::text, jsonb_build_object({', '.join(pairs)})")
    else:
        esql = f"COALESCE({_expr(gid, s)}, 'null'::jsonb)"
        group_by.append(esql)
        fields.append(f"{s.add('_id')}::text, {esql}")
    for name, acc in accs.items():
        fields.append(f"{s.add(name)}::text, {_acc(acc, s)}")
    sel = f"jsonb_build_object({', '.join(fields)})"
    if group_by:
        return f"SELECT {sel} AS d FROM ({cur}) {alias} GROUP BY {', '.join(group_by)}"
    # _id: None — un solo grupo, pero CERO grupos si no hay filas (como Mongo).
    return f"SELECT {sel} AS d FROM ({cur}) {alias} HAVING count(*) > 0"


def _project(spec: dict, cur: str, alias: str, s: Sql) -> str:
    include_id = True
    parts: list[str] = []
    nested: dict[str, list[str]] = {}
    for key, val in spec.items():
        if key == "_id":
            if val in (0, False):
                include_id = False
            elif val not in (1, True):
                parts.append(
                    f"jsonb_build_object({s.add('_id')}::text, COALESCE({_expr(val, s)}, 'null'::jsonb))"
                )
            continue
        if val in (1, True):
            if "." in key:
                root, rest = key.split(".", 1)
                if "." in rest:
                    raise NotImplementedError(f"Proyección anidada >2 niveles: {key}")
                nested.setdefault(root, []).append(rest)
            else:
                k = s.add(key)
                parts.append(
                    f"(CASE WHEN d ? {k}::text THEN jsonb_build_object({k}::text, d -> {k}::text) "
                    f"ELSE '{{}}'::jsonb END)"
                )
        elif val in (0, False):
            raise NotImplementedError(f"Exclusión en $project no soportada: {key}")
        else:
            parts.append(
                f"jsonb_build_object({s.add(key)}::text, COALESCE({_expr(val, s)}, 'null'::jsonb))"
            )
    for root, subkeys in nested.items():
        rk = s.add(root)
        inner: list[str] = []
        for sub in subkeys:
            pth = s.add([root, sub]) + "::text[]"
            inner.append(
                f"(CASE WHEN (d #> {pth}) IS NOT NULL "
                f"THEN jsonb_build_object({s.add(sub)}::text, d #> {pth}) ELSE '{{}}'::jsonb END)"
            )
        parts.append(
            f"(CASE WHEN d ? {rk}::text THEN jsonb_build_object({rk}::text, ({' || '.join(inner)})) "
            f"ELSE '{{}}'::jsonb END)"
        )
    head = f"jsonb_build_object({s.add('_id')}::text, d -> '_id')" if include_id else "'{}'::jsonb"
    sel = " || ".join([head, *parts]) if parts else head
    return f"SELECT ({sel}) AS d FROM ({cur}) {alias}"


def _order_by(sort_spec: dict, s: Sql) -> str:
    terms: list[str] = []
    for field, direction in sort_spec.items():
        e = f"(d #> {s.add(str(field).split('.'))}::text[])"
        dir_sql = "ASC" if int(direction) >= 0 else "DESC"
        rank = (
            f"(CASE WHEN {e} IS NULL OR {e} = 'null'::jsonb THEN 0 "
            f"WHEN jsonb_typeof({e}) = 'number' THEN 1 "
            f"WHEN jsonb_typeof({e}) = 'string' THEN 2 ELSE 3 END)"
        )
        num = f"(CASE WHEN jsonb_typeof({e}) = 'number' THEN ({e} #>> '{{}}')::numeric END)"
        txt = f"(CASE WHEN jsonb_typeof({e}) = 'string' THEN {e} #>> '{{}}' END) COLLATE \"C\""
        terms.append(f"{rank} {dir_sql}")
        terms.append(f"{num} {dir_sql} NULLS LAST")
        terms.append(f"{txt} {dir_sql} NULLS LAST")
    return ("ORDER BY " + ", ".join(terms)) if terms else ""


# ─── Expresiones ────────────────────────────────────────────────────────

_EXPR_OPS = {"$cond", "$ifNull", "$eq", "$gt", "$in", "$size", "$not", "$objectToArray"}
_ACC_OPS = {"$sum", "$avg", "$min", "$max", "$addToSet"}


def _is_op(value: dict) -> bool:
    return len(value) == 1 and next(iter(value)).startswith("$")


def _expr(e: Any, s: Sql) -> str:
    """Expresión de aggregation → SQL jsonb (SQL NULL = 'missing' de Mongo)."""
    if isinstance(e, str) and e.startswith("$"):
        if e.startswith("$$"):
            raise NotImplementedError(f"Variable de aggregation no soportada: {e}")
        return f"(d #> {s.add(e[1:].split('.'))}::text[])"
    if e is None or isinstance(e, (int, float, bool, str)):
        return s.add_json(e)
    if isinstance(e, list):
        items = ", ".join(_expr(x, s) for x in e)
        return f"jsonb_build_array({items})"
    if isinstance(e, dict) and _is_op(e):
        op, arg = next(iter(e.items()))
        if op == "$ifNull":
            a, b = arg
            return f"COALESCE(NULLIF({_expr(a, s)}, 'null'::jsonb), {_expr(b, s)})"
        if op == "$cond":
            if isinstance(arg, dict):
                c, t, f = arg["if"], arg["then"], arg["else"]
            else:
                c, t, f = arg
            return f"(CASE WHEN {_bool(c, s)} THEN {_expr(t, s)} ELSE {_expr(f, s)} END)"
        if op == "$eq":
            a, b = arg
            return (
                f"to_jsonb(COALESCE({_expr(a, s)}, 'null'::jsonb) = "
                f"COALESCE({_expr(b, s)}, 'null'::jsonb))"
            )
        if op == "$gt":
            a, b = arg
            return f"to_jsonb(({_expr(a, s)}) > ({_expr(b, s)}))"
        if op == "$in":
            item, arr = arg
            return (
                f"to_jsonb(({_expr(arr, s)}) @> "
                f"jsonb_build_array(COALESCE({_expr(item, s)}, 'null'::jsonb)))"
            )
        if op == "$not":
            inner = arg[0] if isinstance(arg, list) else arg
            return f"to_jsonb(NOT {_bool(inner, s)})"
        if op == "$size":
            return f"to_jsonb(jsonb_array_length({_expr(arg, s)}))"
        if op == "$add":
            # suma numérica; un operando no-numérico/missing → NULL (como el
            # null de Mongo). Lo usa scripts/arrange_all.py (ancho de nodo).
            parts = " + ".join(f"({_num(_expr(a, s))})" for a in arg)
            return f"to_jsonb({parts})"
        if op == "$strLenCP":
            e2 = _expr(arg, s)
            return (
                f"to_jsonb(char_length(CASE WHEN jsonb_typeof({e2}) = 'string' "
                f"THEN {e2} #>> '{{}}' END))"
            )
        if op == "$objectToArray":
            return (
                f"(SELECT COALESCE(jsonb_agg(jsonb_build_object('k', _k, 'v', _v)), "
                f"'[]'::jsonb) FROM jsonb_each({_expr(arg, s)}) AS _o(_k, _v))"
            )
        if op in _ACC_OPS:
            raise NotImplementedError(f"Acumulador {op} fuera de $group")
        raise NotImplementedError(f"Expresión de aggregation no soportada: {op}")
    if isinstance(e, dict):
        pairs = ", ".join(f"{s.add(k)}::text, COALESCE({_expr(v, s)}, 'null'::jsonb)" for k, v in e.items())
        return f"jsonb_build_object({pairs})"
    raise NotImplementedError(f"Expresión no soportada: {e!r}")


def _bool(e: Any, s: Sql) -> str:
    """Truthiness de Mongo: false, 0, null y missing son falsos."""
    return f"(COALESCE({_expr(e, s)}, 'null'::jsonb) <> ALL ({_JS_FALSY}))"


def _num(e_sql: str) -> str:
    return f"(CASE WHEN jsonb_typeof({e_sql}) = 'number' THEN ({e_sql} #>> '{{}}')::numeric END)"


def _acc(spec: Any, s: Sql) -> str:
    if not (isinstance(spec, dict) and _is_op(spec)):
        raise NotImplementedError(f"Acumulador inválido: {spec!r}")
    op, arg = next(iter(spec.items()))
    if op == "$sum":
        if isinstance(arg, (int, float)) and not isinstance(arg, bool):
            return f"to_jsonb(COALESCE(SUM({s.add(arg)}::numeric), 0))"
        return f"to_jsonb(COALESCE(SUM({_num(_expr(arg, s))}), 0))"
    if op == "$avg":
        return f"to_jsonb(AVG({_num(_expr(arg, s))}))"
    if op in ("$min", "$max"):
        e = _expr(arg, s)
        dir_sql = "ASC" if op == "$min" else "DESC"
        return (
            f"((array_agg({e} ORDER BY {e} {dir_sql}) FILTER "
            f"(WHERE {e} IS NOT NULL AND {e} <> 'null'::jsonb))[1])"
        )
    if op == "$addToSet":
        e = _expr(arg, s)
        return (
            f"COALESCE(jsonb_agg(DISTINCT {e}) FILTER (WHERE {e} IS NOT NULL), '[]'::jsonb)"
        )
    raise NotImplementedError(f"Acumulador no soportado: {op}")
