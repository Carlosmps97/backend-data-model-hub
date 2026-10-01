"""Parser SQL-subset → `QuerySpec` (fase 2). Es una CAPA FINA sobre el mismo IR:
parsea con sqlglot a un AST tipado, camina con ALLOWLIST estricto contra el Field
Catalog, y emite el MISMO QuerySpec que compila/ejecuta el motor. NUNCA arma SQL
contra un motor real ni traduce texto→Mongo a mano.

Soporta: SELECT campos|*, FROM vista, WHERE con AND/OR/NOT + comparadores + IN +
LIKE + IS [NOT] NULL, GROUP BY, agregados (COUNT(*)/COUNT(DISTINCT campo)/SUM/
AVG/MIN/MAX), ORDER BY, LIMIT.
Rechaza: JOIN, subquery, CTE, UNION, DDL/DML y múltiples statements.

Doc 105 (ronda 3): lo que no está en el allowlist es un SqlError (400) con un
mensaje en inglés — antes varias cláusulas y formas (NOT LIKE, IS TRUE, OFFSET,
DISTINCT, HAVING, alias de columna, COUNT(campo), `= NULL`, `LIKE '%x'`…) se
IGNORABAN o se traducían mal y la consulta devolvía otro resultado sin avisar.
"""
from __future__ import annotations

import math

import sqlglot
from sqlglot import exp

from .compiler import QueryError, check_aggregation_names, field_label
from .schema import FieldDef
from .spec import MAX_PAGE, MAX_ROWS, MAX_WHERE_DEPTH, Aggregation, Condition, OrderBy, QuerySpec, WhereGroup


class SqlError(Exception):
    def __init__(self, message: str, line: int = 1, col: int = 1):
        super().__init__(message)
        self.message, self.line, self.col = message, line, col


NULL_COMPARISON = "Compare with NULL using IS NULL or IS NOT NULL."


def _field_key(col: exp.Column, catalog: dict[str, FieldDef]) -> str:
    """Resuelve un identificador a una key del catálogo. `udp."Nombre"` (table=udp)
    → busca el UDP def por label; si no, la key directa (incl. 'udp.<defId>').
    Doc 105 (P12, revisión): lo que no es un campo (`WHERE 1 = x`, `ORDER BY 1`,
    `SUM(1)`) es un SqlError — antes, un AttributeError (500)."""
    if not isinstance(col, exp.Column):
        raise SqlError(f"Expected a field name: {col.sql()}")
    name = col.name
    table = col.table
    if table and table.lower() == "udp":
        labels = {fd.label: fd.key for fd in catalog.values() if fd.udpDefId}
        key = labels.get(name) or _unique_ci(labels, name)
        if key is None:
            raise SqlError(f"Unknown UDP: {name!r}")
        return key
    if name in catalog:
        return name
    # permitir 'udp.<defId>' escrito como identificador plano
    dotted = f"{table}.{name}" if table else name
    if dotted in catalog:
        return dotted
    # Doc 105 (ronda 4): como en SQL, sin distinguir mayúsculas (PHYSICALNAME).
    key = _unique_ci({k: k for k in catalog}, name) or _unique_ci({k: k for k in catalog}, dotted)
    if key is None:
        raise SqlError(f"Unknown field: {name!r}")
    return key


def _unique_ci(names: dict[str, str], wanted: str) -> str | None:
    """El valor del ÚNICO nombre igual a `wanted` sin distinguir mayúsculas. Puro."""
    hits = [v for k, v in names.items() if k.lower() == wanted.lower()]
    return hits[0] if len(hits) == 1 else None


def _number(text: str) -> int | float:
    """Literal numérico → int (sin punto ni exponente) o float, FINITO. Doc 105
    (P12, revisión): `int('1e3')` era un 500 (notación científica legítima) y
    un entero de miles de dígitos pasaba el tope de conversión de Python (otro
    500); `1e999` —o un entero que no cabe en un float— es infinito: el WHERE
    no lo puede comparar (Lakebase escribiría `Infinity`, que Postgres
    rechaza). Puro."""
    try:
        value = float(text)
    except (ValueError, OverflowError):
        raise SqlError(f"Invalid number: {text[:40]}")
    if not math.isfinite(value):
        raise SqlError(f"Number out of range: {text[:40]}")
    return value if any(ch in text for ch in ".eE") else int(text)


def _literal(e) -> object:
    if e is None:                                   # doc 105: `None.sql()` era un 500
        raise SqlError("Missing value.")
    if isinstance(e, exp.Boolean):
        return e.this
    if isinstance(e, exp.Null):
        return None
    if isinstance(e, exp.Literal):
        return e.name if e.is_string else _number(e.name)
    if isinstance(e, exp.Neg):
        # Doc 105: sólo se niega un NÚMERO (`-'x'`/`-NULL` eran un TypeError → 500).
        value = _literal(e.this)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SqlError(f"Unsupported value: {e.sql()}")
        return -value
    raise SqlError(f"Unsupported value: {e.sql()}")


def _value(e) -> object:
    """Valor a comparar: literal NO nulo. En SQL `x = NULL` / `x IN (NULL)` no
    son verdad nunca; el motor los leería como «vacío» — resultado distinto sin
    aviso (doc 105, ronda 3): se pide IS [NOT] NULL."""
    value = _literal(e)
    if value is None:
        raise SqlError(NULL_COMPARISON)
    return value


def _limit(e) -> int:
    """LIMIT entero ≥ 1 (doc 105: `LIMIT 1.5`/`'a'`/`NULL`/`1e3` eran un 500 y
    `LIMIT 0`/`-1` se convertían en 1 sin avisar). Puro."""
    value = _literal(e)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise SqlError("LIMIT must be a whole number.")
    if value < 1:
        raise SqlError("LIMIT must be at least 1.")
    return int(value)


def _like_to_op(pattern: str) -> tuple[str, str]:
    """LIKE → (op, valor): 'x%' → startsWith, '%x%' → contains, 'x' → eq
    (`%` repetido en un extremo vale lo mismo: '%%x%%' ≡ '%x%'). Doc 105 (ronda
    3): '%x' (termina en) y un `%` en medio no tienen operador — antes se
    buscaban como texto LITERAL y salía otro resultado sin avisar. `_` es
    literal (no es comodín). Puro."""
    core = pattern.strip("%")
    if "%" in core:
        raise SqlError("LIKE supports 'text%', '%text%' or 'text' — not '%' inside the text.")
    if not core:
        return ("contains", "") if pattern else ("eq", "")
    lead, trail = pattern.startswith("%"), pattern.endswith("%")
    if lead and trail:
        return "contains", core
    if trail:
        return "startsWith", core
    if lead:
        raise SqlError("LIKE '%text' (ends with) isn't supported: use '%text%' or 'text%'.")
    return "eq", core


_COMPARISONS = {exp.EQ: "eq", exp.NEQ: "ne", exp.GT: "gt", exp.GTE: "gte", exp.LT: "lt", exp.LTE: "lte"}


def _udp_value(e, fd: FieldDef) -> object:
    """Valor para comparar con un UDP (se guardan como TEXTO): un número va con
    el texto TAL COMO SE ESCRIBIÓ (`10.0`, `9007199254740993`); el compilador le
    suma su forma canónica. Doc 105 (ronda 5): pasaba por float y
    9007199254740993 se buscaba como «…992»."""
    if fd.type == "number":
        node, sign = (e.this, "-") if isinstance(e, exp.Neg) else (e, "")
        if isinstance(node, exp.Literal) and not node.is_string:
            _number(node.name)                          # valida la forma (finito)
            return sign + node.name
    return _value(e)


def _operand(e, key: str, catalog: dict[str, FieldDef]) -> object:
    fd = catalog[key]
    return _udp_value(e, fd) if fd.udpDefId else _value(e)


TOO_DEEP_SQL = f"The WHERE is nested too deeply (more than {MAX_WHERE_DEPTH} levels)."


def _condition(e, catalog: dict[str, FieldDef], depth: int = 1):
    """Un nodo del WHERE → Condition | WhereGroup. Doc 105 (ronda 5): una cadena
    PLANA de AND/OR es UN grupo de N condiciones (`flatten`, sin recursión):
    antes, un árbol binario de un nivel por operando — 300 OR congelaban el
    event loop (recursión de pydantic) y 1 000 daban 500. El anidamiento real
    (paréntesis, NOT) tiene tope."""
    if depth > MAX_WHERE_DEPTH:
        raise SqlError(TOO_DEEP_SQL)
    if e.args.get("negate"):
        # Doc 105 (ronda 3): sqlglot 30 da `x NOT LIKE 'p'` como Like(negate=True);
        # ignorarlo ejecutaba el LIKE. Cualquier otro `negate` → no soportado.
        if type(e) is not exp.Like:
            raise SqlError(f"Unsupported expression in WHERE: {e.sql()}")
        plain = e.copy()
        plain.set("negate", None)
        return WhereGroup(op="not", conditions=[_condition(plain, catalog, depth + 1)])
    if isinstance(e, exp.Paren):
        return _condition(e.unnest(), catalog, depth)
    if isinstance(e, (exp.And, exp.Or)):
        op = "and" if isinstance(e, exp.And) else "or"
        return WhereGroup(op=op, conditions=[_condition(x, catalog, depth + 1) for x in e.flatten()])
    if isinstance(e, exp.Not):
        return WhereGroup(op="not", conditions=[_condition(e.this, catalog, depth + 1)])
    if isinstance(e, exp.Is):
        # IS NULL; IS NOT NULL → Not(Is) por sqlglot. Doc 105: IS TRUE/FALSE se
        # traducían como IS NULL (devolvían los vacíos).
        if not isinstance(e.expression, exp.Null):
            raise SqlError(f"Only IS NULL and IS NOT NULL are supported: {e.sql()}")
        return Condition(field=_field_key(e.this, catalog), op="isnull")
    if isinstance(e, exp.In):
        if e.args.get("query") or e.args.get("unnest") or e.args.get("field"):
            raise SqlError(f"Unsupported expression in WHERE: {e.sql()}")
        key = _field_key(e.this, catalog)
        return Condition(field=key, op="in", value=[_operand(x, key, catalog) for x in e.expressions])
    if type(e) is exp.Like:
        pattern = _literal(e.expression)
        if not isinstance(pattern, str):          # doc 105: `LIKE 5` era un 500
            raise SqlError(f"LIKE needs a text pattern: {e.sql()}")
        op, val = _like_to_op(pattern)
        return Condition(field=_field_key(e.this, catalog), op=op, value=val)
    if isinstance(e, exp.Between) and not e.args.get("symmetric"):
        # Doc 105 (ronda 4): `x BETWEEN a AND b` (y NOT BETWEEN, vía Not) — era un 400.
        key = _field_key(e.this, catalog)
        return Condition(field=key, op="between",
                         value=[_operand(e.args.get("low"), key, catalog), _operand(e.args.get("high"), key, catalog)])
    op = _COMPARISONS.get(type(e))
    if op:
        key = _field_key(e.this, catalog)
        return Condition(field=key, op=op, value=_operand(e.expression, key, catalog))
    raise SqlError(f"Unsupported expression in WHERE: {e.sql()}")


def parse_statement(text: str) -> exp.Select:
    """El SELECT del texto, validado — se parsea UNA vez por request (doc 105,
    ronda 4: antes dos, y `to_spec` tomaba `parse(text)[0]`, None con un `;`
    inicial → 500)."""
    try:
        stmts = sqlglot.parse(text)
    except RecursionError:
        raise SqlError(TOO_DEEP_SQL)
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
    _only_supported_clauses(stmt)
    return stmt


def view_name(stmt: exp.Select) -> str:
    """La vista del FROM, sin distinguir mayúsculas (`FROM COLUMNS`)."""
    return stmt.args["from_"].this.name.lower()


# Cláusulas del SELECT que el motor traduce; cualquier otra presente → SqlError
# (doc 105, ronda 3: se ignoraban en silencio).
_SELECT_ARGS = {"expressions", "from_", "where", "group", "order", "limit"}
_CLAUSE = {"distinct": "DISTINCT", "offset": "OFFSET", "having": "HAVING", "qualify": "QUALIFY",
           "with_": "WITH", "windows": "WINDOW", "sort": "SORT BY", "cluster": "CLUSTER BY",
           "distribute": "DISTRIBUTE BY", "locks": "FOR UPDATE", "into": "INTO", "sample": "TABLESAMPLE",
           "laterals": "LATERAL", "pivots": "PIVOT", "connect": "CONNECT BY", "prewhere": "PREWHERE"}


def _present(value) -> bool:
    return value not in (None, False, [], "")


def _unsupported(what: str) -> SqlError:
    return SqlError(f"{what} isn't supported.")


def _only_supported_clauses(stmt: exp.Select) -> None:
    for key, value in stmt.args.items():
        if key not in _SELECT_ARGS and _present(value):
            raise _unsupported(_CLAUSE.get(key, key.rstrip("_").upper()))
    source = stmt.args.get("from_")
    if source is None or not isinstance(source.this, exp.Table):
        raise SqlError("Missing FROM <view>.")
    for key, value in source.this.args.items():
        if key not in ("this", "alias") and _present(value):
            if key in ("db", "catalog"):
                raise SqlError("Use the view name alone: FROM columns, FROM tables…")
            raise _unsupported(_CLAUSE.get(key, key.rstrip("_").upper()))
    group = stmt.args.get("group")
    if group is not None:
        for key, value in group.args.items():
            if key != "expressions" and _present(value):
                raise _unsupported(f"GROUP BY {key.replace('_', ' ').upper()}")
    limit = stmt.args.get("limit")
    if limit is not None:
        if not isinstance(limit, exp.Limit):
            raise SqlError("FETCH isn't supported: use LIMIT n.")
        for key, value in limit.args.items():
            if key != "expression" and _present(value):
                raise SqlError("Only LIMIT n is supported (a whole number of rows).")


_AGGREGATES = {exp.Count: "count", exp.Sum: "sum", exp.Avg: "avg", exp.Min: "min", exp.Max: "max"}


def _aggregation(item, inner, catalog: dict[str, FieldDef]) -> Aggregation:
    """Un agregado del SELECT. COUNT(*) (o de una constante) cuenta filas;
    COUNT(DISTINCT campo) → countDistinct. Doc 105 (ronda 3): COUNT(campo)
    contaba filas (ignoraba el campo) y SUM/AVG(DISTINCT …) ignoraban el
    DISTINCT — ahora son SqlError."""
    fn = _AGGREGATES[type(inner)]
    arg = inner.this
    name = item.alias if isinstance(item, exp.Alias) else fn
    if isinstance(arg, exp.Distinct):
        if fn != "count" or len(arg.expressions) != 1:
            raise SqlError("DISTINCT is only supported as COUNT(DISTINCT field).")
        return Aggregation(fn="countDistinct", field=_field_key(arg.expressions[0], catalog), **{"as": name})
    if inner.expressions:
        raise SqlError(f"Unsupported SELECT item: {item.sql()}")
    if fn == "count":
        if isinstance(arg, (exp.Star, exp.Literal)):
            return Aggregation(fn="count", field=None, **{"as": name})
        raise SqlError("COUNT(field) isn't supported: use COUNT(*) or COUNT(DISTINCT field).")
    return Aggregation(fn=fn, field=_field_key(arg, catalog), **{"as": name})


def _position(e, items: list[tuple[str, str, object]], clause: str) -> tuple[str, str, object] | None:
    """`GROUP BY 1` / `ORDER BY 2`: el ítem N del SELECT (doc 105, ronda 4 — era
    un 400). None si `e` no es un número entero. Puro."""
    if not (isinstance(e, exp.Literal) and not e.is_string):
        return None
    n = _number(e.name)
    if not isinstance(n, int) or not 1 <= n <= len(items):
        raise SqlError(f"{clause} {e.name}: there is no SELECT item {e.name}.")
    return items[n - 1]


def _order(o, catalog: dict[str, FieldDef], items: list[tuple[str, str, object]], aggs: list[Aggregation],
           group_by: list[str], grouped: bool) -> OrderBy:
    """Un ORDER BY: un campo, un alias de agregación (sin distinguir
    mayúsculas), la POSICIÓN de un ítem del SELECT o un agregado que esté en el
    SELECT (`ORDER BY COUNT(*)`). El motor ordena los vacíos PRIMERO en ASC y AL
    FINAL en DESC (el default de sqlglot): pedir lo contrario es un SqlError.
    Doc 105 (ronda 5): el agregado se compara RESUELTO (COUNT(1) ≡ COUNT(*),
    MAX(c.x) ≡ MAX(x)) y un campo escrito exacto gana a un alias que sólo
    difiere en mayúsculas."""
    if not isinstance(o, exp.Ordered):
        o = exp.Ordered(this=o)
    desc = bool(o.args.get("desc"))
    if _present(o.args.get("with_fill")):
        raise _unsupported("WITH FILL")
    nulls_first = o.args.get("nulls_first")
    if nulls_first is not None and bool(nulls_first) == desc:
        raise SqlError("Empty values come first in ASC and last in DESC: NULLS FIRST/LAST can't change that.")
    col = o.this
    item = _position(col, items, "ORDER BY")
    aliases = {a.as_: a.as_ for a in aggs}
    if item is not None:
        if item[0] == "star":
            raise SqlError(f"ORDER BY {col.name}: that SELECT item is *.")
        key = item[1]
    elif type(col) in _AGGREGATES:
        wanted = _aggregation(col, col, catalog)
        same = [a.as_ for a in aggs if (a.fn, a.field) == (wanted.fn, wanted.field)]
        if not same:
            raise SqlError(f"ORDER BY an aggregation that is in the SELECT: {col.sql()}")
        key = same[0]
    elif isinstance(col, exp.Column) and not col.table and col.name in aliases:
        key = col.name                                     # alias (nombre de SALIDA)
    elif isinstance(col, exp.Column) and not col.table and col.name in catalog and \
            (not grouped or col.name in group_by):
        key = col.name                                     # el campo, escrito exacto (sin `udp.`)
    elif isinstance(col, exp.Column) and not col.table and _unique_ci(aliases, col.name):
        key = _unique_ci(aliases, col.name)
    else:
        key = _field_key(col, catalog)
    return OrderBy(field=key, dir="desc" if desc else "asc")


def _name_aggregations(select_items, aggs: list[Aggregation]) -> list[Aggregation]:
    """Nombre de salida de los agregados SIN alias: la función (`count`) y, si ya
    está tomado, `count_2`, `count_3`… (doc 105, ronda 4: dos COUNT sin alias
    eran un 400 por nombre repetido). Los alias explícitos no se tocan. Ronda 6:
    «tomado» SIN distinguir mayúsculas, como `check_aggregation_names` —
    `COUNT(*) AS Count, COUNT(DISTINCT x)` elegía `count` y era un 400."""
    explicit = {a.as_.lower() for (item, _), a in zip(select_items, aggs) if isinstance(item, exp.Alias)}
    taken, out = set(explicit), []
    for (item, _), a in zip(select_items, aggs):
        if isinstance(item, exp.Alias):
            out.append(a)
            continue
        name, n = a.fn if a.fn != "countDistinct" else "count", 2
        base = name
        while name.lower() in taken:
            name, n = f"{base}_{n}", n + 1
        taken.add(name.lower())
        out.append(a.model_copy(update={"as_": name}))
    return out


def to_spec(stmt: exp.Select, catalog: dict[str, FieldDef], from_: str, project_id: str) -> QuerySpec:
    """SELECT (ya validado por `parse_statement`) → QuerySpec del proyecto,
    resolviendo campos contra el catálogo ya cargado."""
    _only_supported_clauses(stmt)
    items: list[tuple[str, str, object]] = []          # (dim|agg|star, key o alias, nodo) en orden de SELECT
    agg_items, aggs, star = [], [], False
    for item in stmt.expressions:
        inner = item.this if isinstance(item, exp.Alias) else item
        if isinstance(inner, exp.Star):
            star = True
            items.append(("star", "*", inner))
        elif type(inner) in _AGGREGATES:
            agg_items.append((item, inner))
            aggs.append(_aggregation(item, inner, catalog))
            items.append(("agg", "", inner))
        elif isinstance(inner, exp.Column):
            if isinstance(item, exp.Alias):
                raise SqlError(f"Column aliases aren't supported (only on aggregations): {item.sql()}")
            items.append(("dim", _field_key(inner, catalog), inner))
        else:
            raise SqlError(f"Unsupported SELECT item: {item.sql()}")
    aggs = _name_aggregations(agg_items, aggs)
    names = iter(a.as_ for a in aggs)
    items = [(kind, next(names) if kind == "agg" else key, node) for kind, key, node in items]
    select = [key for kind, key, _ in items if kind == "dim"]
    where = None
    if stmt.args.get("where"):
        where = _condition(stmt.args["where"].this, catalog)
        if isinstance(where, Condition):
            where = WhereGroup(op="and", conditions=[where])
    group_by = []
    for g in (stmt.args.get("group") or exp.Group()).expressions:
        item = _position(g, items, "GROUP BY")
        if item is not None and item[0] != "dim":
            raise SqlError(f"GROUP BY {g.name}: that SELECT item isn't a field.")
        group_by.append(item[1] if item is not None else _field_key(g, catalog))
    grouped = bool(aggs or group_by)
    result_columns = None
    if grouped:
        # Doc 105 (ronda 3): el SELECT de una consulta agrupada son sus
        # dimensiones y agregados; un campo suelto o `*` se ignoraban.
        if star:
            raise SqlError("SELECT * can't be combined with GROUP BY or aggregations.")
        loose = [k for k in select if k not in group_by]
        if loose:
            raise SqlError(f"Field {field_label(catalog[loose[0]])!r} must be in GROUP BY "
                           "(or inside an aggregation).")
        try:
            check_aggregation_names(group_by, aggs)
        except QueryError as e:
            raise SqlError(str(e)) from e
        # Ronda 5: las columnas del resultado son las del SELECT, en su orden —
        # agregados antes que dimensiones, y una dimensión agrupada que no está
        # en el SELECT no sale (antes, dos 400).
        visible = [key for _, key, _ in items]
        if visible != [*group_by, *(a.as_ for a in aggs)]:
            result_columns = visible
    elif star:
        # `*, campo`: todos los campos (el `*` se perdía y quedaba sólo `campo`).
        select = list(dict.fromkeys([k for k, fd in catalog.items() if fd.udpDefId is None] + select)) if select else []
    order = [_order(o, catalog, items, aggs, group_by, grouped)
             for o in (stmt.args.get("order") or exp.Order()).expressions]
    if grouped:
        aliases = {a.as_ for a in aggs}
        outside = [o.field for o in order if o.field not in group_by and o.field not in aliases]
        if outside:
            shown = field_label(catalog[outside[0]]) if outside[0] in catalog else outside[0]
            raise SqlError(f"Can't sort by {shown!r}: in a grouped query sort by a GROUP BY field "
                           "or an aggregation name.")
    elif len(order) > 1:
        raise SqlError("Sort by one field: rows are paged by a single sort field.")
    # Doc 105: `LIMIT n` es el tope TOTAL (`maxRows`, como en SQL); la página
    # es de a lo más MAX_PAGE. Antes era sólo el tamaño de la primera página y
    # «Load more» y el export seguían más allá. Sin LIMIT: todo, por páginas.
    total = _limit(stmt.args["limit"].expression) if stmt.args.get("limit") else None
    return QuerySpec.model_validate({
        "projectId": project_id,
        "from": from_, "select": select, "where": where, "groupBy": group_by,
        "aggregations": [a.model_dump(by_alias=True) for a in aggs],
        "orderBy": [o.model_dump() for o in order],
        "limit": min(total, MAX_PAGE) if total else 100,
        "maxRows": min(total, MAX_ROWS) if total else None,
        "resultColumns": result_columns})
