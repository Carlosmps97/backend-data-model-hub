"""Lógica de negocio de `domains`. `cascade_filter` es puro (testeable)."""
from __future__ import annotations

from . import repository
from .schemas import ParentDomainBody


def cascade_filter(domain_id: str) -> dict:
    """Query de cascada: columnas ACTIVAS de este dominio SIN override manual
    (el filtro flgactive evita re-tipar columnas soft-deleted y sub-contarlas)."""
    return {"parentDomainId": domain_id, "typeOverridden": {"$ne": True},
            "flgactive": {"$ne": False}}


def summarize_impact(columns: list[dict]) -> dict:
    """Resumen de impacto puro (testeable) a partir de las columnas que usan el
    dominio. `willUpdate` = columnas sin override (se re-derivan al propagar);
    `overridden` = columnas con override manual (se saltan). Incluye una lista
    plana de las afectadas para la UI."""
    affected = [
        {
            "id": c.get("id"),
            "physicalName": c.get("physicalName"),
            "tableId": c.get("tableId"),
            "overridden": bool(c.get("typeOverridden")),
        }
        for c in columns
    ]
    overridden = sum(1 for c in affected if c["overridden"])
    return {
        "columnsUsing": len(affected),
        "willUpdate": len(affected) - overridden,
        "overridden": overridden,
        "columns": affected,
    }


async def list_domains() -> list[dict]:
    return await repository.list_domains()


async def create_domain(body: ParentDomainBody) -> dict:
    return await repository.create_domain(body.model_dump(exclude_none=True))


async def update_domain(domain_id: str, body: ParentDomainBody) -> dict | None:
    # La cascada (respetando override manual) la resuelve el repositorio con el
    # tipo VIEJO del dominio; ver `repository.update_domain`.
    return await repository.update_domain(domain_id, body.model_dump(exclude_none=True), cascade=True)


async def delete_domain(domain_id: str) -> bool:
    return await repository.delete_domain(domain_id)


async def impact(domain_id: str) -> dict:
    """Impacto de propagar el tipo del dominio: cuántas columnas se actualizarán
    (sin override) vs se saltarán (con override). No muta nada."""
    raw = await repository.columns_using(domain_id)
    # Normaliza `_id`→`id` (proyección Mongo) antes del resumen puro.
    cols = [{**c, "id": str(c.get("_id", c.get("id")))} for c in raw]
    return summarize_impact(cols)


async def propagate(domain_id: str) -> dict:
    """Aplica el `defaultDataType` ACTUAL del dominio a todas sus columnas SIN
    override (update directo, retroactivo). Devuelve `{updated}`. Si el dominio
    no existe o no tiene tipo, no actualiza nada."""
    data_type = await repository.get_default_data_type(domain_id)
    if not data_type:
        return {"updated": 0}
    updated = await repository.propagate_type(cascade_filter(domain_id), data_type)
    return {"updated": updated}
