"""Pipeline completo del export (doc 30 §8, contrato del `POST /render`). PURO
— trabaja SOLO con el payload (el front manda el estado efectivo que está
exportando, drafts incluidos) + reglas/config/defs: acá no se abre ni una
lectura de BD, mucho menos escritura (spec §13).

Orden de statements — determinista (spec §7.6, ajustado a la macro BCP en el
doc 76):
    por cada tabla (en el orden del payload):
        [sentencias `before`] + CREATE base (tipos de columna mapeados, doc 90,
        y TBLPROPERTIES inyectadas) →
        ALTER … SET TAGS de tabla (una sentencia por regla) →
        ALTER … ALTER COLUMN … SET TAGS (una por columna) →
        sentencias `after` → artefactos generados (orden topológico, cada uno
        con sus propios tags/sentencias)
    después, las vistas de negocio (en el orden del payload), cada una con sus
    `ALTER TABLE … SET TAGS` de objeto y de columna (doc 93 D6: `ALTER VIEW` solo
    con la Output setting `viewTagsAs = 'view'`).
"""
from __future__ import annotations

from app.core.column_order import display_order
from app.core.facets import is_logical_udp

from . import generators as gen
from . import render
from .context import column_ctx, model_ctx, names_by_id, table_ctx

PHYSICAL = "ddl.tabla_fisica"
BUSINESS_VIEW = "ddl.vista_negocio"


def _dedup_log(entries: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for e in entries:
        key = (e.get("rule"), e.get("status"), e.get("reason"),
               e.get("column"), e.get("artifact"), e.get("object"))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _layout_log(run: list[dict], layout: dict) -> list[dict]:
    """Doc 73/76: la regla de layout la aplica el FRONT al CREATE base (`baseSql`
    ya llega con ese orden); acá queda registrada como aplicada para el sello/
    log del export. Las tablas generadas emiten sus particiones al final siempre
    (`artifact_sql`) y ordenadas por el UDP cuando la regla lo declara."""
    out: list[dict] = []
    for r in run:
        lay = (r.get("action") or {}).get("layout") or {}
        if not lay or "ddl.tabla_fisica" not in (r.get("appliesTo") or []):
            continue
        parts: list[str] = []
        if lay.get("partitionColumns") == "last":
            parts.append("partition columns last")
        udp = gen.partition_udp_of(lay)
        if udp and udp == layout.get("partitionUdp"):
            parts.append(f"partitions from UDP '{udp}'")
        if parts:
            out.append({"rule": r.get("name"), "status": "applied", "artifact": PHYSICAL,
                        "object": "column layout: " + " · ".join(parts)})
    return out


def _addenda(artifact: str, schema: str | None, name: str,
             tags: list[str], extra: list[str]) -> list[dict]:
    """Statements anexos de un objeto: `<artefacto>.tags` y `<artefacto>.extra`
    (el bundle del front los pliega al archivo del objeto)."""
    return ([{"artifact": f"{artifact}.tags", "schema": schema, "name": name, "sql": s} for s in tags]
            + [{"artifact": f"{artifact}.extra", "schema": schema, "name": name, "sql": s} for s in extra])


def render_export(payload: dict, rules: list[dict], config: dict,
                  udp_defs: list[dict], domain_name_by_id: dict[str, str] | None = None) -> dict:
    """payload = {
        model:  {name, udpValues} | None,
        options: {identifierCase, tableFormat, external, location,
                  locationFolderCase, includePartitions, …},
        tables: [{table: {...canónico...}, columns: [...], baseSql: str}],
        views:  [{name, schema, sql, sourceTableIds: [tableId, …], businessView}],
    } → {statements: [{artifact, schema?, name, sql}], log: [...]}"""
    n_by_id = names_by_id(udp_defs)
    dom_names = domain_name_by_id or {}
    config = {**(config or {}),
              "lookups": render.resolve_lookup_names((config or {}).get("lookups") or {}, n_by_id)}
    run, skipped = render.runnable(rules)
    log: list[dict] = list(skipped)
    m_ctx = model_ctx(payload.get("model"), n_by_id)
    # Opciones del export del canvas: los artefactos generados salen ESPEJO del
    # físico (casing/USING/particiones/LOCATION — pedido owner 07-20).
    export_options = payload.get("options") or {}
    # Doc 93 D10: el UDP de particiones debe existir en el proyecto como def de
    # COLUMNA física — la MISMA regla que el front (`withUdpPartitions`), así la
    # física y la _rej nunca eligen particiones distintas (final review #7); si
    # no, las particiones vuelven al flag de la plataforma y queda en el log.
    layout = gen.layout_from_rules(run)
    part_udp = layout.get("partitionUdp")
    if part_udp and not any(d.get("name") == part_udp and (d.get("level") or "column") == "column"
                            and not is_logical_udp(d) for d in udp_defs or []):
        log.append({"rule": None, "status": "skipped", "artifact": PHYSICAL,
                    "reason": f"Partition UDP '{part_udp}' doesn't exist in this project — partitions "
                              "come from the platform's partition flag"})
        layout = {**layout, "partitionUdp": None}
    log += _layout_log(run, layout)

    statements: list[dict] = []
    cols_ctx_by_table: dict[str, dict[str, dict]] = {}
    base_ctx_by_table: dict[str, dict] = {}

    for entry in payload.get("tables") or []:
        table = entry.get("table") or {}
        cols = entry.get("columns") or []
        t_ctx = table_ctx(table, n_by_id)
        cols_ctx = {}
        for c in display_order(cols):                        # doc 94: orden único, PK primero
            col = column_ctx(c, n_by_id, dom_names)
            cols_ctx[col["nombre"]] = col
        # Doc 93 D8: `columnas` = las columnas del objeto en curso (ANY_COLUMN).
        base_ctx = {"tabla": t_ctx, "modelo": m_ctx, "columnas": list(cols_ctx.values())}
        tid = table.get("id") or t_ctx["nombre"]
        cols_ctx_by_table[tid] = cols_ctx
        base_ctx_by_table[tid] = base_ctx
        # Doc 71 H2: los ALTER … SET TAGS referencian la tabla con el MISMO
        # funnel de identificadores del CREATE (backticks + casing del export).
        full = render.full_name(t_ctx.get("esquema"), t_ctx["nombre"], export_options)
        ctx = {**base_ctx, "artefacto": render.artifact_ctx(t_ctx.get("esquema"), t_ctx["nombre"],
                                                              "table", export_options)}

        # 1) [before] + CREATE base + TBLPROPERTIES desde UDP (spec §8.4)
        base_sql = entry.get("baseSql") or ""
        if base_sql:
            # Doc 90: tipos de columna (CHAR → VARCHAR) ANTES de decorar el CREATE
            base_sql, l = render.apply_column_types_sql(base_sql, run, PHYSICAL, ctx, cols_ctx, config)
            log += l
            base_sql, l = render.apply_tblproperties(base_sql, run, PHYSICAL, ctx, config)
            log += l
            before, l = render.statement_snippets(run, PHYSICAL, ctx, config, "before")
            log += l
            if before:
                base_sql = "\n".join(before) + "\n" + base_sql
            statements.append({"artifact": PHYSICAL, "schema": t_ctx.get("esquema"),
                               "name": t_ctx["nombre"], "sql": base_sql})
        # 2) tags de tabla — una sentencia por regla (doc 76 D4)
        t_stmts, l = render.table_tag_statements(run, PHYSICAL, ctx, config, full, object_kind="table",
                                                 options=export_options)
        log += l
        # 3) tags de columna — una sentencia por columna (spec §8.2)
        c_stmts, l = render.column_tag_statements(run, PHYSICAL, ctx, cols_ctx, config, full,
                                                  export_options, object_kind="table")
        log += l
        # 4) sentencias libres `after`
        after, l = render.statement_snippets(run, PHYSICAL, ctx, config, "after")
        log += l
        statements += _addenda(PHYSICAL, t_ctx.get("esquema"), t_ctx["nombre"], t_stmts + c_stmts, after)
        # 5) generadores en cascada (spec §7.3)
        gen_stmts, l = gen.run_generators(run, table, cols, base_ctx, cols_ctx,
                                          config, export_options, layout)
        log += l
        statements += gen_stmts

    # 6) vistas del payload. La VISTA DE NEGOCIO = la que está "on canvas"
    #    (`businessView`, default True): esas las decoran las reglas con
    #    ddl.vista_negocio en appliesTo (expresiones, exclusiones, tags de
    #    objeto y de columna). Las demás pasan INTACTAS al archivo.
    for view in payload.get("views") or []:
        if view.get("businessView") is False:
            statements.append({"artifact": "ddl.vista", "schema": view.get("schema"),
                               "name": view.get("name"), "sql": view.get("sql") or ""})
            continue
        src_ids = view.get("sourceTableIds") or []
        cols_ctx: dict[str, dict] = {}
        for sid in src_ids:
            for name, ctx_c in (cols_ctx_by_table.get(sid) or {}).items():
                cols_ctx.setdefault(name, ctx_c)
        base_ctx = (base_ctx_by_table.get(src_ids[0])
                    if src_ids and src_ids[0] in base_ctx_by_table
                    else {"tabla": {"nombre": "", "udp": {}}, "modelo": m_ctx, "columnas": []})
        view_sql = view.get("sql") or ""
        # El objeto se referencia EXACTO como lo creó el front (esquema por
        # default incluido): se lee del propio CREATE VIEW.
        v_full = (render.view_target(view_sql)
                  or render.full_name(view.get("schema"), view.get("name") or "", export_options))
        v_ctx = {**base_ctx, "artefacto": {**render.artifact_ctx(view.get("schema"), view.get("name") or "",
                                                                  "view", export_options), "ref": v_full}}
        sql, l = render.decorate_view_sql(view_sql, BUSINESS_VIEW, run, config, v_ctx, cols_ctx)
        log += l
        if not sql:
            log.append({"rule": None, "status": "skipped", "artifact": BUSINESS_VIEW,
                        "reason": f"{view.get('name')}: every column was excluded — view not emitted"})
            continue
        statements.append({"artifact": BUSINESS_VIEW, "schema": view.get("schema"),
                           "name": view.get("name"), "sql": sql})
        proj_ctx = render.projected_cols_ctx(sql, cols_ctx)
        # Doc 93 D8: para las reglas de OBJETO, `columnas` = lo que la vista
        # proyecta; si su SQL no se puede leer, se desconoce (ANY_COLUMN se salta).
        obj_ctx = {**v_ctx, "columnas": list(proj_ctx.values()) if render.readable_select(sql) else None}
        t_stmts, l = render.table_tag_statements(run, BUSINESS_VIEW, obj_ctx, config, v_full, object_kind="view",
                                                 options=export_options)
        log += l
        c_stmts, l = render.column_tag_statements(run, BUSINESS_VIEW, obj_ctx, proj_ctx, config, v_full,
                                                  export_options, object_kind="view")
        log += l
        after, l = render.statement_snippets(run, BUSINESS_VIEW, obj_ctx, config, "after")
        log += l
        statements += _addenda(BUSINESS_VIEW, view.get("schema"), view.get("name") or "",
                               t_stmts + c_stmts, after)

    return {"statements": statements, "log": _dedup_log(log)}
