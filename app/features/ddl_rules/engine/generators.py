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
(+ añadidas al final); la disposición «particiones al final» y las
particiones por UDP (`layout.partitionUdp`, doc 93 D10: qué columnas y en qué
orden) se aplican SOLO al emitir un CREATE TABLE. Las vistas listan
`col AS col` en orden físico.

Doc 107: con `partitionColumns: 'partitioned-by'` las particiones no van en la
lista del CREATE TABLE — se declaran con su tipo dentro del PARTITIONED BY.
"""
from __future__ import annotations

import re

from app.core.column_order import display_order

from . import conditions as cond
from . import render
from .expressions import RenderError, expand_template

ROOTS = ("ddl.tabla_fisica", "ddl.vista_negocio")


# ── Doc 73/76/93 · Column layout ────────────────────────────────────────────
# Decisión del owner (2026-09-06): las particiones se DISTRIBUYEN desde las
# reglas del DDL, no cambiando el orden físico del modelo. `layout` admite:
#   partitionColumns: 'last' | 'keep'   → las de partición al final del CREATE
#                     | 'partitioned-by' → doc 107: fuera de la lista; se declaran
#                                          con su tipo DENTRO del PARTITIONED BY
#                                          (`PARTITIONED BY (fecdia date, codmes int)`)
#   partitionUdp: 'Particion'           → doc 93 D10: las columnas de partición
#                                          SON las que tienen PART_nn en ese UDP
#                                          de columna (el flag `isPartition` de la
#                                          plataforma se ignora), ordenadas por nn
#                                          (bloque final y PARTITIONED BY).
#                                          Alias legado: `partitionOrderUdp`.

# Misma convención del kit (`scripts/erwin_migration/policies.py`): PART_01 → 1.
_PARTITION_RE = re.compile(r"^PART[_-]?(\d+)$", re.IGNORECASE)


def partition_correlative(value: str | None) -> int | None:
    """Valor UDP → correlativo de partición (PART_01 → 1). Valores fuera de la
    convención ('No Definido', vacío, None) → None (va después, en orden físico)."""
    m = _PARTITION_RE.match(str(value or "").strip())
    return int(m.group(1)) if m else None


def partition_udp_of(layout: dict | None) -> str | None:
    """UDP de particiones de un `action.layout` (doc 93 D10): `partitionUdp` o su
    alias legado `partitionOrderUdp` (reglas guardadas antes del doc 93)."""
    lay = layout or {}
    name = str(lay.get("partitionUdp") or lay.get("partitionOrderUdp") or "").strip()
    return name or None


def layout_from_rules(rules: list[dict]) -> dict:
    """{'partitionsLast': bool, 'partitionsInClause': bool, 'partitionUdp':
    str|None} a partir de las reglas que CORREN (kind='rule', target='table',
    `action.layout`, appliesTo ∋ ddl.tabla_fisica). La primera en run_order que
    declare el UDP gana. Espejo de `layoutFromRules` del front."""
    out: dict = {"partitionsLast": False, "partitionsInClause": False, "partitionUdp": None}
    for r in render.run_order(rules):
        if (r.get("kind") or "rule") != "rule" or r.get("target") != "table":
            continue
        lay = (r.get("action") or {}).get("layout") or {}
        if "ddl.tabla_fisica" not in (r.get("appliesTo") or []):
            continue
        if lay.get("partitionColumns") == "last":
            out["partitionsLast"] = True
        elif lay.get("partitionColumns") == "partitioned-by":
            out["partitionsInClause"] = True
        udp = partition_udp_of(lay)
        if udp and not out["partitionUdp"]:
            out["partitionUdp"] = udp
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


def partitions_in_clause(cols: list[dict], options: dict | None = None) -> bool:
    """Doc 107: ¿las particiones se declaran con su tipo DENTRO del PARTITIONED
    BY (y no en la lista)? Sí con `partitionsInClause`, si el export emite el
    PARTITIONED BY y la tabla tiene particiones — sin PARTITIONED BY no pueden
    desaparecer del CREATE. Espejo de `tableDDL` en el front."""
    o = options or {}
    return (bool(o.get("partitionsInClause")) and bool(o.get("includePartitions", True))
            and any(c.get("partition") for c in cols))


def layout_columns(cols: list[dict], options: dict | None = None) -> list[dict]:
    """Columnas de la LISTA de un CREATE en su orden de emisión ({name,
    partition, partitionOrder?, …}): con `partitionsLast` las de partición van
    al final, ordenadas por su correlativo; con `partitionsInClause` (doc 107) no
    van — se declaran en el PARTITIONED BY — salvo que el export no lo emita:
    entonces van al final, como con `partitionsLast`. Espejo exacto de
    `tableDDL` en el front (src/features/ddl/generators.ts), que es quien emite
    el CREATE físico. Puro; sin opciones, copia en el mismo orden."""
    o = options or {}
    if partitions_in_clause(cols, o):
        return [c for c in cols if not c.get("partition")]
    if not (o.get("partitionsLast") or o.get("partitionsInClause")):
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
    """SQL del artefacto generado — MISMO estilo que el CREATE físico del front
    (doc 93 D4): identificadores según `quoteIdentifiers`, tipos con `typeCase`,
    CREATE según `createTable`, `USING` en mayúscula, `NOT NULL` solo con
    `includeKeys` (y sin `strip: not_null`), particiones al final ordenadas por su
    correlativo y `PARTITIONED BY` solo con nombres — o, con `partitionsInClause`
    (doc 107), particiones fuera de la lista y declaradas con su tipo dentro del
    PARTITIONED BY; LOCATION = base + carpeta
    con el casing de carpeta (nombre final con su sufijo, p.ej. `…/TBL_X_REJ`).
    Vistas: `col AS col` en el orden guardado (físico + añadidas al final).
    Determinista (spec §7.6)."""
    o = options or {}
    full = render.full_name(art.get("schema"), art["name"], o)
    cols = art.get("columns") or []
    if art.get("kind") == "table":
        meta = source_cols_meta or {}
        keep_not_null = bool(o.get("includeKeys")) and "not_null" not in (art.get("strip") or set())

        def col_def(c: dict) -> str:
            line = f"{render.ident(c['name'], o)} {render.type_text(c['type'], o)}"
            src = meta.get(c["name"]) or {}
            if keep_not_null and (src.get("nullable") is False or src.get("pk")):
                line += " NOT NULL"
            return line

        in_clause = partitions_in_clause(cols, o)
        lines = [f"  {col_def(c)}" for c in layout_columns(cols, {**o, "partitionsLast": True})]
        out = f"{render.create_table_sql(o)} {full} (\n" + ",\n".join(lines) + "\n)"
        out += f"\nUSING {str(o.get('tableFormat') or 'delta').upper()}"
        if o.get("includePartitions", True):
            parts = [col_def(c) if in_clause else render.ident(c["name"], o) for c in ordered_partitions(cols)]
            if parts:
                out += f"\nPARTITIONED BY ({', '.join(parts)})"
        if o.get("external"):
            base = str(o.get("location") or "").rstrip("/")
            out += f"\nLOCATION '{base}/{render.location_folder(art['name'], o)}'"
        return out + ";"
    src = art.get("sourceRef") or {}
    src_full = render.full_name(src.get("schema"), src.get("name") or "", o)
    # Proyecciones `col AS col` con el casing del export; comillas solo si el
    # nombre las necesita (doc 76 D8 · doc 93 D2).
    names = [render.proj_ident(c["name"], o) for c in cols]
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
                     partition_udp: str | None) -> list[dict]:
    """Columnas de la tabla física en su ORDEN ÚNICO explícito (doc 73 O2 · doc
    94 D5: PK primero y cada bloque por `ordinal`, igual que el CREATE del
    front). Doc 93 D10: si el ruleset declara el UDP de particiones, una columna
    ES de partición si y solo si su valor es PART_nn (el flag `isPartition` de la
    plataforma se ignora) y se ordena por nn; sin UDP, rige el flag. El tipo es
    el que emite el front (`ddlType`, doc 93 D4) o, si no viene, el del modelo."""
    out: list[dict] = []
    for c in display_order(table_cols):
        name = c.get("physicalName") or c.get("name")
        if partition_udp:
            udp = (cols_ctx_by_name.get(name) or {}).get("udp") or {}
            order = partition_correlative(udp.get(partition_udp))
            is_part = order is not None
        else:
            is_part, order = bool(c.get("isPartition")), None
        out.append({"name": name,
                    "type": c.get("ddlType") or c.get("dataType") or c.get("type") or "STRING",
                    "partition": is_part, "partitionOrder": order, "added": False})
    return out


def run_generators(rules: list[dict], table: dict, table_cols: list[dict],
                   base_ctx: dict, cols_ctx_by_name: dict[str, dict],
                   config: dict, options: dict | None = None,
                   layout: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Cascada completa para UNA tabla física: ejecuta los generadores en orden
    topológico, decora las VISTAS generadas con las reglas de columna que las
    tengan en `appliesTo` y aplica a cada artefacto las reglas de tabla que lo
    nombren (doc 71 H3 / doc 76): tipos de columna (`types`, doc 90 — tablas),
    sentencias `before` (preámbulo del CREATE),
    TBLPROPERTIES (tablas), tags de objeto (`ALTER TABLE|VIEW … SET TAGS`, una
    sentencia por regla), tags de columna (en vistas, solo sobre las columnas
    proyectadas) y sentencias `after`. `options` = opciones del export del
    canvas para que los artefactos salgan ESPEJO del físico. `layout` = el del
    pipeline (ya validado contra los UDP del proyecto, doc 93 D10); sin él se
    deriva de las reglas. Devuelve (statements, log): statements = [{artifact,
    name, schema, sql, generator}]."""
    runnable_rules, log = render.runnable(rules)
    gens_ordered, unreachable = generator_order(runnable_rules)
    for g in unreachable:
        log.append({"rule": g.get("name"), "status": "skipped",
                    "reason": "unresolved source artifact (cycle or missing generator)"})
    layout = layout if layout is not None else layout_from_rules(runnable_rules)
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    produced: dict[str, dict] = {
        "ddl.tabla_fisica": {
            "schema": base_ctx.get("tabla", {}).get("esquema"),
            "name": base_ctx.get("tabla", {}).get("nombre"),
            "columns": physical_columns(table_cols, cols_ctx_by_name, layout.get("partitionUdp")),
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
        if art["kind"] == "table":
            # Doc 90: las columnas del artefacto pasan por las reglas `types`
            # que lo nombren (la _rej conserva el tipo de sus particiones →
            # también mapean) ANTES de emitir el CREATE. Doc 93 D8: `columnas`
            # = las del artefacto.
            art_ctx = artifact_cols_ctx(art, cols_ctx_by_name)
            ctx = {**ctx, "columnas": list(art_ctx.values())}
            art["columns"], l_ty = render.apply_column_types(
                art["columns"], runnable_rules, art["artifact"], ctx, art_ctx, config)
            log.extend(l_ty)
        # Doc 107: el formato de las particiones lo dicta el layout de las
        # reglas (no las opciones que manda el front, que son informativas).
        sql = artifact_sql(art, meta, {**(options or {}),
                                       "partitionsInClause": bool(layout.get("partitionsInClause"))})
        if art["kind"] == "view":
            # Las reglas de COLUMNA decoran con `columnas` = las de la fuente.
            sql, sub_log = render.decorate_view_sql(
                sql, art["artifact"], runnable_rules, config, ctx, cols_ctx_by_name)
            log.extend(sub_log)
            if not sql:
                log.append({"rule": gen.get("name"), "status": "skipped", "artifact": art["artifact"],
                            "reason": f"{art['schema']}.{art['name']}: every column was excluded — view not emitted"})
                continue
            view_ctx = render.projected_cols_ctx(sql, cols_ctx_by_name)
            ctx = {**ctx, "columnas": list(view_ctx.values())}       # doc 93 D8: lo que proyecta
            t_stmts, l_t = render.table_tag_statements(
                runnable_rules, art["artifact"], ctx, config, full_art, object_kind="view",
                options=options)
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
            ctx = {**ctx, "columnas": list(art_ctx.values())}
            t_stmts, l_t = render.table_tag_statements(
                runnable_rules, art["artifact"], ctx, config, full_art, object_kind="table",
                options=options)
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
