"""Generadores (doc 30 F4): emiten artefactos nuevos derivados de otro
artefacto (tabla de rechazos, vistas técnicas…). PURO.

- El grafo `sourceArtifact → emit.artifact` se resuelve TOPOLÓGICAMENTE y se
  ejecuta en pasadas (spec §7.3): `ddl.vista_rej` depende de `ddl.tabla_rej`,
  que depende de `ddl.tabla_fisica`. Los ciclos se rechazan al VALIDAR el
  ruleset, no en runtime.
- El contexto `tabla.*` de condición y placeholders es SIEMPRE la tabla física
  original de la cadena (sus UDP), aunque la fuente sea un artefacto derivado.
  `artefacto.*` (doc 76 D6) es el objeto que se está emitiendo.
- La decoración de los SELECT generados NO es implícita: la aplican las reglas
  de columna que tengan el artefacto en su `appliesTo` (spec §7.4/§8.7 — la
  prueba de fuego: generador y regla no se conocen).

Doc 76 (macro BCP): las columnas de un artefacto se guardan en ORDEN FÍSICO
(+ añadidas al final); la disposición «particiones al final» y el orden de
las particiones por UDP (`layout.partitionOrderUdp`) se aplican SOLO al
emitir un CREATE TABLE. Las vistas listan `col AS col` en orden físico.
"""
from __future__ import annotations

import re

from . import conditions as cond
from . import render
from .expressions import RenderError, expand_template

ROOTS = ("ddl.tabla_fisica", "ddl.vista_negocio")


# ── Doc 73/76 · Column layout ───────────────────────────────────────────────
# Decisión del owner (2026-09-06): las particiones se DISTRIBUYEN desde las
# reglas del DDL, no cambiando el orden físico del modelo. `layout` admite:
#   partitionColumns: 'last' | 'keep'   → las de partición al final del CREATE
#   partitionOrderUdp: 'Particion'      → orden de las particiones (bloque final
#                                          y PARTITIONED BY) por el correlativo
#                                          PART_nn del UDP de columna (doc 76 D3)

# Misma convención del kit (`scripts/erwin_migration/policies.py`): PART_01 → 1.
_PARTITION_RE = re.compile(r"^PART[_-]?(\d+)$", re.IGNORECASE)


def partition_correlative(value: str | None) -> int | None:
    """Valor UDP → correlativo de partición (PART_01 → 1). Valores fuera de la
    convención ('No Definido', vacío, None) → None (va después, en orden físico)."""
    m = _PARTITION_RE.match(str(value or "").strip())
    return int(m.group(1)) if m else None


def layout_from_rules(rules: list[dict]) -> dict:
    """{'partitionsLast': bool, 'partitionOrderUdp': str|None} a partir de las
    reglas que CORREN (kind='rule', target='table', `action.layout`, appliesTo
    ∋ ddl.tabla_fisica). Varias reglas de layout se combinan (la primera en
    run_order que declare el UDP de orden gana)."""
    out: dict = {"partitionsLast": False, "partitionOrderUdp": None}
    for r in render.run_order(rules):
        if (r.get("kind") or "rule") != "rule" or r.get("target") != "table":
            continue
        lay = (r.get("action") or {}).get("layout") or {}
        if "ddl.tabla_fisica" not in (r.get("appliesTo") or []):
            continue
        if lay.get("partitionColumns") == "last":
            out["partitionsLast"] = True
        udp = str(lay.get("partitionOrderUdp") or "").strip()
        if udp and not out["partitionOrderUdp"]:
            out["partitionOrderUdp"] = udp
    return out


def _partition_key(col: dict) -> tuple:
    """Clave de orden de una columna de partición: primero las que traen
    correlativo (PART_01 < PART_02…), después las demás en orden físico
    (sort estable)."""
    order = col.get("partitionOrder")
    return (order is None, order if order is not None else 0)


def ordered_partitions(cols: list[dict]) -> list[dict]:
    """Columnas de partición de la lista, en el orden de emisión (doc 76 D3)."""
    return sorted([c for c in cols if c.get("partition")], key=_partition_key)


def layout_columns(cols: list[dict], options: dict | None = None) -> list[dict]:
    """Orden de EMISIÓN de las columnas de un CREATE ({name, partition,
    partitionOrder?, …}): con `partitionsLast` las de partición van al final,
    ordenadas por su correlativo — espejo exacto de `tableDDL` en el front
    (src/features/ddl/generators.ts), que es quien emite el CREATE físico.
    Puro; sin la opción, copia en el mismo orden."""
    if not (options or {}).get("partitionsLast"):
        return list(cols)
    return [c for c in cols if not c.get("partition")] + ordered_partitions(cols)


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
    """Columnas del artefacto EN ORDEN FÍSICO de la fuente (flag de partición
    y correlativo incluidos — el _rej debe particionar IGUAL que la física) +
    las añadidas AL FINAL. `force_type` pisa el tipo de las heredadas salvo,
    con `keep_partition_type`, el de las columnas de partición (doc 76: la
    `_rej` conserva el tipo de sus particiones). Doc 71 H5: una añadida
    HOMÓNIMA de una heredada la reemplaza en su lugar (una sola definición por
    nombre). La disposición «particiones al final» NO se aplica acá: es cosa
    del CREATE TABLE (`artifact_sql`); las vistas conservan este orden."""
    spec = emit.get("columns") or {}
    inherit = spec.get("inherit", "all")
    force = (spec.get("force_type") or "").strip()
    keep_partition_type = bool(spec.get("keep_partition_type"))
    wanted = None if inherit in ("all", None) else set(inherit)
    out: list[dict] = []
    for c in source_cols:
        if wanted is not None and c.get("name") not in wanted:
            continue
        is_part = bool(c.get("partition"))
        typ = c.get("type") or "STRING"
        if force and not (keep_partition_type and is_part):
            typ = force
        out.append({"name": c["name"], "type": typ, "partition": is_part,
                    "partitionOrder": c.get("partitionOrder"), "added": bool(c.get("added"))})
    inherited = {c["name"]: c for c in out}
    for extra in emit.get("add_columns") or []:
        name = (extra.get("name") or "").strip()
        if not name:
            continue
        typ = (extra.get("type") or "STRING").strip() or "STRING"
        if name in inherited:
            inherited[name]["type"] = typ          # override en su lugar
        else:
            out.append({"name": name, "type": typ, "partition": False,
                        "partitionOrder": None, "added": True})
    return out


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
    mismas particiones heredadas y misma `LOCATION` (carpeta = nombre final con
    el casing de carpeta del export, p.ej. `…/TBL_X_REJ`) cuando el export es
    external. Tablas: heredadas → añadidas → PARTICIONES al final (pedido owner
    07-20), ordenadas por su correlativo cuando el ruleset lo declara (doc 76).
    Vistas: `col AS col` con el casing del export, sin backticks, en el orden
    guardado (físico + añadidas al final). Determinista (spec §7.6)."""
    o = options or {}
    ident = lambda s: render.ident(s, o)                             # noqa: E731 — mismo funnel (doc 71 H2)
    full = render.full_name(art.get("schema"), art["name"], o)
    cols = art.get("columns") or []
    if art.get("kind") == "table":
        meta = source_cols_meta or {}
        keep_not_null = "not_null" not in (art.get("strip") or set())
        emitted = layout_columns(cols, {"partitionsLast": True})
        names = [ident(c["name"]) for c in emitted]
        width = max((len(n) for n in names), default=0) + 2
        lines = []
        for c, n in zip(emitted, names):
            line = f"  {n:<{width}}{c['type']}"
            src = meta.get(c["name"]) or {}
            if keep_not_null and (src.get("nullable") is False or src.get("pk")):
                line += " NOT NULL"
            lines.append(line)
        out = f"CREATE {'EXTERNAL ' if o.get('external') else ''}TABLE IF NOT EXISTS {full} (\n"
        out += ",\n".join(lines) + "\n)"
        out += f"\nUSING {o.get('tableFormat') or 'delta'}"
        if o.get("includePartitions", True):
            parts = [ident(c["name"]) for c in ordered_partitions(cols)]
            if parts:
                out += f"\nPARTITIONED BY ({', '.join(parts)})"
        if o.get("external"):
            base = str(o.get("location") or "").rstrip("/")
            # misma convención del front: carpeta = nombre final del artefacto
            # (con su sufijo) con el casing de carpeta del export (doc 76 D9).
            out += f"\nLOCATION '{base}/{render.location_folder(art['name'], o)}'"
        return out + ";"
    src = art.get("sourceRef") or {}
    src_full = (f"{ident(src.get('schema'))}.{ident(src.get('name'))}"
                if src.get("schema") else ident(src.get("name") or ""))
    # Proyecciones `col AS col` con el casing del export y SIN backticks (doc
    # 76 D8): identificadores del modelo, como las escribe la macro y como
    # emite el front las vistas de negocio.
    names = [render.cased(c["name"], o) for c in cols]
    proj = ",\n  ".join(f"{n} AS {n}" for n in names) or "*"
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


def physical_columns(table_cols: list[dict], cols_ctx_by_name: dict[str, dict],
                     partition_order_udp: str | None) -> list[dict]:
    """Columnas de la tabla física en ORDEN FÍSICO explícito (no el de llegada,
    doc 73 O2) con su flag de partición y, si el ruleset declara el UDP de
    orden (doc 76 D3), el correlativo PART_nn leído del contexto de la columna."""
    out: list[dict] = []
    for c in sorted(table_cols, key=lambda x: (x.get("ordinal") or 0)):
        name = c.get("physicalName") or c.get("name")
        is_part = bool(c.get("isPartition"))
        order = None
        if is_part and partition_order_udp:
            udp = (cols_ctx_by_name.get(name) or {}).get("udp") or {}
            order = partition_correlative(udp.get(partition_order_udp))
        out.append({"name": name, "type": c.get("dataType") or c.get("type") or "STRING",
                    "partition": is_part, "partitionOrder": order, "added": False})
    return out


def run_generators(rules: list[dict], table: dict, table_cols: list[dict],
                   base_ctx: dict, cols_ctx_by_name: dict[str, dict],
                   config: dict, options: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Cascada completa para UNA tabla física: ejecuta los generadores en orden
    topológico, decora las VISTAS generadas con las reglas de columna que las
    tengan en `appliesTo` y aplica a cada artefacto las reglas de tabla que lo
    nombren (doc 71 H3 / doc 76): sentencias `before` (preámbulo del CREATE),
    TBLPROPERTIES (tablas), tags de objeto (`ALTER TABLE|VIEW … SET TAGS`, una
    sentencia por regla), tags de columna (en vistas, solo sobre las columnas
    proyectadas) y sentencias `after`. `options` = opciones del export del
    canvas para que los artefactos salgan ESPEJO del físico. Devuelve
    (statements, log): statements = [{artifact, name, schema, sql, generator}]."""
    runnable_rules, log = render.runnable(rules)
    gens_ordered, unreachable = generator_order(runnable_rules)
    for g in unreachable:
        log.append({"rule": g.get("name"), "status": "skipped",
                    "reason": "unresolved source artifact (cycle or missing generator)"})
    layout = layout_from_rules(runnable_rules)
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    produced: dict[str, dict] = {
        "ddl.tabla_fisica": {
            "schema": base_ctx.get("tabla", {}).get("esquema"),
            "name": base_ctx.get("tabla", {}).get("nombre"),
            "columns": physical_columns(table_cols, cols_ctx_by_name, layout.get("partitionOrderUdp")),
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
        full_art = render.full_name(art.get("schema"), art["name"], options)
        ctx = {**base_ctx, "artefacto": render.artifact_ctx(art.get("schema"), art["name"],
                                                              art["kind"], options)}
        sql = artifact_sql(art, meta, options)
        extra_stmts: list[dict] = []
        if art["kind"] == "view":
            sql, sub_log = render.decorate_view_sql(
                sql, art["artifact"], runnable_rules, config, ctx, cols_ctx_by_name)
            log.extend(sub_log)
            if not sql:
                log.append({"rule": gen.get("name"), "status": "skipped", "artifact": art["artifact"],
                            "reason": f"{art['schema']}.{art['name']}: every column was excluded — view not emitted"})
                continue
            view_ctx = render.projected_cols_ctx(sql, cols_ctx_by_name)
            t_stmts, l_t = render.table_tag_statements(
                runnable_rules, art["artifact"], ctx, config, full_art, object_kind="view")
            c_stmts, l_c = render.column_tag_statements(
                runnable_rules, art["artifact"], ctx, view_ctx, config, full_art, options,
                object_kind="view")
        else:
            # Doc 71 H3: la tabla generada también recibe las reglas de tabla
            # que la nombren (TBLPROPERTIES dentro del CREATE; tags aparte).
            sql, sub_log = render.apply_tblproperties(sql, runnable_rules, art["artifact"], ctx, config)
            log.extend(sub_log)
            before, l_b = render.statement_snippets(runnable_rules, art["artifact"], ctx, config, "before")
            log.extend(l_b)
            if before:
                sql = "\n".join(before) + "\n" + sql
            art_ctx = artifact_cols_ctx(art, cols_ctx_by_name)
            t_stmts, l_t = render.table_tag_statements(
                runnable_rules, art["artifact"], ctx, config, full_art, object_kind="table")
            c_stmts, l_c = render.column_tag_statements(
                runnable_rules, art["artifact"], ctx, art_ctx, config, full_art, options,
                object_kind="table")
        log.extend(l_t); log.extend(l_c)
        after, l_a = render.statement_snippets(runnable_rules, art["artifact"], ctx, config, "after")
        log.extend(l_a)
        extra_stmts = [{"artifact": f"{art['artifact']}.tags", "schema": art["schema"],
                        "name": art["name"], "sql": s, "generator": gen.get("name")}
                       for s in t_stmts + c_stmts]
        extra_stmts += [{"artifact": f"{art['artifact']}.extra", "schema": art["schema"],
                         "name": art["name"], "sql": s, "generator": gen.get("name")}
                        for s in after]
        log.append({"rule": gen.get("name"), "status": "applied",
                    "artifact": art["artifact"], "object": f"{art['schema']}.{art['name']}"})
        statements.append({"artifact": art["artifact"], "schema": art["schema"],
                           "name": art["name"], "sql": sql, "generator": gen.get("name")})
        statements.extend(extra_stmts)
    return statements, log
