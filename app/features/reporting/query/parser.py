"""Parser SQL-subset → `QuerySpec` (fase 2). Es una CAPA FINA sobre el mismo IR:
parsea con sqlglot a un AST tipado, camina con ALLOWLIST estricto contra el Field
Catalog, y emite el MISMO QuerySpec que compila/ejecuta el motor. NUNCA arma SQL
contra un motor real ni traduce texto→Mongo a mano.

Soporta: SELECT campos|*, FROM vista, WHERE con AND/OR/NOT + comparadores + IN +
LIKE + IS [NOT] NULL, GROUP BY, agregados (COUNT/SUM/AVG/MIN/MAX), ORDER BY, LIMIT.
Rechaza: JOIN, subquery, CTE, UNION, DDL/DML y múltiples statements.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

from .schema import FieldDef
from .spec import Aggregation, Condition, OrderBy, QuerySpec, WhereGroup


class SqlError(Exception):
    def __init__(self, message: str, line: int = 1, col: int = 1):
        super().__init__(message)
        self.message, self.line, self.col = message, line, col


def _field_key(col: exp.Column, catalog: dict[str, FieldDef]) -> str:
    """Resuelve un identificador a una key del catálogo. `udp."Nombre"` (table=udp)
    → busca el UDP def por label; si no, la key directa (incl. 'udp.<defId>')."""
    name = col.name
    table = col.table
    if table and table.lower() == "udp":
        for fd in catalog.values():
            if fd.udpDefId and fd.label == name:
                return fd.key
        raise SqlError(f"Unknown UDP: {name!r}")
    if name in catalog:
        return name
    # permitir 'udp.<defId>' escrito como identificador plano
    dotted = f"{table}.{name}" if table else name
    if dotted in catalog:
        return dotted
    raise SqlError(f"Unknown field: {name!r}")


def _literal(e) -> object:
    if isinstance(e, exp.Boolean):
        return e.this
    if isinstance(e, exp.Null):
        return None
    if isinstance(e, exp.Literal):
        return e.name if e.is_string else (float(e.name) if "." in e.name else int(e.name))
    if isinstance(e, exp.Neg):
        return -_literal(e.this)
    raise SqlError(f"Unsupported value: {e.sql()}")


def _like_to_op(pattern: str) -> tuple[str, str]:
    """LIKE → (op, valor). '%x%'→contains, 'x%'→startsWith, 'x'→eq."""
    if pattern.startswith("%") and pattern.endswith("%"):
        return "contains", pattern.strip("%")
    if pattern.endswith("%") and not pattern.startswith("%"):
        return "startsWith", pattern[:-1]
    return "eq", pattern.strip("%")


def _condition(e, catalog: dict[str, FieldDef]):
    """Un nodo del WHERE → Condition | WhereGroup. Recursivo."""
    if isinstance(e, exp.Paren):
        return _condition(e.this, catalog)
    if isinstance(e, exp.And):
        return WhereGroup(op="and", conditions=[_condition(e.left, catalog), _condition(e.right, catalog)])
    if isinstance(e, exp.Or):
        return WhereGroup(op="or", conditions=[_condition(e.left, catalog), _condition(e.right, catalog)])
    if isinstance(e, exp.Not):
        return WhereGroup(op="not", conditions=[_condition(e.this, catalog)])
    if isinstance(e, exp.Is):
        key = _field_key(e.this, catalog)
        return Condition(field=key, op="isnull")   # IS NULL; IS NOT NULL → Not(Is) por sqlglot
    if isinstance(e, exp.In):
        key = _field_key(e.this, catalog)
        return Condition(field=key, op="in", value=[_literal(x) for x in e.expressions])
    if isinstance(e, exp.Like):
        key = _field_key(e.this, catalog)
        op, val = _like_to_op(_literal(e.expression))
        return Condition(field=key, op=op, value=val)
    OPS = {exp.EQ: "eq", exp.NEQ: "ne", exp.GT: "gt", exp.GTE: "gte", exp.LT: "lt", exp.LTE: "lte"}
    for cls, op in OPS.items():
        if isinstance(e, cls):
            return Condition(field=_field_key(e.this, catalog), op=op, value=_literal(e.expression))
    raise SqlError(f"Unsupported expression in WHERE: {e.sql()}")


def parse_from(text: str) -> str:
    """Extrae la vista del FROM (para poder cargar el catálogo). Valida statement."""
    try:
        stmts = sqlglot.parse(text)
    except Exception as ex:  # noqa: BLE001
        raise SqlError(f"Invalid SQL: {ex}")
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        raise SqlError("Send a single SELECT.")
    stmt = stmts[0]
    if not isinstance(stmt, exp.Select):
        raise SqlError("Only SELECT is allowed.")
    if list(stmt.find_all(exp.Join)):
        raise SqlError("JOIN isn't supported.")
    if list(stmt.find_all(exp.Subquery)):
        raise SqlError("Subqueries aren't supported.")
    tbl = stmt.find(exp.Table)
    if tbl is None:
        raise SqlError("Missing FROM <view>.")
    return tbl.name


def to_spec(text: str, catalog: dict[str, FieldDef], from_: str, project_id: str) -> QuerySpec:
    """SQL → QuerySpec del proyecto, resolviendo campos contra el catálogo ya
    cargado."""
    stmt = sqlglot.parse(text)[0]
    select, aggs, group_by = [], [], []
    for proj in stmt.expressions:
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(inner, exp.Star):
            continue
        if isinstance(inner, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
            fn = {exp.Count: "count", exp.Sum: "sum", exp.Avg: "avg", exp.Min: "min", exp.Max: "max"}[type(inner)]
            arg = inner.this
            fld = None if (fn == "count" or isinstance(arg, exp.Star)) else _field_key(arg, catalog)
            aggs.append(Aggregation(fn=fn, field=fld, **{"as": proj.alias if isinstance(proj, exp.Alias) else fn}))
        elif isinstance(inner, exp.Column):
            select.append(_field_key(inner, catalog))
        else:
            raise SqlError(f"Unsupported SELECT item: {proj.sql()}")
    where = None
    if stmt.args.get("where"):
        where = _condition(stmt.args["where"].this, catalog)
        if isinstance(where, Condition):
            where = WhereGroup(op="and", conditions=[where])
    for g in (stmt.args.get("group") or exp.Group()).expressions:
        group_by.append(_field_key(g, catalog))
    order = []
    if stmt.args.get("order"):
        agg_aliases = {a.as_ for a in aggs}
        for o in stmt.args["order"].expressions:
            name = o.this.name if isinstance(o.this, exp.Column) else o.this.sql()
            # En una query agrupada, ORDER BY puede referir un alias de agregación
            # o una key del groupBy (nombres de SALIDA, no del catálogo).
            key = name if (name in group_by or name in agg_aliases) else _field_key(o.this, catalog)
            order.append(OrderBy(field=key, dir="desc" if o.args.get("desc") else "asc"))
    limit = 100
    if stmt.args.get("limit"):
        limit = int(stmt.args["limit"].expression.name)
    return QuerySpec.model_validate({
        "projectId": project_id,
        "from": from_, "select": select, "where": where, "groupBy": group_by,
        "aggregations": [a.model_dump(by_alias=True) for a in aggs],
        "orderBy": [o.model_dump() for o in order], "limit": min(max(limit, 1), 5000)})
