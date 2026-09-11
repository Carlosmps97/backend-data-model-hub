"""Lecturas del contexto de la carga masiva (doc 55). Todo pasa por los
repositories de las features dueñas (invariante del store); ninguna escritura.

- `load_context`: estructura completa (proyectos/carpetas/canvases/esquemas —
  colecciones chicas), pool de tablas PROYECTADO (a escala, bajar docs
  completos por request no es viable) y estándares vivos por scope.
- `load_columns`: columnas efectivas SOLO de las tablas existentes que el
  workbook referencia (tandas de 500 ids, índice `tableId`).
- `load_folders`: solo las carpetas efectivas (doc 87: candidatas a proyecto
  destino para el popup).
- `load_profile`: el perfil de carga del proyecto (doc 78).
"""
from __future__ import annotations

from app.core.scope import scoped
from app.core.versioning import overlay
from app.features.changesets import repository as cs_repo
from app.features.domains import repository as domains_repo
from app.features.glossary import repository as glossary_repo
from app.features.projects import repository as projects_repo
from app.features.settings import service as settings_service
from app.features.udp import repository as udp_repo

from .context import UploadContext
from .profiles import repository as profiles_repo

TABLE_PROJECTION = {"physicalName": 1, "logicalName": 1, "schema": 1, "description": 1, "udpValues": 1}
VIEW_PROJECTION = {"name": 1, "schema": 1, "sourceTableIds": 1}
_SCOPES = ("table", "column")
_ID_BATCH = 500


async def _effective(cs_id: str, collection: str, flt: dict | None = None,
                     projection: dict | None = None) -> list[dict]:
    pub = await cs_repo.published(collection, flt, projection=projection)
    changes = (await cs_repo.changes_map(cs_id, [collection])).get(collection, {})
    return overlay(pub, changes)


async def load_context(cs_id: str) -> UploadContext:
    """Foto efectiva del changeset + Data Standards vivos DEL PROYECTO del
    changeset (doc 75 D3/D6)."""
    cs = await cs_repo.get(cs_id)
    pid = (cs or {}).get("projectId") or ""
    project = await projects_repo.get_project(pid) if pid else None
    ctx = UploadContext(
        project_id=pid,
        project_name=(project or {}).get("name") or "",
        folders=await _effective(cs_id, "folders", scoped(pid)),
        canvases=await _effective(cs_id, "subject_areas", scoped(pid)),
        schemas=await _effective(cs_id, "schemas", scoped(pid)),
        tables=await _effective(cs_id, "canonical_tables", scoped(pid), projection=TABLE_PROJECTION),
        views=await _effective(cs_id, "views", scoped(pid), projection=VIEW_PROJECTION),
        udp_defs=await udp_repo.list_udp(pid),
        domains=await domains_repo.list_domains(pid),
    )
    for scope in _SCOPES:
        ctx.naming[scope] = await settings_service.get_naming_for(pid, scope)
        entries = await glossary_repo.list_entries(pid, scope)
        ctx.glossary[scope] = {e["term"]: e["abbrev"] for e in entries}
    return ctx


async def load_folders(cs_id: str) -> list[dict]:
    """Carpetas EFECTIVAS del proyecto del changeset (doc 87 §3.5): alimentan
    `upload_targets` para el popup, sin cargar el resto del contexto."""
    cs = await cs_repo.get(cs_id)
    pid = (cs or {}).get("projectId") or ""
    return await _effective(cs_id, "folders", scoped(pid)) if pid else []


async def load_columns(cs_id: str, table_ids: list[str]) -> dict[str, list[dict]]:
    """Columnas efectivas de `table_ids`, agrupadas por tabla. Los cambios del
    draft se acotan al slice (una columna pendiente de OTRA tabla no entra)."""
    if not table_ids:
        return {}
    wanted = set(table_ids)
    pub: list[dict] = []
    for i in range(0, len(table_ids), _ID_BATCH):
        batch = table_ids[i:i + _ID_BATCH]
        pub.extend(await cs_repo.published("canonical_columns", {"tableId": {"$in": batch}}))
    changes = (await cs_repo.changes_map(cs_id, ["canonical_columns"])).get("canonical_columns", {})
    in_slice = {d["id"] for d in pub}
    scoped = {eid: ch for eid, ch in changes.items()
              if eid in in_slice or (ch.get("payload") or {}).get("tableId") in wanted}
    out: dict[str, list[dict]] = {}
    for col in overlay(pub, scoped):
        tid = col.get("tableId")
        if tid in wanted:
            out.setdefault(tid, []).append(col)
    for cols in out.values():
        cols.sort(key=lambda c: (c.get("ordinal") or 0, str(c.get("physicalName") or "")))
    return out


async def load_profile(project_id: str, profile_id: str) -> dict | None:
    """Perfil de carga activo DEL PROYECTO (doc 78); None si no existe."""
    return await profiles_repo.get_profile(project_id, profile_id)
