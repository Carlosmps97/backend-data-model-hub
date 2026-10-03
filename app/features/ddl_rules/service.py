"""Negocio de `ddl_rules`. La edición es VERSIONADA: pasa por
`data_standards.apply` (rulesUpsert/rulesDelete/ddlConfigPatch), como Glossary/
Parent Domains/UDP. Este service expone la lectura, la validación stateless
(bench del editor), el Test contra una tabla real, el impacto estimado y los
helpers que el apply necesita (guard de borrado de UDP, catálogo de artefactos)."""
from __future__ import annotations

import copy

import sqlglot
from fastapi import HTTPException

from app.core.audit import audit
from app.features.catalog import repository as catalog_repo
from app.features.data_standards import repository as std_repo
from app.features.domains import repository as dom_repo
from app.core.facets import physical_udp_defs
from app.features.udp import repository as udp_repo

from . import repository
from .engine import conditions as engine_cond
from .engine import generators as engine_generators
from .engine import pipeline as engine_pipeline
from .engine import render as engine_render
from .engine import validate as engine_validate
from .engine.context import column_ctx, names_by_id, table_ctx
from .engine.expressions import RenderError, build_expression, expand_template
from .models import ROOT_ARTIFACTS
from .output import effective_output
from .templates import SEED_LOOKUPS, SEED_OUTPUT, SEED_RULES, TEMPLATES


async def list_rules(project_id: str) -> list[dict]:
    return await repository.list_rules(project_id)


async def get_config(project_id: str) -> dict:
    return await repository.get_config(project_id)


async def validate_payload(project_id: str, rule: dict) -> dict:
    """Los 5 checks (spec §9) contra el catálogo REAL de UDP defs + config +
    artefactos DEL PROYECTO. NO escribe nada — es el auto-check del editor."""
    defs = await udp_repo.list_udp(project_id)
    config = await repository.get_config(project_id)
    rules = await repository.list_rules(project_id)
    arts = [a["id"] for a in artifact_catalog(rules)]
    return engine_validate.validate_rule(rule, defs, config, arts, artifact_kinds(rules))


def rules_referencing(rules: list[dict], udp_ids: set[str]) -> list[dict]:
    """Reglas (de la lista dada) que referencian alguno de `udp_ids` vía
    `udpRefs` o vía el origen de un lookup. Puro — el llamador decide el estado
    (p.ej. el apply lo evalúa sobre el estado POST-batch). Devuelve
    [{id, name}] ordenado por name."""
    hits: list[dict] = []
    for r in rules:
        refs = {ref.get("udpId") for ref in (r.get("udpRefs") or [])}
        if refs & udp_ids:
            hits.append({"id": r.get("id"), "name": r.get("name")})
    hits.sort(key=lambda h: (h.get("name") or "").lower())
    return hits


def lookups_referencing(lookups: dict, udp_ids: set[str]) -> list[str]:
    """Nombres de lookups cuyo UDP de origen está en `udp_ids`. Puro."""
    return sorted(name for name, lk in (lookups or {}).items()
                  if (lk or {}).get("fromUdpId") in udp_ids)


ROOT_KINDS = {"ddl.tabla_fisica": "table", "ddl.vista_negocio": "view"}


def artifact_catalog(rules: list[dict]) -> list[dict]:
    """Catálogo de artefactos: raíces + los declarados por generadores activos
    (`action.emit.artifact`). NO es un enum cerrado (spec §5). Cada entrada
    lleva `kind` ('table' | 'view', doc 71 H4): el validador avisa cuando una
    acción no aplica al tipo del artefacto y el editor lo muestra. Puro."""
    out = [{**a, "kind": ROOT_KINDS.get(a["id"], "table")} for a in ROOT_ARTIFACTS]
    seen = {a["id"] for a in out}
    for r in rules:
        if r.get("kind") != "generator":
            continue
        emit = (r.get("action") or {}).get("emit") or {}
        art = emit.get("artifact")
        if art and art not in seen:
            seen.add(art)
            out.append({"id": art, "label": art.removeprefix("ddl."),
                        "root": False, "generatedBy": r.get("name"),
                        "kind": engine_generators._emit_kind(emit)})
    return out


def artifact_kinds(rules: list[dict]) -> dict[str, str]:
    """{artifactId: 'table'|'view'} del catálogo. Puro."""
    return {a["id"]: a["kind"] for a in artifact_catalog(rules)}


async def templates_payload(project_id: str) -> dict:
    """Plantillas del picker 16d + semillas del spec §8 con el lookup resuelto
    a los ids REALES de UDP del proyecto (el front las aplica vía standards)."""
    defs = await udp_repo.list_udp(project_id)
    # Doc 69: las semillas enlazan la def FÍSICA (el homónimo lógico no cuenta).
    by_level_name = {(d.get("level"), d.get("name")): d.get("id") for d in physical_udp_defs(defs)}
    lookups = copy.deepcopy(SEED_LOOKUPS)
    for lk in lookups.values():
        udp_id = by_level_name.get((lk.get("fromLevel"), lk.pop("fromUdpName", None)))
        if udp_id:
            lk["fromUdpId"] = udp_id
    return {"templates": TEMPLATES, "seedRules": SEED_RULES, "seedLookups": lookups,
            "seedOutput": copy.deepcopy(SEED_OUTPUT)}


# ── Test contra una tabla real + impacto (bench del editor, 16c) ──────────


async def _engine_inputs(project_id: str) -> tuple[list[dict], dict, dict[str, str], dict[str, str]]:
    defs = await udp_repo.list_udp(project_id)
    config = await repository.get_config(project_id)
    n_by_id = names_by_id(defs)
    config = {**config, "lookups": engine_render.resolve_lookup_names(
        config.get("lookups") or {}, n_by_id)}
    domains = {d["id"]: d["name"] for d in await dom_repo.list_domains(project_id)}
    return defs, config, n_by_id, domains


def _why(rule_condition: str, ctx: dict) -> str | None:
    """Detalle legible de por qué matcheó: el primer UDP citado y su valor en
    el objeto (como el mock 16c: `criticidad = 'DAC'`)."""
    try:
        ast = engine_cond.parse_condition(rule_condition)
        for base, name in engine_cond.udp_names_used(ast, rule_condition):
            val = ((ctx.get(base) or {}).get("udp") or {}).get(name)
            if val is not None:
                return f"{name} = '{val}'"
    except engine_cond.CondError:
        return None
    return None


async def test_rule(project_id: str, rule: dict, table_id: str) -> dict:
    """Corre la regla contra UNA tabla real del catálogo publicado (spec 15.4).
    Devuelve los FRAGMENTOS generados de las columnas/objetos que matchean —
    no el DDL completo (eso es del export)."""
    defs, config, n_by_id, domains = await _engine_inputs(project_id)
    # Doc 93 D1: el bench muestra los fragmentos con las Output settings del
    # proyecto (mismo estilo que el export por default).
    bench = effective_output(config.get("output"))
    tables = await catalog_repo.list_tables_by_ids([table_id])
    if not tables:
        raise HTTPException(status_code=404, detail="That table doesn't exist.")
    table = tables[0]
    cols = await catalog_repo.list_columns(table_id)
    t_ctx = table_ctx(table, n_by_id)
    cols_sorted = sorted(cols, key=lambda c: (c.get("ordinal") or 0))
    cols_ctx = {}
    for c in cols_sorted:
        ctx = column_ctx(c, n_by_id, domains)
        cols_ctx[ctx["nombre"]] = ctx
    full = f"{t_ctx['esquema']}.{t_ctx['nombre']}" if t_ctx.get("esquema") else t_ctx["nombre"]
    # Los fragmentos de tags salen calificados como en el export (doc 71 H2),
    # con el casing por default del export (doc 76 D9).
    full_ident = engine_render.full_name(t_ctx.get("esquema"), t_ctx["nombre"], bench)
    artifact = (rule.get("appliesTo") or ["ddl.tabla_fisica"])[0]
    # Doc 76 D5: el fragmento respeta el TIPO del artefacto destino (ALTER
    # TABLE | ALTER VIEW). El bench muestra el objeto con el nombre de la tabla
    # elegida (la vista generada real nace recién en el export).
    saved_rules = await repository.list_rules(project_id)
    obj_kind = artifact_kinds(saved_rules).get(artifact) \
        or ("view" if "vista" in artifact else "table")
    base_ctx = {"tabla": t_ctx, "modelo": {"nombre": "", "udp": {}},
                "columnas": list(cols_ctx.values()),                  # doc 93 D8: ANY_COLUMN
                "artefacto": engine_render.artifact_ctx(t_ctx.get("esquema"), t_ctx["nombre"], obj_kind,
                                                        bench)}
    lookups = config.get("lookups") or {}
    functions = config.get("functions") or []
    fragments: list[dict] = []
    matched = 0

    try:
        cond_ast = engine_cond.parse_condition(rule.get("condition") or "")
        if rule.get("kind") == "generator":
            # Revisión doc 107: la tabla generada sale con el layout de las
            # reglas del PROYECTO (dónde van las particiones y su UDP), como en
            # el export — el generador solo no lo declara.
            layout, _ = engine_pipeline.effective_layout(engine_render.runnable(saved_rules)[0], defs)
            stmts, _ = engine_generators.run_generators(
                [rule], table, cols_sorted, base_ctx, cols_ctx, config, bench, layout)
            matched = 1 if stmts else 0
            fragments = [{"label": s["artifact"], "sql": s["sql"]} for s in stmts]
            total = 1
        elif rule.get("target") == "table":
            total = 1
            if engine_cond.eval_condition(cond_ast, base_ctx, rule.get("condition") or ""):
                matched = 1
                action = rule.get("action") or {}
                why = _why(rule.get("condition") or "", base_ctx)
                if action.get("statements"):
                    # Doc 76 D6: sentencias libres expandidas (before → after).
                    for position in ("before", "after"):
                        stmts, _ = engine_render.statement_snippets(
                            [rule], artifact, base_ctx, config, position)
                        fragments += [{"label": f"{full} · {position}", "sql": s, "why": why} for s in stmts]
                if action.get("tags"):
                    stmts, _ = engine_render.table_tag_statements(
                        [rule], artifact, base_ctx, config, full_ident, object_kind=obj_kind,
                        options=bench)
                    fragments += [{"label": full, "sql": s, "why": why} for s in stmts]
                if action.get("tblproperties"):
                    pairs = []
                    for k in sorted(action["tblproperties"]):
                        val = expand_template(str(action["tblproperties"][k]),
                                              base_ctx, lookups, functions)
                        if val is not None:
                            pairs.append(f"'{k}' = '{val}'")
                    if pairs:
                        fragments.append({"label": full,
                                          "sql": "TBLPROPERTIES (" + ", ".join(pairs) + ")",
                                          "why": why})
                if action.get("layout"):
                    # Doc 73/76/93: con UDP, las particiones salen de sus PART_nn.
                    # Doc 107: con PARTITIONED BY la lista no las lleva — van ahí
                    # con su tipo —; sin él, al final (`last`) o en orden físico.
                    lay = action["layout"]
                    part_udp = engine_generators.partition_udp_of(lay)
                    cols_light = engine_generators.physical_columns(cols_sorted, cols_ctx, part_udp)
                    last = lay.get("partitionColumns") == "last"
                    parts = engine_generators.ordered_partitions(cols_light)
                    in_clause = engine_generators.partitions_in_clause(cols_light, bench)
                    emitted = ([c for c in cols_light if not c.get("partition")] if in_clause
                               else engine_generators.layout_columns(cols_light, {"partitionsLast": last}))
                    moved = [c["name"] for c in parts]
                    if not moved:
                        detail = "no partition columns in this table"
                    elif in_clause:
                        detail = "partition columns only in PARTITIONED BY: " + ", ".join(moved)
                    else:
                        where = "last" if last else "in physical order"
                        detail = f"partition columns {where} (this export has no PARTITIONED BY): " + ", ".join(moved)
                    if moved and part_udp:
                        detail += f" · from UDP '{part_udp}'"
                    sql = "-- column order: " + ", ".join(c["name"] for c in emitted)
                    if in_clause:
                        sql += "\n-- PARTITIONED BY (" + ", ".join(
                            f"{c['name']} {engine_render.type_text(c['type'], bench)}" for c in parts) + ")"
                    fragments.append({"label": full, "sql": sql, "why": detail})
        else:  # regla de columna
            total = len(cols_sorted)
            action = rule.get("action") or {}
            expr_tpl = (action.get("expression") or "").strip()
            alias_tpl = (action.get("alias") or "").strip()
            # Doc 90 · Data types: el bench lista SOLO las columnas cuyo tipo cambia.
            type_maps = [(rule.get("name"), engine_render.type_map_of(rule))] if action.get("types") else []
            for name, ctx in cols_ctx.items():
                cctx = {**base_ctx, "columna": ctx}
                if not engine_cond.eval_condition(cond_ast, cctx, rule.get("condition") or ""):
                    continue
                if type_maps:
                    new_type, applied = engine_render.map_type_text(ctx.get("tipo") or "", type_maps)
                    if not applied:
                        continue
                    matched += 1
                    fragments.append({"column": name,
                                      "sql": f"{engine_render.ident(name, bench)} "
                                             f"{engine_render.type_text(new_type, bench)}",
                                      "why": f"{ctx.get('tipo')} → {new_type}"})
                    continue
                matched += 1
                why = _why(rule.get("condition") or "", cctx)
                if action.get("exclude") is True:
                    fragments.append({"column": name, "sql": "-- excluded from the SELECT", "why": why})
                elif expr_tpl:
                    # La proyección llega al motor con el casing del export
                    # (doc 76 D8): el bench la muestra igual.
                    col_name = engine_render.cased(name, bench)
                    ast = build_expression(expr_tpl, cctx, sqlglot.parse_one(col_name, read="databricks"),
                                           lookups, functions)
                    alias = expand_template(alias_tpl, cctx, lookups, functions) if alias_tpl else col_name
                    if alias and alias.lower() == col_name.lower():
                        alias = col_name              # `{columna.nombre}` = identidad (no pisa el casing)
                    sql = ast.sql(dialect="databricks", normalize_functions="lower")
                    fragments.append({"column": name, "sql": f"{sql} AS {alias or col_name}", "why": why})
                elif action.get("tags"):
                    stmts, _ = engine_render.column_tag_statements(
                        [rule], artifact, base_ctx, {name: ctx}, config, full_ident, bench,
                        object_kind=obj_kind)
                    fragments += [{"column": name, "sql": s, "why": why} for s in stmts]
                else:
                    fragments.append({"column": name, "sql": "— (no expression/tags)", "why": why})
    except (engine_cond.CondError, RenderError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"table": full, "matched": matched, "total": total, "fragments": fragments}


async def render_export_payload(actor: str, project_id: str, body: dict) -> dict:
    """Puente del export (doc 30 §8): aplica las reglas SELECCIONADAS al payload
    que manda el canvas (estado efectivo, drafts incluidos) vía el pipeline
    PURO, y estampa la versión de standards usada — la evidencia de por qué el
    DDL de marzo salió distinto al de junio (spec §12)."""
    defs = await udp_repo.list_udp(project_id)
    config = await repository.get_config(project_id)
    domains = {d["id"]: d["name"] for d in await dom_repo.list_domains(project_id)}
    wanted = set(body.get("ruleIds") or [])
    rules = [r for r in await repository.list_rules(project_id) if r["id"] in wanted]
    payload = {
        "model": body.get("model"),
        "options": body.get("options") or {},
        "tables": [{"table": t.get("table") or {}, "columns": t.get("columns") or [],
                    "baseSql": t.get("baseSql") or ""} for t in body.get("tables") or []],
        "views": [{"name": v.get("name"), "schema": v.get("schema") or v.get("schema_"),
                   "sql": v.get("sql") or "", "sourceTableIds": v.get("sourceTableIds") or [],
                   "businessView": v.get("businessView", True)}
                  for v in body.get("views") or []],
    }
    out = engine_pipeline.render_export(payload, rules, config, defs, domains)
    versions = await std_repo.list_versions(project_id)
    label = versions[0].get("label") if versions else "v0"
    applied = sum(1 for e in out["log"] if e.get("status") == "applied")
    skipped = sum(1 for e in out["log"] if e.get("status") == "skipped")
    await audit(actor, "ddl.export_render", target=label, target_type="standards_version",
                meta={"projectId": project_id, "rules": len(rules), "applied": applied,
                      "skipped": skipped, "tables": len(payload["tables"]),
                      "views": len(payload["views"])})
    return {**out, "rulesetVersion": label, "applied": applied, "skipped": skipped}


async def impact(project_id: str, rule: dict) -> dict:
    """'Matches 43 columns across 12 tables' — barrido liviano del catálogo
    publicado DEL PROYECTO. A la escala actual es instantáneo; si el catálogo
    crece a millones, este es el punto para el fast-path Mongo + cache."""
    defs, config, n_by_id, domains = await _engine_inputs(project_id)
    try:
        cond_ast = engine_cond.parse_condition(rule.get("condition") or "")
        tables = await repository.tables_light(project_id)
        t_ctx_by_id = {t["id"]: table_ctx(t, n_by_id) for t in tables}
        if rule.get("kind") == "generator" or rule.get("target") == "table":
            # Doc 93 D8: ANY_COLUMN evalúa las columnas de cada tabla (estimado:
            # para reglas de vista cuenta las columnas FÍSICAS de su tabla).
            cols_by_table: dict[str, list[dict]] = {}
            if "ANY_COLUMN" in (rule.get("condition") or "").upper():
                for c in await repository.columns_light(project_id):
                    cols_by_table.setdefault(c.get("tableId"), []).append(column_ctx(c, n_by_id, domains))
            hit = 0
            for tid, t_ctx in t_ctx_by_id.items():
                ctx = {"tabla": t_ctx, "modelo": {"nombre": "", "udp": {}}, "columnas": cols_by_table.get(tid, [])}
                if engine_cond.eval_condition(cond_ast, ctx, rule.get("condition") or ""):
                    hit += 1
            return {"columns": 0, "tables": hit}
        cols = await repository.columns_light(project_id)
        # Doc 90 · Data types: cuenta SOLO las columnas cuyo tipo cambia.
        type_maps = ([(rule.get("name"), engine_render.type_map_of(rule))]
                     if (rule.get("action") or {}).get("types") else [])
        col_ctx = {id(c): column_ctx(c, n_by_id, domains) for c in cols}
        # Doc 93 D8/§7: una regla de COLUMNA también puede usar ANY_COLUMN — ve
        # las columnas de la tabla de cada columna (final review #3).
        cols_by_table: dict[str, list[dict]] = {}
        if "ANY_COLUMN" in (rule.get("condition") or "").upper():
            for c in cols:
                cols_by_table.setdefault(c.get("tableId"), []).append(col_ctx[id(c)])
        matched_cols = 0
        matched_tables: set[str] = set()
        for c in cols:
            t_ctx = t_ctx_by_id.get(c.get("tableId"))
            if t_ctx is None:
                continue
            ctx = {"tabla": t_ctx, "modelo": {"nombre": "", "udp": {}},
                   "columna": col_ctx[id(c)], "columnas": cols_by_table.get(c.get("tableId"), [])}
            if engine_cond.eval_condition(cond_ast, ctx, rule.get("condition") or ""):
                if type_maps and not engine_render.map_type_text(
                        ctx["columna"].get("tipo") or "", type_maps)[1]:
                    continue
                matched_cols += 1
                matched_tables.add(c["tableId"])
        return {"columns": matched_cols, "tables": len(matched_tables)}
    except engine_cond.CondError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
