"""Negocio de `reporting`. La agregación (`table_rows`, `column_rows`) es PURA y
testeable sin base de datos: recibe listas de docs ya leídas por el repository.

Semántica de una fila de tabla (`GET /api/reporting/tables`):
  - id, physicalName, logicalName, schema, description : de `canonical_tables`
    (la def funcional de la tabla es `description`).
  - columnCount        : nº de columnas activas de la tabla (`canonical_columns`).
  - relationshipCount  : nº de relaciones donde la tabla es source O target.
  - subjectAreas       : doc 88 §7 — nombres de las CARPETAS que contienen
    los canvases (`subject_areas`) cuyo `tableIds` incluye la tabla (DDV: las
    hijas de CPYBCA/Otros; UDV: «1. Party»…). Un canvas en la raíz del
    proyecto no aporta. Ordenados, sin duplicados.
  - diagrams           : nombres de esos canvases (lo que ANTES se llamaba
    subject area). Ordenados, sin duplicados.
  - projects           : doc 75: el reporte es de UN proyecto y toda tabla le
    pertenece (esté o no en un canvas), así que la lista trae ese único nombre
    (la columna se conserva en la hoja exportada).
  - udpValues          : UDPs de la tabla como {NOMBRE de la def: valor}
    (traducidos desde {defId: valor} vía `udp_definitions` — export legible).

El alcance por proyecto lo pone el repository (`scoped`); el único filtro del
predicado puro `_matches` es `schema` (igualdad exacta).
"""
from __future__ import annotations

from app.core.facets import udp_display_names
from app.features.projects import repository as projects_repo
from app.features.views.membership import view_on_canvas

from . import repository


def _index_tables_to_canvases(
    subject_areas: list[dict],
) -> dict[str, list[dict]]:
    """tableId -> lista de canvases que la referencian. Puro."""
    out: dict[str, list[dict]] = {}
    for sa in subject_areas:
        for tid in sa.get("tableIds") or []:
            out.setdefault(tid, []).append(sa)
    return out


def _canvas_names(table_id: str, tables_to_canvases: dict[str, list[dict]]) -> list[str]:
    """Nombres de los canvases (diagramas) que referencian la tabla, ordenados
    y sin duplicados. Puro."""
    canvases = tables_to_canvases.get(table_id, [])
    return sorted({(c.get("name") or "") for c in canvases if c.get("name")})


def _folder_names(table_id: str, tables_to_canvases: dict[str, list[dict]],
                  folder_name: dict[str, str]) -> list[str]:
    """Doc 88 §7: subject areas = carpetas que contienen los canvases de la
    tabla (sin carpeta ⇒ nada). Puro."""
    canvases = tables_to_canvases.get(table_id, [])
    names = {folder_name.get(str(c.get("folderId"))) for c in canvases if c.get("folderId")}
    return sorted(n for n in names if n)


def _udp_named(raw: dict | None, name_by_id: dict[str, str]) -> dict:
    """udpValues {defId: valor} → {nombre de la def: valor}. Ids sin def
    conocida se conservan tal cual (no se pierde el valor). Puro."""
    return {name_by_id.get(k, k): v for k, v in (raw or {}).items()}


def _matches(row: dict, filters: dict) -> bool:
    """Predicado de filtro de una fila de tabla (sólo `schema`). Puro."""
    schema = filters.get("schema")
    return schema is None or (row.get("schema") or None) == schema


def table_rows(
    tables: list[dict],
    column_counts: dict[str, int],
    relationships: list[dict],
    subject_areas: list[dict],
    projects: list[dict],
    filters: dict | None = None,
    limit: int | None = None,
    udp_defs: list[dict] | None = None,
    folders: list[dict] | None = None,
) -> list[dict]:
    """Construye las filas del reporte por tabla. Puro. Ver docstring del módulo.

    `column_counts` = {tableId: nº columnas activas}, YA agregado server-side
    (no se materializan las columnas — ver `repository.column_counts`).
    `limit` (opcional) acota la cantidad de filas DESPUÉS de ordenar (carga
    inicial liviana del front); `None` = sin tope. `folders` (doc 88 §7)
    resuelve el subject area (carpeta) de cada canvas.
    """
    filters = filters or {}
    tables_to_canvases = _index_tables_to_canvases(subject_areas)
    folder_name = {str(f.get("id")): str(f.get("name") or "") for f in (folders or [])}
    proj_names = sorted({p.get("name") for p in projects if p.get("name")})
    udp_name_by_id = udp_display_names(udp_defs or [])   # doc 69: «X (Logical)» para la faceta lógica

    col_count: dict[str, int] = column_counts or {}

    rel_count: dict[str, int] = {}
    for r in relationships:
        # set(): una relación auto-referencial (parent == child, p.ej.
        # empleado.jefe_id → empleado.id) cuenta UNA vez para esa tabla, no dos.
        for tid in {r.get("parentTableId"), r.get("childTableId")} - {None}:
            rel_count[tid] = rel_count.get(tid, 0) + 1

    rows: list[dict] = []
    for t in tables:
        tid = t["id"]
        row = {
            "id": tid,
            "physicalName": t.get("physicalName"),
            "logicalName": t.get("logicalName"),
            "schema": t.get("schema"),
            "subjectAreas": _folder_names(tid, tables_to_canvases, folder_name),
            "diagrams": _canvas_names(tid, tables_to_canvases),
            "columnCount": col_count.get(tid, 0),
            "relationshipCount": rel_count.get(tid, 0),
            "projects": proj_names,
            "udpValues": _udp_named(t.get("udpValues"), udp_name_by_id),
            "description": t.get("description"),
            # Doc 70 §4.2 — metadata COMPLETA de la tabla (docs 68/69).
            "physicalNameOverridden": bool(t.get("physicalNameOverridden")),
            "logicalOnly": bool(t.get("logicalOnly")),
            "physicalOnly": bool(t.get("physicalOnly")),
        }
        if _matches(row, filters):
            rows.append(row)

    rows.sort(key=lambda r: (r.get("physicalName") or "").lower())
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows


def column_rows(columns: list[dict], parent_domains: list[dict],
                udp_defs: list[dict] | None = None) -> list[dict]:
    """Filas a nivel columna para el export por niveles. Puro.

    `parentDomain` = nombre del Parent Domain (vía `parentDomainId`), no el id,
    y `udpValues` = {nombre de la def: valor}, para que el export sea legible.
    Ordenadas por (tableId, ordinal).
    """
    domain_name_by_id = {d["id"]: (d.get("name") or "") for d in parent_domains}
    udp_name_by_id = udp_display_names(udp_defs or [])   # doc 69: «X (Logical)» para la faceta lógica
    rows: list[dict] = []
    for c in columns:
        did = c.get("parentDomainId")
        rows.append(
            {
                "id": c.get("id"),
                "tableId": c.get("tableId"),
                "physicalName": c.get("physicalName"),
                "logicalName": c.get("logicalName"),
                "dataType": c.get("dataType"),
                "logicalDataType": c.get("logicalDataType"),   # doc 69: faceta lógica
                "parentDomain": domain_name_by_id.get(did) if did else None,
                "parentDomainId": did,
                "isPrimaryKey": bool(c.get("isPrimaryKey")),
                "isForeignKey": bool(c.get("isForeignKey")),
                "isNullable": c.get("isNullable", True),
                "isPartition": bool(c.get("isPartition")),
                "udpValues": _udp_named(c.get("udpValues"), udp_name_by_id),
                "description": c.get("description"),
                "physicalDescription": c.get("physicalDescription"),   # doc 85
                "ordinal": c.get("ordinal", 0),
                # Doc 70 §4.2 — metadata COMPLETA de la columna (docs 19/68/69).
                "pkPosition": c.get("pkPosition"),
                "typeOverridden": bool(c.get("typeOverridden")),
                "logicalTypeOverridden": bool(c.get("logicalTypeOverridden")),
                "physicalNameOverridden": bool(c.get("physicalNameOverridden")),
                "logicalOnly": bool(c.get("logicalOnly")),
                "physicalOnly": bool(c.get("physicalOnly")),
            }
        )
    rows.sort(key=lambda r: ((r.get("tableId") or ""), r.get("ordinal") or 0))
    return rows


def filter_option_lists(schemas: list, subject_areas: list) -> dict:
    """Doc 70 §4.1: listas de opciones de los filtros — strings no vacíos,
    sin duplicados, orden case-insensitive. Puro."""
    def _clean(values: list) -> list[str]:
        seen = {str(v).strip() for v in values if v is not None and str(v).strip()}
        return sorted(seen, key=lambda x: (x.lower(), x))
    return {"schemas": _clean(schemas), "subjectAreas": _clean(subject_areas)}


async def filter_options(project_id: str) -> dict:
    """Universo COMPLETO de Schema / Subject area del proyecto para los combos
    del reporte tabular (antes salían de las 50 filas de la carga inicial)."""
    raw = await repository.filter_options(project_id)
    return filter_option_lists(raw["schemas"], raw["subjectAreas"])


async def _project_list(project_id: str) -> list[dict]:
    """El único proyecto del reporte (para la columna `projects` de la fila)."""
    project = await projects_repo.get_project(project_id)
    return [project] if project else []


async def list_table_rows(project_id: str, filters: dict | None = None,
                          limit: int | None = None) -> list[dict]:
    """Filas del reporte por tabla del proyecto (filtradas en Python).

    Fast-path de la carga inicial: con `limit` y SIN filtros se traen sólo las
    primeras `limit` tablas (orden físico) + sus conteos, en vez de barrer las
    10k/400k (`report_inputs_page`)."""
    udp_defs = await repository.udp_definitions(project_id)
    projects = await _project_list(project_id)
    if limit is not None and limit >= 0 and not (filters or {}):
        data = await repository.report_inputs_page(project_id, limit)
    else:
        data = await repository.report_inputs(project_id)
    return table_rows(
        data["tables"], data["columnCounts"], data["relationships"],
        data["subjectAreas"], projects, filters, limit, udp_defs, data.get("folders"),
    )


def view_rows(views: list[dict], table_name_by_id: dict[str, str],
              subject_areas: list[dict] | None = None) -> list[dict]:
    """Filas por vista para el export por niveles. Puro.

    Fuentes resueltas a `schema.tabla` legible y `columns` = detalle columna a
    columna del editor de vistas: alias de salida, tabla/columna de origen,
    casteo (`castType`) y transformación (`expression`). Se incluyen además
    `filter` (WHERE de la vista), `joinOverride`, el `sql` completo y
    `canvases` (doc 70): nombres de los canvases donde la vista es miembro
    (membresía explícita `viewIds` o regla legacy en canvases sin lista).
    """
    sas = subject_areas or []
    rows: list[dict] = []
    for v in views:
        canvases = sorted({(sa.get("name") or "") for sa in sas
                           if sa.get("name") and view_on_canvas({**v, "id": v.get("id")}, sa)})
        src_ids = v.get("sourceTableIds") or ([v["tableId"]] if v.get("tableId") else [])
        cols = []
        for s in (v.get("sources") or []):
            if not (s.get("column") or s.get("expression")):
                continue
            src_tid = s.get("tableId") or (src_ids[0] if src_ids else None)
            cols.append({
                "outputAlias": s.get("outputAlias") or s.get("column"),
                "sourceTable": table_name_by_id.get(src_tid, src_tid) if src_tid else None,
                "sourceColumn": s.get("column"),
                "castType": s.get("castType"),
                "expression": s.get("expression"),
            })
        rows.append({
            "id": v["id"],
            "schema": v.get("schema"),
            "name": v.get("name"),
            "sourceTableIds": src_ids,
            "sourceTables": sorted({table_name_by_id.get(t, t) for t in src_ids}),
            "filter": v.get("filter"),
            "joinOverride": v.get("joinOverride"),
            "showOnCanvas": bool(v.get("showOnCanvas")),
            "canvases": canvases,
            "description": v.get("description"),
            "sql": v.get("sql") or None,
            "columns": cols,
        })
    rows.sort(key=lambda r: ((r.get("schema") or ""), (r.get("name") or "").lower()))
    return rows


# Tope de seguridad del export de columnas SIN filtro (evita volcar 400k ~91MB).
UNFILTERED_COLUMNS_CAP = 20000

# Tope de seguridad del export de vistas SIN filtro (los docs llevan `sources`).
UNFILTERED_VIEWS_CAP = 10000


async def list_column_rows(project_id: str, table_id: str | None = None,
                           table_ids: list[str] | None = None,
                           limit: int | None = None) -> list[dict]:
    """Detalle a nivel columna del proyecto; todas, las de una tabla o de un
    lote de tablas. Sin filtro de tabla se aplica un tope (`limit` o
    `UNFILTERED_COLUMNS_CAP`)."""
    if not table_id and not table_ids:
        limit = limit or UNFILTERED_COLUMNS_CAP
    columns = await repository.columns(project_id, table_id, table_ids, limit)
    parent_domains = await repository.parent_domains(project_id)
    udp_defs = await repository.udp_definitions(project_id)
    return column_rows(columns, parent_domains, udp_defs)


async def list_view_rows(project_id: str, table_ids: list[str] | None = None,
                         schema: str | None = None) -> list[dict]:
    """Vistas del proyecto para el export por niveles; con `table_ids`, las
    derivadas de esas tablas; con `schema`, las de ese esquema (Database
    Explorer, doc 18). Resuelve los nombres `schema.tabla` de TODAS las fuentes."""
    limit = None if (table_ids or schema) else UNFILTERED_VIEWS_CAP
    views = await repository.views_for_tables(project_id, table_ids, limit, schema=schema)
    src_ids = sorted({t for v in views for t in (v.get("sourceTableIds") or [])}
                     | {v["tableId"] for v in views if v.get("tableId")})
    names = await repository.table_names(src_ids)
    name_by_id = {
        tid: (f"{d.get('schema')}.{d.get('physicalName')}" if d.get("schema")
              else (d.get("physicalName") or tid))
        for tid, d in names.items()
    }
    # Doc 70: canvases que contienen alguna fuente = candidatos a membresía
    # (una vista miembro siempre conserva ≥1 fuente en su canvas).
    sas = await repository._subject_areas(project_id, src_ids) if src_ids else []
    return view_rows(views, name_by_id, sas)
