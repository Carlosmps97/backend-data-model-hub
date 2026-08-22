"""Endpoints de Parent Domains."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.features.auth.deps import write_guard

from app.core.api.envelope import ok

from . import service
from .schemas import ParentDomainBody

router = APIRouter(prefix="/api/domains", tags=["domains"],
                   dependencies=[Depends(write_guard("standards.edit"))])


@router.get("")
async def list_domains():
    return ok(await service.list_domains())


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_domain(body: ParentDomainBody):
    return ok(await service.create_domain(body))


@router.put("/{domain_id}")
async def update_domain(domain_id: str, body: ParentDomainBody):
    return ok(await service.update_domain(domain_id, body))


@router.delete("/{domain_id}")
async def delete_domain(domain_id: str):
    return ok(await service.delete_domain(domain_id))


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


@router.post("/{domain_id}/propagate")
async def domain_propagate(domain_id: str):
    """Aplica el `defaultDataType` actual del dominio a sus columnas sin
    override (retroactivo, directo sobre canonical_columns)."""
    return ok(await service.propagate(domain_id))
