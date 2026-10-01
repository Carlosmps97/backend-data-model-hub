"""Endpoints del motor de consulta del reporting (Field Catalog + query + facets).
Lecturas abiertas (como el resto de reporting; el login global gatea en prod).
Doc 75: todo es de UN proyecto — `projectId` obligatorio (query param o cuerpo)."""
from __future__ import annotations

import csv
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app.core.api.envelope import ok
from app.core.db.client import get_db
from app.core.identity import Principal, current_principal
from app.core.scope import scoped

from .. import versions, views
from . import executor as ex
from . import parser
from . import reports
from .compiler import QueryError
from .schema import build_catalog
from .spec import QuerySpec

router = APIRouter(prefix="/api/reporting", tags=["reporting-query"])


# Doc 105 (ronda 4): tope del texto del editor SQL — sqlglot parsea de forma
# síncrona (~0,85 s por MB): sin tope, un texto enorme frenaba el event loop.
# Ronda 5: se valida aquí (no con `max_length`), para responder un `detail` de
# TEXTO — la lista de pydantic el front la mostraba como «Unexpected response shape».
SQL_TEXT_MAX = 100_000
SQL_TOO_LONG = f"The SQL text is too long: up to {SQL_TEXT_MAX:,} characters."


class SqlBody(BaseModel):
    text: str
    projectId: str = Field(min_length=1)


async def _spec_from_sql(text: str, project_id: str) -> tuple[QuerySpec, dict]:
    """(spec, catálogo) del texto — parseado UNA vez (doc 105, ronda 4)."""
    if len(text) > SQL_TEXT_MAX:
        raise HTTPException(422, SQL_TOO_LONG)
    stmt = parser.parse_statement(text)
    from_ = parser.view_name(stmt)
    if from_ not in ex.COLL_OF:
        raise parser.SqlError(f"Unknown view in FROM: {from_}")
    cat = build_catalog(from_, await ex._udp_defs(project_id))
    try:
        return parser.to_spec(stmt, cat, from_, project_id), cat
    except RecursionError:                            # doc 105 (ronda 5): nunca un 500
        raise parser.SqlError(parser.TOO_DEEP_SQL)


@router.get("/catalog")
async def catalog(projectId: str = Query(min_length=1),
                  from_: str = Query(default="columns", alias="from")):
    """Field Catalog de la vista para el proyecto: campos estáticos + UDP
    dinámicos (los del proyecto), con ops por tipo, enumValues,
    sortable/indexed. Alimenta el query-builder y el SQL."""
    if from_ not in ex.COLL_OF:
        raise HTTPException(400, f"Unknown view: {from_}")
    cat = build_catalog(from_, await ex._udp_defs(projectId))
    return ok({"from": from_, "fields": [fd.to_public() for fd in cat.values()]})


@router.post("/query")
async def run_query(spec: QuerySpec, cursor: str | None = Query(default=None)):
    """Ejecuta un QuerySpec → filas (keyset) o grupos. `cursor` para la próxima
    página. Devuelve {rows, columns, nextCursor, hasMore, meta}."""
    try:
        return ok(await ex.run_query(spec, cursor=cursor or spec.cursor))
    except QueryError as e:
        raise HTTPException(e.code, str(e)) from e


@router.post("/query/validate")
async def validate_sql(body: SqlBody):
    """SQL-like → QuerySpec (round-trip para el editor). Devuelve {spec, errors}
    con {line,col,message} para subrayar en la caja de texto."""
    try:
        spec, cat = await _spec_from_sql(body.text, body.projectId)
        # Doc 105 (ronda 4): también lo que el motor rechazaría al correrla —
        # antes «válido» y luego `/query/sql` respondía 422.
        ex.check_spec(spec, cat)
        return ok({"spec": spec.model_dump(by_alias=True), "errors": []})
    except parser.SqlError as e:
        return ok({"spec": None, "errors": [{"line": e.line, "col": e.col, "message": e.message}]})
    except QueryError as e:
        return ok({"spec": None, "errors": [{"line": 1, "col": 1, "message": str(e)}]})


@router.post("/query/sql")
async def run_sql(body: SqlBody, cursor: str | None = Query(default=None)):
    """Parsea el SQL → QuerySpec y lo ejecuta (mismo motor que /query)."""
    try:
        spec, _ = await _spec_from_sql(body.text, body.projectId)
    except parser.SqlError as e:
        raise HTTPException(400, e.message) from e
    try:
        return ok(await ex.run_query(spec, cursor=cursor))
    except QueryError as e:
        raise HTTPException(e.code, str(e)) from e


@router.post("/export")
async def export_csv(spec: QuerySpec):
    """Export CSV por STREAMING (O(1) memoria): keyset-pagina internamente y hace
    yield línea por línea. Respeta los filtros/columnas del spec; nunca materializa
    todo ni construye el archivo en el browser. Doc 105 (P12, revisión): la
    PRIMERA página se pide antes de abrir el stream — un spec inválido (campo,
    valor u orden) es un 4xx con `detail`; dentro del generador era un 500 o un
    archivo cortado."""
    page_spec = spec.model_copy(update={"limit": 2000})
    try:
        # Doc 105: un agrupado se exporta ENTERO (todos sus grupos o hasta el
        # LIMIT) — antes, la «página» de 2 000 cortaba el CSV en silencio.
        first = await ex.run_query(page_spec, cursor=None, all_groups=True)
    except QueryError as e:
        raise HTTPException(e.code, str(e)) from e

    async def _gen():
        buf = io.StringIO()
        w = csv.writer(buf)

        def flush():
            data = buf.getvalue()
            buf.seek(0); buf.truncate(0)
            return data

        keys = [c["key"] for c in first["columns"]]
        w.writerow([c["label"] for c in first["columns"]]); yield flush()
        page = first
        while True:
            for row in page["rows"]:
                w.writerow(["" if (v := row.get(k)) is None else v for k in keys])
            yield flush()
            if not page.get("hasMore") or not page.get("nextCursor"):
                break
            page = await ex.run_query(page_spec, cursor=page["nextCursor"])

    return StreamingResponse(_gen(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="report-{spec.from_}.csv"'})


# ── Vistas curadas / Insights (07 §3) ────────────────────────────────────────
@router.get("/insights/scorecard")
async def insights_scorecard(projectId: str = Query(min_length=1)):
    """Model Health Scorecard del proyecto: métricas de calidad + completenessScore."""
    return ok(await views.scorecard(projectId))


@router.get("/insights/udp-coverage")
async def insights_udp_coverage(projectId: str = Query(min_length=1)):
    """Cobertura por UDP: setCount, missingCount, coveragePct, valueBreakdown, invalidCount."""
    return ok(await views.udp_coverage(projectId))


@router.get("/insights/domain-usage")
async def insights_domain_usage(projectId: str = Query(min_length=1)):
    """Uso de cada Parent Domain: columnCount, overridePct, distinctDataTypes, isUnused."""
    return ok(await views.domain_usage(projectId))


@router.get("/insights/glossary-usage")
async def insights_glossary_usage(projectId: str = Query(min_length=1)):
    return ok(await views.glossary_usage(projectId))


@router.get("/insights/relationships")
async def insights_relationships(projectId: str = Query(min_length=1),
                                 limit: int = Query(default=2000, ge=1, le=5000),
                                 changesetId: str | None = Query(default=None),
                                 principal: Principal = Depends(current_principal)):
    """Relaciones con ambos extremos resueltos (schema.tabla.columna), cardinalidad.
    Doc 102: con `changesetId`, las de una versión PROPIA sin publicar."""
    changes = await versions.version_changes(projectId, changesetId, principal, versions.RELATIONSHIP_INPUTS)
    return ok(await views.relationships_report(projectId, limit=limit, changes=changes))


@router.get("/reports")
async def list_reports(projectId: str = Query(min_length=1),
                       principal: Principal = Depends(current_principal)):
    return ok(await reports.list_reports(principal.username, projectId))


def _stamped_body(body: reports.SavedReportBody) -> reports.SavedReportBody:
    """Defensa en profundidad: solo se persisten specs que sean un QuerySpec bien
    formado (aunque /query re-valida al ejecutar, no guardamos blobs arbitrarios).
    El spec queda estampado con el proyecto del reporte (doc 75): un spec de
    OTRO proyecto es un error, no se re-etiqueta en silencio."""
    spec_pid = body.spec.get("projectId")
    if spec_pid is not None and spec_pid != body.projectId:
        raise HTTPException(422, "The report spec belongs to another project.")
    spec = {**body.spec, "projectId": body.projectId}
    try:
        QuerySpec.model_validate(spec)
    except ValidationError as e:
        raise HTTPException(422, "The report spec isn't a valid query.") from e
    return body.model_copy(update={"spec": spec})


@router.post("/reports", status_code=status.HTTP_201_CREATED)
async def create_report(body: reports.SavedReportBody, principal: Principal = Depends(current_principal)):
    return ok(await reports.create_report(principal.username, _stamped_body(body)))


@router.put("/reports/{rid}")
async def update_report(rid: str, body: reports.SavedReportBody, principal: Principal = Depends(current_principal)):
    r = await reports.update_report(principal.username, rid, _stamped_body(body))
    if r is None:
        raise HTTPException(404, "Report not found (or it isn't yours).")
    return ok(r)


@router.delete("/reports/{rid}")
async def delete_report(rid: str, principal: Principal = Depends(current_principal)):
    if not await reports.delete_report(principal.username, rid):
        raise HTTPException(404, "Report not found (or it isn't yours).")
    return ok({"id": rid})


@router.get("/facets")
async def facets(field: str, projectId: str = Query(min_length=1),
                 from_: str = Query(default="columns", alias="from"),
                 q: str | None = Query(default=None, max_length=80), limit: int = Query(default=50, ge=1, le=200)):
    """Opciones de un campo del proyecto para el typeahead del filtro
    (server-side). enum → allowedValues; dominio → {value:id, label:name};
    resto → distinct acotado. Reemplaza el hack de cargar 400k client-side."""
    cat = build_catalog(from_, await ex._udp_defs(projectId))
    fd = cat.get(field)
    if fd is None:
        raise HTTPException(400, f"Unknown field: {field}")
    if fd.hydrate == "derived":
        # Defensa en profundidad (mismo criterio que el compiler en where/
        # groupBy/agregaciones): los campos calculados post-fetch no existen en
        # Mongo — facetarlos agruparía por su path fuente (p.ej. el array
        # tableIds) y devolvería basura. El builder ya no los ofrece (ops=[]);
        # esto cubre clientes no-browser.
        raise HTTPException(422, f"Field {fd.key} is calculated and has no facets")
    db = await get_db()
    if fd.enumValues:
        vals = [v for v in fd.enumValues if not q or q.lower() in v.lower()][:limit]
        return ok([{"value": v, "label": v} for v in vals])
    active = scoped(projectId, ex.ACTIVE)
    if fd.hydrate == "domain":
        query = dict(active)
        if q:
            query["name"] = {"$regex": re.escape(q), "$options": "i"}  # escapado: sin ReDoS/inyección
        doms = await db["parent_domains"].find(query, {"name": 1}).limit(limit).to_list(limit)
        return ok([{"value": str(d["_id"]), "label": d.get("name")} for d in doms])
    if from_ == "view_columns" and fd.path.startswith("sources."):
        # Entidad virtual (F5): las columnas viven en el array `sources` → unwind
        # antes de facetar (el distinct genérico agruparía por el array entero).
        pipe = [{"$match": active}, {"$unwind": "$sources"}]
        if q:
            pipe.append({"$match": {fd.path: {"$regex": re.escape(q), "$options": "i"}}})  # escapado
        pipe += [{"$group": {"_id": f"${fd.path}"}}, {"$sort": {"_id": 1}}, {"$limit": limit}]
        vals = await db["views"].aggregate(pipe, maxTimeMS=ex.MAX_TIME_MS).to_list(limit)
        return ok([{"value": v["_id"], "label": str(v["_id"])} for v in vals if v["_id"] is not None])
    # distinct acotado sobre el path (barato si el campo está indexado). Campos
    # cross-entity (schema en columns vive en la tabla) se facetan en la tabla.
    coll = db["canonical_tables"] if fd.entity == "table" else db[ex.COLL_OF[from_]]
    match = dict(active)
    if q:
        match[fd.path] = {"$regex": re.escape(q), "$options": "i"}  # escapado: sin ReDoS/inyección
    vals = await coll.aggregate(
        [{"$match": match}, {"$group": {"_id": f"${fd.path}"}}, {"$sort": {"_id": 1}}, {"$limit": limit}],
        maxTimeMS=ex.MAX_TIME_MS).to_list(limit)
    return ok([{"value": v["_id"], "label": str(v["_id"])} for v in vals if v["_id"] is not None])
