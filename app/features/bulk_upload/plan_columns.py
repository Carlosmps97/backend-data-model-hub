"""Planner — columnas (doc 55 §3.3-3.4, §4.2, §5): la fila referencia su
tabla por lógico (fila de `Tablas` o tabla efectiva única); identidad por
físico (declarado/derivado) con fallback por lógico único; tipo heredado del
parent domain o declarado (gramática de la plataforma); PK autoritativa con
`pkPosition` por orden de la hoja; UDP por definición. Puro.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.features.catalog.models import CanonicalColumnDoc

from .normalize import clean_text, norm_ci, norm_key, norm_name, norm_type, partition_correlative
from .parser import ColumnRow, ParsedWorkbook
from .plan_tables import TableIndex, TablePlan, changed_fields
from .report import SHEET_COLUMNS, ReportBuilder
from .standards import Standards, apply_udps

_PARTITION_KEY = "particion"   # norm_key del UDP «Particion»


@dataclass
class ColumnPlan:
    row: int
    table: TablePlan
    id: str
    action: str                      # create | update | unchanged | error
    existing: dict | None = None
    doc: dict | None = None
    pk: bool = False
    fields: dict | None = None       # campos resueltos antes del cierre (pkPosition)


def column_doc(fields: dict) -> dict:
    return CanonicalColumnDoc.model_validate(fields).model_dump()


def normalized_existing(existing: dict) -> dict:
    return CanonicalColumnDoc.model_validate(existing).model_dump()


def _resolve_table(r: ColumnRow, by_sheet: dict[str, TablePlan], index: TableIndex,
                   implicit: dict[str, TablePlan], table_plans: list[TablePlan],
                   rb: ReportBuilder) -> TablePlan | None:
    key = norm_name(r.table_logical)
    if not key:
        rb.error(SHEET_COLUMNS, "missing-required", "TABLA_LOGICO is required.", row=r.row, column="TABLA_LOGICO")
        return None
    tp = by_sheet.get(key)
    if tp is not None:
        return tp
    candidates = index.by_logical.get(key, [])
    if len(candidates) == 1:
        doc = candidates[0]
        tid = str(doc["id"])
        if tid not in implicit:
            implicit[tid] = TablePlan(row=None, logical=str(doc.get("logicalName") or ""),
                                      physical=str(doc.get("physicalName") or ""),
                                      schema=doc.get("schema"), id=tid, action="unchanged",
                                      existing=doc, from_sheet=False)
            table_plans.append(implicit[tid])
        return implicit[tid]
    if len(candidates) > 1:
        names = ", ".join(str(t.get("physicalName")) for t in candidates)
        rb.error(SHEET_COLUMNS, "ambiguous-table",
                 f"Several tables share the logical name '{r.table_logical}' ({names}); add the table to the "
                 "'Tablas' sheet with its TABLA_FISICA.", row=r.row, column="TABLA_LOGICO")
        return None
    rb.error(SHEET_COLUMNS, "unknown-table",
             f"Table '{r.table_logical}' is neither in the 'Tablas' sheet nor in the model.",
             row=r.row, column="TABLA_LOGICO")
    return None


def plan_columns(parsed: ParsedWorkbook, ctx, std: Standards, table_plans: list[TablePlan],
                 udp_map: dict[str, dict], rb: ReportBuilder, new_id: Callable[[], str]) -> list[ColumnPlan]:
    by_sheet = {norm_name(tp.logical): tp for tp in table_plans if tp.from_sheet and tp.logical}
    index = TableIndex(ctx.tables)
    implicit: dict[str, TablePlan] = {}
    groups: dict[str, list[ColumnRow]] = {}
    plan_by_id: dict[str, TablePlan] = {}
    for r in parsed.columns:
        tp = _resolve_table(r, by_sheet, index, implicit, table_plans, rb)
        if tp is None:
            continue
        tp.column_rows.append(r.row)
        plan_by_id[tp.id] = tp
        groups.setdefault(tp.id, []).append(r)

    partition_header = next((h for h, d in udp_map.items() if norm_key(d.get("name")) == _PARTITION_KEY), None)
    out: list[ColumnPlan] = []
    for tid, rows in groups.items():
        out.extend(_plan_table_columns(plan_by_id[tid], rows, ctx, std, udp_map, partition_header, rb, new_id))
    return out


def _plan_table_columns(tp: TablePlan, rows: list[ColumnRow], ctx, std: Standards, udp_map: dict[str, dict],
                        partition_header: str | None, rb: ReportBuilder,
                        new_id: Callable[[], str]) -> list[ColumnPlan]:
    existing_cols = list(ctx.columns_by_table.get(tp.id, [])) if tp.existing is not None else []
    by_phys = {norm_ci(c.get("physicalName")): c for c in existing_cols}
    by_logical: dict[str, list[dict]] = {}
    for c in existing_cols:
        by_logical.setdefault(norm_name(c.get("logicalName")), []).append(c)
    next_ordinal = max((int(c.get("ordinal") or 0) for c in existing_cols), default=-1) + 1
    max_len = std.max_len("column")
    seen_phys: dict[str, int] = {}
    seen_logical: dict[str, int] = {}
    plans: list[ColumnPlan] = []

    for r in rows:
        cp = _plan_row(r, tp, by_phys, by_logical, std, udp_map, partition_header, rb, new_id,
                       seen_phys, seen_logical, max_len)
        if cp.action == "create":
            cp.fields["ordinal"] = next_ordinal
            next_ordinal += 1
        plans.append(cp)

    # PK: la hoja es autoritativa para las columnas listadas; las PK existentes
    # NO listadas conservan su posición y las de la hoja van después, en orden.
    listed_ids = {cp.id for cp in plans if cp.action != "error"}
    base = sum(1 for c in existing_cols if c.get("isPrimaryKey") and str(c.get("id")) not in listed_ids)
    pk_index = 0
    for cp in plans:
        if cp.action == "error":
            continue
        if cp.pk:
            cp.fields["pkPosition"] = base + pk_index
            pk_index += 1
        cp.fields["projectId"] = ctx.project_id            # doc 75 I1
        _close(cp, rb, tp)
    return plans


def _plan_row(r: ColumnRow, tp: TablePlan, by_phys: dict[str, dict], by_logical: dict[str, list[dict]],
              std: Standards, udp_map: dict[str, dict], partition_header: str | None, rb: ReportBuilder,
              new_id: Callable[[], str], seen_phys: dict[str, int], seen_logical: dict[str, int],
              max_len: int) -> ColumnPlan:
    logical = clean_text(r.logical)
    if not logical:
        rb.error(SHEET_COLUMNS, "missing-required", "CAMPO_LOGICO is required.", row=r.row, column="CAMPO_LOGICO")
        return ColumnPlan(row=r.row, table=tp, id=new_id(), action="error")
    declared = bool(clean_text(r.physical))
    physical = clean_text(r.physical) if declared else std.physicalize(logical, "column")

    existing = by_phys.get(norm_ci(physical))
    if existing is None and not declared:
        candidates = by_logical.get(norm_name(logical), [])
        if len(candidates) > 1:
            rb.error(SHEET_COLUMNS, "ambiguous-match",
                     f"Several columns of '{tp.physical}' share the logical name '{logical}'; declare CAMPO_FISICO.",
                     row=r.row, column="CAMPO_FISICO")
            return ColumnPlan(row=r.row, table=tp, id=new_id(), action="error")
        if len(candidates) == 1:
            existing = candidates[0]
            physical = str(existing.get("physicalName") or physical)
            rb.warning(SHEET_COLUMNS, "matched-by-logical",
                       f"Row matched the existing column '{physical}' by its logical name.",
                       row=r.row, column="CAMPO_LOGICO")
    cid = str(existing["id"]) if existing is not None else new_id()
    cp = ColumnPlan(row=r.row, table=tp, id=cid, action="create" if existing is None else "update",
                    existing=existing, pk=r.pk)

    pk_key, lk = norm_ci(physical), norm_name(logical)
    dup_row = seen_phys.get(pk_key) or seen_logical.get(lk)
    if dup_row is not None:
        rb.error(SHEET_COLUMNS, "duplicate-in-file",
                 f"Column '{logical}' ({physical}) of '{tp.physical}' is repeated (see row {dup_row}).",
                 row=r.row, column="CAMPO_LOGICO")
        cp.action = "error"
        return cp
    seen_phys[pk_key] = r.row
    seen_logical[lk] = r.row

    errors_before = rb.error_count
    current_phys = str(existing.get("physicalName") or "") if existing else None
    if max_len and len(physical) > max_len and physical != current_phys:
        rb.error(SHEET_COLUMNS, "name-too-long",
                 f"Physical name '{physical}' has {len(physical)} characters, over the {max_len}-character limit.",
                 row=r.row, column="CAMPO_FISICO" if declared else "CAMPO_LOGICO")

    # Parent domain + tipo.
    domain_id = (existing or {}).get("parentDomainId")
    if clean_text(r.domain):
        dom = std.domain(r.domain)
        if dom is None:
            rb.error(SHEET_COLUMNS, "unknown-domain", f"Parent domain '{r.domain}' doesn't exist in Data Standards.",
                     row=r.row, column="PARENT_DOMAIN")
        else:
            domain_id = str(dom["id"])
    domain_changed = existing is not None and domain_id != existing.get("parentDomainId")
    dom_default = std.domain_default(domain_id)
    canonical: str | None = None
    if clean_text(r.data_type):
        canonical = std.canonical_type(r.data_type)
        if canonical is None:
            rb.error(SHEET_COLUMNS, "invalid-type",
                     f"Data type '{r.data_type}' is not a valid type in the platform.", row=r.row, column="TIPO_DATO")
    if canonical is not None:
        data_type = canonical
    elif existing is not None and not domain_changed:
        data_type = str(existing.get("dataType") or "")
    elif dom_default:
        data_type = dom_default
    else:
        data_type = ""
        if existing is None and not clean_text(r.data_type):
            rb.error(SHEET_COLUMNS, "missing-required",
                     "TIPO_DATO or PARENT_DOMAIN is required to create a column.", row=r.row, column="TIPO_DATO")
    type_overridden = bool(domain_id and dom_default and data_type and norm_type(data_type) != norm_type(dom_default))
    # Doc 69: faceta LÓGICA del tipo — hereda del dominio (o se conserva si la
    # columna existe y el dominio no cambió); sin UI de edición en Fase 1.
    if existing is not None and not domain_changed:
        logical_type = existing.get("logicalDataType")
        logical_overridden = bool(existing.get("logicalTypeOverridden"))
    else:
        logical_type = std.domain_logical(domain_id)
        logical_overridden = False

    description = clean_text(r.description) or None
    if existing is not None and description is None:
        description = clean_text(existing.get("description")) or None
    udp_values = apply_udps(udp_map, r.udp, (existing or {}).get("udpValues"), rb, SHEET_COLUMNS, r.row)

    if partition_header is not None and clean_text(r.udp.get(partition_header)):
        is_partition = partition_correlative(r.udp.get(partition_header)) is not None
    else:
        is_partition = bool(existing.get("isPartition")) if existing is not None else False

    if rb.error_count > errors_before:
        cp.action = "error"
        return cp

    is_nullable = False if r.pk else (bool(existing.get("isNullable", True)) if existing is not None else True)
    cp.fields = {
        "id": cid, "tableId": tp.id, "physicalName": physical, "logicalName": logical,
        "parentDomainId": domain_id, "dataType": data_type, "typeOverridden": type_overridden,
        "isPrimaryKey": True if r.pk else None, "pkPosition": None,
        "isForeignKey": (existing or {}).get("isForeignKey"),
        "isNullable": is_nullable, "isPartition": is_partition, "description": description,
        "ordinal": int(existing.get("ordinal") or 0) if existing is not None else 0,
        "udpValues": udp_values,
        # Doc 69: campos de faceta (el update conserva los existentes).
        "logicalDataType": logical_type, "logicalTypeOverridden": logical_overridden,
        "logicalOnly": bool((existing or {}).get("logicalOnly")),
        "physicalOnly": bool((existing or {}).get("physicalOnly")),
    }
    return cp


def _close(cp: ColumnPlan, rb: ReportBuilder, tp: TablePlan) -> None:
    """Cierra el plan de la columna: doc completo, clasificación y warnings."""
    cp.doc = column_doc(cp.fields)
    if cp.existing is None:
        cp.action = "create"
        return
    before = normalized_existing(cp.existing)
    changed = changed_fields(before, cp.doc)
    if not changed:
        cp.action = "unchanged"
        cp.doc = None
        return
    cp.action = "update"
    physical = cp.doc["physicalName"]
    if before.get("isPrimaryKey") and not cp.doc.get("isPrimaryKey"):
        rb.warning(SHEET_COLUMNS, "pk-removed",
                   f"Column '{physical}' of '{tp.physical}' is a primary key today and the sheet doesn't mark it (PK).",
                   row=cp.row, column="PK")
    if "logicalName" in changed or "physicalName" in changed:
        rb.warning(SHEET_COLUMNS, "rename",
                   f"Column '{before.get('physicalName')}' of '{tp.physical}' will be renamed "
                   f"({before.get('logicalName')} → {cp.doc['logicalName']}, "
                   f"{before.get('physicalName')} → {physical}).", row=cp.row, column="CAMPO_LOGICO")
    rb.warning(SHEET_COLUMNS, "existing-column",
               f"Column '{physical}' of '{tp.physical}' already exists — it will be updated ({', '.join(changed)}).",
               row=cp.row, column="CAMPO_LOGICO")
