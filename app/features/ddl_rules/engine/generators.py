"""Generadores (doc 30 F4): emiten artefactos nuevos derivados de otro
artefacto (tabla de rechazos, vista técnica…). PURO.

- El grafo `sourceArtifact → emit.artifact` se resuelve TOPOLÓGICAMENTE y se
  ejecuta en pasadas (spec §7.3): `ddl.vista_rej` depende de `ddl.tabla_rej`,
  que depende de `ddl.tabla_fisica`. Los ciclos se rechazan al VALIDAR el
  ruleset, no en runtime.
- El contexto `tabla.*` de condición y placeholders es SIEMPRE la tabla física
  original de la cadena (sus UDP), aunque la fuente sea un artefacto derivado.
- La decoración de los SELECT generados NO es implícita: la aplican las reglas
  de columna que tengan el artefacto en su `appliesTo` (spec §7.4/§8.7 — la
  prueba de fuego: generador y regla no se conocen).
"""
from __future__ import annotations

from . import conditions as cond
from . import render
from .expressions import RenderError, expand_template

ROOTS = ("ddl.tabla_fisica", "ddl.vista_negocio")


# ── Doc 73 · Column layout ───────────────────────────────────────────────────
# Decisión del owner (2026-09-06): las particiones se DISTRIBUYEN desde las
# reglas del DDL, no cambiando el orden físico del modelo. La única acción de
# `layout` hoy es `partitionColumns: 'last'`: las columnas de partición se
# emiten al final del CREATE (físico y artefactos tabla que heredan sus
# columnas). Es un estándar del ruleset: sin condición, sin UDPs.

def layout_from_rules(rules: list[dict]) -> dict:
    """{'partitionsLast': bool} a partir de las reglas que CORREN (kind='rule',
    target='table', `action.layout`, appliesTo ∋ ddl.tabla_fisica)."""
    out = {"partitionsLast": False}
    for r in rules:
        if (r.get("kind") or "rule") != "rule" or r.get("target") != "table":
            continue
        lay = (r.get("action") or {}).get("layout") or {}
        if lay.get("partitionColumns") == "last" and "ddl.tabla_fisica" in (r.get("appliesTo") or []):
            out["partitionsLast"] = True
    return out


def layout_columns(cols: list[dict], options: dict | None = None) -> list[dict]:
    """Orden de EMISIÓN de las columnas del CREATE físico ({name, partition,
    …}): con `partitionsLast` las de partición van al final (estable) — espejo
    exacto de `tableDDL` en el front (src/features/ddl/generators.ts), que es
    quien emite el CREATE base. Puro; sin la opción, copia en el mismo orden."""
    if not (options or {}).get("partitionsLast"):
        return list(cols)
    return [c for c in cols if not c.get("partition")] + [c for c in cols if c.get("partition")]


def generators_of(rules: list[dict]) -> list[dict]:
    return [r for r in rules if r.get("kind") == "generator"]


def generator_order(generators: list[dict]) -> tuple[list[dict], list[dict]]:
    """Kahn sobre `sourceArtifact → emit.artifact` partiendo de las raíces.
    Devuelve (orden de ejecución, inalcanzables) — inalcanzables incluye los
    ciclos y las fuentes que nadie produce. Determinista: cada pasada procesa
    en run_order (priority DESC, name ASC)."""
    available = set(ROOTS)
    pending = render.run_order(generators_of(generators))
    ordered: list[dict] = []
    while True:
        ready = [g for g in pending
                 if (g.get("sourceArtifact") or "") in available]
        if not ready:
            break
        for g in ready:
            ordered.append(g)
            art = ((g.get("action") or {}).get("emit") or {}).get("artifact")
            if art:
                available.add(art)
        pending = [g for g in pending if g not in ready]
    return ordered, pending


def cycle_error(rules: list[dict]) -> str | None:
    """Mensaje de error SOLO si hay un ciclo genuino entre generadores (spec
    §7.3): inalcanzables cuyo source lo emite otro inalcanzable. Un generador
    huérfano (fuente que nadie produce todavía) NO es error acá — lo reporta la
    validación individual / el estado passive."""
    _, unreachable = generator_order(rules)
    if not unreachable:
        return None
    emits = {((g.get("action") or {}).get("emit") or {}).get("artifact")
             for g in unreachable}
    cyclic = [g for g in unreachable if (g.get("sourceArtifact") or "") in emits]
    if not cyclic:
        return None
    names = ", ".join(f"'{g.get('name')}'" for g in cyclic)
    return (f"Generators {names} form a dependency cycle "
            "(each one's source is another one's output). Break the cycle to save.")


# ── Emisión de un artefacto ────────────────────────────────────────────────


def _emit_columns(emit: dict, source_cols: list[dict]) -> list[dict]:
    """Columnas del artefacto: hereda las de la fuente (flag de partición
    incluido — el _rej debe particionar IGUAL que la física) + las añadidas.
    Orden (pedido owner 07-20): heredadas sin partición → añadidas → columnas
    de PARTICIÓN al final (las added siempre quedan antes de las particiones).
    Doc 71 H5: una añadida HOMÓNIMA de una heredada la reemplaza en su lugar
    (una sola definición por nombre) en vez de duplicar la columna."""
    spec = emit.get("columns") or {}
    inherit = spec.get("inherit", "all")
    force = (spec.get("force_type") or "").strip()
    plain: list[dict] = []
    parts: list[dict] = []
    wanted = None if inherit in ("all", None) else set(inherit)
    for c in source_cols:
        if wanted is not None and c.get("name") not in wanted:
            continue
        col = {"name": c["name"], "type": force or c.get("type") or "STRING",
               "partition": bool(c.get("partition"))}
        (parts if col["partition"] else plain).append(col)
    added: list[dict] = []
    inherited = {c["name"]: c for c in plain + parts}
    for extra in emit.get("add_columns") or []:
        name = (extra.get("name") or "").strip()
        if not name:
            continue
        col = {"name": name, "type": (extra.get("type") or "STRING").strip() or "STRING", "partition": False}
        if name in inherited:
            inherited[name]["type"] = col["type"]      # override en su lugar
        else:
            added.append(col)
    return plain + added + parts


def _emit_kind(emit: dict) -> str:
    """'table' | 'view'. Explícito en `emit.type`; si falta se infiere del id
    del artefacto ('ddl.vista_*' → view)."""
    t = (emit.get("type") or "").strip().lower()
    if t in ("table", "view"):
        return t
    return "view" if "vista" in (emit.get("artifact") or "") else "table"


def emit_for_table(gen: dict, produced: dict[str, dict], base_ctx: dict,
                   lookups: dict, functions: list[dict]) -> dict | None:
    """Ejecuta un generador para UNA tabla física. `produced` mapea artefacto →
    {schema, name, columns} ya generado (la física incluida). Devuelve el
    artefacto nuevo o None si la condición no aplica."""
    action = gen.get("action") or {}
    emit = action.get("emit") or {}
    src_id = gen.get("sourceArtifact") or ""
    source = produced.get(src_id)
    if source is None:
        return None
    text = gen.get("condition") or ""
    if not cond.eval_condition(cond.parse_condition(text), base_ctx, text):
        return None
    schema = expand_template(emit.get("schema") or "{tabla.esquema}", base_ctx, lookups, functions)
    name = expand_template(emit.get("name") or "{tabla.nombre}", base_ctx, lookups, functions)
    if schema is None or name is None or not str(name).strip():
        raise RenderError(f"Generator '{gen.get('name')}' produced an empty schema/name.")
    return {
        "artifact": emit.get("artifact"),
        "kind": _emit_kind(emit),
        "schema": str(schema), "name": str(name),
        "columns": _emit_columns(emit, source.get("columns") or []),
        "sourceRef": {"schema": source.get("schema"), "name": source.get("name")},
        "strip": set((emit.get("columns") or {}).get("strip") or []),
        "generator": gen.get("name"),
    }


def artifact_sql(art: dict, source_cols_meta: dict[str, dict] | None = None,
                 options: dict | None = None) -> str:
    """SQL del artefacto generado — ESPEJO del export del canvas (pedido owner
    07-20): mismos backticks, mismo `identifierCase`, mismo `USING <format>`,
    mismas particiones heredadas y misma `LOCATION` (carpeta = nombre final,
    p.ej. `…/TBL_X_rej`) cuando el export es external. Solo cambian el nombre
    (sufijo del emit) y los tipos forzados. Determinista (spec §7.6)."""
    o = options or {}
    ident = lambda s: render.ident(s, o)                             # noqa: E731 — mismo funnel (doc 71 H2)
    full = render.full_name(art.get("schema"), art["name"], o)
    cols = art.get("columns") or []
    if art.get("kind") == "table":
        meta = source_cols_meta or {}
        keep_not_null = "not_null" not in (art.get("strip") or set())
        names = [ident(c["name"]) for c in cols]
        width = max((len(n) for n in names), default=0) + 2
        lines = []
        for c, n in zip(cols, names):
            line = f"  {n:<{width}}{c['type']}"
            src = meta.get(c["name"]) or {}
            if keep_not_null and (src.get("nullable") is False or src.get("pk")):
                line += " NOT NULL"
            lines.append(line)
        out = f"CREATE {'EXTERNAL ' if o.get('external') else ''}TABLE IF NOT EXISTS {full} (\n"
        out += ",\n".join(lines) + "\n)"
        out += f"\nUSING {o.get('tableFormat') or 'delta'}"
        if o.get("includePartitions", True):
            parts = [ident(c["name"]) for c in cols if c.get("partition")]
            if parts:
                out += f"\nPARTITIONED BY ({', '.join(parts)})"
        if o.get("external"):
            base = str(o.get("location") or "").rstrip("/")
            # misma convención del front: carpeta = nombre físico CRUDO (acá,
            # el nombre final del artefacto, con su sufijo).
            out += f"\nLOCATION '{base}/{art['name']}'"
        return out + ";"
    src = art.get("sourceRef") or {}
    src_full = (f"{ident(src.get('schema'))}.{ident(src.get('name'))}"
                if src.get("schema") else ident(src.get("name") or ""))
    # Columnas del SELECT en crudo — mismo criterio F4 del export: el casing
    # nunca toca las proyecciones de una vista.
    proj = ",\n  ".join(c["name"] for c in cols) or "*"
    return f"CREATE OR REPLACE VIEW {full} AS\nSELECT\n  {proj}\nFROM {src_full};"


def artifact_cols_ctx(art: dict, cols_ctx_by_name: dict[str, dict]) -> dict[str, dict]:
    """Contexto `columna.*` de un artefacto TABLA generado (doc 71 H3): las
    heredadas conservan el contexto de la física (UDP, dominio, nulable…) con el
    TIPO del artefacto; las añadidas nacen sin UDP. Orden del artefacto. Puro."""
    out: dict[str, dict] = {}
    for i, c in enumerate(art.get("columns") or []):
        base = cols_ctx_by_name.get(c["name"]) or {
            "nombre": c["name"], "nulable": True, "pk": False, "orden": i,
            "comentario": None, "dominio": None, "udp": {},
        }
        out[c["name"]] = {**base, "nombre": c["name"], "tipo": c.get("type") or ""}
    return out


def run_generators(rules: list[dict], table: dict, table_cols: list[dict],
                   base_ctx: dict, cols_ctx_by_name: dict[str, dict],
                   config: dict, options: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Cascada completa para UNA tabla física: ejecuta los generadores en orden
    topológico, decora las VISTAS generadas con las reglas de columna que las
    tengan en `appliesTo` y (doc 71 H3) aplica TBLPROPERTIES y SET TAGS a las
    TABLAS generadas que alguna regla nombre. `options` = opciones del export
    del canvas (identifierCase/tableFormat/external/location/includePartitions)
    para que los artefactos generados salgan ESPEJO del físico. Devuelve
    (statements, log): statements = [{artifact, name, schema, sql, generator}]."""
    runnable_rules, log = render.runnable(rules)
    gens_ordered, unreachable = generator_order(runnable_rules)
    for g in unreachable:
        log.append({"rule": g.get("name"), "status": "skipped",
                    "reason": "unresolved source artifact (cycle or missing generator)"})
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    produced: dict[str, dict] = {
        "ddl.tabla_fisica": {
            "schema": base_ctx.get("tabla", {}).get("esquema"),
            "name": base_ctx.get("tabla", {}).get("nombre"),
            # Orden FÍSICO explícito (no el de llegada, doc 73 O2). Las tablas
            # generadas emiten SIEMPRE sus particiones al final (_emit_columns,
            # pedido owner 07-20); la regla `layout` del doc 73 alinea el CREATE
            # físico con ese mismo criterio.
            "columns": [{"name": c.get("physicalName") or c.get("name"),
                         "type": c.get("dataType") or c.get("type") or "STRING",
                         "partition": bool(c.get("isPartition"))}
                        for c in sorted(table_cols, key=lambda x: (x.get("ordinal") or 0))],
        },
    }
    meta = {(c.get("physicalName") or c.get("name")): {
                "nullable": c.get("isNullable", True), "pk": bool(c.get("isPrimaryKey"))}
            for c in table_cols}
    statements: list[dict] = []
    for gen in gens_ordered:
        try:
            art = emit_for_table(gen, produced, base_ctx, lookups, functions)
        except (RenderError, cond.CondError) as e:
            log.append({"rule": gen.get("name"), "status": "skipped", "reason": str(e)})
            continue
        if art is None:
            continue
        produced[art["artifact"]] = art
        sql = artifact_sql(art, meta, options)
        extra_stmts: list[dict] = []
        if art["kind"] == "view":
            sql, sub_log = render.decorate_view_sql(
                sql, art["artifact"], runnable_rules, config, base_ctx, cols_ctx_by_name)
            log.extend(sub_log)
        else:
            # Doc 71 H3: la tabla generada también recibe las reglas de tabla
            # que la nombren (TBLPROPERTIES dentro del CREATE; tags aparte).
            sql, sub_log = render.apply_tblproperties(sql, runnable_rules, art["artifact"], base_ctx, config)
            log.extend(sub_log)
            art_ctx = artifact_cols_ctx(art, cols_ctx_by_name)
            full_art = render.full_name(art.get("schema"), art["name"], options)
            c_stmts, l_c = render.column_tag_statements(
                runnable_rules, art["artifact"], base_ctx, art_ctx, config, full_art, options)
            t_stmts, l_t = render.table_tag_statements(
                runnable_rules, art["artifact"], base_ctx, config, full_art)
            log.extend(l_c); log.extend(l_t)
            extra_stmts = [{"artifact": f"{art['artifact']}.tags", "schema": art["schema"],
                            "name": art["name"], "sql": s, "generator": gen.get("name")}
                           for s in c_stmts + t_stmts]
        log.append({"rule": gen.get("name"), "status": "applied",
                    "artifact": art["artifact"], "object": f"{art['schema']}.{art['name']}"})
        statements.append({"artifact": art["artifact"], "schema": art["schema"],
                           "name": art["name"], "sql": sql, "generator": gen.get("name")})
        statements.extend(extra_stmts)
    return statements, log
