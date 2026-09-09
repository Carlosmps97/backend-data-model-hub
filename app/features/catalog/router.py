"""Endpoints del catálogo canónico. Doc 75 D6/D4: las lecturas de ALCANCE
(tablas, columnas, buscador, inventario) viven bajo `/api/projects/{pid}/catalog`;
las operaciones por id de entidad (columnas de una tabla, uso, inspector) siguen
en `/api/catalog`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal
from app.features.auth.deps import write_guard
from app.features.changesets.access import ensure_changeset_visible
from app.features.projects.deps import alive_project

from . import service
from .schemas import CanonicalColumnBody, CanonicalTableBody

router = APIRouter(prefix="/api/catalog", tags=["catalog"],
                   dependencies=[Depends(write_guard("model.edit"))])

projects_catalog_router = APIRouter(prefix="/api/projects/{project_id}/catalog", tags=["catalog"],
                                    dependencies=[Depends(write_guard("model.edit")), Depends(alive_project)])


@projects_catalog_router.get("/tables")
async def list_tables(
    project_id: str,
    q: str | None = Query(default=None, description="búsqueda por nombre (contains, case-insensitive)"),
    limit: int | None = Query(default=None, ge=1, le=500),
    schema: str | None = Query(default=None, description="acota a UN esquema (filtro del modal Import existing)"),
):
    """Tablas del proyecto. `q`+`limit` = búsqueda server-side (modales de
    catálogo); `schema` acota a un esquema (combinable con `q`)."""
    return ok(await service.list_tables(project_id, q, limit, schema))


@projects_catalog_router.post("/tables", status_code=status.HTTP_201_CREATED)
async def create_table(project_id: str, body: CanonicalTableBody):
    return ok(await service.create_table(project_id, body))


@projects_catalog_router.get("/columns")
async def search_columns(
    project_id: str,
    q: str = Query(min_length=1, description="búsqueda por nombre de columna (contains, case-insensitive)"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Búsqueda por COLUMNA del proyecto: hits con su tabla resuelta (`table` +
    `schema`) para mostrarse como esquema.tabla."""
    return ok(await service.search_columns(project_id, q, limit))


@projects_catalog_router.get("/search")
async def search_model(
    project_id: str,
    q: str = Query(default="", max_length=120, description="contains, case-insensitive; vacío = navegar el alcance de los filtros"),
    limit: int = Query(default=25, ge=1, le=100, description="tope POR grupo (tamaño de página)"),
    offset: int = Query(default=0, ge=0, le=1_000_000, description="página siguiente de cada grupo (doc 72 r2)"),
    changesetId: str | None = Query(default=None),
    scope: str = Query(default="all", pattern="^(all|tables|columns|definitions)$"),
    folderId: str | None = Query(default=None, description="sólo tablas en canvases de esta carpeta y su subárbol (sub-proyecto o dominio)"),
    canvasId: str | None = Query(default=None, description="sólo tablas de este modelo (canvas)"),
    principal: Principal = Depends(current_principal),
):
    """Doc 70 §11 — buscador del Model dentro del proyecto: tablas, columnas y
    definiciones de columna, cada hit con su tabla y los canvases donde está
    (redirigir + centrar). `scope` acota los grupos; `folderId` (sub-proyecto o
    dominio: alcanza su subárbol) / `canvasId` acotan a las tablas de esos
    canvases. Con `changesetId` aplica el overlay del draft (también `asof:`)."""
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    return ok(await service.search_model(project_id, q, limit, changesetId, scope=scope,
                                         folder_id=folderId, canvas_id=canvasId, offset=offset))


@projects_catalog_router.get("/inventory")
async def project_inventory(project_id: str, changesetId: str | None = Query(default=None),
                            principal: Principal = Depends(current_principal)):
    """Doc 72 §1 — inventario del proyecto para el Explorer: sus tablas (con
    `columnCount`) y las vistas de esas tablas en UNA request; con
    `changesetId` aplica el overlay del draft (también `asof:`)."""
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    return ok(await service.project_inventory(project_id, changesetId))


@projects_catalog_router.get("/views/{view_id}/columns")
async def view_columns(project_id: str, view_id: str, changesetId: str | None = Query(default=None),
                       principal: Principal = Depends(current_principal)):
    """Columnas de salida de UNA vista, al expandirla en el Explorer: el
    inventario sólo trae el conteo (como con las tablas), así no viajan las
    columnas de las ~2 000 vistas del proyecto. Con `changesetId` aplica el
    overlay del draft (también `asof:`)."""
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    cols = await service.view_columns(view_id, changesetId)
    if cols is None:
        raise HTTPException(status_code=404, detail="View not found.")
    return ok(cols)


@router.get("/tables/{table_id}/columns")
async def list_columns(table_id: str):
    return ok(await service.list_columns(table_id))


@router.post("/tables/{table_id}/columns", status_code=status.HTTP_201_CREATED)
async def create_column(table_id: str, body: CanonicalColumnBody):
    return ok(await service.create_column(table_id, body))


@router.get("/tables/{table_id}/usage")
async def table_usage(table_id: str, changesetId: str | None = Query(default=None),
                      principal: Principal = Depends(current_principal)):
    """Dónde se usa la tabla (V3, doc 19 §12b): canvases que la referencian con
    carpeta y proyecto; con `changesetId` aplica el overlay del draft."""
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    return ok(await service.table_usage(table_id, changesetId))


@router.get("/inspect/tables/{table_id}")
async def inspect_table(table_id: str, changesetId: str | None = Query(default=None),
                        principal: Principal = Depends(current_principal)):
    """Doc 72 §3 — Object Inspector: tabla + columnas efectivas + relaciones +
    vistas + dónde se usa, en UNA request y sin necesidad de canvas."""
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    out = await service.inspect_table(table_id, changesetId)
    if out is None:
        raise HTTPException(status_code=404, detail="Table not found.")
    return ok(out)


@router.get("/inspect/views/{view_id}")
async def inspect_view(view_id: str, changesetId: str | None = Query(default=None),
                       principal: Principal = Depends(current_principal)):
    """Doc 72 §3 — Object Inspector de una vista: vista efectiva + tablas
    fuente + canvases donde es miembro."""
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    out = await service.inspect_view(view_id, changesetId)
    if out is None:
        raise HTTPException(status_code=404, detail="View not found.")
    return ok(out)
