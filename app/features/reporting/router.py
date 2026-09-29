"""Endpoints de `reporting` (tabla de metadata + detalle por columna, pr).
Doc 75: el reporte es de UN proyecto — `projectId` obligatorio en todas las
lecturas. Doc 102: `changesetId` opcional = una versión PROPIA sin publicar
(draft / en revisión) superpuesta a producción; los LOTES de ids viajan por
POST (en la URL, cientos de ids devolvían 431)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal

from . import service, versions
from .schemas import ReportBatchBody

router = APIRouter(prefix="/api/reporting", tags=["reporting"])


def _ids(values: list[str]) -> list[str]:
    """Ids sin vacíos ni repetidos, en su orden. Puro."""
    return list(dict.fromkeys(v for v in values if v))


def _lot(values: list[str]) -> list[str]:
    """Ids del LOTE sin vacíos ni repetidos; 422 si no queda ninguno — un lote
    vacío NO es «todo el proyecto» (ese camino es sin lote, con tope)."""
    ids = _ids(values)
    if not ids:
        raise HTTPException(status_code=422, detail="At least one table id is required.")
    return ids


@router.get("/tables")
async def report_tables(
    projectId: str = Query(min_length=1),
    schema: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
    changesetId: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
):
    """Filas del reporte por tabla del proyecto. Filtro opcional por `schema`;
    `limit` acota la cantidad (carga inicial liviana del front) y `offset`
    (doc 92 D3) pagina el scroll infinito — sólo aplica en el fast path sin
    filtros (orden `physicalName`). Doc 102: `changesetId` = versión propia."""
    changes = await versions.version_changes(projectId, changesetId, principal, versions.TABLE_INPUTS)
    filters = {"schema": schema} if schema is not None else {}
    return ok(await service.list_table_rows(projectId, filters, limit, offset, changes=changes))


@router.get("/tables/count")
async def report_tables_count(projectId: str = Query(min_length=1),
                              changesetId: str | None = Query(default=None),
                              principal: Principal = Depends(current_principal)):
    """Doc 92 D4: total de tablas activas del proyecto (conteo real de la barra
    del Reporting y del Select all, aunque la grilla tenga sólo una página).
    Doc 102: `changesetId` = versión propia."""
    changes = await versions.version_changes(projectId, changesetId, principal, versions.COUNT_INPUTS)
    return ok({"total": await service.count_tables(projectId, changes)})


@router.get("/filters")
async def report_filters(projectId: str = Query(min_length=1),
                         changesetId: str | None = Query(default=None),
                         principal: Principal = Depends(current_principal)):
    """Doc 70 §4.1: universo completo de los filtros del reporte tabular
    (esquemas de tablas activas y canvases del proyecto) — sin tope. Doc 102:
    `changesetId` = versión propia."""
    changes = await versions.version_changes(projectId, changesetId, principal, versions.FILTER_INPUTS)
    return ok(await service.filter_options(projectId, changes))


@router.get("/columns")
async def report_columns(
    projectId: str = Query(min_length=1),
    tableId: str | None = Query(default=None),
    tableIds: str | None = Query(default=None, description="ids separados por coma (compat: el front usa POST /columns/query)"),
    limit: int | None = Query(default=None, ge=1, le=100000),
    changesetId: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
):
    """Detalle a nivel columna para el export por niveles. `tableIds` acota el
    export a las tablas seleccionadas; sin filtros se aplica un tope de seguridad
    (`limit` o el cap por defecto) para no volcar cientos de miles de columnas.
    Doc 102: los lotes van por `POST /columns/query`; `tableIds` queda por
    compatibilidad durante el deploy."""
    cs_id = await versions.open_version(projectId, changesetId, principal)
    id_list = [s for s in (tableIds.split(",") if tableIds else []) if s] or None
    return ok(await service.list_column_rows(projectId, tableId, id_list, limit, changeset_id=cs_id))


@router.post("/columns/query")
async def report_columns_batch(body: ReportBatchBody, principal: Principal = Depends(current_principal)):
    """Doc 102 (431): columnas de un LOTE de tablas con los ids en el CUERPO."""
    ids = _lot(body.tableIds)
    cs_id = await versions.open_version(body.projectId, body.changesetId, principal)
    return ok(await service.list_column_rows(body.projectId, None, ids, None, changeset_id=cs_id))


@router.get("/views")
async def report_views(
    projectId: str = Query(min_length=1),
    tableIds: str | None = Query(default=None, description="ids separados por coma (compat: el front usa POST /views/query)"),
    schema: str | None = Query(default=None, description="solo las vistas de este esquema (Database Explorer)"),
    changesetId: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
):
    """Vistas para el export por niveles: fuentes resueltas (`schema.tabla`),
    SQL y detalle columna a columna (alias de salida, origen, casteo,
    expresión). `tableIds` acota a las vistas derivadas de las tablas
    seleccionadas; `schema` a las de un esquema (Database Explorer); sin
    filtros aplica un tope de seguridad. Doc 102: `changesetId` = versión propia."""
    changes = await versions.version_changes(projectId, changesetId, principal, versions.VIEW_INPUTS)
    id_list = [s for s in (tableIds.split(",") if tableIds else []) if s] or None
    return ok(await service.list_view_rows(projectId, id_list, schema=schema, changes=changes))


@router.post("/views/query")
async def report_views_batch(body: ReportBatchBody, principal: Principal = Depends(current_principal)):
    """Doc 102 (431): vistas derivadas de un LOTE de tablas con los ids en el CUERPO."""
    ids = _lot(body.tableIds)
    changes = await versions.version_changes(body.projectId, body.changesetId, principal, versions.VIEW_INPUTS)
    return ok(await service.list_view_rows(body.projectId, ids, changes=changes))
