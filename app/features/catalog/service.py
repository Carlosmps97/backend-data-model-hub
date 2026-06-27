"""Negocio de `catalog`: deriva físico (vía diccionario) + dataType (vía dominio)."""
from __future__ import annotations

from app.features.dictionary import service as dictionary_service

from . import repository
from .schemas import CanonicalColumnBody, CanonicalTableBody


def derive_column(logical: str, physical: str, domain_default: str | None,
                  override: str | None) -> dict:
    """Resuelve físico + dataType de una columna canónica. Si hay `override`
    manual, gana y marca `typeOverridden`; si no, hereda el tipo del dominio."""
    return {
        "physicalName": physical,
        "dataType": override if override is not None else (domain_default or ""),
        "typeOverridden": override is not None,
    }


async def list_tables() -> list[dict]:
    return await repository.list_tables()


async def create_table(body: CanonicalTableBody) -> dict:
    data = body.model_dump(exclude_none=True, by_alias=True)
    if not data.get("physicalName"):
        data["physicalName"] = await dictionary_service.physicalize_name(body.logicalName)
    return await repository.create_table(data)


async def list_columns(table_id: str) -> list[dict]:
    return await repository.list_columns(table_id)


async def create_column(table_id: str, body: CanonicalColumnBody) -> dict:
    physical = body.physicalName or await dictionary_service.physicalize_name(body.logicalName)
    default = await repository.domain_default(body.parentDomainId) if body.parentDomainId else None
    derived = derive_column(body.logicalName, physical, default, body.dataType)
    return await repository.create_column({
        "tableId": table_id,
        "logicalName": body.logicalName,
        "parentDomainId": body.parentDomainId,
        "isPrimaryKey": body.isPrimaryKey,
        "isForeignKey": body.isForeignKey,
        "isNullable": body.isNullable,
        "isPartition": body.isPartition,
        "description": body.description,
        "ordinal": body.ordinal,
        **derived,
    })
