"""Negocio de `reporting`. La agregación (`table_rows`, `column_rows`) es PURA y
testeable sin base de datos: recibe listas de docs ya leídas por el repository.

Semántica de una fila de tabla (`GET /api/reporting/tables`):
  - id, physicalName, logicalName, schema, description : de `canonical_tables`
    (la def funcional de la tabla es `description`).
  - columnCount        : nº de columnas activas de la tabla (`canonical_columns`).
  - relationshipCount  : nº de relaciones donde la tabla es source O target.
  - subjectAreas       : nombres de los canvases (`subject_areas`) cuyo
    `tableIds` incluye la tabla. Ordenados, sin duplicados.
  - projects           : nombres de los proyectos que contienen alguno de esos
    canvases (un proyecto referencia la tabla si cualquiera de sus canvases la
    referencia). Ordenados, sin duplicados.
  - udpValues          : UDPs de la tabla como {NOMBRE de la def: valor}
    (traducidos desde {defId: valor} vía `udp_definitions` — export legible).

Filtros opcionales (predicado puro `_matches`), pensados para crecer:
  - schema    : igualdad exacta sobre el schema de la tabla.
  - projectId : la tabla debe estar referenciada por algún canvas de ese
    proyecto (la fila se filtra; sus listas `subjectAreas`/`projects` siguen
    mostrando TODAS las referencias, no sólo las del proyecto filtrado).
"""
from __future__ import annotations

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


def _names_for_table(
    table_id: str,
    tables_to_canvases: dict[str, list[dict]],
    projects_by_id: dict[str, str],
) -> tuple[list[str], list[str], set[str]]:
    """(nombres de canvases, nombres de proyectos, set de projectIds) que
    referencian la tabla. Nombres ordenados y sin duplicados. Puro."""
    canvases = tables_to_canvases.get(table_id, [])
    sa_names = sorted({(c.get("name") or "") for c in canvases if c.get("name")})
    project_ids = {c.get("projectId") for c in canvases if c.get("projectId")}
    proj_names = sorted(
        {projects_by_id[pid] for pid in project_ids if pid in projects_by_id}
    )
    return sa_names, proj_names, project_ids


def _udp_named(raw: dict | None, name_by_id: dict[str, str]) -> dict:
    """udpValues {defId: valor} → {nombre de la def: valor}. Ids sin def
    conocida se conservan tal cual (no se pierde el valor). Puro."""
    return {name_by_id.get(k, k): v for k, v in (raw or {}).items()}


def _matches(row: dict, project_ids: set[str], filters: dict) -> bool:
    """Predicado de filtro de una fila de tabla. Puro y extensible."""
    schema = filters.get("schema")
    if schema is not None and (row.get("schema") or None) != schema:
        return False
    project_id = filters.get("projectId")
    if project_id is not None and project_id not in project_ids:
        return False
    return True


def table_rows(
    tables: list[dict],
    column_counts: dict[str, int],
    relationships: list[dict],
    subject_areas: list[dict],
    projects: list[dict],
    filters: dict | None = None,
    limit: int | None = None,
    udp_defs: list[dict] | None = None,
) -> list[dict]:
    """Construye las filas del reporte por tabla. Puro. Ver docstring del módulo.

    `column_counts` = {tableId: nº columnas activas}, YA agregado server-side
    (no se materializan las columnas — ver `repository.column_counts`).
    `limit` (opcional) acota la cantidad de filas DESPUÉS de ordenar (carga
    inicial liviana del front); `None` = sin tope.
    """
    filters = filters or {}
    tables_to_canvases = _index_tables_to_canvases(subject_areas)
    projects_by_id = {p["id"]: (p.get("name") or "") for p in projects}
    udp_name_by_id = {d["id"]: (d.get("name") or d["id"]) for d in (udp_defs or [])}

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
        sa_names, proj_names, project_ids = _names_for_table(
            tid, tables_to_canvases, projects_by_id
        )
        row = {
            "id": tid,
            "physicalName": t.get("physicalName"),
            "logicalName": t.get("logicalName"),
            "schema": t.get("schema"),
            "subjectAreas": sa_names,
            "columnCount": col_count.get(tid, 0),
            "relationshipCount": rel_count.get(tid, 0),
            "projects": proj_names,
            "udpValues": _udp_named(t.get("udpValues"), udp_name_by_id),
            "description": t.get("description"),
        }
        if _matches(row, project_ids, filters):
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
    udp_name_by_id = {d["id"]: (d.get("name") or d["id"]) for d in (udp_defs or [])}
    rows: list[dict] = []
    for c in columns:
        did = c.get("parentDomainId")
        rows.append(
            {
                "tableId": c.get("tableId"),
                "physicalName": c.get("physicalName"),
                "logicalName": c.get("logicalName"),
                "dataType": c.get("dataType"),
                "parentDomain": domain_name_by_id.get(did) if did else None,
                "isPrimaryKey": bool(c.get("isPrimaryKey")),
                "isForeignKey": bool(c.get("isForeignKey")),
                "isNullable": c.get("isNullable", True),
                "isPartition": bool(c.get("isPartition")),
                "udpValues": _udp_named(c.get("udpValues"), udp_name_by_id),
                "description": c.get("description"),
                "ordinal": c.get("ordinal", 0),
            }
        )
    rows.sort(key=lambda r: ((r.get("tableId") or ""), r.get("ordinal") or 0))
    return rows


async def list_table_rows(filters: dict | None = None, limit: int | None = None) -> list[dict]:
    """Filas del reporte por tabla (todas las tablas, filtradas en Python).

    Fast-path de la carga inicial: con `limit` y SIN filtros se traen sólo las
    primeras `limit` tablas (orden físico) + sus conteos, en vez de barrer las
    10k/400k (`report_inputs_page`)."""
    udp_defs = await repository.udp_definitions()
    if limit is not None and limit >= 0 and not (filters or {}):
        data = await repository.report_inputs_page(limit)
        return table_rows(
            data["tables"], data["columnCounts"], data["relationships"],
            data["subjectAreas"], data["projects"], filters, limit, udp_defs,
        )
    data = await repository.report_inputs()
    return table_rows(
        data["tables"],
        data["columnCounts"],
        data["relationships"],
        data["subjectAreas"],
        data["projects"],
        filters,
        limit,
        udp_defs,
    )


def view_rows(views: list[dict], table_name_by_id: dict[str, str]) -> list[dict]:
    """Filas por vista para el export por niveles. Puro.

    Fuentes resueltas a `schema.tabla` legible y `columns` = detalle columna a
    columna del editor de vistas: alias de salida, tabla/columna de origen,
    casteo (`castType`) y transformación (`expression`). Se incluyen además
    `filter` (WHERE de la vista), `joinOverride` y el `sql` completo.
    """
    rows: list[dict] = []
    for v in views:
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


async def list_column_rows(table_id: str | None = None, table_ids: list[str] | None = None,
                           limit: int | None = None) -> list[dict]:
    """Detalle a nivel columna; todas, las de una tabla o de un lote de tablas.
    Sin filtro de tabla se aplica un tope (`limit` o `UNFILTERED_COLUMNS_CAP`)."""
    if not table_id and not table_ids:
        limit = limit or UNFILTERED_COLUMNS_CAP
    columns = await repository.columns(table_id, table_ids, limit)
    parent_domains = await repository.parent_domains()
    udp_defs = await repository.udp_definitions()
    return column_rows(columns, parent_domains, udp_defs)


async def list_view_rows(table_ids: list[str] | None = None,
                         schema: str | None = None) -> list[dict]:
    """Vistas para el export por niveles; con `table_ids`, las derivadas de
    esas tablas; con `schema`, las de ese esquema (Database Explorer, doc 18).
    Resuelve los nombres `schema.tabla` de TODAS las fuentes."""
    limit = None if (table_ids or schema) else UNFILTERED_VIEWS_CAP
    views = await repository.views_for_tables(table_ids, limit, schema=schema)
    src_ids = sorted({t for v in views for t in (v.get("sourceTableIds") or [])}
                     | {v["tableId"] for v in views if v.get("tableId")})
    names = await repository.table_names(src_ids)
    name_by_id = {
        tid: (f"{d.get('schema')}.{d.get('physicalName')}" if d.get("schema")
              else (d.get("physicalName") or tid))
        for tid, d in names.items()
    }
    return view_rows(views, name_by_id)
