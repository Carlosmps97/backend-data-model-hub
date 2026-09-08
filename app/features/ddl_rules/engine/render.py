"""Motor de aplicación de reglas de COLUMNA sobre un SELECT (doc 30 F3). PURO.

- Orden SIEMPRE determinista: (priority DESC, name ASC) — nunca el orden del
  archivo (spec §7.1).
- Encadenamiento: varias reglas sobre la misma columna se APILAN — `{col}` de
  la regla N es el AST de salida de la N-1 (spec §7.2). Nada de strings.
- Reglas deshabilitadas o no-válidas se SALTAN sin abortar y se reportan
  (spec §7.5).
- Si ninguna regla toca la vista, el SQL de entrada vuelve INTACTO
  (byte-identical); si alguna aplica, el statement se re-emite con el formato
  determinista de sqlglot (pretty, dialecto databricks) — idempotente (§7.6).
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from . import conditions as cond
from .expressions import RenderError, build_expression, expand_template


# ── Identificadores (doc 71 H2): UN solo funnel para todo el archivo ────────
# El CREATE del front sale con backticks + `identifierCase`; los ALTER … SET
# TAGS y los artefactos generados usan el MISMO criterio (antes los tags iban
# crudos y el archivo mezclaba dos estilos).


def _case(s: str, mode: str) -> str:
    return s.lower() if mode == "lower" else s.upper() if mode == "upper" else s


def ident(s: str, options: dict | None = None) -> str:
    """Identificador backtickeado con el casing de las opciones del export
    (`identifierCase`: as-is | lower | upper)."""
    case = (options or {}).get("identifierCase") or "as-is"
    return f"`{_case(str(s), case)}`"


def full_name(schema: str | None, name: str, options: dict | None = None) -> str:
    """`\`schema\`.\`name\`` (o solo el nombre) con el casing del export."""
    return f"{ident(schema, options)}.{ident(name, options)}" if schema else ident(name, options)


def run_order(rules: list[dict]) -> list[dict]:
    """(priority DESC, name ASC) — el único orden del motor (spec §7.1)."""
    return sorted(rules, key=lambda r: (-(r.get("priority") or 0), (r.get("name") or "").lower()))


def runnable(rules: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa las que corren de las que se saltan (disabled / invalid / stale
    solo avisa pero corre — el valor sigue siendo válido sintácticamente).
    Devuelve (corren, skipped_log)."""
    run: list[dict] = []
    skipped: list[dict] = []
    for r in rules:
        if not r.get("enabled", True):
            skipped.append({"rule": r.get("name"), "status": "skipped", "reason": "disabled"})
        elif r.get("validationState") == "invalid":
            skipped.append({"rule": r.get("name"), "status": "skipped", "reason": "invalid"})
        else:
            run.append(r)
    return run, skipped


def resolve_lookup_names(lookups: dict, name_by_id: dict[str, str]) -> dict:
    """Enriquece cada lookup con `fromName` (el render evalúa por nombre de UDP;
    la config persiste `fromUdpId` — binding por id, doc 30 A6)."""
    out: dict = {}
    for name, lk in (lookups or {}).items():
        lk = dict(lk or {})
        if lk.get("fromUdpId") and not lk.get("fromName"):
            lk["fromName"] = name_by_id.get(lk["fromUdpId"])
        out[name] = lk
    return out


def column_rules_for(rules: list[dict], artifact: str) -> list[dict]:
    return [r for r in rules
            if r.get("kind") == "rule" and r.get("target") == "column"
            and artifact in (r.get("appliesTo") or [])]


def _find_select(ast: exp.Expression) -> exp.Select | None:
    if isinstance(ast, exp.Select):
        return ast
    return next((n for n in ast.walk() if isinstance(n, exp.Select)), None)


def _projection_parts(proj: exp.Expression) -> tuple[str | None, str | None, exp.Expression]:
    """(nombre_columna_fuente, nombre_salida, expresión_fuente) de una
    proyección. Fuente None si es Star o una expresión sin columna clara."""
    if isinstance(proj, exp.Alias):
        inner = proj.this
        out_name = proj.alias
        src = inner.name if isinstance(inner, exp.Column) else None
        return src, out_name, inner
    if isinstance(proj, exp.Column):
        return proj.name, proj.name, proj
    return None, None, proj


def decorate_select(select: exp.Select, artifact: str, rules: list[dict],
                    config: dict, base_ctx: dict,
                    cols_ctx_by_name: dict[str, dict]) -> list[dict]:
    """Aplica las reglas de columna (ya filtradas por runnable) a las
    proyecciones del SELECT, IN PLACE. Devuelve el log de aplicaciones:
    [{rule, column, status: applied|skipped, reason?}]."""
    log: list[dict] = []
    ordered = run_order(column_rules_for(rules, artifact))
    if not ordered:
        return log
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, exp.Expression] = {}

    for i, proj in enumerate(list(select.expressions)):
        src_name, out_name, src_expr = _projection_parts(proj)
        match_name = src_name or out_name
        if match_name is None or match_name not in cols_ctx_by_name:
            continue
        ctx = {**base_ctx, "columna": cols_ctx_by_name[match_name]}
        acc = src_expr
        alias_tpl: str | None = None
        touched = False
        for r in ordered:
            rid = r.get("id") or r.get("name")
            try:
                ast_cond = parsed.get(rid)
                if ast_cond is None:
                    ast_cond = cond.parse_condition(r.get("condition") or "")
                    parsed[rid] = ast_cond
                if not cond.eval_condition(ast_cond, ctx, r.get("condition") or ""):
                    continue
                tpl = ((r.get("action") or {}).get("expression") or "").strip()
                if not tpl:
                    continue
                acc = build_expression(tpl, ctx, acc, lookups, functions)
                alias_tpl = ((r.get("action") or {}).get("alias") or "").strip() or alias_tpl
                touched = True
                log.append({"rule": r.get("name"), "column": match_name, "status": "applied"})
            except (RenderError, cond.CondError) as e:
                log.append({"rule": r.get("name"), "column": match_name,
                            "status": "skipped", "reason": str(e)})
        if not touched:
            continue
        alias = out_name or match_name
        if alias_tpl:
            expanded = expand_template(alias_tpl, ctx, lookups, functions)
            if expanded:
                alias = expanded
        select.set("expressions", [
            *select.expressions[:i],
            exp.alias_(acc, alias, quoted=False),
            *select.expressions[i + 1:],
        ])
    return log


def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _expand_pairs(pairs: dict, ctx: dict, lookups: dict, functions: list[dict],
                  rule_name: str, target_desc: str, log: list[dict]) -> list[tuple[str, str]]:
    """Expande {k: template} a pares (k, valor) ORDENADOS por clave (idempotencia
    §7.6). Un lookup a null omite el par (spec §6.7); un valor >256 chars o un
    placeholder irresoluble saltan el par con log — nunca abortan (§7.5)."""
    out: list[tuple[str, str]] = []
    for k in sorted(pairs):
        try:
            val = expand_template(str(pairs[k]), ctx, lookups, functions)
        except RenderError as e:
            log.append({"rule": rule_name, "status": "skipped",
                        "reason": f"{target_desc} · tag '{k}': {e}"})
            continue
        if val is None:
            continue
        if len(val) > 256:
            log.append({"rule": rule_name, "status": "skipped",
                        "reason": f"{target_desc} · tag '{k}' value exceeds 256 chars (Databricks limit)"})
            continue
        out.append((str(k), val))
    return out


def _eval_rule_condition(rule: dict, ctx: dict, parsed: dict) -> bool:
    rid = rule.get("id") or rule.get("name")
    ast_cond = parsed.get(rid)
    if ast_cond is None:
        ast_cond = cond.parse_condition(rule.get("condition") or "")
        parsed[rid] = ast_cond
    return cond.eval_condition(ast_cond, ctx, rule.get("condition") or "")


def column_tag_statements(rules: list[dict], artifact: str, base_ctx: dict,
                          cols_ctx_by_name: dict[str, dict], config: dict,
                          table_full: str, options: dict | None = None) -> tuple[list[str], list[dict]]:
    """Tags de gobierno por COLUMNA (spec §8.2): Databricks NO admite varias
    columnas en un ALTER — sale UNA sentencia por columna, pares ordenados.
    `cols_ctx_by_name` debe venir en orden de columna (dict ordenado).
    `table_full` ya viene calificado (`full_name`); la columna se emite con el
    mismo `ident()` (doc 71 H2)."""
    log: list[dict] = []
    stmts: list[str] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    ordered = run_order([r for r in rules
                         if r.get("kind") == "rule" and r.get("target") == "column"
                         and (r.get("action") or {}).get("tags")
                         and artifact in (r.get("appliesTo") or [])])
    for col_name, col_ctx in cols_ctx_by_name.items():
        ctx = {**base_ctx, "columna": col_ctx}
        pairs: list[tuple[str, str]] = []
        for r in ordered:
            try:
                if not _eval_rule_condition(r, ctx, parsed):
                    continue
            except cond.CondError as e:
                log.append({"rule": r.get("name"), "status": "skipped", "reason": str(e)})
                continue
            new = _expand_pairs((r.get("action") or {}).get("tags") or {}, ctx,
                                lookups, functions, r.get("name"), col_name, log)
            if new:
                log.append({"rule": r.get("name"), "column": col_name, "status": "applied"})
            pairs.extend(new)
        if not pairs:
            continue
        seen: dict[str, str] = {}
        for k, v in pairs:               # la primera regla (mayor prioridad) gana la key
            seen.setdefault(k, v)
        body = ", ".join(f"{_lit(k)} = {_lit(v)}" for k, v in sorted(seen.items()))
        stmts.append(f"ALTER TABLE {table_full} ALTER COLUMN {ident(col_name, options)} SET TAGS ({body});")
    return stmts, log


def table_tag_statements(rules: list[dict], artifact: str, base_ctx: dict,
                         config: dict, table_full: str) -> tuple[list[str], list[dict]]:
    """Tags a nivel de TABLA (spec §8.3): un solo ALTER multi-par, ordenado."""
    log: list[dict] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    pairs: list[tuple[str, str]] = []
    for r in run_order([r for r in rules
                        if r.get("kind") == "rule" and r.get("target") == "table"
                        and (r.get("action") or {}).get("tags")
                        and artifact in (r.get("appliesTo") or [])]):
        try:
            if not _eval_rule_condition(r, base_ctx, parsed):
                continue
        except cond.CondError as e:
            log.append({"rule": r.get("name"), "status": "skipped", "reason": str(e)})
            continue
        new = _expand_pairs((r.get("action") or {}).get("tags") or {}, base_ctx,
                            lookups, functions, r.get("name"), table_full, log)
        if new:
            log.append({"rule": r.get("name"), "status": "applied"})
        pairs.extend(new)
    if not pairs:
        return [], log
    seen: dict[str, str] = {}
    for k, v in pairs:
        seen.setdefault(k, v)
    if len(seen) > 50:
        log.append({"rule": None, "status": "skipped",
                    "reason": f"{table_full}: more than 50 tags — Databricks limit; emitting first 50"})
        seen = dict(sorted(seen.items())[:50])
    body = ", ".join(f"{_lit(k)} = {_lit(v)}" for k, v in sorted(seen.items()))
    return [f"ALTER TABLE {table_full} SET TAGS ({body});"], log


def _scan_end(sql: str, start: int, stop_char: str) -> int:
    """Índice del primer `stop_char` a partir de `start` FUERA de comillas
    simples (con '' escapado) y backticks; -1 si no hay."""
    i, n = start, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif ch in ("'", "`"):
            quote = ch
        elif ch == stop_char:
            return i
        i += 1
    return -1


def _tblproperties_block(stmt: str) -> tuple[int, int] | None:
    """(open_paren_idx, close_paren_idx) del bloque `TBLPROPERTIES (…)` del
    statement, o None si no tiene."""
    m = re.search(r"\bTBLPROPERTIES\s*\(", stmt, re.IGNORECASE)
    if not m:
        return None
    open_idx = m.end() - 1
    # cierre: primer ')' fuera de comillas después del '(' (los valores no
    # anidan paréntesis; si lo hicieran, el fallback sqlglot toma el relevo).
    close_idx = _scan_end(stmt, open_idx + 1, ")")
    return (open_idx, close_idx) if close_idx > open_idx else None


def existing_tblproperty_keys(sql: str) -> set[str]:
    """Keys ya presentes en el bloque TBLPROPERTIES del primer statement."""
    end = _scan_end(sql, 0, ";")
    stmt = sql if end < 0 else sql[:end + 1]
    blk = _tblproperties_block(stmt)
    if not blk:
        return set()
    inner = stmt[blk[0] + 1:blk[1]]
    return {m.group(1).replace("''", "'") for m in re.finditer(r"'((?:[^']|'')*)'\s*=", inner)}


def inject_tblproperties_text(sql: str, pairs: list[tuple[str, str]]) -> str | None:
    """Doc 71 H1 — inyección TEXTUAL de pares en el `CREATE` base (primer
    statement): si ya hay bloque `TBLPROPERTIES (…)` los agrega dentro; si no,
    añade el bloque justo antes del `;`. Todo lo demás queda byte-idéntico (el
    formato del generador del front se conserva). None si el texto no tiene la
    forma esperada (sin `;` de cierre) → el llamador cae al fallback sqlglot."""
    if not pairs:
        return sql
    end = _scan_end(sql, 0, ";")
    if end < 0:
        return None
    stmt, rest = sql[:end + 1], sql[end + 1:]
    items = [f"{_lit(k)} = {_lit(v)}" for k, v in pairs]
    blk = _tblproperties_block(stmt)
    if blk:
        open_idx, close_idx = blk
        inner = stmt[open_idx + 1:close_idx]
        if "\n" in inner:
            new_inner = inner.rstrip() + ",\n  " + ",\n  ".join(items) + "\n"
        else:
            new_inner = inner.rstrip() + ", " + ", ".join(items)
        return stmt[:open_idx + 1] + new_inner + stmt[close_idx:] + rest
    body = stmt[:-1].rstrip()
    return f"{body}\nTBLPROPERTIES (\n  " + ",\n  ".join(items) + "\n);" + rest


def apply_tblproperties(sql: str, rules: list[dict], artifact: str, base_ctx: dict,
                        config: dict) -> tuple[str, list[dict]]:
    """TBLPROPERTIES desde UDP (spec §8.4): inserta los pares DENTRO del
    `CREATE TABLE` base. Una key ya presente en el base NO se pisa (lo del
    modeler manda) — se reporta. Sin pares → SQL intacto.

    Doc 71 H1: la inyección es TEXTUAL (`inject_tblproperties_text`) — el CREATE
    conserva el formato del generador del front y solo gana el bloque; sqlglot
    queda como fallback si el texto no tiene la forma esperada."""
    log: list[dict] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    pairs: list[tuple[str, str]] = []
    for r in run_order([r for r in rules
                        if r.get("kind") == "rule" and r.get("target") == "table"
                        and (r.get("action") or {}).get("tblproperties")
                        and artifact in (r.get("appliesTo") or [])]):
        try:
            if not _eval_rule_condition(r, base_ctx, parsed):
                continue
        except cond.CondError as e:
            log.append({"rule": r.get("name"), "status": "skipped", "reason": str(e)})
            continue
        new = _expand_pairs((r.get("action") or {}).get("tblproperties") or {}, base_ctx,
                            lookups, functions, r.get("name"), "tblproperties", log)
        if new:
            log.append({"rule": r.get("name"), "status": "applied"})
        pairs.extend(new)
    if not pairs:
        return sql, log
    seen_txt: dict[str, str] = {}
    for k, v in pairs:
        seen_txt.setdefault(k, v)
    existing_txt = existing_tblproperty_keys(sql)
    kept: list[tuple[str, str]] = []
    for k, v in sorted(seen_txt.items()):
        if k in existing_txt:
            log.append({"rule": None, "status": "skipped",
                        "reason": f"TBLPROPERTIES key '{k}' already set on the base CREATE — kept as-is"})
        else:
            kept.append((k, v))
    if not kept:
        return sql, log
    injected = inject_tblproperties_text(sql, kept)
    if injected is not None:
        return injected, log
    # Fallback: el base de una tabla puede traer VARIOS statements (CREATE +
    # ALTER … FK) o una forma no reconocida: se parsea la lista, se decora el
    # CREATE y se re-emite todo con sqlglot.
    try:
        stmts = [s for s in sqlglot.parse(sql, read="databricks") if s is not None]
    except Exception:
        return sql, log + [{"rule": None, "status": "skipped",
                            "reason": "base CREATE didn't parse — TBLPROPERTIES not injected"}]
    ast = next((s for s in stmts if isinstance(s, exp.Create)), None)
    if ast is None:
        return sql, log
    props = ast.args.get("properties")
    if props is None:
        props = exp.Properties(expressions=[])
        ast.set("properties", props)
    existing = {p.this.name for p in props.expressions
                if isinstance(p, exp.Property) and isinstance(p.this, exp.Literal)}
    seen: dict[str, str] = {}
    for k, v in pairs:
        seen.setdefault(k, v)
    added = False
    for k, v in sorted(seen.items()):
        if k in existing:
            continue                      # ya reportado arriba (existing_txt)
        props.append("expressions", exp.Property(this=exp.Literal.string(k),
                                                 value=exp.Literal.string(v)))
        added = True
    if not added:
        return sql, log
    out = ";\n".join(s.sql(dialect="databricks", pretty=True, normalize_functions="lower")
                     for s in stmts)
    if sql.rstrip().endswith(";"):
        out += ";"
    return out, log


def decorate_view_sql(sql: str, artifact: str, rules: list[dict], config: dict,
                      base_ctx: dict, cols_ctx_by_name: dict[str, dict]) -> tuple[str, list[dict]]:
    """Decora el SELECT del `CREATE VIEW` (o SELECT suelto) con las reglas de
    columna. Sin matches → SQL de entrada INTACTO. Con matches → re-emisión
    determinista sqlglot (pretty, databricks)."""
    run, skipped = runnable(rules)
    try:
        ast = sqlglot.parse_one(sql, read="databricks")
    except Exception:
        return sql, [{"rule": None, "status": "skipped",
                      "reason": "base SQL didn't parse — left untouched"}]
    select = _find_select(ast)
    if select is None:
        return sql, skipped
    log = decorate_select(select, artifact, run, config, base_ctx, cols_ctx_by_name)
    if not any(e["status"] == "applied" for e in log):
        return sql, skipped + log
    # normalize_functions="lower": sqlglot emite las funciones CONOCIDAS con su
    # nombre canónico (SHA2) salvo que se normalice — el spec §8 y los
    # modeladores escriben sha2/trim/concat en minúscula, y un casing fijo es
    # requisito de la idempotencia byte-identical (§7.6).
    out = ast.sql(dialect="databricks", pretty=True, normalize_functions="lower")
    if sql.rstrip().endswith(";"):
        out += ";"
    return out, skipped + log
