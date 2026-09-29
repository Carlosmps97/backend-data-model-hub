"""Planner — vistas `_vu` automáticas (doc 87 §3.1). Por cada fila de la hoja
de tablas (create / update / unchanged): la vista NORMAL (mismo nombre que la
tabla) siempre, y la vista DAC (`<TABLA>DAC`) solo si el UDP de tabla
«Clasificacion del Dato» vale DAC; ambas en `<esquema>_vu`, con las columnas
efectivas de la tabla en el orden de display del doc 81. Doc 102 (D3): en una
tabla DAC la vista normal va SIN las columnas DAC (`DAC-…`, misma regla del
Export DDL `excluir_dac_vista_sin_dac`) y la DAC las lleva todas. Una vista que
ya existe (mismo esquema y nombre, CI) no se toca (D3); las nuevas entran al
canvas de la tabla (D4). Puro.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.column_order import display_order
from app.core.facets import LOGICAL, PHYSICAL, udp_view
from app.features.views.models import ViewDoc

from .normalize import clean_text, norm_ci, norm_enum, norm_key
from .plan_columns import ColumnPlan
from .plan_tables import TablePlan
from .report import SHEET_TABLES, ReportBuilder

# `norm_key` quita tildes, stopwords («del») y el prefijo «UDP»: se calcula con
# la misma función para no depender de su grafía interna.
CLASSIFICATION_KEY = norm_key("Clasificacion del Dato")
DAC_VALUE = "DAC"
# Doc 102 (D3): una COLUMNA es DAC si su «Clasificacion del Dato» empieza con
# `DAC-` (DAC-DOCUMENTO, DAC-NOMBRE…) — la regla del Export DDL (`LIKE 'DAC-%'`).
DAC_COLUMN_PREFIX = "DAC-"
_VIEW_ACTIONS = ("create", "update", "unchanged")


@dataclass
class ViewPlan:
    table: TablePlan
    id: str
    name: str
    schema: str
    kind: str                # normal | dac
    action: str              # create | unchanged
    doc: dict | None = None


class ViewIndex:
    """Vistas efectivas por (esquema, nombre) CI; suma las creadas en el plan."""

    def __init__(self, views: list[dict]) -> None:
        self._ids: dict[tuple[str, str], str] = {}
        for v in views:
            self._ids.setdefault((norm_ci(v.get("schema")), norm_ci(v.get("name"))), str(v.get("id") or ""))

    def get(self, schema: str, name: str) -> str | None:
        return self._ids.get((norm_ci(schema), norm_ci(name)))

    def add(self, schema: str, name: str, view_id: str) -> None:
        self._ids[(norm_ci(schema), norm_ci(name))] = view_id


def classification_defs(udp_defs: list[dict], level: str = "table") -> dict[str, str]:
    """{faceta: id} de las defs «Clasificacion del Dato» del nivel pedido
    (doc 69: una por faceta). Sin def ⇒ nada de ese nivel es DAC."""
    out: dict[str, str] = {}
    for d in udp_defs:
        if (d.get("level") or "column") != level or norm_key(d.get("name")) != CLASSIFICATION_KEY or not d.get("id"):
            continue
        out.setdefault(udp_view(d), str(d["id"]))
    return out


def _classification(udp_values: dict | None, defs: dict[str, str]) -> str:
    """Clasificación normalizada: decide la faceta FÍSICA si tiene valor; si no,
    la lógica. '' sin valor. Puro."""
    values = udp_values or {}
    for facet in (PHYSICAL, LOGICAL):
        val = clean_text(values.get(defs.get(facet, "")))
        if val:
            return norm_enum(val)
    return ""


def is_dac(udp_values: dict | None, defs: dict[str, str]) -> bool:
    """Tabla DAC: clasificación = DAC."""
    return _classification(udp_values, defs) == DAC_VALUE


def is_dac_column(udp_values: dict | None, defs: dict[str, str]) -> bool:
    """Doc 102: columna DAC — clasificación que empieza con `DAC-`."""
    return _classification(udp_values, defs).startswith(DAC_COLUMN_PREFIX)


def dac_suffix(naming_case: str | None) -> str:
    return "dac" if (naming_case or "").lower() == "lower" else "DAC"


def effective_columns(tp: TablePlan, plans: list[ColumnPlan], columns_by_table: dict[str, list[dict]]) -> list[dict]:
    """Columnas de la tabla tras la carga (existentes pisadas por las del plan),
    en orden de display (PK primero, resto por ordinal)."""
    cols: dict[str, dict] = {}
    if tp.existing is not None:
        for c in columns_by_table.get(tp.id, []):
            cols[str(c.get("id"))] = c
    for cp in plans:
        if cp.action in ("create", "update") and cp.doc is not None:
            cols[cp.id] = cp.doc
    return display_order(list(cols.values()))


def view_doc(vid: str, project_id: str, name: str, schema: str, tp: TablePlan, cols: list[dict]) -> dict:
    """Doc COMPLETO de la vista (invariante del overlay), forma persistida
    (`schema` por alias). Réplica exacta de la tabla: una fuente, un source por
    columna en orden, alias = físico (como las `_vu` del kit Erwin)."""
    table = tp.doc or tp.existing or {}
    return ViewDoc.model_validate({
        "id": vid, "projectId": project_id, "name": name, "schema": schema, "sql": "",
        "description": clean_text(table.get("description")) or None,
        "tableId": tp.id, "sourceTableIds": [tp.id],
        "sources": [{"tableId": tp.id, "column": c["physicalName"], "outputAlias": c["physicalName"]} for c in cols],
        "outputAlias": None, "expression": None,
        "showOnCanvas": True, "customSql": None, "udpValues": {},
    }).model_dump(by_alias=True)


def plan_views(table_plans: list[TablePlan], column_plans: list[ColumnPlan], ctx, schemas, structure,
               rb: ReportBuilder, new_id: Callable[[], str], h: Callable[[str, str], str]) -> list[ViewPlan]:
    index = ViewIndex(ctx.views)
    defs = classification_defs(ctx.udp_defs)
    col_defs = classification_defs(ctx.udp_defs, level="column")
    suffix = dac_suffix((ctx.naming.get("table") or {}).get("case"))
    by_table: dict[str, list[ColumnPlan]] = {}
    for cp in column_plans:
        by_table.setdefault(cp.table.id, []).append(cp)

    out: list[ViewPlan] = []
    for tp in table_plans:
        if not tp.from_sheet or tp.action not in _VIEW_ACTIONS:
            continue
        cols = effective_columns(tp, by_table.get(tp.id, []), ctx.columns_by_table)
        if not cols:
            rb.warning(SHEET_TABLES, "view-no-columns",
                       f"Table '{tp.physical}' has no columns yet — its views were not created.",
                       row=tp.row, column=h("logicalName", "TABLA_LOGICO"))
            continue
        if not clean_text(tp.schema):
            rb.warning(SHEET_TABLES, "view-no-schema",
                       f"Table '{tp.physical}' has no schema — its views were not created.",
                       row=tp.row, column=h("schema", "ESQUEMA"))
            continue
        schema = schemas.resolve_view_schema(tp.schema, tp.row, column=h("schema", "ESQUEMA"))
        if schema is None:
            continue                                   # `view-schema-kind` ya reportado
        table = tp.doc or tp.existing or {}
        dac_table = is_dac(table.get("udpValues"), defs)
        # Doc 102 (D3): en una tabla DAC la vista normal va SIN las columnas
        # DAC-* (misma regla del Export DDL); la DAC las lleva todas.
        plain = [c for c in cols if not is_dac_column(c.get("udpValues"), col_defs)] if dac_table else cols
        wanted = [(tp.physical, "normal", plain)]
        if dac_table:
            wanted.append((tp.physical + suffix, "dac", cols))
        for name, kind, view_cols in wanted:
            existing_id = index.get(schema, name)
            if existing_id is not None:
                out.append(ViewPlan(tp, existing_id, name, schema, kind, "unchanged"))
                continue
            if not view_cols:
                rb.warning(SHEET_TABLES, "view-only-dac-columns",
                           f"Table '{tp.physical}' only has DAC columns — its non-DAC view '{name}' was not created.",
                           row=tp.row, column=h("logicalName", "TABLA_LOGICO"))
                continue
            vid = new_id()
            index.add(schema, name, vid)
            out.append(ViewPlan(tp, vid, name, schema, kind, "create",
                                view_doc(vid, ctx.project_id, name, schema, tp, view_cols)))
            structure.place_view(tp, vid)
    return out
