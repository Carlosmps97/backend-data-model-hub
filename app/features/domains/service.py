"""Lógica de negocio de `domains`. `cascade_filter` es puro (testeable)."""
from __future__ import annotations

from . import repository
from .schemas import ParentDomainBody


def cascade_filter(domain_id: str) -> dict:
    """Query de cascada: columnas ACTIVAS de este dominio SIN override manual
    (el filtro flgactive evita re-tipar columnas soft-deleted y sub-contarlas)."""
    return {"parentDomainId": domain_id, "typeOverridden": {"$ne": True},
            "flgactive": {"$ne": False}}


def logical_cascade_filter(domain_id: str) -> dict:
    """Doc 69: cascada de la faceta LÓGICA — columnas activas del dominio sin
    override del tipo lógico (independiente del override físico)."""
    return {"parentDomainId": domain_id, "logicalTypeOverridden": {"$ne": True},
            "flgactive": {"$ne": False}}


def summarize_impact(groups: list[dict], names: dict[str, dict], models_affected: int,
                     q: str | None = None, offset: int = 0, limit: int = 200) -> dict:
    """Resumen de impacto AGRUPADO POR TABLA (F2 #2). Puro (testeable).

    `groups` = salida del $group por tableId sobre canonical_columns
    (`{_id, columns, overridden}`); `names` = tableId → {schema, physicalName,
    logicalName} (join batch, NO $lookup). Los TOTALES de columnas y de modelos
    son GLOBALES (no dependen de q); `totalTables` cuenta las tablas que
    matchean `q` (sin q = todas) para que la UI pagine la lista filtrada."""
    columns_using = sum(g.get("columns", 0) for g in groups)
    overridden = sum(g.get("overridden", 0) for g in groups)
    rows = []
    for g in groups:
        t = names.get(g["_id"]) or {}
        rows.append({
            "tableId": g["_id"],
            "schema": t.get("schema"),
            "physicalName": t.get("physicalName") or "",
            "logicalName": t.get("logicalName") or "",
            "columns": g.get("columns", 0),
            "overridden": g.get("overridden", 0),
        })
    if q and q.strip():
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r["physicalName"].lower()
                or needle in r["logicalName"].lower()]
    rows.sort(key=lambda r: (r["physicalName"].lower(), r["tableId"]))
    return {
        "columnsUsing": columns_using,
        "willUpdate": columns_using - overridden,
        "overridden": overridden,
        "totalTables": len(rows),
        "modelsAffected": models_affected,
        "tables": rows[offset:offset + limit],
    }


async def list_domains(project_id: str) -> list[dict]:
    return await repository.list_domains(project_id)


async def create_domain(project_id: str, body: ParentDomainBody) -> dict:
    return await repository.create_domain(project_id, body.model_dump(exclude_none=True))


async def update_domain(domain_id: str, body: ParentDomainBody) -> dict | None:
    # La cascada (respetando override manual) la resuelve el repositorio con el
    # tipo VIEJO del dominio; ver `repository.update_domain`.
    return await repository.update_domain(domain_id, body.model_dump(exclude_none=True), cascade=True)


async def delete_domain(domain_id: str) -> bool:
    return await repository.delete_domain(domain_id)


async def impact(domain_id: str, q: str | None = None,
                 offset: int = 0, limit: int = 200) -> dict:
    """Impacto DETALLADO de propagar el tipo del dominio (F2 #2): totales de
    columnas + tablas agrupadas (búsqueda `q` server-side + paginación) + nº de
    modelos (canvases) afectados. No muta nada."""
    groups = await repository.impact_groups(domain_id)
    table_ids = [g["_id"] for g in groups if g.get("_id")]
    names = await repository.tables_by_ids(table_ids)
    models = await repository.models_affected(table_ids)
    return summarize_impact(groups, names, models, q=q, offset=offset, limit=limit)


async def propagate(domain_id: str) -> dict:
    """Aplica los tipos ACTUALES del dominio (físico y lógico, cada uno a las
    columnas SIN override de ESA faceta — doc 69). Devuelve `{updated,
    updatedLogical}`. Sin dominio o sin tipo, no actualiza nada."""
    data_type, logical_type = await repository.get_domain_types(domain_id)
    updated = await repository.propagate_type(cascade_filter(domain_id), data_type) if data_type else 0
    updated_logical = (await repository.propagate_field(logical_cascade_filter(domain_id), "logicalDataType", logical_type)
                       if logical_type else 0)
    return {"updated": updated, "updatedLogical": updated_logical}
