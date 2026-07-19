"""Endpoints del motor de consulta del reporting (Field Catalog + query + facets).
Lecturas abiertas (como el resto de reporting; el login global gatea en prod)."""
from __future__ import annotations

import csv
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.core.api.envelope import ok
from app.core.db.client import get_db
from app.core.identity import Principal, current_principal

from .. import views
from . import executor as ex
from . import parser
from . import reports
from .compiler import QueryError
from .schema import build_catalog
from .spec import QuerySpec

router = APIRouter(prefix="/api/reporting", tags=["reporting-query"])


class SqlBody(BaseModel):
    text: str


async def _spec_from_sql(text: str) -> QuerySpec:
    from_ = parser.parse_from(text)
    if from_ not in ex.COLL_OF:
        raise parser.SqlError(f"Vista desconocida en FROM: {from_}")
    cat = build_catalog(from_, await ex._udp_defs())
    return parser.to_spec(text, cat, from_)


@router.get("/catalog")
async def catalog(from_: str = Query(default="columns", alias="from")):
    """Field Catalog de la vista: campos estáticos + UDP dinámicos, con ops por
    tipo, enumValues, sortable/indexed. Alimenta el query-builder y el SQL."""
    if from_ not in ex.COLL_OF:
        raise HTTPException(400, f"Vista desconocida: {from_}")
    cat = build_catalog(from_, await ex._udp_defs())
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
        spec = await _spec_from_sql(body.text)
        return ok({"spec": spec.model_dump(by_alias=True), "errors": []})
    except parser.SqlError as e:
        return ok({"spec": None, "errors": [{"line": e.line, "col": e.col, "message": e.message}]})


@router.post("/query/sql")
async def run_sql(body: SqlBody, cursor: str | None = Query(default=None)):
    """Parsea el SQL → QuerySpec y lo ejecuta (mismo motor que /query)."""
    try:
        spec = await _spec_from_sql(body.text)
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
    todo ni construye el archivo en el browser."""
    page_spec = spec.model_copy(update={"limit": 2000})

    async def _gen():
        buf = io.StringIO()
        w = csv.writer(buf)

        def flush():
            data = buf.getvalue()
            buf.seek(0); buf.truncate(0)
            return data

        first = await ex.run_query(page_spec, cursor=None)
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
async def insights_scorecard():
    """Model Health Scorecard: métricas globales de calidad + completenessScore."""
    return ok(await views.scorecard())


@router.get("/insights/udp-coverage")
async def insights_udp_coverage():
    """Cobertura por UDP: setCount, missingCount, coveragePct, valueBreakdown, invalidCount."""
    return ok(await views.udp_coverage())


@router.get("/insights/domain-usage")
async def insights_domain_usage():
    """Uso de cada Parent Domain: columnCount, overridePct, distinctDataTypes, isUnused."""
    return ok(await views.domain_usage())


@router.get("/insights/glossary-usage")
async def insights_glossary_usage():
    return ok(await views.glossary_usage())


@router.get("/insights/relationships")
async def insights_relationships(limit: int = Query(default=2000, ge=1, le=5000)):
    """Relaciones con ambos extremos resueltos (schema.tabla.columna), cardinalidad."""
    return ok(await views.relationships_report(limit=limit))


@router.get("/reports")
async def list_reports(principal: Principal = Depends(current_principal)):
    return ok(await reports.list_reports(principal.username))


def _validate_report_spec(spec: dict) -> None:
    """Defensa en profundidad: solo se persisten specs que sean un QuerySpec bien
    formado (aunque /query re-valida al ejecutar, no guardamos blobs arbitrarios)."""
    try:
        QuerySpec.model_validate(spec)
    except ValidationError as e:
        raise HTTPException(422, "El spec del reporte no es una consulta válida.") from e


@router.post("/reports", status_code=status.HTTP_201_CREATED)
async def create_report(body: reports.SavedReportBody, principal: Principal = Depends(current_principal)):
    _validate_report_spec(body.spec)
    return ok(await reports.create_report(principal.username, body))


@router.put("/reports/{rid}")
async def update_report(rid: str, body: reports.SavedReportBody, principal: Principal = Depends(current_principal)):
    _validate_report_spec(body.spec)
    r = await reports.update_report(principal.username, rid, body)
    if r is None:
        raise HTTPException(404, "Reporte no encontrado (o no es tuyo).")
    return ok(r)


@router.delete("/reports/{rid}")
async def delete_report(rid: str, principal: Principal = Depends(current_principal)):
    if not await reports.delete_report(principal.username, rid):
        raise HTTPException(404, "Reporte no encontrado (o no es tuyo).")
    return ok({"id": rid})


@router.get("/facets")
async def facets(field: str, from_: str = Query(default="columns", alias="from"),
                 q: str | None = Query(default=None, max_length=80), limit: int = Query(default=50, ge=1, le=200)):
    """Opciones de un campo para el typeahead del filtro (server-side). enum →
    allowedValues; dominio → {value:id, label:name}; resto → distinct acotado.
    Reemplaza el hack de cargar 400k client-side para las facetas."""
    cat = build_catalog(from_, await ex._udp_defs())
    fd = cat.get(field)
    if fd is None:
        raise HTTPException(400, f"Campo desconocido: {field}")
    if fd.hydrate == "derived":
        # Defensa en profundidad (mismo criterio que el compiler en where/
        # groupBy/agregaciones): los campos calculados post-fetch no existen en
        # Mongo — facetarlos agruparía por su path fuente (p.ej. el array
        # tableIds) y devolvería basura. El builder ya no los ofrece (ops=[]);
        # esto cubre clientes no-browser.
        raise HTTPException(422, f"El campo {fd.key} es calculado y no admite facetas")
    db = await get_db()
    if fd.enumValues:
        vals = [v for v in fd.enumValues if not q or q.lower() in v.lower()][:limit]
        return ok([{"value": v, "label": v} for v in vals])
    if fd.hydrate == "domain":
        query = {"flgactive": {"$ne": False}}
        if q:
            query["name"] = {"$regex": re.escape(q), "$options": "i"}  # escapado: sin ReDoS/inyección
        doms = await db["parent_domains"].find(query, {"name": 1}).limit(limit).to_list(limit)
        return ok([{"value": str(d["_id"]), "label": d.get("name")} for d in doms])
    if from_ == "view_columns" and fd.path.startswith("sources."):
        # Entidad virtual (F5): las columnas viven en el array `sources` → unwind
        # antes de facetar (el distinct genérico agruparía por el array entero).
        pipe = [{"$match": {"flgactive": {"$ne": False}}}, {"$unwind": "$sources"}]
        if q:
            pipe.append({"$match": {fd.path: {"$regex": re.escape(q), "$options": "i"}}})  # escapado
        pipe += [{"$group": {"_id": f"${fd.path}"}}, {"$sort": {"_id": 1}}, {"$limit": limit}]
        vals = await db["views"].aggregate(pipe, maxTimeMS=ex.MAX_TIME_MS).to_list(limit)
        return ok([{"value": v["_id"], "label": str(v["_id"])} for v in vals if v["_id"] is not None])
    # distinct acotado sobre el path (barato si el campo está indexado). Campos
    # cross-entity (schema en columns vive en la tabla) se facetan en la tabla.
    coll = db["canonical_tables"] if fd.entity == "table" else db[ex.COLL_OF[from_]]
    match = {"flgactive": {"$ne": False}}
    if q:
        match[fd.path] = {"$regex": re.escape(q), "$options": "i"}  # escapado: sin ReDoS/inyección
    vals = await coll.aggregate(
        [{"$match": match}, {"$group": {"_id": f"${fd.path}"}}, {"$sort": {"_id": 1}}, {"$limit": limit}],
        maxTimeMS=ex.MAX_TIME_MS).to_list(limit)
    return ok([{"value": v["_id"], "label": str(v["_id"])} for v in vals if v["_id"] is not None])
