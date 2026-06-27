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
    columns: list[dict],
    relationships: list[dict],
    subject_areas: list[dict],
    projects: list[dict],
    filters: dict | None = None,
) -> list[dict]:
    """Construye las filas del reporte por tabla. Puro. Ver docstring del módulo."""
    filters = filters or {}
    tables_to_canvases = _index_tables_to_canvases(subject_areas)
    projects_by_id = {p["id"]: (p.get("name") or "") for p in projects}

    col_count: dict[str, int] = {}
    for c in columns:
        tid = c.get("tableId")
        if tid is not None:
            col_count[tid] = col_count.get(tid, 0) + 1

    rel_count: dict[str, int] = {}
    for r in relationships:
        for tid in (r.get("sourceTableId"), r.get("targetTableId")):
            if tid is not None:
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
            "description": t.get("description"),
        }
        if _matches(row, project_ids, filters):
            rows.append(row)

    rows.sort(key=lambda r: (r.get("physicalName") or "").lower())
    return rows


def column_rows(columns: list[dict], parent_domains: list[dict]) -> list[dict]:
    """Filas a nivel columna para el export por niveles. Puro.

    `parentDomain` = nombre del Parent Domain (vía `parentDomainId`), no el id,
    para que el export sea legible. Ordenadas por (tableId, ordinal).
    """
    domain_name_by_id = {d["id"]: (d.get("name") or "") for d in parent_domains}
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
                "description": c.get("description"),
                "ordinal": c.get("ordinal", 0),
            }
        )
    rows.sort(key=lambda r: ((r.get("tableId") or ""), r.get("ordinal") or 0))
    return rows


async def list_table_rows(filters: dict | None = None) -> list[dict]:
    """Filas del reporte por tabla (todas las tablas, filtradas en Python)."""
    data = await repository.report_inputs()
    return table_rows(
        data["tables"],
        data["columns"],
        data["relationships"],
        data["subjectAreas"],
        data["projects"],
        filters,
    )


async def list_column_rows(table_id: str | None = None) -> list[dict]:
    """Detalle a nivel columna; todas o las de una tabla (`tableId`)."""
    columns = await repository.columns(table_id)
    parent_domains = await repository.parent_domains()
    return column_rows(columns, parent_domains)
