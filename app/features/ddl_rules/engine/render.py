"""Motor de aplicación de reglas de COLUMNA sobre un SELECT (doc 30 F3) +
tags, TBLPROPERTIES y sentencias libres por objeto. PURO.

- Orden SIEMPRE determinista: (priority DESC, name ASC) — nunca el orden del
  archivo (spec §7.1).
- Encadenamiento: varias reglas sobre la misma columna se APILAN — `{col}` de
  la regla N es el AST de salida de la N-1 (spec §7.2). Nada de strings.
- Reglas deshabilitadas o no-válidas se SALTAN sin abortar y se reportan
  (spec §7.5).
- Si ninguna regla toca la vista, el SQL de entrada vuelve INTACTO
  (byte-identical); si alguna aplica, el statement se re-emite con el formato
  determinista de sqlglot (pretty, dialecto databricks) — idempotente (§7.6).

Doc 76 (macro BCP): `exclude` quita una proyección; los tags salen también
sobre VISTAS (`ALTER VIEW`); los tags de tabla van UNA sentencia por regla;
`statements` (before/after) emite sentencias libres con placeholders; el
contexto `artefacto.*` identifica el objeto que se emite.
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


def cased(s: str, options: dict | None = None) -> str:
    """Identificador con el casing del export (`identifierCase`) SIN backticks
    — para las proyecciones de las vistas generadas (doc 76 D8)."""
    return _case(str(s), (options or {}).get("identifierCase") or "as-is")


def ident(s: str, options: dict | None = None) -> str:
    """Identificador backtickeado con el casing de las opciones del export
    (`identifierCase`: as-is | lower | upper)."""
    return f"`{cased(s, options)}`"


def full_name(schema: str | None, name: str, options: dict | None = None) -> str:
    """``schema``.``name`` con backticks (o solo el nombre) y el casing del export."""
    return f"{ident(schema, options)}.{ident(name, options)}" if schema else ident(name, options)


def location_folder(name: str, options: dict | None = None) -> str:
    """Carpeta del `LOCATION` (doc 76 D9): el nombre del objeto con el casing de
    carpeta del export (`locationFolderCase`, default MAYÚSCULA — convención
    ADLS de la macro). Es un literal de path: no lleva backticks."""
    return _case(str(name), (options or {}).get("locationFolderCase") or "upper")


def artifact_ctx(schema: str | None, name: str, kind: str, options: dict | None = None) -> dict:
    """Contexto `artefacto.*` (doc 76 D6): el objeto que se está emitiendo —
    `ref` calificado con backticks + casing (para `-- DROP TABLE IF EXISTS
    {artefacto.ref};`), `esquema`/`nombre` crudos, `tipo` table|view."""
    return {"esquema": schema, "nombre": name, "tipo": kind,
            "ref": full_name(schema, name, options)}


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


def _ci_index(cols_ctx_by_name: dict[str, dict]) -> dict[str, dict]:
    """Índice case-insensitive nombre → contexto (doc 76 D8: las proyecciones
    llegan con el casing del export; el modelo guarda el físico crudo)."""
    out: dict[str, dict] = {}
    for name, ctx in cols_ctx_by_name.items():
        out.setdefault(str(name).lower(), ctx)
    return out


def decorate_select(select: exp.Select, artifact: str, rules: list[dict],
                    config: dict, base_ctx: dict,
                    cols_ctx_by_name: dict[str, dict]) -> list[dict]:
    """Aplica las reglas de columna (ya filtradas por runnable) a las
    proyecciones del SELECT, IN PLACE: `expression` (encadenada) y `exclude`
    (quita la proyección — doc 76 D7). Devuelve el log de aplicaciones:
    [{rule, column, status: applied|skipped, reason?}]."""
    log: list[dict] = []
    ordered = run_order(column_rules_for(rules, artifact))
    if not ordered:
        return log
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, exp.Expression] = {}
    by_ci = _ci_index(cols_ctx_by_name)
    new_exprs: list[exp.Expression] = []
    changed = False

    for proj in list(select.expressions):
        src_name, out_name, src_expr = _projection_parts(proj)
        match_name = src_name or out_name
        col_ctx = by_ci.get(str(match_name).lower()) if match_name is not None else None
        if col_ctx is None:
            new_exprs.append(proj)
            continue
        ctx = {**base_ctx, "columna": col_ctx}
        acc = src_expr
        alias_tpl: str | None = None
        touched = False
        excluded = False
        for r in ordered:
            rid = r.get("id") or r.get("name")
            try:
                ast_cond = parsed.get(rid)
                if ast_cond is None:
                    ast_cond = cond.parse_condition(r.get("condition") or "")
                    parsed[rid] = ast_cond
                if not cond.eval_condition(ast_cond, ctx, r.get("condition") or ""):
                    continue
                action = r.get("action") or {}
                if action.get("exclude") is True:
                    excluded = True
                    log.append({"rule": r.get("name"), "column": match_name, "status": "applied",
                                "reason": "excluded"})
                    break
                tpl = (action.get("expression") or "").strip()
                if not tpl:
                    continue
                acc = build_expression(tpl, ctx, acc, lookups, functions)
                alias_tpl = (action.get("alias") or "").strip() or alias_tpl
                touched = True
                log.append({"rule": r.get("name"), "column": match_name, "status": "applied"})
            except (RenderError, cond.CondError) as e:
                log.append({"rule": r.get("name"), "column": match_name,
                            "status": "skipped", "reason": str(e)})
        if excluded:
            changed = True
            continue
        if not touched:
            new_exprs.append(proj)
            continue
        # El alias por default es el nombre de SALIDA de la proyección (el alias
        # que puso el modelador, ya con el casing del export). La plantilla
        # `{columna.nombre}` es la IDENTIDAD (expande al físico crudo): no pisa
        # ni el alias ni el casing; solo un rename real (p.ej.
        # `{columna.nombre}_hash`) cambia el alias.
        alias = out_name or match_name
        if alias_tpl:
            expanded = expand_template(alias_tpl, ctx, lookups, functions)
            if expanded and expanded.lower() not in {str(alias).lower(), str(match_name).lower()}:
                alias = expanded
        new_exprs.append(exp.alias_(acc, alias, quoted=False))
        changed = True
    if changed:
        select.set("expressions", new_exprs)
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


def _alter_kw(object_kind: str | None) -> str:
    """`ALTER VIEW` para artefactos vista, `ALTER TABLE` para el resto (doc 76 D5)."""
    return "VIEW" if object_kind == "view" else "TABLE"


def column_tag_statements(rules: list[dict], artifact: str, base_ctx: dict,
                          cols_ctx_by_name: dict[str, dict], config: dict,
                          table_full: str, options: dict | None = None,
                          object_kind: str = "table") -> tuple[list[str], list[dict]]:
    """Tags de gobierno por COLUMNA (spec §8.2): Databricks NO admite varias
    columnas en un ALTER — sale UNA sentencia por columna, pares ordenados.
    `cols_ctx_by_name` debe venir en orden de columna (dict ordenado) y, en
    vistas, contener SOLO las columnas proyectadas con su nombre de salida.
    `table_full` ya viene calificado (`full_name`); la columna se emite con el
    mismo `ident()` (doc 71 H2). `object_kind='view'` ⇒ `ALTER VIEW`."""
    log: list[dict] = []
    stmts: list[str] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    kw = _alter_kw(object_kind)
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
        stmts.append(f"ALTER {kw} {table_full} ALTER COLUMN {ident(col_name, options)} SET TAGS ({body});")
    return stmts, log


def table_tag_statements(rules: list[dict], artifact: str, base_ctx: dict,
                         config: dict, table_full: str,
                         object_kind: str = "table") -> tuple[list[str], list[dict]]:
    """Tags a nivel de OBJETO (spec §8.3 · doc 76 D4): UNA sentencia por REGLA
    que aplique (orden de prioridad), pares ordenados dentro de cada una; una
    key ya emitida por una regla de mayor prioridad no se repite. Límite
    Databricks: 50 tags por objeto. `object_kind='view'` ⇒ `ALTER VIEW`."""
    log: list[dict] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    kw = _alter_kw(object_kind)
    stmts: list[str] = []
    seen: dict[str, str] = {}
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
        new = [(k, v) for k, v in _expand_pairs((r.get("action") or {}).get("tags") or {}, base_ctx,
                                                lookups, functions, r.get("name"), table_full, log)
               if k not in seen]
        if not new:
            continue
        room = 50 - len(seen)
        if len(new) > room:
            log.append({"rule": r.get("name"), "status": "skipped",
                        "reason": f"{table_full}: more than 50 tags — Databricks limit; "
                                  f"emitting the first {max(room, 0)} of this rule"})
            new = new[:max(room, 0)]
            if not new:
                continue
        for k, v in new:
            seen[k] = v
        log.append({"rule": r.get("name"), "status": "applied"})
        body = ", ".join(f"{_lit(k)} = {_lit(v)}" for k, v in sorted(new))
        stmts.append(f"ALTER {kw} {table_full} SET TAGS ({body});")
    return stmts, log


def statement_snippets(rules: list[dict], artifact: str, ctx: dict, config: dict,
                       position: str) -> tuple[list[str], list[dict]]:
    """Sentencias LIBRES de las reglas de tabla con `action.statements`
    (doc 76 D6): plantillas con placeholders (`{artefacto.ref}`, `{tabla.*}`,
    `{udp:…}`, `{lookup:…}`) en `before` (se anteponen al CREATE del artefacto)
    o `after` (salen detrás de los tags). Una por línea de la lista; un lookup
    a null omite la sentencia. Orden de prioridad."""
    log: list[dict] = []
    out: list[str] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    for r in run_order([r for r in rules
                        if r.get("kind") == "rule" and r.get("target") == "table"
                        and isinstance((r.get("action") or {}).get("statements"), dict)
                        and artifact in (r.get("appliesTo") or [])]):
        try:
            if not _eval_rule_condition(r, ctx, parsed):
                continue
        except cond.CondError as e:
            log.append({"rule": r.get("name"), "status": "skipped", "reason": str(e)})
            continue
        emitted = 0
        for tpl in ((r.get("action") or {}).get("statements") or {}).get(position) or []:
            try:
                txt = expand_template(str(tpl), ctx, lookups, functions)
            except RenderError as e:
                log.append({"rule": r.get("name"), "status": "skipped",
                            "reason": f"statements.{position}: {e}"})
                continue
            if txt is None or not txt.strip():
                continue
            out.append(txt.strip())
            emitted += 1
        if emitted:
            log.append({"rule": r.get("name"), "status": "applied", "artifact": artifact,
                        "object": f"statements.{position}"})
    return out, log


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


def _parse_view(sql: str) -> tuple[exp.Expression | None, exp.Select | None]:
    try:
        ast = sqlglot.parse_one(sql, read="databricks")
    except Exception:
        return None, None
    return ast, _find_select(ast)


def view_projections(sql: str) -> list[tuple[str | None, str | None]]:
    """[(nombre_fuente, nombre_salida)] de las proyecciones del SELECT de un
    `CREATE VIEW` (o SELECT suelto). `*`/expresiones sin columna → (None, alias
    o None). Vacío si el SQL no parsea. Puro."""
    _, select = _parse_view(sql)
    if select is None:
        return []
    return [(src, out) for src, out, _ in (_projection_parts(p) for p in select.expressions)]


def projected_cols_ctx(sql: str, cols_ctx_by_name: dict[str, dict]) -> dict[str, dict]:
    """Contexto `columna.*` de las columnas que una VISTA realmente proyecta
    (doc 76 D5): {nombre_de_salida: contexto de la columna fuente con
    `nombre` = nombre de salida}. Matching case-insensitive por la fuente (o
    por el alias cuando la proyección es una expresión decorada). Las
    proyecciones que no vienen de una columna conocida quedan fuera."""
    by_ci = _ci_index(cols_ctx_by_name)
    _, select = _parse_view(sql)
    out: dict[str, dict] = {}
    for proj in (select.expressions if select is not None else []):
        src, out_name, inner = _projection_parts(proj)
        if not out_name:
            continue
        ctx = by_ci.get(str(src).lower()) if src else None
        if ctx is None:
            # Proyección DECORADA (decrypt(col, …) AS alias): la fuente es la
            # primera columna conocida dentro de la expresión.
            for node in inner.walk():
                if isinstance(node, exp.Column) and str(node.name).lower() in by_ci:
                    ctx = by_ci[str(node.name).lower()]
                    break
        if ctx is None:
            ctx = by_ci.get(str(out_name).lower())
        if ctx is None:
            continue
        out[out_name] = {**ctx, "nombre": out_name}
    return out


def view_target(sql: str) -> str | None:
    """Nombre calificado EXACTO (con sus backticks) del objeto de un
    `CREATE [OR REPLACE] VIEW` — para que los `ALTER VIEW` referencien la
    vista tal cual la creó el front (esquema por default incluido)."""
    ast, _ = _parse_view(sql)
    if not isinstance(ast, exp.Create):
        return None
    target = ast.this
    if isinstance(target, exp.Schema):
        target = target.this
    return target.sql(dialect="databricks") if target is not None else None


def decorate_view_sql(sql: str, artifact: str, rules: list[dict], config: dict,
                      base_ctx: dict, cols_ctx_by_name: dict[str, dict]) -> tuple[str, list[dict]]:
    """Decora el SELECT del `CREATE VIEW` (o SELECT suelto) con las reglas de
    columna. Sin matches → SQL de entrada INTACTO. Con matches → re-emisión
    determinista sqlglot (pretty, databricks). Si TODAS las proyecciones fueron
    excluidas devuelve "" (la vista no se emite) con el motivo en el log."""
    run, skipped = runnable(rules)
    ast, select = _parse_view(sql)
    if ast is None:
        return sql, [{"rule": None, "status": "skipped",
                      "reason": "base SQL didn't parse — left untouched"}]
    if select is None:
        return sql, skipped
    log = decorate_select(select, artifact, run, config, base_ctx, cols_ctx_by_name)
    if not any(e["status"] == "applied" for e in log):
        return sql, skipped + log
    if not select.expressions:
        return "", skipped + log + [{"rule": None, "status": "skipped", "artifact": artifact,
                                     "reason": "every column was excluded — view not emitted"}]
    # normalize_functions="lower": sqlglot emite las funciones CONOCIDAS con su
    # nombre canónico (SHA2) salvo que se normalice — el spec §8 y los
    # modeladores escriben sha2/trim/concat en minúscula, y un casing fijo es
    # requisito de la idempotencia byte-identical (§7.6).
    out = ast.sql(dialect="databricks", pretty=True, normalize_functions="lower")
    if sql.rstrip().endswith(";"):
        out += ";"
    return out, skipped + log
