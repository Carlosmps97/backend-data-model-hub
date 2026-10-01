"""Validación de una regla (spec doc 30 §9): los 5 checks, con estos nombres
EXACTOS (van en la UI, pantalla 16c):

    Condition syntax · UDP exists in catalog · Value allowed for UDP ·
    Expression syntax (Databricks) · Placeholders resolved

PURO — recibe la regla + catálogo de UDP defs + config + artefactos conocidos.
Errores estilo compilador pero legibles por un no-programador: caret al token,
sin jerga de parser, sugerencia Levenshtein cuando hay una cercana.
"""
from __future__ import annotations

import re

import sqlglot

from . import conditions as cond
from app.core.facets import physical_udp_defs

from .conditions import BASE_TO_LEVEL, CondError

CHECK_NAMES = ("Condition syntax", "UDP exists in catalog", "Value allowed for UDP",
               "Expression syntax (Databricks)", "Placeholders resolved")

# Placeholders de resolución literal (spec §6.5). {udp:...}/{lookup:...}/
# {funcion(...)} se validan por separado.
_PLAIN_PLACEHOLDERS = {
    "col",
    "columna.nombre", "columna.tipo", "columna.comentario",
    "tabla.nombre", "tabla.esquema", "tabla.catalogo",
    # doc 76 D6: el objeto que se emite (ref = calificado con backticks + casing)
    "artefacto.ref", "artefacto.nombre", "artefacto.esquema", "artefacto.tipo",
}
_PH_RX = re.compile(r"\{([^{}]+)\}")
# Doc 90: un tipo BASE (CHAR, VARCHAR, STRING…) — sin argumentos ni `<…>`.
_TYPE_NAME_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _lev(a: str, b: str) -> int:
    """Levenshtein chico (nombres cortos; sin dependencia)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _closest(name: str, candidates: list[str]) -> str | None:
    """La candidata más cercana si está razonablemente cerca (≤2 ediciones o
    ~un tercio del largo)."""
    best, dist = None, 10**9
    for c in candidates:
        d = _lev(name, c)
        if d < dist:
            best, dist = c, d
    return best if best is not None and dist <= max(2, len(name) // 3) else None


def _caret_snippet(text: str, token: str) -> str:
    """La línea del token con un caret debajo (Error detail de 16c·2)."""
    idx = text.find(token)
    if idx < 0:
        return text
    line_start = text.rfind("\n", 0, idx) + 1
    line_end = text.find("\n", idx)
    line = text[line_start:len(text) if line_end < 0 else line_end]
    return f"{line}\n{' ' * (idx - line_start)}^"


def _defs_by_level(udp_defs: list[dict]) -> dict[str, dict[str, dict]]:
    """{level: {nombre: def}} SOLO con defs físicas (doc 69): una regla DDL
    enlaza siempre el UDP de la faceta física; el homónimo lógico se ignora."""
    out: dict[str, dict[str, dict]] = {"table": {}, "column": {}, "canvas": {}}
    for d in physical_udp_defs(udp_defs):
        out.setdefault(d.get("level") or "column", {})[d.get("name") or ""] = d
    return out


def recanonize(condition: str, udp_refs: list[dict], udp_defs: list[dict]) -> str:
    """Renombre de UDP (doc 30 A6): las reglas enlazan por id, así que si el
    texto guardado quedó con el nombre viejo se re-escribe al vigente. Sustituye
    SOLO nombres citados dentro de udp[...] que ya no existen y cuyo binding
    apunta a un def cuyo nombre actual no aparece en el texto. Puro/best-effort."""
    if not condition or not udp_refs:
        return condition
    by_id = {d.get("id"): d for d in udp_defs}
    known = {d.get("name") for d in udp_defs}
    # Nombres EXACTOS citados dentro de udp["…"] (no substring del texto).
    cited = [a or b for a, b in
             re.findall(r"""udp\[\s*(?:"([^"]+)"|'([^']+)')\s*\]""", condition)]
    stale_names = sorted({n for n in cited if n not in known})
    # Bindings cuyo nombre vigente NO está citado = el def fue renombrado.
    candidates = sorted({by_id[r["udpId"]]["name"] for r in udp_refs
                         if by_id.get(r.get("udpId"))
                         and by_id[r["udpId"]]["name"] not in cited})
    # Conservador: solo el caso real (UN rename a la vez); con ambigüedad no
    # se toca el texto (el check 2 lo reporta con sugerencia).
    if len(stale_names) == 1 and len(candidates) == 1:
        old, new = stale_names[0], candidates[0]
        return condition.replace(f'"{old}"', f'"{new}"').replace(f"'{old}'", f"'{new}'")
    return condition


def validate_rule(rule: dict, udp_defs: list[dict], config: dict,
                  artifacts: list[str], artifact_kinds: dict[str, str] | None = None) -> dict:
    """Los 5 checks + extras estructurales. Devuelve:
    {state: 'valid'|'invalid', checks: [{name, ok(bool|None), detail}],
     errors: [{check, message, token?, suggestion?, snippet?, line, col}],
     warnings: [str], udpRefs: [{udpId, level}], condition: texto canonizado}.
    `ok=None` = waiting (un check previo del que depende falló)."""
    kind = rule.get("kind") or "rule"
    target = rule.get("target") if kind == "rule" else "table"  # generators condicionan por tabla
    action = rule.get("action") or {}
    by_level = _defs_by_level(udp_defs)
    lookups = (config or {}).get("lookups") or {}
    functions = {f.get("name") for f in (config or {}).get("functions") or []}

    condition = recanonize(rule.get("condition") or "", rule.get("udpRefs") or [], udp_defs)
    checks: dict[str, dict] = {n: {"name": n, "ok": True, "detail": ""} for n in CHECK_NAMES}
    errors: list[dict] = []
    warnings: list[str] = []
    udp_refs: list[dict] = []

    def fail(check: str, message: str, *, token: str | None = None,
             suggestion: str | None = None, snippet_src: str | None = None,
             line: int = 1, col: int = 1) -> None:
        checks[check]["ok"] = False
        checks[check]["detail"] = (token or message)[:60]
        errors.append({
            "check": check, "message": message, "token": token, "suggestion": suggestion,
            "snippet": _caret_snippet(snippet_src, token) if snippet_src and token else None,
            "line": line, "col": col,
        })

    # ── 1 · Condition syntax ───────────────────────────────────────────────
    ast = None
    try:
        ast = cond.parse_condition(condition)
        cond.extract_refs(ast, condition)          # valida la forma de TODAS las referencias
        checks["Condition syntax"]["detail"] = "parsed" if condition.strip() else "empty = always applies"
        # columna.* solo existe en reglas de columna — y, doc 93 D8, dentro de
        # ANY_COLUMN(...) en cualquier regla o generador.
        if target != "column":
            for path in cond.refs_outside_any_column(ast, condition):
                if path[0] == "columna":
                    fail("Condition syntax",
                         f"'columna.*' is not available in a {kind if kind == 'generator' else 'table'} "
                         "rule — there is no column in context.",
                         token="columna", snippet_src=condition,
                         col=cond._col_of(condition, "columna"))
                    break
    except CondError as e:
        fail("Condition syntax", e.message, token=e.token, snippet_src=condition,
             line=e.line, col=e.col)
        ast = None

    # ── 2 · UDP exists in catalog ──────────────────────────────────────────
    if ast is None or checks["Condition syntax"]["ok"] is False:
        checks["UDP exists in catalog"]["ok"] = None
        checks["UDP exists in catalog"]["detail"] = "waiting"
        checks["Value allowed for UDP"]["ok"] = None
        checks["Value allowed for UDP"]["detail"] = "waiting"
    else:
        names = cond.udp_names_used(ast, condition)
        resolved: dict[tuple[str, str], dict] = {}
        for base, name in names:
            level = BASE_TO_LEVEL[base]
            d = by_level.get(level, {}).get(name)
            if d is None:
                sug = _closest(name, list(by_level.get(level, {}).keys()))
                msg = f"Unknown UDP '{name}'" + (f" at {level.capitalize()} level." if name in
                                                 {n for lv in by_level.values() for n in lv} else ".")
                if sug:
                    msg += f" Did you mean '{sug}'?"
                fail("UDP exists in catalog", msg, token=name, suggestion=sug,
                     snippet_src=condition, col=cond._col_of(condition, name))
            else:
                resolved[(base, name)] = d
                if {"udpId": d["id"], "level": level} not in udp_refs:
                    udp_refs.append({"udpId": d["id"], "level": level})
        if checks["UDP exists in catalog"]["ok"]:
            checks["UDP exists in catalog"]["detail"] = \
                ", ".join(n for _, n in names) if names else "no UDP referenced"

        # ── 3 · Value allowed for UDP ──────────────────────────────────────
        if checks["UDP exists in catalog"]["ok"] is False:
            checks["Value allowed for UDP"]["ok"] = None
            checks["Value allowed for UDP"]["detail"] = "waiting"
        else:
            checked: list[str] = []
            for base, name, _op, lits in cond.literal_comparisons(ast, condition):
                d = resolved.get((base, name))
                if not d or d.get("dataType") != "list":
                    continue
                allowed = d.get("allowedValues") or []
                for lit in lits:
                    if lit in allowed:
                        checked.append(f"'{lit}'")
                        continue
                    # La trampa del catálogo real (spec §3.3): el valor plano
                    # existe como PREFIJO de una familia (p.ej. 'DAC' vs
                    # 'DAC-DOCUMENTO') → enseña el LIKE anclado.
                    family = sorted(v for v in allowed if v.startswith(f"{lit}-"))
                    if family:
                        preview = ", ".join(family[:3]) + (", …" if len(family) > 3 else "")
                        fail("Value allowed for UDP",
                             f"'{lit}' is not an allowed value for this UDP at "
                             f"{BASE_TO_LEVEL[base].capitalize()} level. Values use a prefix "
                             f"family: {preview}. Did you mean:  LIKE '{lit}-%'  ?",
                             token=lit, suggestion=f"LIKE '{lit}-%'", snippet_src=condition,
                             col=cond._col_of(condition, lit))
                    else:
                        sug = _closest(lit, allowed)
                        fail("Value allowed for UDP",
                             f"'{lit}' is not an allowed value for UDP '{name}'."
                             + (f" Did you mean '{sug}'?" if sug else ""),
                             token=lit, suggestion=sug, snippet_src=condition,
                             col=cond._col_of(condition, lit))
            if checks["Value allowed for UDP"]["ok"]:
                checks["Value allowed for UDP"]["detail"] = ", ".join(checked) or "—"

    # Warning obligatorio (spec §9): '%DAC%' también matchea 'No DAC'.
    if re.search(r"LIKE\s+'%DAC%'", condition, re.IGNORECASE):
        warnings.append("'%DAC%' also matches 'No DAC', which means *not* critical. "
                        "Use LIKE 'DAC-%' to match only the critical family.")

    # ── 4 · Expression syntax (Databricks) ─────────────────────────────────
    expr = (action.get("expression") or "").strip()
    if expr:
        probe = _PH_RX.sub("__ph__", expr)
        try:
            sqlglot.parse_one(probe, read="databricks")
            fn = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr)
            checks["Expression syntax (Databricks)"]["detail"] = f"{fn.group(1)}()" if fn else "parsed"
        except Exception:
            fail("Expression syntax (Databricks)",
                 "The expression is not valid Databricks SQL.", token=expr[:40])
    else:
        checks["Expression syntax (Databricks)"]["detail"] = "—"

    # Límites de tags de Databricks (spec §8.2): máx. 50 por objeto, 256 chars
    # por clave (los VALORES con placeholders se validan al renderizar).
    tags = action.get("tags") or {}
    if len(tags) > 50:
        fail("Expression syntax (Databricks)",
             f"Databricks allows at most 50 tags per object — this rule sets {len(tags)}.")
    for k in list(tags) + list((action.get("tblproperties") or {})):
        if len(str(k)) > 256:
            fail("Expression syntax (Databricks)",
                 f"Tag/property key '{str(k)[:30]}…' exceeds 256 characters.")

    # Errores estructurales de las acciones → check "Action" (como "Artifacts").
    def _bad(message: str, token: str | None = None) -> None:
        errors.append({"check": "Action", "message": message, "token": token,
                       "suggestion": None, "snippet": None, "line": 1, "col": 1})

    # Doc 73/76/93 · Column layout: acción de TABLA sobre la física, sin
    # condición y sin mezclarse con otras acciones (es un estándar del ruleset,
    # no una regla por UDP). `partitionUdp` (alias legado `partitionOrderUdp`)
    # enlaza un UDP de COLUMNA (faceta física) cuyos valores PART_nn DEFINEN las
    # particiones — qué columnas y en qué orden (doc 93 D10).
    layout = action.get("layout")
    if layout is not None:
        allowed = {"partitionColumns": ("keep", "last", "partitioned-by")}   # doc 107
        if not isinstance(layout, dict) or not layout:
            _bad("Column layout needs at least one setting (partitionColumns: last).")
        else:
            for k, val in layout.items():
                if k in ("partitionUdp", "partitionOrderUdp"):
                    name = str(val or "").strip()
                    if not name:
                        _bad("Column layout 'partitionUdp' needs the name of a column UDP (e.g. Particion).")
                        continue
                    d = by_level.get("column", {}).get(name)
                    if d is None:
                        sug = _closest(name, list(by_level.get("column", {}).keys()))
                        fail("UDP exists in catalog",
                             f"Unknown column UDP '{name}' in column layout (partitionUdp)."
                             + (f" Did you mean '{sug}'?" if sug else ""),
                             token=name, suggestion=sug)
                    elif {"udpId": d["id"], "level": "column"} not in udp_refs:
                        udp_refs.append({"udpId": d["id"], "level": "column"})
                elif k not in allowed:
                    _bad(f"Unknown column layout setting '{k}'.", token=str(k))
                elif val not in allowed[k]:
                    _bad(f"Column layout '{k}' must be one of: {', '.join(allowed[k])}.", token=str(val))
        if kind != "rule" or target != "table":
            _bad("Column layout is a table rule (target: table).")
        if (rule.get("condition") or "").strip():
            _bad("Column layout rules apply to every physical table of the export — leave the condition empty.")
        if any(action.get(k) for k in ("expression", "exclude", "tags", "tblproperties", "statements", "emit")):
            _bad("Column layout can't be combined with other actions in the same rule.")
        for art in rule.get("appliesTo") or []:
            if art != "ddl.tabla_fisica":
                warnings.append(f"Column layout only applies to the physical table — '{art}' is ignored.")

    # Doc 76 D7 · Exclude column: regla de COLUMNA que quita la proyección del
    # SELECT de un artefacto vista; no se combina con una expresión.
    exclude = action.get("exclude")
    if exclude is not None:
        if exclude is not True:
            _bad("Exclude column must be `true` (or leave it out).", token=str(exclude))
        if kind != "rule" or target != "column":
            _bad("Exclude column is a column rule (target: column).")
        if (action.get("expression") or "").strip():
            _bad("Exclude column can't be combined with an expression in the same rule.")

    # Doc 76 D6 · Statements: regla de TABLA con sentencias libres `before`
    # (antes del CREATE del artefacto) y/o `after` (detrás de los tags).
    statements = action.get("statements")
    if statements is not None:
        if not isinstance(statements, dict):
            _bad("Statements must be an object with `before` and/or `after` lists.")
        else:
            for k, val in statements.items():
                if k not in ("before", "after"):
                    _bad(f"Unknown statements position '{k}' — use before or after.", token=str(k))
                elif not isinstance(val, list) or any(not isinstance(s, str) for s in val):
                    _bad(f"statements.{k} must be a list of SQL text lines.", token=str(k))
            if not any(isinstance(statements.get(k), list) and any(str(s).strip() for s in statements[k])
                       for k in ("before", "after")):
                _bad("Statements need at least one non-empty entry under before or after.")
        if kind != "rule" or target != "table":
            _bad("Statements is a table rule (target: table).")

    # Doc 90 · Data types: regla de COLUMNA que renombra el tipo BASE de las
    # columnas que matchean (CHAR → VARCHAR) en los artefactos TABLA; cada
    # columna conserva su longitud/precisión si el destino la admite.
    types = action.get("types")
    if types is not None:
        if not isinstance(types, dict) or not types:
            _bad("Data types needs at least one mapping (from type → to type), e.g. CHAR → VARCHAR.")
        else:
            for k, val in types.items():
                for txt in (k, val):
                    if not _TYPE_NAME_RX.match(str(txt or "").strip()):
                        _bad(f"Data types: '{txt}' isn't a base type name — write the type without "
                             "arguments (CHAR, not CHAR(10)); each column keeps its own length/precision.",
                             token=str(txt))
        if kind != "rule" or target != "column":
            _bad("Data types is a column rule (target: column).")
        if (action.get("expression") or "").strip() or action.get("exclude") is True:
            _bad("Data types can't be combined with an expression or exclude in the same rule.")

    # Doc 76 · keep_partition_type del generador: booleano.
    kpt = ((action.get("emit") or {}).get("columns") or {}).get("keep_partition_type") if kind == "generator" else None
    if kpt is not None and not isinstance(kpt, bool):
        _bad("emit.columns.keep_partition_type must be true or false.", token=str(kpt))

    # ── 5 · Placeholders resolved ──────────────────────────────────────────
    ph_seen: list[str] = []
    target_level = BASE_TO_LEVEL.get({"column": "columna", "table": "tabla"}.get(target or "", ""), None)

    def _walk_strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from _walk_strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from _walk_strings(v)

    for s in _walk_strings(action):
        for ph in _PH_RX.findall(s):
            ph = ph.strip()
            ph_seen.append(f"{{{ph}}}")
            if ph in _PLAIN_PLACEHOLDERS:
                if ph == "col" and target != "column":
                    fail("Placeholders resolved",
                         "{col} is the accumulated column expression — only column rules have it.",
                         token="{col}")
                if ph.startswith("columna.") and target != "column":
                    fail("Placeholders resolved",
                         f"{{{ph}}} needs a column in context — this rule targets {target}.",
                         token=f"{{{ph}}}")
                continue
            if ph.startswith("udp:"):
                name = ph[4:].strip()
                levels = [target_level] if target_level else ["table"]
                if kind == "generator":
                    levels = ["table"]
                if not any(name in by_level.get(lv, {}) for lv in levels if lv):
                    pool = [n for lv in levels if lv for n in by_level.get(lv, {})]
                    sug = _closest(name, pool)
                    fail("Placeholders resolved",
                         f"Unknown UDP '{name}' in {{udp:…}}."
                         + (f" Did you mean '{sug}'?" if sug else ""),
                         token=f"{{udp:{name}}}", suggestion=sug)
                continue
            if ph.startswith("lookup:"):
                name = ph[7:].strip()
                if name not in lookups:
                    sug = _closest(name, list(lookups))
                    fail("Placeholders resolved",
                         f"Unknown lookup '{name}'. Define it in Lookups first."
                         + (f" Did you mean '{sug}'?" if sug else ""),
                         token=f"{{lookup:{name}}}", suggestion=sug)
                continue
            call = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", ph)
            if call:
                if call.group(1) not in functions:
                    sug = _closest(call.group(1), sorted(functions))
                    fail("Placeholders resolved",
                         f"Unknown function '{call.group(1)}'. Define it in Functions first."
                         + (f" Did you mean '{sug}'?" if sug else ""),
                         token=f"{{{ph}}}", suggestion=sug)
                continue
            fail("Placeholders resolved",
                 f"Unknown placeholder {{{ph}}}. Allowed: {{col}}, {{columna.*}}, "
                 "{tabla.*}, {udp:Name}, {lookup:name}, {function(...)}.",
                 token=f"{{{ph}}}")
    if checks["Placeholders resolved"]["ok"]:
        checks["Placeholders resolved"]["detail"] = " ".join(dict.fromkeys(ph_seen)) or "—"

    # ── Estructura (fuera de los 5 checks; la UI los muestra en Error detail) ──
    kinds = artifact_kinds or {}
    if kind == "rule":
        # Artefacto destino sin generador que lo declare = WARNING, no error:
        # se puede autorar la regla antes que su generador; hasta entonces el
        # export simplemente no la aplica ahí.
        # Doc 76: `exclude` es de vistas (como `expression`); tags y statements
        # aplican a tablas Y vistas (`ALTER VIEW`); TBLPROPERTIES y `types`
        # (doc 90) solo a tablas.
        has_expr = bool((action.get("expression") or "").strip()) or action.get("exclude") is True
        has_any_kind = bool(action.get("tags") or action.get("statements"))
        # Doc 71 H4 / doc 90: TBLPROPERTIES y los mapeos de tipo solo tienen
        # sentido en artefactos TABLA (las vistas no declaran tipos).
        table_only = [label for label, key in (("TBLPROPERTIES", "tblproperties"),
                                               ("data type mappings", "types")) if action.get(key)]
        has_table_only = bool(table_only)
        for art in rule.get("appliesTo") or []:
            if art not in artifacts:
                warnings.append(f"No generator declares '{art}' yet — the rule won't "
                                "apply there until one does.")
                continue
            # Doc 71 H4: acción incompatible con el TIPO del artefacto = la regla
            # valida pero nunca emite nada ahí — se avisa.
            k = kinds.get(art)
            if k == "table" and has_expr and not (has_any_kind or has_table_only):
                warnings.append(f"'{art}' is a table artifact — expression and exclude rules only "
                                "decorate the SELECT of a view; nothing will be emitted there.")
            if k == "view" and has_table_only and not (has_expr or has_any_kind):
                warnings.append(f"'{art}' is a view artifact — {' and '.join(table_only)} only apply to "
                                "table artifacts; nothing will be emitted there.")
        if not (rule.get("appliesTo") or []):
            warnings.append("This rule has no target artifacts yet — it won't apply anywhere on export.")
    else:
        src = rule.get("sourceArtifact")
        emit_art = ((action.get("emit") or {}).get("artifact") or "").strip()
        if not src or src not in artifacts:
            errors.append({"check": "Artifacts", "message":
                           f"A generator needs a source artifact ({', '.join(artifacts)}).",
                           "token": src, "suggestion": None, "snippet": None, "line": 1, "col": 1})
        if not emit_art.startswith("ddl."):
            errors.append({"check": "Artifacts", "message":
                           "A generator must emit an artifact id with the 'ddl.' prefix "
                           "(action.emit.artifact).", "token": emit_art or None,
                           "suggestion": None, "snippet": None, "line": 1, "col": 1})
        elif emit_art == src:
            errors.append({"check": "Artifacts", "message":
                           "A generator can't emit its own source artifact (cycle).",
                           "token": emit_art, "suggestion": None, "snippet": None,
                           "line": 1, "col": 1})
        # Doc 71 H4: columnas añadidas incompletas producirían identificadores
        # vacíos en el CREATE generado.
        for i, extra in enumerate((action.get("emit") or {}).get("add_columns") or [], start=1):
            if not (str(extra.get("name") or "").strip() and str(extra.get("type") or "").strip()):
                errors.append({"check": "Artifacts", "message":
                               f"Added column #{i} needs both a name and a type.",
                               "token": str(extra.get("name") or "") or None, "suggestion": None,
                               "snippet": None, "line": 1, "col": 1})

    state = "invalid" if errors else "valid"
    return {"state": state,
            "checks": [checks[n] for n in CHECK_NAMES],
            "errors": errors, "warnings": warnings,
            "udpRefs": udp_refs, "condition": condition}


def passive_state(report: dict) -> str:
    """Estado para RE-validaciones pasivas (la regla no fue tocada por el
    usuario): fallo SOLO de 'Value allowed for UDP' = 'stale' (un valor salió
    de la lista, spec §4) — la regla se conserva y avisa. Cualquier otro error
    = 'invalid'."""
    if report["state"] == "valid":
        return "valid"
    if all(e.get("check") == "Value allowed for UDP" for e in report["errors"]):
        return "stale"
    return "invalid"
