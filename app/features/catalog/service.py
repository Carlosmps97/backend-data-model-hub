"""Negocio de `catalog`: deriva físico (vía diccionario) + dataType (vía dominio).
Doc 75 D6: el catálogo es POR PROYECTO (ya no hay pool universal)."""
from __future__ import annotations

from fastapi import HTTPException

from app.features.glossary import service as glossary_service
from app.features.views import repository as views_repo

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


async def list_tables(project_id: str, q: str | None = None, limit: int | None = None,
                      schema: str | None = None) -> list[dict]:
    return await repository.list_tables(project_id, q, limit, schema)


async def search_columns(project_id: str, q: str, limit: int = 50) -> list[dict]:
    """Búsqueda por COLUMNA del proyecto: cada hit sale con su tabla resuelta
    (`table` = nombre físico + `schema`) para mostrarse como esquema.tabla.
    Hits cuya tabla ya no está activa se descartan (huérfanos)."""
    cols = await repository.search_columns(project_id, q, limit)
    table_ids = sorted({c["tableId"] for c in cols if c.get("tableId")})
    tables = {t["id"]: t for t in await repository.list_tables_by_ids(table_ids)}
    out: list[dict] = []
    for c in cols:
        t = tables.get(c.get("tableId") or "")
        if not t:
            continue
        out.append({**c, "table": t.get("physicalName") or "", "schema": t.get("schema")})
    return out


async def create_table(project_id: str, body: CanonicalTableBody) -> dict:
    data = body.model_dump(exclude_none=True, by_alias=True)
    data["projectId"] = project_id                      # doc 75 I1: lo estampa el servidor
    if not data.get("physicalName"):
        data["physicalName"] = await glossary_service.physicalize_name(project_id, body.logicalName, "table")
    return await repository.create_table(data)


async def list_columns(table_id: str) -> list[dict]:
    return await repository.list_columns(table_id)


async def create_column(table_id: str, body: CanonicalColumnBody) -> dict:
    project_id = await repository.project_of_table(table_id)   # doc 75 I1: hereda el de la tabla
    if project_id is None:
        raise HTTPException(status_code=404, detail="Table not found.")
    physical = body.physicalName or await glossary_service.physicalize_name(project_id, body.logicalName)
    default = await repository.domain_default(body.parentDomainId) if body.parentDomainId else None
    derived = derive_column(body.logicalName, physical, default, body.dataType)
    return await repository.create_column({
        "projectId": project_id,
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

    rows = await _usage_rows(sas, ch_map)
    return {"usage": rows, "total": len(rows)}


async def _usage_rows(sas: list[dict], ch_map: dict | None = None) -> list[dict]:
    """Filas «dónde se usa» (proyecto › carpeta › canvas) de canvases ya
    acotados/overlayeados. Compartida por tablas (`table_usage`) y vistas
    (`inspect_view`, doc 72). Ordenadas por proyecto › carpeta › canvas."""
    ch_map = ch_map or {}
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
    return rows


# ── Buscador del Model (doc 70 §11): tablas · columnas · definiciones ─────────

SEARCH_NAME_FIELDS = ("physicalName", "logicalName")
SEARCH_DEF_FIELDS = ("description",)


def _contains(doc: dict, fields: tuple[str, ...], q: str) -> bool:
    """¿Algún `field` del doc contiene `q` (case-insensitive)? Sin término
    (navegación por filtros, doc 72 r2) pasa si algún campo tiene contenido —
    así «definitions» sin `q` sólo lista columnas CON definición. Puro."""
    needle = q.lower()
    if not needle:
        return any(str(doc.get(f) or "") for f in fields)
    return any(needle in str(doc.get(f) or "").lower() for f in fields)


def _where_ok(doc: dict, where: dict | None) -> bool:
    """Filtros de igualdad / `$in` (mismo mini-lenguaje del filtro Mongo que
    usa el repository) sobre un doc en memoria. Puro."""
    for k, v in (where or {}).items():
        if isinstance(v, dict) and "$in" in v:
            if doc.get(k) not in set(v["$in"]):
                return False
        elif doc.get(k) != v:
            return False
    return True


def overlay_search(pub: list[dict], changes: dict, fields: tuple[str, ...], q: str,
                   limit: int, where: dict | None = None, include_new: bool = True) -> list[dict]:
    """Publicado (ya filtrado y paginado en la BD) + cambios del changeset:
    los que tocan un hit de la página (edición/baja) siempre; las ALTAS del
    draft que matchean `q` en `fields` (y `where`) sólo con `include_new`
    (primera página — en las siguientes se repetirían, doc 72 r2). Re-filtrado
    post-overlay (un rename puede sacar/meter la entidad del match) y capado a
    `limit` + las altas (una alta no debe desplazar un hit publicado que ya no
    saldría en la página siguiente). Puro."""
    from app.core.versioning import overlay

    def _ok(d: dict) -> bool:
        return _contains(d, fields, q) and _where_ok(d, where)

    in_slice = {d["id"] for d in pub}
    kept = {eid: ch for eid, ch in (changes or {}).items()
            if eid in in_slice or (include_new and _ok(ch.get("payload") or {}))}
    new_n = sum(1 for eid in kept if eid not in in_slice)
    out = [d for d in overlay(pub, kept) if _ok(d)] if kept else list(pub)
    out.sort(key=lambda d: str(d.get("physicalName") or d.get("logicalName") or "").lower())
    return out[:limit + new_n]


def _canvases_of(table_id: str, sas: list[dict]) -> list[dict]:
    hits = [{"id": sa["id"], "name": sa.get("name") or "", "projectId": sa.get("projectId")}
            for sa in sas if table_id in (sa.get("tableIds") or [])]
    hits.sort(key=lambda c: c["name"].lower())
    return hits


def folder_subtree(folders: list[dict], root_id: str) -> list[str]:
    """Ids de la carpeta `root_id` y de TODAS sus descendientes (BFS por
    `parentFolderId`). El explorer anida Proyecto › Sub-proyecto › Dominio ›
    Modelo (canvas): elegir un sub-proyecto debe alcanzar los canvases que
    cuelgan de sus dominios. Puro."""
    children: dict[str, list[str]] = {}
    for f in folders:
        parent = f.get("parentFolderId")
        if parent:
            children.setdefault(str(parent), []).append(str(f["id"]))
    out = [root_id]
    seen = {root_id}
    i = 0
    while i < len(out):
        for child in children.get(out[i], []):
            if child not in seen:
                seen.add(child)
                out.append(child)
        i += 1
    return out


def _sa_in_scope(sa: dict, project_id: str | None, folder_ids: set[str] | None, canvas_id: str | None) -> bool:
    """¿El canvas cae en el alcance del filtro? Puro."""
    if canvas_id and sa.get("id") != canvas_id:
        return False
    if project_id and sa.get("projectId") != project_id:
        return False
    if folder_ids is not None and sa.get("folderId") not in folder_ids:
        return False
    return True


async def search_model(project_id: str, q: str, limit: int = 25, changeset_id: str | None = None, *,
                       scope: str = "all", folder_id: str | None = None,
                       canvas_id: str | None = None, offset: int = 0) -> dict:
    """Buscador del Model (doc 70 §11) — SIEMPRE dentro de un proyecto (doc 75
    D8): un solo request devuelve tres grupos —
    `tables` (nombre físico/lógico), `columns` (nombre físico/lógico) y
    `definitions` (definición funcional de la columna contiene `q`) — cada hit
    con su tabla resuelta y los CANVASES donde está (para redirigir y centrar).
    Publicado + overlay del changeset (tablas/columnas/canvases del draft).

    Selectores y filtros (owner): `scope` ∈ all|tables|columns|definitions;
    `folder_id` / `canvas_id` acotan a las tablas de esos canvases (el
    proyecto ya acota todo). La jerarquía del explorer es Proyecto › Sub-proyecto (carpeta
    raíz) › Dominio (sub-carpeta) › Modelo (canvas): `folder_id` puede ser un
    sub-proyecto o un dominio y alcanza TODO su subárbol (`folder_subtree`).

    Doc 72 r2 — navegación y paginación: `q` puede ir VACÍO (los filtros
    solos listan el alcance) y `offset` pide la página siguiente de cada grupo
    (OFFSET en la BD, orden por nombre físico); `next` trae el offset de la
    página siguiente por grupo o None si se agotó. Las altas del draft entran
    sólo en la primera página (`overlay_search(include_new=offset == 0)`).

    Contrato: {"tables": [{id, physicalName, logicalName, schema, description,
    canvases: [{id, name, projectId}]}], "columns": [{id, tableId, tableName,
    tableLogicalName, schema, physicalName, logicalName, dataType, description,
    canvases}], "definitions": [<misma forma que columns>], "limit": int,
    "scope": str, "offset": int, "next": {"tables": int|None, "columns":
    int|None, "definitions": int|None}}."""
    from app.core.versioning import overlay
    from app.features.changesets import repository as cs_repo

    q = (q or "").strip()
    limit = max(1, min(int(limit or 25), 100))
    offset = max(0, int(offset or 0))
    include_new = offset == 0
    changes: dict = {}
    if changeset_id:
        changes = await cs_repo.changes_map(
            changeset_id, ["canonical_tables", "canonical_columns", "subject_areas", "folders"])
    tbl_ch = changes.get("canonical_tables") or {}
    col_ch = changes.get("canonical_columns") or {}
    sa_ch = changes.get("subject_areas") or {}

    # ── Alcance por sub-proyecto o dominio (subárbol) / modelo — el proyecto
    # ya acota TODA lectura del repositorio (doc 75 D6).
    allowed: list[str] | None = None
    if folder_id or canvas_id:
        folder_ids: set[str] | None = None
        if folder_id:
            folders = await repository.list_folders_lite(project_id)
            fold_ch = changes.get("folders") or {}
            if fold_ch:
                known_f = {f["id"] for f in folders}
                folders = overlay(folders, {eid: ch for eid, ch in fold_ch.items()
                                            if eid in known_f
                                            or (ch.get("payload") or {}).get("projectId") == project_id})
            folder_ids = set(folder_subtree(folders, folder_id))
        scoped = await repository.canvases_in_scope(
            project_id, sorted(folder_ids) if folder_ids is not None else None, canvas_id)
        if sa_ch:
            known = {sa["id"] for sa in scoped}
            scoped = [sa for sa in overlay(scoped, {
                eid: ch for eid, ch in sa_ch.items()
                if eid in known or _sa_in_scope({**(ch.get("payload") or {}), "id": eid}, project_id, folder_ids, canvas_id)
            }) if _sa_in_scope(sa, project_id, folder_ids, canvas_id)]
        allowed = sorted({t for sa in scoped for t in (sa.get("tableIds") or [])})

    want_tables = scope in ("all", "tables")
    want_cols = scope in ("all", "columns")
    want_defs = scope in ("all", "definitions")
    col_where: dict = {}
    if allowed is not None:
        col_where["tableId"] = {"$in": allowed}

    tables = await repository.list_tables(project_id, q or None, limit, None, ids=allowed, skip=offset) if want_tables else []
    cols_name = await repository.search_columns_by(project_id, q, limit, SEARCH_NAME_FIELDS, col_where or None, skip=offset) if want_cols else []
    cols_def = await repository.search_columns_by(project_id, q, limit, SEARCH_DEF_FIELDS, col_where or None, skip=offset) if want_defs else []
    # Página siguiente por grupo: la página PUBLICADA vino llena ⇒ puede haber más.
    nxt = {"tables": offset + limit if len(tables) >= limit else None,
           "columns": offset + limit if len(cols_name) >= limit else None,
           "definitions": offset + limit if len(cols_def) >= limit else None}
    if changes:
        tbl_where = {"id": {"$in": allowed}} if allowed is not None else None
        if want_tables:
            tables = overlay_search(tables, tbl_ch, SEARCH_NAME_FIELDS, q, limit, tbl_where, include_new)
        if want_cols:
            cols_name = overlay_search(cols_name, col_ch, SEARCH_NAME_FIELDS, q, limit, col_where or None, include_new)
        if want_defs:
            cols_def = overlay_search(cols_def, col_ch, SEARCH_DEF_FIELDS, q, limit, col_where or None, include_new)

    # Tablas dueñas de las columnas (nombre/esquema), publicadas + overlay; una
    # columna cuya tabla no existe en el efectivo (borrada) se descarta.
    col_tids = sorted({c.get("tableId") for c in cols_name + cols_def if c.get("tableId")})
    owners = await repository.list_tables_by_ids(col_tids)
    if changes:
        owners = overlay(owners, {eid: ch for eid, ch in tbl_ch.items() if eid in set(col_tids)})
    owner_by_id = {t["id"]: t for t in owners}

    all_tids = sorted({t["id"] for t in tables} | set(owner_by_id))
    sas = await repository.canvases_with_tables(all_tids)
    if changes:
        known = {sa["id"] for sa in sas}
        wanted = set(all_tids)

        def _touches(ch: dict) -> bool:
            return bool(wanted & set((ch.get("payload") or {}).get("tableIds") or []))

        sas = overlay(sas, {eid: ch for eid, ch in sa_ch.items() if eid in known or _touches(ch)})

    def _col_hit(c: dict) -> dict | None:
        t = owner_by_id.get(c.get("tableId") or "")
        if not t:
            return None
        return {"id": c["id"], "tableId": c["tableId"], "tableName": t.get("physicalName") or "",
                "tableLogicalName": t.get("logicalName"), "schema": t.get("schema"),
                "physicalName": c.get("physicalName"), "logicalName": c.get("logicalName"),
                "dataType": c.get("dataType"), "description": c.get("description"),
                "canvases": _canvases_of(c["tableId"], sas)}

    return {
        "tables": [{"id": t["id"], "physicalName": t.get("physicalName"), "logicalName": t.get("logicalName"),
                    "schema": t.get("schema"), "description": t.get("description"),
                    "canvases": _canvases_of(t["id"], sas)} for t in tables],
        "columns": [h for h in (_col_hit(c) for c in cols_name) if h],
        "definitions": [h for h in (_col_hit(c) for c in cols_def) if h],
        "limit": limit,
        "scope": scope,
        "offset": offset,
        "next": nxt,
    }


# ── Inventario por proyecto (doc 72 §1): tablas + vistas en UNA request ───────

def adjust_column_counts(counts: dict[str, int], col_changes: dict,
                         published_table_of: dict[str, str], scope: set[str]) -> dict[str, int]:
    """Ajusta los conteos PUBLICADOS por tabla con los cambios de columnas del
    draft: alta (+1 en su tabla), baja publicada (−1), edición en sitio (0),
    mudanza de tabla (−1 / +1). Solo toca tablas del alcance; nunca baja de 0.
    `published_table_of` = {columnId: tableId} de las columnas publicadas
    tocadas (una baja de columna desconocida no resta nada). Pura."""
    out = dict(counts)

    def _dec(t: str | None) -> None:
        if t in scope:
            out[t] = max(0, out.get(t, 0) - 1)

    def _inc(t: str | None) -> None:
        if t in scope:
            out[t] = out.get(t, 0) + 1

    for cid, ch in col_changes.items():
        old_t = published_table_of.get(cid)
        if ch.get("op") == "delete":
            _dec(old_t)
            continue
        new_t = (ch.get("payload") or {}).get("tableId")
        if old_t == new_t:
            continue
        _dec(old_t)
        _inc(new_t)
    return out


def view_row(v: dict) -> dict:
    """Fila SLIM de vista para el árbol del Explorer: lo que se pinta (nombre,
    esquema, fuentes) + el CONTEO de columnas de salida. Las columnas en sí se
    piden al expandir (`view_columns`), igual que las de una tabla.

    Acepta las DOS formas que conviven tras el overlay: una fila ya slim del
    repositorio (trae `columnCount`) o el doc COMPLETO del payload de un cambio
    del draft (del que se derivan el conteo y el flag `custom`, doc 61). Pura.
    """
    if "columnCount" in v:
        return v
    from app.features.views.membership import view_sources
    custom = bool((v.get("customSql") or "").strip())
    cols = v.get("customColumns") if custom else v.get("sources")
    return {
        "id": v.get("id"),
        "name": v.get("name") or "",
        "schema": v.get("schema"),
        "sourceTableIds": list(view_sources(v)),
        "columnCount": len(cols or []),
        "custom": custom,
    }


async def project_inventory(project_id: str, changeset_id: str | None = None) -> dict:
    """Inventario del proyecto para el Explorer unificado (doc 72 §1): sus
    tablas (con `columnCount`) y las vistas cuyas fuentes son esas tablas, en
    UNA request. Alcance = las tablas con `projectId` del proyecto (doc 75
    §6.2: una tabla sin canvas también es del proyecto). Con `changeset_id`
    aplica el overlay del draft a canvases (filtrados por `projectId`), tablas
    (altas del draft entran, bajas salen), conteos de columnas (ajuste por
    cambios) y vistas (las que tocan el alcance; re-filtro por fuentes).

    Contrato: {"tables": [{id, physicalName, logicalName, schema, description,
    columnCount}], "views": [{id, name, schema, sourceTableIds, columnCount,
    custom}], "canvases": int, "total": {"tables": int, "views": int}} — tablas
    por esquema › nombre. Ambas listas llevan CONTEO de columnas, no columnas:
    las de una tabla se piden con `list_columns` y las de una vista con
    `view_columns`, al expandir."""
    from app.core.versioning.overlay import overlay
    from app.features.changesets import repository as cs_repo
    from app.features.views.membership import view_sources

    sas = await repository.canvases_of_project(project_id)
    # UNA consulta lite trae las tablas del proyecto Y sus ids (antes eran dos:
    # `table_ids_of_project` + `list_tables_by_ids` con el doc completo).
    tables = await repository.table_rows_of_project(project_id)
    table_ids = [t["id"] for t in tables]
    changes: dict = {}
    if changeset_id:
        changes = await cs_repo.changes_map(
            changeset_id, ["subject_areas", "canonical_tables", "canonical_columns", "views"])
        sa_ch = changes.get("subject_areas") or {}
        in_slice = {s["id"] for s in sas}

        def _mine(c: dict) -> bool:
            return (c.get("payload") or {}).get("projectId") == project_id

        sas = overlay(sas, {e: c for e, c in sa_ch.items() if e in in_slice or _mine(c)})
        sas = [s for s in sas if s.get("projectId") == project_id]
        # Altas del draft (tablas nuevas DEL proyecto) entran al alcance; bajas salen.
        for eid, ch in (changes.get("canonical_tables") or {}).items():
            if ch.get("op") == "delete":
                table_ids = [t for t in table_ids if t != eid]
            elif (ch.get("payload") or {}).get("projectId") == project_id and eid not in table_ids:
                table_ids.append(eid)

    scope = set(table_ids)
    t_ch = changes.get("canonical_tables") or {}
    if t_ch:
        # El overlay suma las altas del draft (su doc COMPLETO) sobre las filas
        # lite; las bajas ya salieron de `scope` y el armado de `rows` las filtra.
        tables = overlay(tables, {e: c for e, c in t_ch.items() if e in scope})
    counts = await repository.column_counts_for(project_id) if table_ids else {}
    c_ch = changes.get("canonical_columns") or {}
    if c_ch:
        known = await repository.column_tables_by_ids(list(c_ch))
        counts = adjust_column_counts(counts, c_ch, known, scope)

    # Perf del Explorer en proyectos grandes (medido 2026-09-08): las vistas se
    # acotan por `projectId` (índice, doc 75 D19) — no con un `$in` de miles de
    # ids sobre el array jsonb `sourceTableIds`, que ESCANEA las vistas de TODOS
    # los proyectos — y llegan SLIM (`rows_of_project`: lo que se pinta + el
    # conteo), sin `sql` ni `sources`. Juntos bajan «Modelo DDV» de 36.7 MB a
    # ~1.2 MB; antes el volumen disparaba «Couldn't load the project catalog».
    views = await views_repo.rows_of_project(project_id) if table_ids else []
    v_ch = changes.get("views") or {}
    if v_ch:
        in_v = {v["id"] for v in views}

        def _touches(c: dict) -> bool:
            return bool(scope & set(view_sources(c.get("payload") or {})))

        # `overlay` deja el doc COMPLETO del cambio junto a las filas slim;
        # `view_row` normaliza ambas formas a la fila del árbol.
        merged = overlay(views, {e: c for e, c in v_ch.items() if e in in_v or _touches(c)})
        views = [view_row(v) for v in merged]
    # Re-filtro post-overlay: un upsert pudo re-apuntar la vista fuera del alcance.
    views = [v for v in views if scope & set(v["sourceTableIds"])]
    views.sort(key=lambda v: ((v.get("schema") or "").lower(), (v.get("name") or "").lower()))

    rows = [{
        "id": t["id"], "physicalName": t.get("physicalName") or "", "logicalName": t.get("logicalName") or "",
        "schema": t.get("schema"), "description": t.get("description"),
        "columnCount": counts.get(t["id"], 0),
    } for t in tables if t.get("id") in scope]
    rows.sort(key=lambda t: ((t["schema"] or "").lower(), t["physicalName"].lower()))
    return {"tables": rows, "views": views, "canvases": len(sas),
            "total": {"tables": len(rows), "views": len(views)}}


async def view_columns(view_id: str, changeset_id: str | None = None) -> list[dict] | None:
    """Columnas de SALIDA de UNA vista, para expandirla en el Explorer — el
    inventario sólo trae el conteo, igual que con las tablas. Vista Regular →
    sus `sources`; Personalizada (doc 61) → sus `customColumns`. Con
    `changeset_id` aplica el overlay del draft. None si la vista no existe en el
    estado efectivo (el router responde 404).

    Forma UNIFORME (las dos clases de vista salen igual, así el cliente tiene un
    solo mapeo) y sólo con lo que el árbol pinta — `description` y el resto del
    doc no viajan: {outputAlias, column, tableId, table, castType, expression}.
    `tableId` deja que el cliente resuelva el nombre de la tabla fuente con el
    inventario que ya tiene.
    """
    from app.core.versioning.overlay import overlay
    from app.features.changesets import repository as cs_repo

    docs = await views_repo.list_by_ids([view_id])
    if changeset_id:
        changes = await cs_repo.changes_map(changeset_id, ["views"])
        docs = overlay(docs, {e: c for e, c in (changes.get("views") or {}).items() if e == view_id})
    view = next((v for v in docs if v.get("id") == view_id), None)
    if view is None:
        return None
    if bool((view.get("customSql") or "").strip()):
        return [{"outputAlias": c.get("name"), "column": None, "tableId": None,
                 "table": None, "castType": None, "expression": c.get("expression")}
                for c in (view.get("customColumns") or [])]
    return [{"outputAlias": s.get("outputAlias"), "column": s.get("column"),
             "tableId": s.get("tableId"), "table": s.get("table"),
             "castType": s.get("castType"), "expression": s.get("expression")}
            for s in (view.get("sources") or [])]


# ── Object Inspector (doc 72 §3): metadata completa de UN objeto, sin canvas ──

async def inspect_table(table_id: str, changeset_id: str | None = None) -> dict | None:
    """Dossier de solo lectura de una tabla/entidad para el inspector: la tabla
    y sus columnas EFECTIVAS (publicado + overlay del draft), sus relaciones
    globales (`relationships.table_links`), sus vistas con canvases
    (`views.table_views`) y dónde se usa (`table_usage`). None si la tabla no
    existe en el estado efectivo (el router responde 404)."""
    from app.core.versioning.overlay import overlay
    from app.features.changesets import repository as cs_repo
    from app.features.relationships import service as rel_service
    from app.features.views import service as views_service

    tables = await repository.list_tables_by_ids([table_id])
    cols = await repository.list_columns(table_id)
    if changeset_id:
        changes = await cs_repo.changes_map(changeset_id, ["canonical_tables", "canonical_columns"])
        t_ch = changes.get("canonical_tables") or {}
        tables = overlay(tables, {e: c for e, c in t_ch.items() if e == table_id})
        c_ch = changes.get("canonical_columns") or {}
        in_slice = {c["id"] for c in cols}

        def _mine(c: dict) -> bool:
            return (c.get("payload") or {}).get("tableId") == table_id

        cols = overlay(cols, {e: c for e, c in c_ch.items() if e in in_slice or _mine(c)})
        cols = [c for c in cols if c.get("tableId") == table_id]
    table = next((t for t in tables if t.get("id") == table_id), None)
    if table is None:
        return None
    cols.sort(key=lambda c: (c.get("ordinal") or 0, (c.get("physicalName") or "").lower()))
    links = await rel_service.table_links(table_id, changeset_id)
    views = await views_service.table_views(table_id, changeset_id)
    usage = await table_usage(table_id, changeset_id)
    return {"table": table, "columns": cols, "links": links["links"],
            "views": views["views"], "usage": usage["usage"]}


async def inspect_view(view_id: str, changeset_id: str | None = None) -> dict | None:
    """Dossier de solo lectura de una vista: la vista efectiva (normalizada),
    sus tablas fuente resueltas (nombres del draft incluidos) y los canvases
    donde es MIEMBRO (`view_on_canvas`: `viewIds` explícito o regla legacy)
    entre los canvases de sus fuentes, con overlay del draft. None si no existe."""
    from app.core.versioning.overlay import overlay
    from app.features.changesets import repository as cs_repo
    from app.features.views import repository as views_repo
    from app.features.views.membership import view_on_canvas, view_sources

    views = await views_repo.list_by_ids([view_id])
    ch_map: dict = {}
    if changeset_id:
        ch_map = await cs_repo.changes_map(
            changeset_id, ["views", "canonical_tables", "subject_areas", "projects", "folders"])
        v_ch = ch_map.get("views") or {}
        merged = overlay(views, {e: c for e, c in v_ch.items() if e == view_id})
        views = [views_repo.normalize(v) for v in merged]
    view = next((v for v in views if v.get("id") == view_id), None)
    if view is None:
        return None
    src_ids = view_sources(view)
    sources = await repository.list_tables_by_ids(src_ids)
    t_ch = ch_map.get("canonical_tables") or {}
    if t_ch:
        sources = overlay(sources, {e: c for e, c in t_ch.items() if e in set(src_ids)})
    by_id = {t["id"]: t for t in sources}
    src_rows = [{"id": i, "physicalName": by_id[i].get("physicalName") or i,
                 "logicalName": by_id[i].get("logicalName") or "", "schema": by_id[i].get("schema")}
                for i in src_ids if i in by_id]

    sas = await repository.canvases_with_tables(src_ids) if src_ids else []
    sa_ch = ch_map.get("subject_areas") or {}
    if sa_ch:
        in_slice = {s["id"] for s in sas}
        src_set = set(src_ids)

        def _touches(c: dict) -> bool:
            p = c.get("payload") or {}
            return bool(src_set & set(p.get("tableIds") or [])) or view_id in (p.get("viewIds") or [])

        sas = overlay(sas, {e: c for e, c in sa_ch.items() if e in in_slice or _touches(c)})
    sas = [s for s in sas if view_on_canvas(view, s)]
    usage = await _usage_rows(sas, ch_map)
    return {"view": view, "sources": src_rows, "usage": usage}

