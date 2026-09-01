"""Parser/validador del SQL custom de una vista Personalizada (doc 61 §2.4).

Puro (sin I/O). Reglas:
- UN solo statement; la raíz debe ser un SELECT — `WITH … AS (…) SELECT …`,
  joins, subqueries y set-ops (UNION/EXCEPT/INTERSECT, se toma la proyección
  del PRIMER select) están soportados por sqlglot nativamente.
- Anti-`SELECT *` (política doc 09): el select FINAL debe nominar sus columnas
  (`COUNT(*)` DENTRO de una expresión sí es válido).
- Expresión compleja sin alias → error (la vista necesita nombres de salida).
- Nombres de salida duplicados (CI) → error (Databricks lo rechazaría).

Salida: {"columns": [{"name", "expression"?}], "tables": [str]} — `tables` son
las referencias reales (CTEs excluidos), para diagnóstico en la UI.
Dialecto: `databricks` (el mismo de TODO el motor DDL, doc 30).
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

# Set-ops: en sqlglot 30.x Union/Except/Intersect heredan de SetOperation.
_SET_OPS = tuple(t for t in (getattr(exp, "SetOperation", None), exp.Union) if t)


class CustomSqlError(Exception):
    """Error de SQL custom con posición 1-based (para el caret del sandbox)."""

    def __init__(self, message: str, line: int = 1, col: int = 1):
        super().__init__(message)
        self.message, self.line, self.col = message, line, col


def _pos_of(text: str, token: str) -> tuple[int, int]:
    """(line, col) 1-based del token en el texto. Best-effort (sqlglot no
    expone offsets por nodo) — mismo criterio que ddl_rules/conditions."""
    idx = text.find(token)
    if idx < 0:
        return 1, 1
    line = text.count("\n", 0, idx) + 1
    line_start = text.rfind("\n", 0, idx) + 1
    return line, idx - line_start + 1


def _final_select(ast: exp.Expression) -> exp.Select | None:
    """Desciende paréntesis/subquery y set-ops hasta el PRIMER SELECT (el que
    define nombres y orden de las columnas de salida)."""
    node: exp.Expression | None = ast
    while node is not None:
        if isinstance(node, exp.Select):
            return node
        if isinstance(node, (exp.Paren, exp.Subquery)) or isinstance(node, _SET_OPS):
            node = node.this
            continue
        return None
    return None


def parse_custom_sql(sql: str) -> dict:
    """Valida e interpreta el script. Levanta `CustomSqlError`; si es válido
    devuelve columnas de salida y tablas referenciadas (ver docstring módulo)."""
    src = (sql or "").strip()
    if not src:
        raise CustomSqlError("Custom SQL is empty.")
    try:
        stmts = [s for s in sqlglot.parse(src, read="databricks") if s is not None]
    except Exception as e:  # sqlglot.ParseError
        errs = getattr(e, "errors", None) or [{}]
        first = errs[0]
        raise CustomSqlError(
            "The SQL couldn't be parsed. Check the syntax.",
            line=first.get("line") or 1, col=first.get("col") or 1) from e
    if len(stmts) != 1:
        raise CustomSqlError("Custom SQL must be a single statement.")
    select = _final_select(stmts[0])
    if select is None:
        raise CustomSqlError("Custom SQL must be a SELECT query "
                             "(WITH … SELECT is supported).")

    columns: list[dict] = []
    seen: set[str] = set()
    for e in select.expressions:
        # `SELECT *` / `SELECT t.*` en el select final: prohibidos (la vista
        # debe nominar su proyección — política anti-SELECT * del producto).
        if isinstance(e, exp.Star) or (isinstance(e, exp.Column)
                                       and isinstance(e.this, exp.Star)):
            tok = e.sql(dialect="databricks")
            line, col = _pos_of(src, tok)
            raise CustomSqlError(
                "`SELECT *` is not allowed: name every output column.",
                line=line, col=col)
        name = e.alias_or_name or ""
        if not name:
            tok = e.sql(dialect="databricks")
            line, col = _pos_of(src, tok)
            raise CustomSqlError(
                f"The expression '{tok}' needs an alias (… AS name).",
                line=line, col=col)
        if name.lower() in seen:
            line, col = _pos_of(src, name)
            raise CustomSqlError(f"Duplicate output column name: '{name}'.",
                                 line=line, col=col)
        seen.add(name.lower())
        col_out: dict = {"name": name}
        inner = e.this if isinstance(e, exp.Alias) else e
        if not isinstance(inner, exp.Column):
            col_out["expression"] = inner.sql(dialect="databricks")
        columns.append(col_out)
    if not columns:
        raise CustomSqlError("The SELECT has no output columns.")

    cte_names = {c.alias_or_name.lower() for c in stmts[0].find_all(exp.CTE)
                 if c.alias_or_name}
    tables: list[str] = []
    seen_t: set[str] = set()
    for t in stmts[0].find_all(exp.Table):
        if (t.name or "").lower() in cte_names:
            continue
        full = ".".join(p for p in (t.catalog, t.db, t.name) if p)
        if full and full.lower() not in seen_t:
            seen_t.add(full.lower())
            tables.append(full)
    return {"columns": columns, "tables": tables}
