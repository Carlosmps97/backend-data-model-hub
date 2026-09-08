"""Expresiones de las acciones (spec doc 30 §6.5–6.8). PURO.

CRÍTICO (spec §7.2): las reglas producen OBJETOS de expresión sqlglot, nunca
strings — `{col}` es el AST ACUMULADO de la columna hasta ese punto, así el
encadenamiento (trim + hash) compone sin paréntesis rotos y el SQL final se
valida antes de entregarse.

Placeholders soportados (resolución literal, sin lógica embebida):
    {col} · {columna.nombre|tipo|comentario} · {tabla.nombre|esquema|catalogo}
    {udp:Nombre del UDP} · {lookup:nombre} · {funcion(args)}
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

_COL_MARK = "__DMH_COL__"
_PH_RX = re.compile(r"\{([^{}]+)\}")


class RenderError(Exception):
    """Error al renderizar una acción (placeholder irresoluble, SQL inválido).
    El export NO aborta por esto: la regla se salta y se reporta (spec §7.5)."""


def _context_value(path: str, ctx: dict):
    """Resuelve `columna.nombre`, `tabla.esquema`, `modelo.nombre` o
    `X.udp["Nombre"]` contra el contexto. None si no hay valor."""
    m = re.fullmatch(r"""(tabla|columna|modelo)\.udp\[\s*["']([^"']+)["']\s*\]""", path.strip())
    if m:
        return ((ctx.get(m.group(1)) or {}).get("udp") or {}).get(m.group(2))
    parts = path.strip().split(".")
    # `artefacto.*` (doc 76 D6) = el objeto que se está emitiendo (ref/esquema/
    # nombre/tipo); lo inyectan pipeline/generators, no tiene UDP propios.
    if len(parts) == 2 and parts[0] in ("tabla", "columna", "modelo", "artefacto"):
        return (ctx.get(parts[0]) or {}).get(parts[1])
    raise RenderError(f"Can't resolve '{path}' in this context.")


def _split_args(argstr: str) -> list[str]:
    """Split por comas de nivel superior (respeta comillas y corchetes)."""
    args, buf, depth, quote = [], "", 0, None
    for ch in argstr:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch; buf += ch
        elif ch in "([":
            depth += 1; buf += ch
        elif ch in ")]":
            depth -= 1; buf += ch
        elif ch == "," and depth == 0:
            args.append(buf.strip()); buf = ""
        else:
            buf += ch
    if buf.strip():
        args.append(buf.strip())
    return args


def _resolve_lookup(name: str, ctx: dict, lookups: dict) -> str | None:
    """{lookup:name}: BUSCARV del valor del UDP de origen en el objeto en curso.
    Valor sin mapear (o mapeado a null) con default null → None = NO emitir
    (spec §6.7)."""
    lk = (lookups or {}).get(name)
    if lk is None:
        raise RenderError(f"Unknown lookup '{name}'.")
    src_name = lk.get("fromName")
    base = lk.get("fromLevel") or "table"
    base_dsl = {"table": "tabla", "column": "columna", "canvas": "modelo"}.get(base, "tabla")
    key = ((ctx.get(base_dsl) or {}).get("udp") or {}).get(src_name) if src_name else None
    values = lk.get("values") or {}
    if key is not None and key in values:
        return values[key]
    return lk.get("default")


def expand_template(template: str, ctx: dict, lookups: dict | None = None,
                    functions: list[dict] | None = None, *, allow_col: bool = False) -> str | None:
    """Texto con placeholders → texto plano ({col} se conserva como marcador si
    `allow_col`). Devuelve None si un {lookup:...} resolvió a null — el llamador
    NO debe emitir nada para ese valor (spec §6.7)."""
    fn_by_name = {f.get("name"): f for f in (functions or [])}
    out: list[str] = []
    last = 0
    for m in _PH_RX.finditer(template):
        out.append(template[last:m.start()])
        last = m.end()
        ph = m.group(1).strip()
        if ph == "col":
            if not allow_col:
                raise RenderError("{col} is only available in column expressions.")
            out.append(f"{{{ph}}}")
            continue
        if ph.startswith("lookup:"):
            val = _resolve_lookup(ph[7:].strip(), ctx, lookups or {})
            if val is None:
                return None
            out.append(str(val))
            continue
        if ph.startswith("udp:"):
            name = ph[4:].strip()
            for base in ("columna", "tabla", "modelo"):
                udp = (ctx.get(base) or {}).get("udp") or {}
                if name in udp:
                    out.append(str(udp[name]))
                    break
            else:
                raise RenderError(f"UDP '{name}' has no value on this object.")
            continue
        call = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)", ph, re.DOTALL)
        if call and call.group(1) in fn_by_name:
            fn = fn_by_name[call.group(1)]
            params = fn.get("params") or []
            args = _split_args(call.group(2))
            if len(args) != len(params):
                raise RenderError(f"Function '{fn['name']}' expects {len(params)} argument(s).")
            body = fn.get("body") or ""
            for p, a in zip(params, args):
                if a == "col":
                    val = "{col}"
                elif a and a[0] in "'\"":
                    val = a[1:-1]
                else:
                    v = _context_value(a, ctx)
                    val = "" if v is None else str(v)
                body = body.replace(f"{{{p}}}", val)
            # el body puede usar {col} u otros placeholders planos → recursión 1 nivel
            expanded = expand_template(body, ctx, lookups, None, allow_col=allow_col)
            if expanded is None:
                return None
            out.append(expanded)
            continue
        # placeholder plano de contexto: {columna.nombre}, {tabla.esquema}, …
        val = _context_value(ph, ctx)
        out.append("" if val is None else str(val))
    out.append(template[last:])
    return "".join(out)


def build_expression(template: str, ctx: dict, col_ast: exp.Expression,
                     lookups: dict | None = None,
                     functions: list[dict] | None = None) -> exp.Expression:
    """`action.expression` → AST sqlglot con `{col}` sustituido por el AST
    ACUMULADO (copy). Levanta RenderError si el SQL resultante no parsea."""
    txt = expand_template(template, ctx, lookups, functions, allow_col=True)
    if txt is None:
        raise RenderError("A lookup resolved to null — nothing to emit.")
    try:
        ast = sqlglot.parse_one(txt.replace("{col}", _COL_MARK), read="databricks")
    except Exception as e:
        raise RenderError(f"Expression is not valid Databricks SQL: {txt[:80]}") from e
    if isinstance(ast, exp.Column) and ast.name == _COL_MARK:
        return col_ast.copy()
    for node in list(ast.walk()):
        if isinstance(node, exp.Column) and node.name == _COL_MARK:
            node.replace(col_ast.copy())
    return ast
