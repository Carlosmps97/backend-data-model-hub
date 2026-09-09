"""Planner de la carga masiva (doc 55 §5-6 · doc 78): del workbook ya
interpretado por el perfil (`profiles/apply`) + contexto efectivo a un `Plan`
— reporte (errores/warnings/resumen), cambios en orden de dependencia (docs
COMPLETOS) y canvases afectados. Puro y determinista (los ids nuevos se
inyectan). `PlanOptions` trae lo que el perfil decide sobre el planner:
`mustExist` por campo y las políticas `onExisting*`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable

from .context import UploadContext
from .normalize import clean_text, norm_name
from .parser import ParsedWorkbook
from .plan_columns import plan_columns
from .plan_structure import SchemaResolver, StructurePlanner, zero_counts
from .plan_tables import TableIndex, TablePlan, plan_tables, resolve_identity
from .report import SHEET_COLUMNS, SHEET_TABLES, ReportBuilder
from .standards import Standards

# Orden de dependencia para el apply (mismo criterio que VERSIONED).
_ORDER = ("projects", "folders", "subject_areas", "schemas", "canonical_tables", "canonical_columns")
_FATAL = frozenset({"missing-sheet", "empty-workbook", "missing-header"})


@dataclass
class PlanOptions:
    must_exist: dict[str, str] = field(default_factory=dict)   # field → 'error' | 'warning'
    on_existing_table: str = "update"
    on_existing_column: str = "update"

    @classmethod
    def from_profile(cls, profile: dict) -> "PlanOptions":
        must: dict[str, str] = {}
        for role in ("tables", "columns"):
            for m in (((profile.get("sheets") or {}).get(role) or {}).get("mappings") or []):
                t = m.get("target") or {}
                if t.get("kind") != "field":
                    continue
                for r in m.get("rules") or []:
                    if r.get("type") == "mustExist":
                        must[t["field"]] = r.get("severity") or "error"
        pol = profile.get("policies") or {}
        return cls(must_exist=must, on_existing_table=pol.get("onExistingTable") or "update",
                   on_existing_column=pol.get("onExistingColumn") or "update")


@dataclass
class Plan:
    report: dict
    changes: list[dict] = field(default_factory=list)
    affected_canvas_ids: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    has_errors: bool = False


def _empty_counts() -> dict:
    return {k: zero_counts() for k in ("projects", "folders", "canvases", "schemas", "tables", "columns")}


def referenced_table_ids(parsed: ParsedWorkbook, ctx: UploadContext) -> list[str]:
    """Tablas EXISTENTES que el workbook toca (para cargar sus columnas)."""
    std = Standards(ctx)
    index = TableIndex(ctx.tables)
    ids: set[str] = set()
    sheet_logicals: set[str] = set()
    for r in parsed.tables:
        logical = clean_text(r.logical)
        if not logical:
            continue
        sheet_logicals.add(norm_name(logical))
        declared = bool(clean_text(r.physical))
        physical = clean_text(r.physical) if declared else std.physicalize(logical, "table")
        hit, _, _ = resolve_identity(index, logical, physical, declared)
        if hit is not None:
            ids.add(str(hit["id"]))
    for c in parsed.columns:
        key = norm_name(c.table_logical)
        if not key or key in sheet_logicals:
            continue
        candidates = index.by_logical.get(key, [])
        if len(candidates) == 1:
            ids.add(str(candidates[0]["id"]))
    return sorted(ids)


def _canvas_label(tp: TablePlan) -> str | None:
    c = tp.canvas or {}
    parts = [clean_text(c.get(k)) for k in ("project", "space", "subject", "diagram")]
    return " / ".join(p for p in parts if p) or None


def _breakdown(table_plans: list[TablePlan], rb: ReportBuilder) -> list[dict]:
    rows = []
    for tp in table_plans:
        issues = rb.issues_in(SHEET_TABLES, [tp.row] if tp.row is not None else [])
        issues += rb.issues_in(SHEET_COLUMNS, tp.column_rows)
        rows.append({
            "row": tp.row, "logicalName": tp.logical, "physicalName": tp.physical, "schema": tp.schema,
            "action": tp.action, "canvas": _canvas_label(tp), "columns": dict(tp.column_counts), "issues": issues,
        })
    return rows


def build_plan(parsed: ParsedWorkbook, ctx: UploadContext,
               new_id: Callable[[], str] | None = None, options: PlanOptions | None = None) -> Plan:
    new_id = new_id or (lambda: str(uuid.uuid4()))
    options = options or PlanOptions()
    rb = ReportBuilder(sheet_names=parsed.sheet_names)
    rb.extend(parsed.issues)
    counts = _empty_counts()
    meta = {"profile": parsed.profile_ref, "sheets": parsed.sheets_info}
    if parsed.fatal or any(i.code in _FATAL for i in parsed.issues):
        return Plan(report=rb.build(counts, [], **meta), counts=counts, has_errors=True)

    std = Standards(ctx)
    schemas = SchemaResolver(ctx, rb, new_id)
    table_plans = plan_tables(parsed, ctx, std, schemas, parsed.table_udp, rb, new_id, options)
    column_plans = plan_columns(parsed, ctx, std, table_plans, parsed.column_udp, rb, new_id, options)

    structure = StructurePlanner(ctx, rb, new_id, headers=parsed.headers.get("tables"), must_exist=options.must_exist)
    for tp in table_plans:
        if tp.from_sheet and tp.action != "error":
            structure.place(tp)
    structure.canvas_warnings()
    diagram_header = parsed.header("tables", "diagram", "DIAGRAMA")
    for tp in table_plans:
        if tp.from_sheet and tp.action == "create" and not clean_text((tp.canvas or {}).get("diagram")):
            rb.warning(SHEET_TABLES, "no-canvas",
                       f"Table '{tp.physical}' will be created without a diagram ({diagram_header} is empty).",
                       row=tp.row, column=diagram_header)

    by_coll: dict[str, list[dict]] = {k: [] for k in _ORDER}
    for ch in structure.changes() + schemas.changes():
        by_coll[ch["collection"]].append(ch)
    for tp in table_plans:
        if tp.action in ("create", "update"):
            counts["tables"][tp.action] += 1
            by_coll["canonical_tables"].append(
                {"collection": "canonical_tables", "entityId": tp.id, "op": "upsert", "payload": tp.doc})
        elif tp.action == "unchanged" and tp.from_sheet:
            counts["tables"]["unchanged"] += 1
    for cp in column_plans:
        if cp.action in ("create", "update", "unchanged"):
            counts["columns"][cp.action] += 1
            cp.table.column_counts[cp.action] += 1
        if cp.action in ("create", "update"):
            by_coll["canonical_columns"].append(
                {"collection": "canonical_columns", "entityId": cp.id, "op": "upsert", "payload": cp.doc})
    counts.update(structure.counts())
    counts["schemas"] = schemas.counts()

    changes = [ch for coll in _ORDER for ch in by_coll[coll]]
    return Plan(report=rb.build(counts, _breakdown(table_plans, rb), **meta), changes=changes,
                affected_canvas_ids=structure.affected_canvas_ids(), counts=counts, has_errors=rb.has_errors)
