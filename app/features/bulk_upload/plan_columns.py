"""Planner — columnas (doc 55 §3.3-3.4, §4.2, §5 · doc 78): la fila referencia
su tabla por lógico (fila de la hoja de tablas o tabla efectiva única);
identidad por físico (declarado/derivado) con fallback por lógico único; tipo
heredado del parent domain o declarado (gramática de la plataforma); PK
autoritativa para las columnas listadas (doc 94: sin orden de llave aparte —
las columnas nuevas nacen PK primero y el orden único rige al mostrar y
exportar); UDP por el mapeo del perfil (una cabecera → N defs). Defaults solo en columnas nuevas;
`onExistingColumn: reject` corta las existentes. Puro.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.datatypes import mirror_complex, synced_other_facet
from app.features.catalog.models import CanonicalColumnDoc

from .normalize import clean_logical, clean_text, is_pk_mark, norm_ci, norm_key, norm_name, norm_type, partition_correlative
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
    fields: dict | None = None       # campos resueltos antes del cierre (ordinal, projectId)


def column_doc(fields: dict) -> dict:
    return CanonicalColumnDoc.model_validate(fields).model_dump()


def normalized_existing(existing: dict) -> dict:
    return CanonicalColumnDoc.model_validate(existing).model_dump()


def _resolve_table(r: ColumnRow, by_sheet: dict[str, TablePlan], index: TableIndex,
                   implicit: dict[str, TablePlan], table_plans: list[TablePlan],
                   rb: ReportBuilder, h: Callable[[str, str], str], tables_label: str) -> TablePlan | None:
    key = norm_name(r.table_logical)
    if not key:
        rb.error(SHEET_COLUMNS, "missing-required", f"{h('tableRef', 'TABLA_LOGICO')} is required.",
                 row=r.row, column=h("tableRef", "TABLA_LOGICO"))
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
                 f"'{tables_label}' sheet with its physical name.", row=r.row, column=h("tableRef", "TABLA_LOGICO"))
        return None
    rb.error(SHEET_COLUMNS, "unknown-table",
             f"Table '{r.table_logical}' is neither in the '{tables_label}' sheet nor in the model.",
             row=r.row, column=h("tableRef", "TABLA_LOGICO"))
    return None


def plan_columns(parsed: ParsedWorkbook, ctx, std: Standards, table_plans: list[TablePlan],
                 udp_map: dict[str, list[dict]], rb: ReportBuilder, new_id: Callable[[], str],
                 options) -> list[ColumnPlan]:
    by_sheet = {norm_name(tp.logical): tp for tp in table_plans if tp.from_sheet and tp.logical}
    index = TableIndex(ctx.tables)
    implicit: dict[str, TablePlan] = {}
    groups: dict[str, list[ColumnRow]] = {}
    plan_by_id: dict[str, TablePlan] = {}
    h = lambda f, fallback: parsed.header("columns", f, fallback)  # noqa: E731
    tables_label = rb.sheet_name("tables")
    for r in parsed.columns:
        tp = _resolve_table(r, by_sheet, index, implicit, table_plans, rb, h, tables_label)
        if tp is None:
            continue
        tp.column_rows.append(r.row)
        plan_by_id[tp.id] = tp
        groups.setdefault(tp.id, []).append(r)

    partition_header = next((hdr for hdr, defs in udp_map.items()
                             if any(norm_key(d.get("name")) == _PARTITION_KEY for d in defs)), None)
    out: list[ColumnPlan] = []
    for tid, rows in groups.items():
        out.extend(_plan_table_columns(plan_by_id[tid], rows, ctx, std, udp_map, partition_header, rb, new_id,
                                       h, options))
    return out


def _plan_table_columns(tp: TablePlan, rows: list[ColumnRow], ctx, std: Standards, udp_map: dict[str, list[dict]],
                        partition_header: str | None, rb: ReportBuilder, new_id: Callable[[], str],
                        h: Callable[[str, str], str], options) -> list[ColumnPlan]:
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
                       seen_phys, seen_logical, max_len, h, options)
        plans.append(cp)

    # Ordinal PK-first (doc 81): entre las columnas NUEVAS, las PK van PRIMERO —
    # en el orden de la hoja, que es su orden de llave — y luego el resto. Antes
    # el ordinal seguía el orden crudo de la hoja: si una no-PK caía entre PKs,
    # el canvas (que agrupa PK arriba) y el properties (puro ordinal) mostraban
    # órdenes distintos. Así el orden guardado ya nace con la llave arriba y
    # coincide con ambos, sin depender del re-agrupado de display. Es la misma
    # política «PKs primero» que aplica la migración Erwin (doc 74). Las
    # columnas existentes conservan su ordinal (no se re-numera lo ya guardado).
    creates = [cp for cp in plans if cp.action == "create"]
    for cp in [c for c in creates if c.pk] + [c for c in creates if not c.pk]:
        cp.fields["ordinal"] = next_ordinal
        next_ordinal += 1

    # PK: la hoja es autoritativa para las columnas listadas (doc 94: la llave
    # no tiene orden aparte — lo da el ordinal; las existentes lo conservan).
    for cp in plans:
        if cp.action == "error":
            continue
        cp.fields["projectId"] = ctx.project_id            # doc 75 I1
        _close(cp, rb, tp, h)
    return plans


def _plan_row(r: ColumnRow, tp: TablePlan, by_phys: dict[str, dict], by_logical: dict[str, list[dict]],
              std: Standards, udp_map: dict[str, list[dict]], partition_header: str | None, rb: ReportBuilder,
              new_id: Callable[[], str], seen_phys: dict[str, int], seen_logical: dict[str, int],
              max_len: int, h: Callable[[str, str], str], options) -> ColumnPlan:
    logical = clean_logical(clean_text(r.logical), SHEET_COLUMNS, r.row, h("logicalName", "CAMPO_LOGICO"), rb)
    if not logical:
        rb.error(SHEET_COLUMNS, "missing-required", f"{h('logicalName', 'CAMPO_LOGICO')} is required.",
                 row=r.row, column=h("logicalName", "CAMPO_LOGICO"))
        return ColumnPlan(row=r.row, table=tp, id=new_id(), action="error")
    declared = bool(clean_text(r.physical))
    physical = clean_text(r.physical) if declared else std.physicalize(logical, "column")

    existing = by_phys.get(norm_ci(physical))
    if existing is None and not declared:
        candidates = by_logical.get(norm_name(logical), [])
        if len(candidates) > 1:
            rb.error(SHEET_COLUMNS, "ambiguous-match",
                     f"Several columns of '{tp.physical}' share the logical name '{logical}'; declare "
                     f"{h('physicalName', 'CAMPO_FISICO')}.", row=r.row, column=h("physicalName", "CAMPO_FISICO"))
            return ColumnPlan(row=r.row, table=tp, id=new_id(), action="error")
        if len(candidates) == 1:
            existing = candidates[0]
            physical = str(existing.get("physicalName") or physical)
            rb.warning(SHEET_COLUMNS, "matched-by-logical",
                       f"Row matched the existing column '{physical}' by its logical name.",
                       row=r.row, column=h("logicalName", "CAMPO_LOGICO"))
    if existing is None:
        # Doc 78 S5: los defaults del perfil solo aplican a columnas NUEVAS.
        for attr, val in r.defaults.items():
            if attr == "pk":
                r.pk = r.pk or is_pk_mark(val)
            elif not clean_text(getattr(r, attr, "")):
                setattr(r, attr, val)
    elif options.on_existing_column == "reject":
        rb.error(SHEET_COLUMNS, "existing-not-allowed",
                 f"Column '{physical}' of '{tp.physical}' already exists and this profile only allows new columns.",
                 row=r.row, column=h("logicalName", "CAMPO_LOGICO"))
        return ColumnPlan(row=r.row, table=tp, id=str(existing["id"]), action="error", existing=existing)
    cid = str(existing["id"]) if existing is not None else new_id()
    cp = ColumnPlan(row=r.row, table=tp, id=cid, action="create" if existing is None else "update",
                    existing=existing, pk=r.pk)

    pk_key, lk = norm_ci(physical), norm_name(logical)
    dup_row = seen_phys.get(pk_key) or seen_logical.get(lk)
    if dup_row is not None:
        rb.error(SHEET_COLUMNS, "duplicate-in-file",
                 f"Column '{logical}' ({physical}) of '{tp.physical}' is repeated (see row {dup_row}).",
                 row=r.row, column=h("logicalName", "CAMPO_LOGICO"))
        cp.action = "error"
        return cp
    seen_phys[pk_key] = r.row
    seen_logical[lk] = r.row

    errors_before = rb.error_count
    current_phys = str(existing.get("physicalName") or "") if existing else None
    if max_len and len(physical) > max_len and physical != current_phys:
        rb.error(SHEET_COLUMNS, "name-too-long",
                 f"Physical name '{physical}' has {len(physical)} characters, over the {max_len}-character limit.",
                 row=r.row, column=h("physicalName", "CAMPO_FISICO") if declared else h("logicalName", "CAMPO_LOGICO"))

    # Parent domain + tipo.
    domain_id = (existing or {}).get("parentDomainId")
    if clean_text(r.domain):
        dom = std.domain(r.domain)
        if dom is None:
            rb.error(SHEET_COLUMNS, "unknown-domain", f"Parent domain '{r.domain}' doesn't exist in Data Standards.",
                     row=r.row, column=h("parentDomain", "PARENT_DOMAIN"))
        else:
            domain_id = str(dom["id"])
    domain_changed = existing is not None and domain_id != existing.get("parentDomainId")
    dom_default = std.domain_default(domain_id)
    canonical: str | None = None
    if clean_text(r.data_type):
        canonical = std.canonical_type(r.data_type)
        if canonical is None:
            rb.error(SHEET_COLUMNS, "invalid-type",
                     f"Data type '{r.data_type}' is not a valid type in the platform.",
                     row=r.row, column=h("dataType", "TIPO_DATO"))
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
                     f"{h('dataType', 'TIPO_DATO')} or {h('parentDomain', 'PARENT_DOMAIN')} is required to create a column.",
                     row=r.row, column=h("dataType", "TIPO_DATO"))
    type_overridden = bool(domain_id and dom_default and data_type and norm_type(data_type) != norm_type(dom_default))
    # Doc 69: faceta LÓGICA del tipo — hereda del dominio (o se conserva si la
    # columna existe y el dominio no cambió); sin UI de edición en Fase 1.
    if existing is not None and not domain_changed:
        logical_type = existing.get("logicalDataType")
        logical_overridden = bool(existing.get("logicalTypeOverridden"))
    else:
        logical_type = std.domain_logical(domain_id)
        logical_overridden = False
    # Doc 96 D8: un complejo con estructura es el mismo en las dos facetas; el tipo
    # de la plantilla (físico) manda. En una actualización, si las dos facetas eran
    # el mismo complejo, el lógico sigue al físico nuevo (final review #2).
    prev_phys = str(existing.get("dataType") or "") if existing is not None else ""
    aligned = mirror_complex(data_type, synced_other_facet(data_type, prev_phys, logical_type))
    if aligned != logical_type:
        dom_logical = std.domain_logical(domain_id)
        logical_type = aligned
        logical_overridden = bool(domain_id and dom_logical and logical_type != dom_logical)

    description = clean_text(r.description) or None
    if existing is not None and description is None:
        description = clean_text(existing.get("description")) or None
    udp_values = apply_udps(udp_map, r.udp, (existing or {}).get("udpValues"), rb, SHEET_COLUMNS, r.row,
                            udp_defaults=r.udp_defaults, is_new=existing is None)

    partition_raw = clean_text(r.udp.get(partition_header)) if partition_header is not None else ""
    if not partition_raw and existing is None and partition_header is not None:
        partition_raw = clean_text(r.udp_defaults.get(partition_header))
    if partition_raw:
        is_partition = partition_correlative(partition_raw) is not None
    else:
        is_partition = bool(existing.get("isPartition")) if existing is not None else False

    if rb.error_count > errors_before:
        cp.action = "error"
        return cp

    is_nullable = False if r.pk else (bool(existing.get("isNullable", True)) if existing is not None else True)
    cp.fields = {
        "id": cid, "tableId": tp.id, "physicalName": physical, "logicalName": logical,
        "parentDomainId": domain_id, "dataType": data_type, "typeOverridden": type_overridden,
        "isPrimaryKey": True if r.pk else None,
        "isForeignKey": (existing or {}).get("isForeignKey"),
        "isNullable": is_nullable, "isPartition": is_partition, "description": description,
        "ordinal": int(existing.get("ordinal") or 0) if existing is not None else 0,
        "udpValues": udp_values,
        # Doc 69: campos de faceta (el update conserva los existentes).
        "logicalDataType": logical_type, "logicalTypeOverridden": logical_overridden,
        "logicalOnly": bool((existing or {}).get("logicalOnly")),
        "physicalOnly": bool((existing or {}).get("physicalOnly")),
        # Doc 85: el comment físico no viene de la plantilla — se conserva.
        "physicalDescription": (existing or {}).get("physicalDescription"),
    }
    return cp


def _close(cp: ColumnPlan, rb: ReportBuilder, tp: TablePlan, h: Callable[[str, str], str]) -> None:
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
                   f"Column '{physical}' of '{tp.physical}' is a primary key today and the sheet doesn't mark it "
                   f"({h('pk', 'PK')}).", row=cp.row, column=h("pk", "PK"))
    if "logicalName" in changed or "physicalName" in changed:
        rb.warning(SHEET_COLUMNS, "rename",
                   f"Column '{before.get('physicalName')}' of '{tp.physical}' will be renamed "
                   f"({before.get('logicalName')} → {cp.doc['logicalName']}, "
                   f"{before.get('physicalName')} → {physical}).", row=cp.row, column=h("logicalName", "CAMPO_LOGICO"))
    rb.warning(SHEET_COLUMNS, "existing-column",
               f"Column '{physical}' of '{tp.physical}' already exists — it will be updated ({', '.join(changed)}).",
               row=cp.row, column=h("logicalName", "CAMPO_LOGICO"))
