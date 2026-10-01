"""Endpoints de Parent Domains."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.core.api.envelope import ok
from app.features.data_standards.deps import standards_read_only
from app.features.projects.deps import alive_project

from . import service
from .schemas import ParentDomainBody

# Doc 75 D3/D4: dominios POR PROYECTO — prefijo `/api/projects/{project_id}/domains`.
# Doc 105 (D1b): el router sólo LEE. Sus escrituras (alta, edición, baja y
# `propagate`) siguen declaradas pero responden 409 en `standards_read_only`:
# los dominios se cambian por Data Standards (`standards/apply`, versionado).
router = APIRouter(prefix="/api/projects/{project_id}/domains", tags=["domains"],
                   dependencies=[Depends(standards_read_only), Depends(alive_project)])


@router.get("")
async def list_domains(project_id: str):
    return ok(await service.list_domains(project_id))


# ── Cerradas (doc 105 D1b): 409 antes de cualquier efecto ──────────────────
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_domain(project_id: str, body: ParentDomainBody):
    return ok(await service.create_domain(project_id, body))


@router.put("/{domain_id}")
async def update_domain(domain_id: str, body: ParentDomainBody):
    return ok(await service.update_domain(domain_id, body))


@router.delete("/{domain_id}")
async def delete_domain(domain_id: str):
    return ok(await service.delete_domain(domain_id))


@router.post("/{domain_id}/propagate")
async def domain_propagate(domain_id: str):
    """Aplicaba el `defaultDataType` actual del dominio a sus columnas sin
    override, directo sobre canonical_columns (cerrada, doc 105 D1b)."""
    return ok(await service.propagate(domain_id))


# ── Lecturas ────────────────────────────────────────────────────────────────
@router.get("/{domain_id}/impact")
async def domain_impact(
    domain_id: str,
    q: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Impacto detallado del dominio (F2 #2): totales globales + tablas
    agrupadas con búsqueda server-side por nombre (`q`) y paginación."""
    return ok(await service.impact(domain_id, q=q, offset=offset, limit=limit))


@router.get("/{domain_id}/impact/columns")
async def domain_column_impact(
    domain_id: str,
    physicalTo: str | None = Query(default=None),
    logicalTo: str | None = Query(default=None),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(change|override|differs)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=0, le=2000),
):
    """Doc 95 D7: impacto EXACTO por columna (misma regla que la cascada):
    totales por estado + filas con búsqueda (`q`) y filtro (`status`)
    server-side, paginadas. `limit=0` = solo totales (panel del dominio)."""
    return ok(await service.column_impact(domain_id, physicalTo, logicalTo, q=q, status=status,
                                          offset=offset, limit=limit))
