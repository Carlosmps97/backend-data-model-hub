"""Planner — tablas (doc 55 §3.3-3.4, §4.1, §5 · doc 78): identidad por físico
(declarado o derivado, CI global) con fallback por lógico único, docs
COMPLETOS para el overlay, clasificación create / update / unchanged y
reporte de incidencias. Los defaults del perfil se aplican SOLO a tablas
nuevas; `onExistingTable: reject` corta las existentes; el `column` de cada
incidencia es la cabecera REAL del perfil. Puro.
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
    # Doc 87: vistas `_vu` de la fila (create | unchanged; nunca update).
    view_counts: dict[str, int] = field(default_factory=lambda: {"create": 0, "update": 0, "unchanged": 0})


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
              description: str | None, udp_values: dict[str, str], project_id: str) -> dict:
    """Doc COMPLETO (invariante del overlay) con la forma persistida (`schema`)
    y el proyecto del changeset (doc 75 I1)."""
    return CanonicalTableDoc.model_validate({
        "id": tid, "projectId": project_id, "physicalName": physical, "logicalName": logical,
        "schema": schema, "description": description, "udpValues": udp_values,
    }).model_dump(by_alias=True)


def normalized_existing(existing: dict) -> dict:
    return CanonicalTableDoc.model_validate(existing).model_dump(by_alias=True)


def changed_fields(before: dict, after: dict) -> list[str]:
    return [k for k in after if k != "id" and before.get(k) != after.get(k)]


def _headers(parsed: ParsedWorkbook) -> Callable[[str, str], str]:
    return lambda f, fallback: parsed.header("tables", f, fallback)


def plan_tables(parsed: ParsedWorkbook, ctx, std: Standards, schemas, udp_map: dict[str, list[dict]],
                rb: ReportBuilder, new_id: Callable[[], str], options) -> list[TablePlan]:
    index = TableIndex(ctx.tables)
    seen_phys: dict[str, int] = {}
    seen_logical: dict[str, int] = {}
    plans: list[TablePlan] = []
    max_len = std.max_len("table")
    h = _headers(parsed)

    for r in parsed.tables:
        plans.append(_plan_row(r, index, std, schemas, udp_map, rb, new_id, seen_phys, seen_logical, max_len,
                               ctx.project_id, h, options))
    return plans


def _canvas_of(r: TableRow) -> dict | None:
    return ({"project": r.project, "space": r.space, "subject": r.subject, "diagram": r.diagram}
            if any((r.project, r.space, r.subject, r.diagram)) else None)


def _plan_row(r: TableRow, index: TableIndex, std: Standards, schemas, udp_map: dict[str, list[dict]],
              rb: ReportBuilder, new_id: Callable[[], str], seen_phys: dict[str, int],
              seen_logical: dict[str, int], max_len: int, project_id: str,
              h: Callable[[str, str], str], options) -> TablePlan:
    logical = clean_text(r.logical)
    if not logical:
        rb.error(SHEET_TABLES, "missing-required", f"{h('logicalName', 'TABLA_LOGICO')} is required.",
                 row=r.row, column=h("logicalName", "TABLA_LOGICO"))
        return TablePlan(row=r.row, logical="", physical="", schema=None, id=new_id(), action="error", canvas=_canvas_of(r))

    declared = bool(clean_text(r.physical))
    physical = clean_text(r.physical) if declared else std.physicalize(logical, "table")
    existing, matched_by, ambiguous = resolve_identity(index, logical, physical, declared)
    if ambiguous:
        names = ", ".join(str(t.get("physicalName")) for t in ambiguous)
        rb.error(SHEET_TABLES, "ambiguous-match",
                 f"Several tables share the logical name '{logical}' ({names}); declare "
                 f"{h('physicalName', 'TABLA_FISICA')} to pick one.",
                 row=r.row, column=h("physicalName", "TABLA_FISICA"))
        return TablePlan(row=r.row, logical=logical, physical=physical, schema=None, id=new_id(), action="error",
                         canvas=_canvas_of(r))
    if existing is None:
        # Doc 78 S5: los defaults del perfil solo aplican a entidades NUEVAS.
        for attr, val in r.defaults.items():
            if not clean_text(getattr(r, attr, "")):
                setattr(r, attr, val)
    elif options.on_existing_table == "reject":
        rb.error(SHEET_TABLES, "existing-not-allowed",
                 f"Table '{physical}' already exists and this profile only allows new tables.",
                 row=r.row, column=h("logicalName", "TABLA_LOGICO"))
        return TablePlan(row=r.row, logical=logical, physical=physical, schema=None, id=str(existing["id"]),
                         action="error", existing=existing, canvas=_canvas_of(r))
    canvas = _canvas_of(r)
    if existing is not None and matched_by == "logical":
        physical = str(existing.get("physicalName") or physical)
        rb.warning(SHEET_TABLES, "matched-by-logical",
                   f"Row matched the existing table '{physical}' by its logical name (the derived physical "
                   f"name differs).", row=r.row, column=h("logicalName", "TABLA_LOGICO"))
    if existing is None and declared and index.by_logical.get(norm_name(logical)):
        others = ", ".join(str(t.get("physicalName")) for t in index.by_logical[norm_name(logical)])
        rb.warning(SHEET_TABLES, "logical-exists",
                   f"Another table already uses the logical name '{logical}' ({others}); a new table "
                   f"'{physical}' will be created.", row=r.row, column=h("logicalName", "TABLA_LOGICO"))

    tid = str(existing["id"]) if existing is not None else new_id()
    plan = TablePlan(row=r.row, logical=logical, physical=physical, schema=None, id=tid,
                     existing=existing, canvas=canvas)

    # Duplicados dentro del archivo (misma entidad dos veces).
    pk, lk = norm_ci(physical), norm_name(logical)
    dup_row = seen_phys.get(pk) or seen_logical.get(lk)
    if dup_row is not None:
        rb.error(SHEET_TABLES, "duplicate-in-file",
                 f"Table '{logical}' ({physical}) is repeated (see row {dup_row}).",
                 row=r.row, column=h("logicalName", "TABLA_LOGICO"))
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
                 row=r.row, column=h("physicalName", "TABLA_FISICA") if declared else h("logicalName", "TABLA_LOGICO"))

    # Esquema: obligatorio al crear; vacío conserva en update.
    schema: str | None = None
    if clean_text(r.schema):
        schema = schemas.resolve(r.schema, r.row, must_exist=options.must_exist.get("schema"),
                                 column=h("schema", "ESQUEMA"))
    elif existing is not None:
        schema = clean_text(existing.get("schema") or existing.get("sql_schema")) or None
    else:
        rb.error(SHEET_TABLES, "missing-required", f"{h('schema', 'ESQUEMA')} is required to create a table.",
                 row=r.row, column=h("schema", "ESQUEMA"))
    plan.schema = schema

    description = clean_text(r.description) or None
    if existing is not None and description is None:
        description = clean_text(existing.get("description")) or None
    udp_values = apply_udps(udp_map, r.udp, (existing or {}).get("udpValues"), rb, SHEET_TABLES, r.row,
                            udp_defaults=r.udp_defaults, is_new=existing is None)

    if rb.error_count > errors_before:
        plan.action = "error"
        return plan

    plan.doc = table_doc(tid, physical, logical, schema, description, udp_values, project_id)
    if existing is not None:   # doc 69: flags de faceta se conservan en el update
        plan.doc["logicalOnly"] = bool(existing.get("logicalOnly"))
        plan.doc["physicalOnly"] = bool(existing.get("physicalOnly"))
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
                   row=r.row, column=h("logicalName", "TABLA_LOGICO"))
    rb.warning(SHEET_TABLES, "existing-table",
               f"Table '{physical}' already exists — it will be updated ({', '.join(changed)}).",
               row=r.row, column=h("logicalName", "TABLA_LOGICO"))
    return plan
