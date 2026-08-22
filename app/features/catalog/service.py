"""Negocio de `catalog`: deriva físico (vía diccionario) + dataType (vía dominio)."""
from __future__ import annotations

from app.features.glossary import service as glossary_service

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


async def list_tables(q: str | None = None, limit: int | None = None,
                      schema: str | None = None) -> list[dict]:
    return await repository.list_tables(q, limit, schema)


async def search_columns(q: str, limit: int = 50) -> list[dict]:
    """Búsqueda por COLUMNA del Database Explorer: cada hit sale con su tabla
    resuelta (`table` = nombre físico + `schema`) para mostrarse como
    esquema.tabla. Hits cuya tabla ya no está activa se descartan (huérfanos)."""
    cols = await repository.search_columns(q, limit)
    table_ids = sorted({c["tableId"] for c in cols if c.get("tableId")})
    tables = {t["id"]: t for t in await repository.list_tables_by_ids(table_ids)}
    out: list[dict] = []
    for c in cols:
        t = tables.get(c.get("tableId") or "")
        if not t:
            continue
        out.append({**c, "table": t.get("physicalName") or "", "schema": t.get("schema")})
    return out


async def create_table(body: CanonicalTableBody) -> dict:
    data = body.model_dump(exclude_none=True, by_alias=True)
    if not data.get("physicalName"):
        data["physicalName"] = await glossary_service.physicalize_name(body.logicalName)
    return await repository.create_table(data)


async def list_columns(table_id: str) -> list[dict]:
    return await repository.list_columns(table_id)


async def create_column(table_id: str, body: CanonicalColumnBody) -> dict:
    physical = body.physicalName or await glossary_service.physicalize_name(body.logicalName)
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
        "udpValues": body.udpValues or {},
        **derived,
    })


# ── Uso de la tabla (V3, doc 19 §12b): proyecto › carpeta › canvas ──────────


async def table_usage(table_id: str, changeset_id: str | None = None) -> dict:
    """Canvases (subject areas) que referencian la tabla, con su carpeta y
    proyecto resueltos. Con `changeset_id`, el slice de canvases aplica el
    OVERLAY del draft (un canvas nuevo o una membresía pendiente cuentan).

    Contrato: {"usage": [{canvasId, canvas, folderId, folder, projectId,
    project}], "total": int} — ordenado por proyecto › carpeta › canvas."""
    from app.core.versioning import overlay
    from app.features.changesets import repository as cs_repo

    sas = await repository.canvases_with_table(table_id)
    ch_map: dict = {}
    if changeset_id:
        # projects/folders también: sus NOMBRES pueden vivir solo en el draft.
        ch_map = await cs_repo.changes_map(
            changeset_id, ["subject_areas", "projects", "folders"])
        ch = ch_map.get("subject_areas", {})
        in_slice = {s["id"] for s in sas}

        def _touches(c: dict) -> bool:
            return table_id in ((c.get("payload") or {}).get("tableIds") or [])

        sas = overlay(sas, {eid: c for eid, c in ch.items()
                            if eid in in_slice or _touches(c)})
        sas = [s for s in sas if table_id in (s.get("tableIds") or [])]

    folder_ids = sorted({s.get("folderId") for s in sas if s.get("folderId")})
    project_ids = sorted({s.get("projectId") for s in sas if s.get("projectId")})
    folders = await repository.names_by_ids("folders", folder_ids)
    projects = await repository.names_by_ids("projects", project_ids)
    # Overlay de NOMBRES (doc 21b): un proyecto/carpeta CREADO o renombrado en
    # el draft aún no está publicado — sin esto la ruta salía "— › root" para
    # canvases de estructura nueva del propio draft.
    for coll, ids, out in (("folders", folder_ids, folders),
                           ("projects", project_ids, projects)):
        for eid, c in (ch_map.get(coll) or {}).items():
            if eid in ids and c.get("op") != "delete":
                name = (c.get("payload") or {}).get("name")
                if name:
                    out[eid] = name
    rows = [{
        "canvasId": s["id"], "canvas": s.get("name") or s["id"],
        "folderId": s.get("folderId"), "folder": folders.get(s.get("folderId") or ""),
        "projectId": s.get("projectId"), "project": projects.get(s.get("projectId") or ""),
    } for s in sas]
    rows.sort(key=lambda r: ((r["project"] or "").lower(), (r["folder"] or "").lower(),
                             (r["canvas"] or "").lower()))
    return {"usage": rows, "total": len(rows)}
