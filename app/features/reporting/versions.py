"""Doc 102 — versión del Reporting: producción (sin `changesetId`) o una versión
PROPIA sin publicar (draft / en revisión) del MISMO proyecto. Un solo lugar
decide si el id vale y trae el mapa de cambios que el service superpone
(`draft.py`). Nunca cae a producción en silencio: 404 / 409 / 403 legibles."""
from __future__ import annotations

from fastapi import HTTPException, status

from app.core.identity import Principal
from app.features.auth import service as auth_service
from app.features.changesets import repository as cs_repo
from app.features.changesets.access import FORBIDDEN_DETAIL, can_view

OPEN_STATUSES = ("draft", "submitted")

# Colecciones que lee cada endpoint (solo esas se traen del ledger). Las
# columnas no van aquí: cada lote lee SOLO sus cambios (service.list_column_rows),
# y /tables sólo su `op` + `tableId` (doc 105, A7: `table_changes`). Los lotes
# de relaciones (`POST /insights/relationships/query`) tampoco: leen sólo su
# tajada del ledger (`views.relationships_lot_report`); RELATIONSHIP_INPUTS es
# del GET de compatibilidad (una lectura por request, con tope).
TABLE_INPUTS = ["folders", "subject_areas", "canonical_tables", "relationships"]
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
    # Doc 105 (A7): la visibilidad se decide sobre la cabecera YA leída, con la
    # misma regla del canvas (`can_view`); `ensure_changeset_visible` la volvía
    # a leer en cada request.
    user = await auth_service.resolve_session_user(principal.username)
    if not can_view(cs, principal.username, (user or {}).get("permissions")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN_DETAIL)
    return changeset_id


async def version_changes(project_id: str, changeset_id: str | None, principal: Principal,
                          collections: list[str]) -> dict[str, dict] | None:
    """None = producción. Si no, {colección: {entityId: cambio}} de la versión
    (vacío si no tiene cambios en esas colecciones)."""
    cs_id = await open_version(project_id, changeset_id, principal)
    if cs_id is None:
        return None
    return await cs_repo.changes_map(cs_id, collections)


async def table_changes(project_id: str, changeset_id: str | None,
                        principal: Principal) -> dict[str, dict] | None:
    """`version_changes` de /tables: None = producción; si no, los cambios de
    `TABLE_INPUTS` + los de columnas PROYECTADOS (doc 105, A7)."""
    cs_id = await open_version(project_id, changeset_id, principal)
    if cs_id is None:
        return None
    return await table_changes_of(cs_id)


async def table_changes_of(cs_id: str) -> dict[str, dict]:
    """Doc 105 (revisión, hallazgo 5): la clave de columnas sólo si la versión
    las toca — un mapa vacío es «igual a producción» y /tables con `limit`
    vuelve a la página rápida (con `{"canonical_columns": {}}` armaba siempre
    el proyecto entero, aun con un draft sin cambios)."""
    changes = await cs_repo.changes_map(cs_id, TABLE_INPUTS)
    columns = await cs_repo.column_change_tables(cs_id)
    if columns:
        changes["canonical_columns"] = columns
    return changes
