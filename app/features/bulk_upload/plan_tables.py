"""Planner — tablas (doc 55 §3.3-3.4, §4.1, §5): identidad por físico
(declarado o derivado, CI global) con fallback por lógico único, docs
COMPLETOS para el overlay, clasificación create / update / unchanged y
reporte de incidencias. Puro.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.features.catalog.models import CanonicalTableDoc

from .normalize import clean_text, norm_ci, norm_name
from .parser import ParsedWorkbook, TableRow
from .report import SHEET_TABLES, ReportBuilder
from .standards import Standards, apply_udps


@dataclass
class TablePlan:
    row: int | None
    logical: str
    physical: str
    schema: str | None
    id: str
    action: str = "create"                 # create | update | unchanged | error
    existing: dict | None = None
    doc: dict | None = None
    from_sheet: bool = True
    canvas: dict | None = None             # {project, space, subject, diagram} crudos
    column_counts: dict[str, int] = field(default_factory=lambda: {"create": 0, "update": 0, "unchanged": 0})
    column_rows: list[int] = field(default_factory=list)


class TableIndex:
    """Pool efectivo indexado por físico (CI) y por lógico (nombre normalizado)."""

    def __init__(self, tables: list[dict]) -> None:
        self.by_phys: dict[str, dict] = {}
        self.by_logical: dict[str, list[dict]] = {}
        for t in tables:
            self.by_phys.setdefault(norm_ci(t.get("physicalName")), t)
            self.by_logical.setdefault(norm_name(t.get("logicalName")), []).append(t)


def resolve_identity(index: TableIndex, logical: str, physical: str,
                     declared: bool) -> tuple[dict | None, str | None, list[dict]]:
    """(tabla existente, 'physical'|'logical'|None, candidatos ambiguos).
    Físico declarado → SOLO por físico (el modelador nombró la entidad)."""
    hit = index.by_phys.get(norm_ci(physical))
    if hit is not None:
        return hit, "physical", []
    if declared:
        return None, None, []
    candidates = index.by_logical.get(norm_name(logical), [])
    if len(candidates) == 1:
        return candidates[0], "logical", []
    return None, None, candidates


def table_doc(tid: str, physical: str, logical: str, schema: str | None,
              description: str | None, udp_values: dict[str, str]) -> dict:
    """Doc COMPLETO (invariante del overlay) con la forma persistida (`schema`)."""
    return CanonicalTableDoc.model_validate({
        "id": tid, "physicalName": physical, "logicalName": logical, "schema": schema,
        "description": description, "udpValues": udp_values,
    }).model_dump(by_alias=True)


def normalized_existing(existing: dict) -> dict:
    return CanonicalTableDoc.model_validate(existing).model_dump(by_alias=True)


def changed_fields(before: dict, after: dict) -> list[str]:
    return [k for k in after if k != "id" and before.get(k) != after.get(k)]


def plan_tables(parsed: ParsedWorkbook, ctx, std: Standards, schemas, udp_map: dict[str, dict],
                rb: ReportBuilder, new_id: Callable[[], str]) -> list[TablePlan]:
    index = TableIndex(ctx.tables)
    seen_phys: dict[str, int] = {}
    seen_logical: dict[str, int] = {}
    plans: list[TablePlan] = []
    max_len = std.max_len("table")

    for r in parsed.tables:
        plans.append(_plan_row(r, index, std, schemas, udp_map, rb, new_id, seen_phys, seen_logical, max_len))
    return plans


def _plan_row(r: TableRow, index: TableIndex, std: Standards, schemas, udp_map: dict[str, dict],
              rb: ReportBuilder, new_id: Callable[[], str], seen_phys: dict[str, int],
              seen_logical: dict[str, int], max_len: int) -> TablePlan:
    canvas = ({"project": r.project, "space": r.space, "subject": r.subject, "diagram": r.diagram}
              if any((r.project, r.space, r.subject, r.diagram)) else None)
    logical = clean_text(r.logical)
    if not logical:
        rb.error(SHEET_TABLES, "missing-required", "TABLA_LOGICO is required.", row=r.row, column="TABLA_LOGICO")
        return TablePlan(row=r.row, logical="", physical="", schema=None, id=new_id(), action="error", canvas=canvas)

    declared = bool(clean_text(r.physical))
    physical = clean_text(r.physical) if declared else std.physicalize(logical, "table")
    existing, matched_by, ambiguous = resolve_identity(index, logical, physical, declared)
    if ambiguous:
        names = ", ".join(str(t.get("physicalName")) for t in ambiguous)
        rb.error(SHEET_TABLES, "ambiguous-match",
                 f"Several tables share the logical name '{logical}' ({names}); declare TABLA_FISICA to pick one.",
                 row=r.row, column="TABLA_FISICA")
        return TablePlan(row=r.row, logical=logical, physical=physical, schema=None, id=new_id(), action="error", canvas=canvas)
    if existing is not None and matched_by == "logical":
        physical = str(existing.get("physicalName") or physical)
        rb.warning(SHEET_TABLES, "matched-by-logical",
                   f"Row matched the existing table '{physical}' by its logical name (the derived physical "
                   f"name differs).", row=r.row, column="TABLA_LOGICO")
    if existing is None and declared and index.by_logical.get(norm_name(logical)):
        others = ", ".join(str(t.get("physicalName")) for t in index.by_logical[norm_name(logical)])
        rb.warning(SHEET_TABLES, "logical-exists",
                   f"Another table already uses the logical name '{logical}' ({others}); a new table "
                   f"'{physical}' will be created.", row=r.row, column="TABLA_LOGICO")

    tid = str(existing["id"]) if existing is not None else new_id()
    plan = TablePlan(row=r.row, logical=logical, physical=physical, schema=None, id=tid,
                     existing=existing, canvas=canvas)

    # Duplicados dentro del archivo (misma entidad dos veces).
    pk, lk = norm_ci(physical), norm_name(logical)
    dup_row = seen_phys.get(pk) or seen_logical.get(lk)
    if dup_row is not None:
        rb.error(SHEET_TABLES, "duplicate-in-file",
                 f"Table '{logical}' ({physical}) is repeated (see row {dup_row}).", row=r.row, column="TABLA_LOGICO")
        plan.action = "error"
        return plan
    seen_phys[pk] = r.row
    seen_logical[lk] = r.row

    errors_before = rb.error_count
    # Longitud del físico (al crear o renombrar; heredado sin cambios no penaliza).
    current_phys = str(existing.get("physicalName") or "") if existing else None
    if max_len and len(physical) > max_len and physical != current_phys:
        rb.error(SHEET_TABLES, "name-too-long",
                 f"Physical name '{physical}' has {len(physical)} characters, over the {max_len}-character limit.",
                 row=r.row, column="TABLA_FISICA" if declared else "TABLA_LOGICO")

    # Esquema: obligatorio al crear; vacío conserva en update.
    schema: str | None = None
    if clean_text(r.schema):
        schema = schemas.resolve(r.schema, r.row)
    elif existing is not None:
        schema = clean_text(existing.get("schema") or existing.get("sql_schema")) or None
    else:
        rb.error(SHEET_TABLES, "missing-required", "ESQUEMA is required to create a table.",
                 row=r.row, column="ESQUEMA")
    plan.schema = schema

    description = clean_text(r.description) or None
    if existing is not None and description is None:
        description = clean_text(existing.get("description")) or None
    udp_values = apply_udps(udp_map, r.udp, (existing or {}).get("udpValues"), rb, SHEET_TABLES, r.row)

    if rb.error_count > errors_before:
        plan.action = "error"
        return plan

    plan.doc = table_doc(tid, physical, logical, schema, description, udp_values)
    if existing is None:
        plan.action = "create"
        return plan

    before = normalized_existing(existing)
    changed = changed_fields(before, plan.doc)
    if not changed:
        plan.action = "unchanged"
        plan.doc = None
        return plan
    plan.action = "update"
    if "logicalName" in changed or "physicalName" in changed:
        rb.warning(SHEET_TABLES, "rename",
                   f"Table '{before.get('physicalName')}' will be renamed "
                   f"({before.get('logicalName')} → {logical}, {before.get('physicalName')} → {physical}).",
                   row=r.row, column="TABLA_LOGICO")
    rb.warning(SHEET_TABLES, "existing-table",
               f"Table '{physical}' already exists — it will be updated ({', '.join(changed)}).",
               row=r.row, column="TABLA_LOGICO")
    return plan
