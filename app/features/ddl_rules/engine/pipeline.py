"""Pipeline completo del export (doc 30 §8, contrato del `POST /render`). PURO
— trabaja SOLO con el payload (el front manda el estado efectivo que está
exportando, drafts incluidos) + reglas/config/defs: acá no se abre ni una
lectura de BD, mucho menos escritura (spec §13).

Orden de statements — determinista (spec §7.6):
    por cada tabla (en el orden del payload):
        CREATE base (con TBLPROPERTIES inyectadas) → ALTER column tags →
        ALTER table tags → artefactos generados (orden topológico)
    después, las vistas de negocio (en el orden del payload).
"""
from __future__ import annotations

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


def render_export(payload: dict, rules: list[dict], config: dict,
                  udp_defs: list[dict], domain_name_by_id: dict[str, str] | None = None) -> dict:
    """payload = {
        model:  {name, udpValues} | None,
        tables: [{table: {...canónico...}, columns: [...], baseSql: str}],
        views:  [{name, schema, sql, sourceTableIds: [tableId, …]}],
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
    # Doc 73: la regla de layout (particiones al final) la aplica el FRONT al
    # CREATE base (`baseSql` ya llega con ese orden); acá sólo queda registrada
    # como aplicada para el sello/log del export. Las tablas generadas emiten
    # sus particiones al final siempre (_emit_columns).
    if gen.layout_from_rules(run)["partitionsLast"]:
        log += [{"rule": r.get("name"), "status": "applied", "artifact": PHYSICAL,
                 "object": "column layout: partition columns last"}
                for r in run if ((r.get("action") or {}).get("layout") or {}).get("partitionColumns") == "last"]

    statements: list[dict] = []
    cols_ctx_by_table: dict[str, dict[str, dict]] = {}
    base_ctx_by_table: dict[str, dict] = {}

    for entry in payload.get("tables") or []:
        table = entry.get("table") or {}
        cols = entry.get("columns") or []
        t_ctx = table_ctx(table, n_by_id)
        base_ctx = {"tabla": t_ctx, "modelo": m_ctx}
        cols_ctx = {}
        for c in sorted(cols, key=lambda x: (x.get("ordinal") or 0)):
            ctx = column_ctx(c, n_by_id, dom_names)
            cols_ctx[ctx["nombre"]] = ctx
        tid = table.get("id") or t_ctx["nombre"]
        cols_ctx_by_table[tid] = cols_ctx
        base_ctx_by_table[tid] = base_ctx
        # Doc 71 H2: los ALTER … SET TAGS referencian la tabla con el MISMO
        # funnel de identificadores del CREATE (backticks + casing del export).
        full = render.full_name(t_ctx.get("esquema"), t_ctx["nombre"], export_options)

        # 1) CREATE base + TBLPROPERTIES desde UDP (spec §8.4)
        base_sql = entry.get("baseSql") or ""
        if base_sql:
            base_sql, l = render.apply_tblproperties(base_sql, run, PHYSICAL, base_ctx, config)
            log += l
            statements.append({"artifact": PHYSICAL, "schema": t_ctx.get("esquema"),
                               "name": t_ctx["nombre"], "sql": base_sql})
        # 2) tags de columna — una sentencia por columna (spec §8.2)
        stmts, l = render.column_tag_statements(run, PHYSICAL, base_ctx, cols_ctx, config, full, export_options)
        log += l
        statements += [{"artifact": f"{PHYSICAL}.tags", "schema": t_ctx.get("esquema"),
                        "name": t_ctx["nombre"], "sql": s} for s in stmts]
        # 3) tags de tabla — multi-par en una sentencia (spec §8.3)
        stmts, l = render.table_tag_statements(run, PHYSICAL, base_ctx, config, full)
        log += l
        statements += [{"artifact": f"{PHYSICAL}.tags", "schema": t_ctx.get("esquema"),
                        "name": t_ctx["nombre"], "sql": s} for s in stmts]
        # 4) generadores en cascada (spec §7.3)
        gen_stmts, l = gen.run_generators(run, table, cols, base_ctx, cols_ctx,
                                          config, export_options)
        log += l
        statements += gen_stmts

    # 5) vistas del payload. La VISTA DE NEGOCIO = la que está "on canvas"
    #    (`businessView`, default True): esas las decoran las reglas con
    #    ddl.vista_negocio en appliesTo. Las demás pasan INTACTAS al archivo.
    for view in payload.get("views") or []:
        if view.get("businessView") is False:
            statements.append({"artifact": "ddl.vista", "schema": view.get("schema"),
                               "name": view.get("name"), "sql": view.get("sql") or ""})
            continue
        src_ids = view.get("sourceTableIds") or []
        cols_ctx: dict[str, dict] = {}
        for sid in src_ids:
            for name, ctx in (cols_ctx_by_table.get(sid) or {}).items():
                cols_ctx.setdefault(name, ctx)
        base_ctx = (base_ctx_by_table.get(src_ids[0])
                    if src_ids and src_ids[0] in base_ctx_by_table
                    else {"tabla": {"nombre": "", "udp": {}}, "modelo": m_ctx})
        sql, l = render.decorate_view_sql(view.get("sql") or "", BUSINESS_VIEW,
                                          run, config, base_ctx, cols_ctx)
        log += l
        statements.append({"artifact": BUSINESS_VIEW, "schema": view.get("schema"),
                           "name": view.get("name"), "sql": sql})

    return {"statements": statements, "log": _dedup_log(log)}
