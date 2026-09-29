"""Doc 102 — versión del Reporting: producción (sin `changesetId`) o una versión
PROPIA sin publicar (draft / en revisión) del MISMO proyecto. Un solo lugar
decide si el id vale y trae el mapa de cambios que el service superpone
(`draft.py`). Nunca cae a producción en silencio: 404 / 409 / 403 legibles."""
from __future__ import annotations

from fastapi import HTTPException, status

from app.core.identity import Principal
from app.features.changesets import repository as cs_repo
from app.features.changesets.access import ensure_changeset_visible

OPEN_STATUSES = ("draft", "submitted")

# Colecciones que lee cada endpoint (solo esas se traen del ledger). Las
# columnas no van aquí: cada lote lee SOLO sus cambios (service.list_column_rows).
TABLE_INPUTS = ["folders", "subject_areas", "canonical_tables", "canonical_columns", "relationships"]
COUNT_INPUTS = ["canonical_tables"]
FILTER_INPUTS = ["folders", "subject_areas", "canonical_tables"]
VIEW_INPUTS = ["views", "canonical_tables", "subject_areas"]
RELATIONSHIP_INPUTS = ["relationships", "canonical_tables", "canonical_columns"]


async def open_version(project_id: str, changeset_id: str | None, principal: Principal) -> str | None:
    """None = producción. Si no, valida que `changeset_id` sea una versión
    PROPIA abierta (draft / en revisión) de ESTE proyecto y la devuelve:
    404 / 409 / 403 legibles, nunca producción en silencio."""
    if not changeset_id:
        return None
    cs = await cs_repo.get(changeset_id)
    if cs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found.")
    if cs.get("projectId") != project_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That version belongs to another project.")
    if cs.get("status") not in OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=("That version is no longer open — it was published or closed. "
                                    "Pick another version."))
    await ensure_changeset_visible(changeset_id, principal)
    return changeset_id


async def version_changes(project_id: str, changeset_id: str | None, principal: Principal,
                          collections: list[str]) -> dict[str, dict] | None:
    """None = producción. Si no, {colección: {entityId: cambio}} de la versión
    (vacío si no tiene cambios en esas colecciones)."""
    cs_id = await open_version(project_id, changeset_id, principal)
    if cs_id is None:
        return None
    return await cs_repo.changes_map(cs_id, collections)
