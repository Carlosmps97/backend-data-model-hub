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

Doc 90: `types` (regla de columna) renombra el tipo BASE de las columnas al
emitir un CREATE TABLE — el CREATE físico del front se reescribe por TOKEN
(sqlglot) y las tablas generadas tipo por tipo; las vistas no declaran tipos.
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from app.core.datatypes import CATALOG

from . import conditions as cond
from .expressions import RenderError, build_expression, expand_template


# ── Identificadores (doc 71 H2): UN solo funnel para todo el archivo ────────
# El CREATE del front sale con backticks + `identifierCase`; los ALTER … SET
# TAGS y los artefactos generados usan el MISMO criterio (antes los tags iban
# crudos y el archivo mezclaba dos estilos).


def _case(s: str, mode: str) -> str:
    return s.lower() if mode == "lower" else s.upper() if mode == "upper" else s


def cased(s: str, options: dict | None = None) -> str:
    """Identificador con el casing del export (`identifierCase`) SIN comillas."""
    return _case(str(s), (options or {}).get("identifierCase") or "as-is")


# Doc 93 D2: sin backticks salvo que el nombre los NECESITE. ESPEJO EXACTO de
# `RESERVED_WORDS` del front (web-data-model-hub/src/features/ddl/generators.ts).
RESERVED_WORDS = frozenset("""
all alter and anti any as authorization between both by case cast check collate column
constraint create cross current_date current_time current_timestamp current_user delete
describe distinct drop else end escape except exists external false fetch filter for foreign
from full function global grant group having in inner insert intersect interval into is join
lateral leading left like local minus natural no not null of on only or order out outer
overlaps position primary range references revoke right rollback rollup row rows select semi
session_user set some start table tablesample then time to trailing true truncate union
unique unknown update user using values when where window with
""".split())

_SAFE_IDENT_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def needs_quote(name: str) -> bool:
    """True si el identificador no compila sin backticks en Databricks (o no lo
    leería el parser del motor): caracteres fuera de [A-Za-z0-9_], empieza con
    dígito, o es palabra reservada."""
    s = str(name)
    return not _SAFE_IDENT_RX.match(s) or s.lower() in RESERVED_WORDS


def _quote(s: str) -> str:
    return "`" + s.replace("`", "``") + "`"


def ident(s: str, options: dict | None = None) -> str:
    """Identificador con el casing del export y comillas según `quoteIdentifiers`
    (`when-needed` por default · `always` = estilo anterior al doc 93)."""
    c = cased(s, options)
    if (options or {}).get("quoteIdentifiers") == "always" or needs_quote(c):
        return _quote(c)
    return c


def proj_ident(s: str, options: dict | None = None) -> str:
    """Identificador de una PROYECCIÓN de vista (`col AS col`): casing del export
    y comillas SOLO si el nombre las necesita (doc 76 D8 · doc 93 D2)."""
    c = cased(s, options)
    return _quote(c) if needs_quote(c) else c


def full_name(schema: str | None, name: str, options: dict | None = None) -> str:
    """`schema.name` (o solo el nombre) con el casing y las comillas del export."""
    return f"{ident(schema, options)}.{ident(name, options)}" if schema else ident(name, options)


def type_text(t: str, options: dict | None = None) -> str:
    """Tipo de dato con el casing del export (`typeCase`: lower | upper; default
    lower, doc 93 D3). Los tipos complejos (`<…>`) salen tal cual."""
    s = str(t or "").strip() or "STRING"
    if "<" in s:
        return s
    return s.upper() if (options or {}).get("typeCase") == "upper" else s.lower()


def create_table_sql(options: dict | None = None) -> str:
    """Encabezado del CREATE TABLE (doc 93 D5): `or-replace` (default) →
    `CREATE OR REPLACE TABLE` (con LOCATION ya es externa; Databricks no admite
    `CREATE OR REPLACE EXTERNAL`); `if-not-exists` → la forma anterior."""
    o = options or {}
    if o.get("createTable") == "if-not-exists":
        return "CREATE EXTERNAL TABLE IF NOT EXISTS" if o.get("external") else "CREATE TABLE IF NOT EXISTS"
    return "CREATE OR REPLACE TABLE"


def location_folder(name: str, options: dict | None = None) -> str:
    """Carpeta del `LOCATION` (doc 76 D9): el nombre del objeto con el casing de
    carpeta del export (`locationFolderCase`, default MAYÚSCULA). Literal de path."""
    return _case(str(name), (options or {}).get("locationFolderCase") or "upper")


def artifact_ctx(schema: str | None, name: str, kind: str, options: dict | None = None) -> dict:
    """Contexto `artefacto.*` (doc 76 D6): `ref` calificado con el casing y las
    comillas del export (para `-- DROP TABLE IF EXISTS {artefacto.ref};`),
    `esquema`/`nombre` crudos, `tipo` table|view."""
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
        # Doc 93 D2: el alias se cita solo si el nombre lo necesita.
        new_exprs.append(exp.alias_(acc, alias, quoted=needs_quote(str(alias))))
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


def _alter_kw(object_kind: str | None, options: dict | None = None) -> str:
    """Doc 93 D6: `ALTER TABLE` para todo objeto (como la macro BCP; la sintaxis
    documentada de ALTER VIEW no trae ALTER COLUMN … SET TAGS). `ALTER VIEW` solo
    para vistas cuando las Output settings dicen `viewTagsAs = 'view'`."""
    return "VIEW" if object_kind == "view" and (options or {}).get("viewTagsAs") == "view" else "TABLE"


def column_tag_statements(rules: list[dict], artifact: str, base_ctx: dict,
                          cols_ctx_by_name: dict[str, dict], config: dict,
                          table_full: str, options: dict | None = None,
                          object_kind: str = "table") -> tuple[list[str], list[dict]]:
    """Tags de gobierno por COLUMNA (spec §8.2): Databricks NO admite varias
    columnas en un ALTER — sale UNA sentencia por columna, pares ordenados.
    `cols_ctx_by_name` debe venir en orden de columna (dict ordenado) y, en
    vistas, contener SOLO las columnas proyectadas con su nombre de salida.
    `table_full` ya viene calificado (`full_name`); la columna se emite con el
    mismo `ident()` (doc 71 H2). `object_kind='view'` + `viewTagsAs='view'` ⇒
    `ALTER VIEW`; si no, `ALTER TABLE` (doc 93 D6)."""
    log: list[dict] = []
    stmts: list[str] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    kw = _alter_kw(object_kind, options)
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
                         object_kind: str = "table",
                         options: dict | None = None) -> tuple[list[str], list[dict]]:
    """Tags a nivel de OBJETO (spec §8.3 · doc 76 D4): UNA sentencia por REGLA
    que aplique (orden de prioridad), pares ordenados dentro de cada una; una
    key ya emitida por una regla de mayor prioridad no se repite. Límite
    Databricks: 50 tags por objeto. `object_kind='view'` + `viewTagsAs='view'`
    ⇒ `ALTER VIEW`; si no, `ALTER TABLE` (doc 93 D6)."""
    log: list[dict] = []
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    parsed: dict[str, object] = {}
    kw = _alter_kw(object_kind, options)
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


def readable_select(sql: str) -> bool:
    """True si el SQL parsea y tiene un SELECT del que se pueden leer TODAS las
    proyecciones (doc 93 D8: sin eso, las columnas de la vista se desconocen).
    Un `*` o `t.*` no dice qué columnas expone la vista: también cuenta como
    ilegible, así las reglas con ANY_COLUMN se saltan en vez de inventar un
    `isDAC` (final review #2)."""
    select = _parse_view(sql)[1]
    return select is not None and not any(p.is_star for p in select.expressions)


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


# ── Doc 90 · Data types: `action.types = {FROM: TO}` (regla de COLUMNA) ────
# Renombra el tipo BASE de las columnas que matchean al emitir un CREATE TABLE
# (CHAR → VARCHAR). El CREATE físico llega como TEXTO del front y se reescribe
# por TOKEN (tokenizer de sqlglot, dialecto databricks): los literales
# (COMMENT 'CHAR(3)'), los identificadores (`char`) y los comentarios no son
# tokens de tipo, así que quedan intactos y el resto del texto sale
# byte-idéntico. Los argumentos `(n)`/`(p,s)` se conservan si el tipo destino
# los admite según `_RULE_TYPE_ARGS`; si no, se descartan (`CHAR(10)` →
# `STRING`). Las tablas generadas pasan por el mismo mapeador tipo por tipo.
# Las vistas no declaran tipos: no aplica.

# Tipos destino que conservan los argumentos. Copia CONGELADA de la gramática del
# catálogo antes del doc 106: el catálogo ahora es multimotor (TIMESTAMP(6),
# FLOAT(53), BINARY(16)… de Oracle / SQL Server), pero este motor es de Databricks
# y una regla `CHAR → TIMESTAMP` sigue descartando el largo.
_RULE_TYPE_ARGS: frozenset[str] = frozenset({
    "DECIMAL", "NUMERIC", "NUMBER", "VARCHAR", "CHAR", "NVARCHAR", "VARBINARY", "VARCHAR2",
})

# Doc 106: un tipo de varias palabras del catálogo (`TIMESTAMP WITH TIME ZONE`,
# `LONG RAW`) es UNA unidad — el tokenizer lo parte en palabras sueltas y una
# regla de `TIME` o `LONG` no debe tocar una de ellas. Los más largos primero.
_MULTIWORD_TYPES: tuple[tuple[str, ...], ...] = tuple(sorted(
    (tuple(t.split()) for t in CATALOG if " " in t), key=len, reverse=True))

_TYPE_WORD_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def column_type_rules_for(rules: list[dict], artifact: str) -> list[dict]:
    """Reglas de columna con `action.types` (mapa no vacío) que nombran el artefacto."""
    out: list[dict] = []
    for r in rules:
        types = (r.get("action") or {}).get("types")
        if (r.get("kind") == "rule" and r.get("target") == "column"
                and isinstance(types, dict) and types
                and artifact in (r.get("appliesTo") or [])):
            out.append(r)
    return out


def type_map_of(rule: dict) -> dict[str, str]:
    """{FROM en MAYÚSCULA: to tal cual lo escribió el autor}; pares vacíos fuera."""
    types = (rule.get("action") or {}).get("types") or {}
    return {str(k).strip().upper(): str(v).strip()
            for k, v in types.items() if str(k).strip() and str(v).strip()}


def _match_case(new: str, old: str) -> str:
    """El destino copia el estilo de mayúsculas del token original (el front
    emite `CHAR(10)`; un modelo puede guardar `char(8)`): el DDL sale homogéneo."""
    if old.isupper():
        return new.upper()
    if old.islower():
        return new.lower()
    return new


def _tokenize(text: str):
    try:
        return sqlglot.tokenize(text, read="databricks")
    except Exception:
        return None


def _retype_tokens(text: str, toks: list, i0: int, i1: int,
                   maps: list[tuple[str, dict[str, str]]]) -> list[dict]:
    """Reemplazos de tipo en toks[i0:i1]: [{start, end, new, old_disp, new_disp,
    rules}] (`end` exclusivo sobre `text`). Cada token-palabra que matchee un
    mapa se encadena por los mapas EN ORDEN (CHAR → VARCHAR → STRING); los
    argumentos `( … )` que le siguen se conservan o se descartan según el
    destino FINAL."""
    out: list[dict] = []
    maps = [(name, {" ".join(str(k).split()).upper(): v for k, v in m.items()}) for name, m in maps]   # case-insensitive
    close_of = _paren_pairs(toks, i0, i1)
    i = i0
    while i < i1:
        t = toks[i]
        if not _TYPE_WORD_RX.match(text[t.start:t.end + 1]):
            i += 1
            continue
        last = _type_unit_end(text, toks, i, i1)   # doc 106: varias palabras = una unidad
        span = text[t.start:toks[last].end + 1]
        cur, applied = " ".join(span.split()), []
        for name, m in maps:
            to = m.get(cur.upper())
            if to is not None:
                cur, applied = to, applied + [name]
        args_end = close_of.get(last + 1)          # índice del ')' que cierra los argumentos
        if not applied:
            # doc 106: los argumentos de un tipo no se recorren (`VARCHAR2(30 CHAR)`)
            i = (args_end if args_end is not None else last) + 1
            continue
        new_base = _match_case(cur, span)
        if args_end is None:
            out.append({"start": t.start, "end": toks[last].end + 1, "new": new_base,
                        "old_disp": span, "new_disp": new_base, "rules": applied})
            i = last + 1
            continue
        args = text[toks[last].end + 1:toks[args_end].end + 1]
        if cur.upper() in _RULE_TYPE_ARGS:         # el destino admite argumentos: verbatim
            out.append({"start": t.start, "end": toks[last].end + 1, "new": new_base,
                        "old_disp": span + args, "new_disp": new_base + args, "rules": applied})
        else:                                       # no los admite: `CHAR(10)` → `STRING`
            out.append({"start": t.start, "end": toks[args_end].end + 1, "new": new_base,
                        "old_disp": span + args, "new_disp": new_base, "rules": applied})
        i = args_end + 1
    return out


def _type_unit_end(text: str, toks: list, i: int, i1: int) -> int:
    """Índice del ÚLTIMO token del tipo que empieza en toks[i]: el de un tipo de
    varias palabras del catálogo si sus palabras siguen tal cual, si no `i`."""
    for words in _MULTIWORD_TYPES:
        j = i + len(words)
        if j <= i1 and all(text[toks[k].start:toks[k].end + 1].upper() == w
                           for k, w in zip(range(i, j), words)):
            return j - 1
    return i


def _paren_pairs(toks: list, i0: int, i1: int) -> dict[int, int]:
    """{índice de cada `(` de toks[i0:i1]: índice del `)` que lo cierra} — una sola
    pasada (doc 106 · revisión 2: buscar el cierre palabra por palabra era cuadrático
    con paréntesis sin cerrar). Un `(` sin cierre no figura."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for k in range(i0, i1):
        tt = toks[k].token_type
        if tt == TokenType.L_PAREN:
            stack.append(k)
        elif tt == TokenType.R_PAREN and stack:
            pairs[stack.pop()] = k
    return pairs


def _splice(text: str, repl: list[dict]) -> str:
    out = text
    for r in sorted(repl, key=lambda r: r["start"], reverse=True):
        out = out[:r["start"]] + r["new"] + out[r["end"]:]
    return out


def map_type_text(text: str, maps: list[tuple[str, dict[str, str]]]) -> tuple[str, list[str]]:
    """Un TIPO suelto (`CHAR(10)`, `STRUCT<a: CHAR(3)>`) por los mapas
    [(regla, {FROM: TO})] en orden. Devuelve (texto, reglas que aplicaron —
    sin repetir, en orden). Sin matches (o texto no tokenizable) → intacto."""
    if not text or not maps:
        return text, []
    toks = _tokenize(text)
    if toks is None:
        return text, []
    repl = _retype_tokens(text, toks, 0, len(toks), maps)
    if not repl:
        return text, []
    applied = list(dict.fromkeys(n for r in repl for n in r["rules"]))
    return _splice(text, repl), applied


# Doc 93 D13: sin comillas, una columna llamada `char`/`array` se tokeniza como
# PALABRA CLAVE; igual es la cabeza de su definición (salvo las de constraint).
_CONSTRAINT_HEADS = {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "KEY", "INDEX"}


def _column_regions(toks: list) -> list[tuple[str, int, int]]:
    """[(nombre, i0, i1)] de las definiciones de columna del PRIMER grupo
    `( … )` de los tokens (la lista de columnas del CREATE): toks[i0:i1] son
    los tokens DESPUÉS del nombre hasta la coma de nivel 1 (o el cierre). Las
    comas dentro de `< … >` (STRUCT/MAP) y de `( … )` anidados no cortan; una
    entrada que no empieza por un nombre (CONSTRAINT …, PRIMARY KEY …) no es
    columna."""
    depth, angle, started = 0, 0, False
    start = 0
    raw: list[tuple[int, int]] = []
    for i, t in enumerate(toks):
        tt = t.token_type
        if tt == TokenType.L_PAREN:
            depth += 1
            if depth == 1 and not started:
                started, start = True, i + 1
            continue
        if tt == TokenType.R_PAREN:
            depth -= 1
            if started and depth == 0:
                raw.append((start, i))
                break
            continue
        if not started or depth != 1:
            continue
        if tt == TokenType.LT:
            angle += 1
        elif tt == TokenType.GT:
            angle -= 1
        elif tt == TokenType.COMMA and angle <= 0:
            raw.append((start, i))
            start, angle = i + 1, 0
    out: list[tuple[str, int, int]] = []
    for a, b in raw:
        if b - a < 2:                    # nombre sin tipo (o entrada vacía)
            continue
        head = toks[a]
        if head.token_type in (TokenType.IDENTIFIER, TokenType.VAR) or (
                _TYPE_WORD_RX.match(head.text or "") and head.text.upper() not in _CONSTRAINT_HEADS):
            out.append((head.text, a + 1, b))
    return out


def _applicable_maps(ordered: list[dict], ctx: dict, parsed: dict,
                     log: list[dict]) -> list[tuple[str, dict[str, str]]]:
    """[(regla, mapa)] de las reglas cuya condición aplica a la columna en `ctx`."""
    out: list[tuple[str, dict[str, str]]] = []
    for r in ordered:
        try:
            if not _eval_rule_condition(r, ctx, parsed):
                continue
        except cond.CondError as e:
            log.append({"rule": r.get("name"), "status": "skipped", "reason": str(e)})
            continue
        m = type_map_of(r)
        if m:
            out.append((r.get("name"), m))
    return out


def apply_column_types_sql(sql: str, rules: list[dict], artifact: str, base_ctx: dict,
                           cols_ctx_by_name: dict[str, dict], config: dict) -> tuple[str, list[dict]]:
    """Doc 90 · reescribe los TIPOS de las columnas del CREATE base (texto del
    front) según las reglas de columna con `action.types` que nombren el
    artefacto: por columna (condición evaluada con su contexto) y por token
    (comentarios, literales e identificadores intactos). Sin matches → SQL
    byte-idéntico. `config` se acepta por simetría con las demás acciones."""
    del config
    ordered = run_order(column_type_rules_for(rules, artifact))
    if not ordered or not sql:
        return sql, []
    toks = _tokenize(sql)
    if toks is None:
        return sql, [{"rule": None, "status": "skipped", "artifact": artifact,
                      "reason": "base CREATE didn't tokenize — data types not mapped"}]
    log: list[dict] = []
    parsed: dict[str, object] = {}
    by_ci = _ci_index(cols_ctx_by_name)
    repl: list[dict] = []
    for name, i0, i1 in _column_regions(toks):
        col_ctx = by_ci.get(str(name).lower())
        if col_ctx is None:
            continue
        maps = _applicable_maps(ordered, {**base_ctx, "columna": col_ctx}, parsed, log)
        if not maps:
            continue
        for r in _retype_tokens(sql, toks, i0, i1, maps):
            repl.append(r)
            for rule_name in dict.fromkeys(r["rules"]):
                log.append({"rule": rule_name, "column": name, "status": "applied", "artifact": artifact,
                            "object": f"{r['old_disp']} → {r['new_disp']}"})
    if not repl:
        return sql, log
    return _splice(sql, repl), log


def apply_column_types(cols: list[dict], rules: list[dict], artifact: str, base_ctx: dict,
                       cols_ctx_by_name: dict[str, dict], config: dict) -> tuple[list[dict], list[dict]]:
    """Doc 90 · el mismo mapeo sobre las columnas de un artefacto TABLA
    generado ([{name, type, …}] → copia con `type` mapeado). `cols_ctx_by_name`
    = contexto de esas columnas (`artifact_cols_ctx`: el de la física con el
    tipo del artefacto). Sin matches → la MISMA lista."""
    del config
    ordered = run_order(column_type_rules_for(rules, artifact))
    if not ordered:
        return cols, []
    log: list[dict] = []
    parsed: dict[str, object] = {}
    by_ci = _ci_index(cols_ctx_by_name)
    out: list[dict] = []
    changed = False
    for c in cols:
        col_ctx = by_ci.get(str(c.get("name")).lower())
        maps = _applicable_maps(ordered, {**base_ctx, "columna": col_ctx}, parsed, log) if col_ctx else []
        new_type, applied = map_type_text(c.get("type") or "", maps) if maps else (c.get("type"), [])
        if not applied:
            out.append(c)
            continue
        for rule_name in applied:
            log.append({"rule": rule_name, "column": c.get("name"), "status": "applied", "artifact": artifact,
                        "object": f"{c.get('type')} → {new_type}"})
        out.append({**c, "type": new_type})
        changed = True
    return (out if changed else cols), log
