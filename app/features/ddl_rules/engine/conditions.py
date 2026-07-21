"""Parser + evaluador del DSL de condiciones (spec doc 30 §6). PURO — sin BD.

Las condiciones usan sintaxis SQL (los modeladores ya la hablan): se parsean con
sqlglot en dialecto Databricks y se evalúan caminando el AST con ALLOWLIST
estricta contra un dict de contexto — mismo patrón que el parser SQL del
reporting (`reporting/query/parser.py`). NUNCA se ejecuta SQL real.

Contexto (spec §6.3) — dict por objeto en curso:
    {"tabla":   {"nombre","esquema","catalogo","tipo","comentario","udp":{name:val}},
     "columna": {"nombre","tipo","nulable","pk","orden","comentario","dominio","udp":{...}},
     "modelo":  {"nombre","udp":{...}}}

Referencias a UDP: forma canónica `columna.udp["Nombre con espacios"]`
(Bracket); se acepta `columna.udp.nombre` solo para identificadores simples.
Comparaciones de string CASE-SENSITIVE (spec §6.4): los valores de UDP son
lista cerrada — estricto a propósito.
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

# Atributos válidos por objeto (además de `udp`). `dominio` = Parent Domain.
CONTEXT_ATTRS: dict[str, tuple[str, ...]] = {
    "tabla": ("nombre", "esquema", "catalogo", "tipo", "comentario"),
    "columna": ("nombre", "tipo", "nulable", "pk", "orden", "comentario", "dominio"),
    "modelo": ("nombre",),
}
# base del DSL → nivel de UDP en la plataforma (el "Model" del spec = canvas).
BASE_TO_LEVEL = {"columna": "column", "tabla": "table", "modelo": "canvas"}


class CondError(Exception):
    """Error de condición con posición 1-based (para el caret del editor)."""

    def __init__(self, message: str, line: int = 1, col: int = 1, token: str | None = None):
        super().__init__(message)
        self.message, self.line, self.col, self.token = message, line, col, token


def _col_of(text: str, token: str) -> int:
    """Columna 1-based del token en la PRIMERA línea que lo contiene (caret de
    la UI). Best-effort: sqlglot no expone offsets por nodo."""
    idx = text.find(token)
    if idx < 0:
        return 1
    line_start = text.rfind("\n", 0, idx) + 1
    return idx - line_start + 1


# ── Azúcar sintáctica (spec §6.4): STARTS WITH / ENDS WITH → LIKE ──────────

_STARTS = re.compile(r"\bSTARTS\s+WITH\s+('(?:[^']|'')*')", re.IGNORECASE)
_ENDS = re.compile(r"\bENDS\s+WITH\s+('(?:[^']|'')*')", re.IGNORECASE)


def desugar(text: str) -> str:
    """`STARTS WITH 'x'` → `LIKE 'x%'` · `ENDS WITH 'x'` → `LIKE '%x'`.
    (Si el literal contiene % o _ reales, usar LIKE directamente.)"""
    text = _STARTS.sub(lambda m: f"LIKE '{m.group(1)[1:-1]}%'", text)
    text = _ENDS.sub(lambda m: f"LIKE '%{m.group(1)[1:-1]}'", text)
    return text


# ── Parse ──────────────────────────────────────────────────────────────────

_ALLOWED_NODES = (
    exp.And, exp.Or, exp.Not, exp.Paren,
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.In, exp.Like, exp.Is,
    exp.Bracket, exp.Column, exp.Identifier,
    exp.Literal, exp.Boolean, exp.Null, exp.Tuple,
)


def parse_condition(text: str) -> exp.Expression:
    """Texto → AST validado contra la allowlist. Condición vacía ≡ `true`
    (la regla aplica siempre). Levanta `CondError` con line/col."""
    src = (text or "").strip()
    if not src:
        return exp.Boolean(this=True)
    try:
        ast = sqlglot.parse_one(desugar(src), read="databricks")
    except Exception as e:  # sqlglot.ParseError
        errs = getattr(e, "errors", None) or [{}]
        first = errs[0]
        raise CondError(
            "The condition couldn't be parsed. Check operators and quotes.",
            line=first.get("line") or 1, col=first.get("col") or 1) from e
    for node in ast.walk():
        if not isinstance(node, _ALLOWED_NODES):
            tok = node.sql(dialect="databricks")
            raise CondError(
                f"'{tok}' is not supported in conditions. Allowed: comparisons, "
                "IN, LIKE, IS NULL, STARTS/ENDS WITH, AND, OR, NOT.",
                col=_col_of(src, tok), token=tok)
    return ast


# ── Referencias del AST ────────────────────────────────────────────────────


def _path_of(node: exp.Expression, text: str) -> tuple[str, ...] | None:
    """(base, attr) | (base, 'udp', nombre) para Column/Bracket; None si el
    nodo no es una referencia de contexto."""
    if isinstance(node, exp.Bracket):
        base = node.this
        if not (isinstance(base, exp.Column) and len(base.parts) == 2
                and base.parts[1].name == "udp"):
            tok = node.sql(dialect="databricks")
            raise CondError(f"Only <objeto>.udp[\"Name\"] supports brackets — got '{tok}'.",
                            col=_col_of(text, tok), token=tok)
        keys = node.expressions
        if len(keys) != 1 or not (isinstance(keys[0], exp.Literal) and keys[0].is_string):
            raise CondError("udp[...] needs exactly one quoted UDP name.",
                            col=_col_of(text, "["))
        return (base.parts[0].name, "udp", keys[0].name)
    if isinstance(node, exp.Column):
        parts = [p.name for p in node.parts]
        if len(parts) == 2:
            return (parts[0], parts[1])
        if len(parts) == 3 and parts[1] == "udp":
            return (parts[0], "udp", parts[2])
        tok = node.sql(dialect="databricks")
        raise CondError(f"Unknown reference '{tok}'. Use tabla.*, columna.* or modelo.*.",
                        col=_col_of(text, tok), token=tok)
    return None


def _check_path(path: tuple[str, ...], text: str) -> None:
    base = path[0]
    if base not in CONTEXT_ATTRS:
        raise CondError(f"Unknown object '{base}'. Use tabla, columna or modelo.",
                        col=_col_of(text, base), token=base)
    if len(path) == 2 and path[1] not in CONTEXT_ATTRS[base]:
        tok = f"{base}.{path[1]}"
        raise CondError(
            f"'{path[1]}' is not a field of {base}. "
            f"Available: {', '.join(CONTEXT_ATTRS[base])}, udp[\"…\"].",
            col=_col_of(text, tok), token=tok)


def extract_refs(ast: exp.Expression, text: str = "") -> list[tuple[str, ...]]:
    """Todas las referencias de contexto del AST, validadas de forma. Los
    Column internos de un Bracket no se re-reportan."""
    refs: list[tuple[str, ...]] = []
    skip: set[int] = set()
    for node in ast.walk():
        if id(node) in skip:
            continue
        path = _path_of(node, text)
        if path is None:
            continue
        if isinstance(node, exp.Bracket):
            for inner in node.this.walk():
                skip.add(id(inner))
        _check_path(path, text)
        refs.append(path)
    return refs


def udp_names_used(ast: exp.Expression, text: str = "") -> list[tuple[str, str]]:
    """[(base, nombreUDP)] únicos, en orden de aparición."""
    seen: list[tuple[str, str]] = []
    for path in extract_refs(ast, text):
        if len(path) == 3 and (path[0], path[2]) not in seen:
            seen.append((path[0], path[2]))
    return seen


def literal_comparisons(ast: exp.Expression, text: str = "") -> list[tuple[str, str, str, list[str]]]:
    """Comparaciones UDP-vs-literal para el check 'Value allowed for UDP':
    [(base, nombreUDP, op, [literales string])]. Solo EQ/NEQ/IN — LIKE es
    patrón, no valor."""
    out: list[tuple[str, str, str, list[str]]] = []

    def _udp_of(side) -> tuple[str, str] | None:
        path = _path_of(side, text) if isinstance(side, (exp.Column, exp.Bracket)) else None
        return (path[0], path[2]) if path and len(path) == 3 else None

    for node in ast.walk():
        if isinstance(node, (exp.EQ, exp.NEQ)):
            for udp_side, lit_side in ((node.this, node.expression), (node.expression, node.this)):
                ref = _udp_of(udp_side)
                if ref and isinstance(lit_side, exp.Literal) and lit_side.is_string:
                    op = "=" if isinstance(node, exp.EQ) else "<>"
                    out.append((ref[0], ref[1], op, [lit_side.name]))
        elif isinstance(node, exp.In):
            ref = _udp_of(node.this)
            if ref:
                lits = [e.name for e in node.expressions
                        if isinstance(e, exp.Literal) and e.is_string]
                if lits:
                    out.append((ref[0], ref[1], "IN", lits))
    return out


# ── Eval ───────────────────────────────────────────────────────────────────


def _like_match(value: str, pattern: str) -> bool:
    """LIKE SQL: % = cualquier secuencia, _ = un carácter. Case-sensitive."""
    rx = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                 for ch in pattern)
    return re.fullmatch(rx, value, flags=re.DOTALL) is not None


def _resolve(node: exp.Expression, ctx: dict, text: str):
    if isinstance(node, exp.Paren):
        return _resolve(node.this, ctx, text)
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.name
        return float(node.name) if "." in node.name else int(node.name)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    path = _path_of(node, text)
    if path is None:
        tok = node.sql(dialect="databricks")
        raise CondError(f"Can't evaluate '{tok}' in a condition.", col=_col_of(text, tok), token=tok)
    _check_path(path, text)
    obj = ctx.get(path[0]) or {}
    if len(path) == 3:
        return (obj.get("udp") or {}).get(path[2])
    return obj.get(path[1])


def _compare(node: exp.Expression, left, right) -> bool:
    if left is None or right is None:
        return False  # semántica SQL: NULL no compara (usar IS NULL)
    if isinstance(left, bool) or isinstance(right, bool):
        return (left is right) if isinstance(node, exp.EQ) else \
               (left is not right) if isinstance(node, exp.NEQ) else False
    if isinstance(left, (int, float)) != isinstance(right, (int, float)):
        # tipo mixto (ej. orden >= '3'): coacciona a número si se puede
        try:
            left, right = float(left), float(right)
        except (TypeError, ValueError):
            return False
    if isinstance(node, exp.EQ):
        return left == right
    if isinstance(node, exp.NEQ):
        return left != right
    if isinstance(node, exp.GT):
        return left > right
    if isinstance(node, exp.GTE):
        return left >= right
    if isinstance(node, exp.LT):
        return left < right
    return left <= right  # LTE


def eval_condition(ast: exp.Expression, ctx: dict, text: str = "") -> bool:
    """Evalúa el AST contra el contexto. Determinista, sin efectos."""
    if isinstance(ast, exp.Paren):
        return eval_condition(ast.this, ctx, text)
    if isinstance(ast, exp.Boolean):
        return bool(ast.this)
    if isinstance(ast, exp.And):
        return eval_condition(ast.this, ctx, text) and eval_condition(ast.expression, ctx, text)
    if isinstance(ast, exp.Or):
        return eval_condition(ast.this, ctx, text) or eval_condition(ast.expression, ctx, text)
    if isinstance(ast, exp.Not):
        return not eval_condition(ast.this, ctx, text)
    if isinstance(ast, exp.Is):
        val = _resolve(ast.this, ctx, text)
        if isinstance(ast.expression, exp.Null):
            return val is None
        if isinstance(ast.expression, exp.Boolean):
            return val is bool(ast.expression.this)
        raise CondError("IS only supports NULL / TRUE / FALSE.")
    if isinstance(ast, exp.In):
        val = _resolve(ast.this, ctx, text)
        if val is None:
            return False
        opts = {_resolve(e, ctx, text) for e in ast.expressions}
        return val in opts
    if isinstance(ast, exp.Like):
        val = _resolve(ast.this, ctx, text)
        pat = _resolve(ast.expression, ctx, text)
        if val is None or not isinstance(pat, str):
            return False
        hit = _like_match(str(val), pat)
        return (not hit) if ast.args.get("negate") else hit
    if isinstance(ast, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return _compare(ast, _resolve(ast.this, ctx, text), _resolve(ast.expression, ctx, text))
    tok = ast.sql(dialect="databricks")
    raise CondError(f"'{tok}' is not supported in conditions.", col=_col_of(text, tok), token=tok)
