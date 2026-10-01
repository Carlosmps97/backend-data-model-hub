"""Doc 105 (revisión de los arreglos) — Reporting, hallazgos 3, 4, 5 y 7.

- 3: `GET /api/reporting/columns?tableId=` (vacío) en modo versión devolvía TODO
  el draft (el lote quedaba vacío = «sin lote»); producción devolvía `[]`.
  Ningún llamador del front manda `tableId` vacío (`listReportColumns` sólo lo
  pone si viene): 422, como `projectId`.
- 4: el tope UTF-16 del `sheetName` también validaba al LEER (lo heredaba
  `SheetTemplateDoc`): una plantilla guardada antes (≤31 code points pero >31
  unidades UTF-16) dejaba en 500 el listado del proyecto. Sólo al escribir.
- 5: `table_changes_of` agregaba SIEMPRE la clave `canonical_columns`: /tables con
  `limit` en modo versión perdía la página rápida aun con un draft vacío.
- 7: el scorecard contaba las tablas «huérfanas» mirando sólo `sourceTableId`/
  `targetTableId` (legacy): con relaciones v2 las huérfanas salían de más."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.core.db.lakebase.aggregate import compile_pipeline
from app.core.db.lakebase.translate import Sql
from app.features.reporting import repository as rep_repo
from app.features.reporting import service, versions
from app.features.reporting import views as rep_views
from app.features.reporting.sheet_templates.models import SheetTemplateBody, SheetTemplateDoc
from tests.support.fakedb import FakeDb

from .helpers import P, ch

ANA = {"X-Dev-User": "ana"}


# ── Hallazgo 3 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("version", [None, "cs1"])
def test_h3_columns_con_tableid_vacio_es_422(report_version_db, client, version):
    """Base: repro del revisor R2 (`test_columns_tableid_vacio_en_version_devuelve_todo_el_draft`)."""
    params = {"projectId": P, "tableId": "", **({"changesetId": version} if version else {})}
    res = client.get("/api/reporting/columns", params=params, headers=ANA)
    assert res.status_code == 422, (res.status_code, res.text[:200])


def test_h3_columns_con_tableid_o_sin_el_siguen_igual(report_version_db, client):
    one = client.get("/api/reporting/columns", params={"projectId": P, "tableId": "t2", "changesetId": "cs1"},
                     headers=ANA)
    assert one.status_code == 200 and {c["id"] for c in one.json()["data"]} == {"c3", "c9"}
    prod = client.get("/api/reporting/columns", params={"projectId": P}, headers=ANA)   # sin lote: con tope
    assert prod.status_code == 200 and {c["id"] for c in prod.json()["data"]} == {"c1", "c2", "c3"}


# ── Hallazgo 4 ──────────────────────────────────────────────────────────────
OLD_SHEET = "📊" * 20            # 20 code points, 40 unidades UTF-16 (sólo por API, antes del tope)
TEMPLATE = {"name": "Vieja", "sheetName": OLD_SHEET, "shared": True,
            "columns": [{"header": "Tabla", "source": "table.physicalName"}]}


def test_h4_el_doc_leido_acepta_la_hoja_guardada_antes_del_tope():
    doc = SheetTemplateDoc.model_validate({**TEMPLATE, "id": "tpl-vieja", "projectId": P, "owner": "ana"})
    assert doc.sheetName == OLD_SHEET


def test_h4_al_escribir_el_tope_utf16_sigue():
    with pytest.raises(ValueError, match="sheet name"):
        SheetTemplateBody.model_validate(TEMPLATE)
    assert SheetTemplateBody.model_validate({**TEMPLATE, "sheetName": "📊" * 15}).sheetName == "📊" * 15


def test_h4_el_doc_leido_sigue_validando_la_forma():
    """Sólo se afloja el tope UTF-16: recorte, caracteres prohibidos y 31 code points siguen."""
    base = {**TEMPLATE, "id": "x", "projectId": P}
    assert SheetTemplateDoc.model_validate({**base, "sheetName": "  HOJA  "}).sheetName == "HOJA"
    for bad in ("A/B", "X" * 32, "   "):
        with pytest.raises(ValueError):
            SheetTemplateDoc.model_validate({**base, "sheetName": bad})


# ── Hallazgo 5 ──────────────────────────────────────────────────────────────
@pytest.fixture
def paths(monkeypatch) -> list[str]:
    """Qué camino tomó /tables: la página rápida (PAGE) o el proyecto completo (FULL)."""
    calls: list[str] = []
    real_full, real_page = rep_repo.report_inputs, rep_repo.report_inputs_page

    async def full(pid):
        calls.append("FULL")
        return await real_full(pid)

    async def page(pid, limit, offset=0):
        calls.append("PAGE")
        return await real_page(pid, limit, offset)

    monkeypatch.setattr(rep_repo, "report_inputs", full)
    monkeypatch.setattr(rep_repo, "report_inputs_page", page)
    return calls


def _tables(client, **params) -> list[dict]:
    res = client.get("/api/reporting/tables", params={"projectId": P, **params}, headers=ANA)
    assert res.status_code == 200, res.text[:200]
    return res.json()["data"]


def test_h5_draft_sin_cambios_de_tablas_usa_la_pagina_rapida(report_version_db, client, paths):
    """Repro del revisor R2 (`test_a7_draft_vacio_pierde_el_camino_rapido`): medía ['FULL']."""
    report_version_db.raw["changesets"].insert_one(
        {"_id": "cs-vacio", "title": "vacío", "owner": "ana", "projectId": P, "status": "draft"})
    report_version_db.raw["changeset_changes"].insert_one(          # un cambio que /tables no lee
        {**ch("views", "v9", "upsert", {"name": "V9", "schema": "x_vu"}), "_id": "cs-vacio::views::v9",
         "csId": "cs-vacio"})
    prod = _tables(client, limit=2)
    assert paths == ["PAGE"]
    paths.clear()
    assert _tables(client, limit=2, changesetId="cs-vacio") == prod
    assert paths == ["PAGE"]


def test_h5_draft_con_solo_cambios_de_columnas_arma_el_proyecto(report_version_db, client, paths):
    """Guarda del A7: si la versión SÓLO toca columnas, los conteos cambian —
    no puede caer a la página de producción."""
    report_version_db.raw["changesets"].insert_one(
        {"_id": "cs-cols", "title": "cols", "owner": "ana", "projectId": P, "status": "draft"})
    report_version_db.raw["changeset_changes"].insert_one(
        {**ch("canonical_columns", "c2", "delete"), "_id": "cs-cols::canonical_columns::c2", "csId": "cs-cols"})
    rows = {r["id"]: r["columnCount"] for r in _tables(client, limit=5, changesetId="cs-cols")}
    assert paths == ["FULL"] and rows == {"t1": 1, "t2": 1, "t3": 0}


def test_h5_table_changes_of_sin_columnas_no_agrega_la_clave(report_version_db):
    report_version_db.raw["changesets"].insert_one(
        {"_id": "cs-vacio", "title": "vacío", "owner": "ana", "projectId": P, "status": "draft"})
    assert asyncio.run(versions.table_changes_of("cs-vacio")) == {}
    assert "canonical_columns" in asyncio.run(versions.table_changes_of("cs1"))   # cs1 sí toca columnas


# ── Hallazgo 7 ──────────────────────────────────────────────────────────────
def _scorecard_world(fake: FakeDb) -> None:
    on = {"projectId": P, "flgactive": True}
    fake.raw["projects"].insert_one({"_id": P, "name": "MODELO", "flgactive": True})
    fake.raw["canonical_tables"].insert_many(
        [{"_id": f"t{i}", **on, "physicalName": f"T{i}"} for i in range(1, 7)]
        + [{"_id": "t7", **on, "physicalName": "T7", "flgactive": False}])      # inactiva
    fake.raw["relationships"].insert_many([
        {"_id": "r1", **on, "parentTableId": "t1", "childTableId": "t2"},                      # v2
        {"_id": "r2", **on, "sourceTableId": "t3", "targetTableId": "t1"},                     # legacy
        {"_id": "r3", **on, "parentTableId": "t4", "childTableId": "t4"},                      # auto-referencial
        {"_id": "r4", **on, "parentTableId": "t5", "childTableId": "t6", "flgactive": False},  # inactiva
        {"_id": "r5", "projectId": "p2", "flgactive": True, "parentTableId": "t5", "childTableId": "t6"},
        {"_id": "r6", **on, "parentTableId": "t2", "childTableId": "t7"},                      # a una tabla inactiva
    ])


@pytest.fixture
def score_db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    return fake


def test_h7_huerfanas_con_relaciones_v2_y_legacy(score_db):
    _scorecard_world(score_db)
    card = asyncio.run(rep_views.scorecard(P))
    assert card["tables"] == 6 and card["orphanTables"] == 2                      # t5 y t6
    # La MISMA regla que el conteo por tabla del reporte: huérfana = relationshipCount 0.
    rows = asyncio.run(service.list_table_rows(P, {}, None))
    assert card["orphanTables"] == sum(1 for r in rows if r["relationshipCount"] == 0)


def test_h7_proyecto_sin_tablas_no_tiene_huerfanas(score_db):
    assert asyncio.run(rep_views.scorecard(P))["orphanTables"] == 0


def test_h7_proyecto_sin_tablas_no_tiene_tablas_sin_pk(score_db):
    """Mismo artefacto que las huérfanas: el divisor `tables or 1` se usaba
    también como CONTEO — un proyecto vacío tenía «1 tabla sin PK» y su
    completitud salía 0.75 en vez de completa."""
    card = asyncio.run(rep_views.scorecard(P))
    assert card["tablesWithoutPk"] == 0
    assert card["completenessScore"] == 1.0


def test_h7_sin_relaciones_todas_son_huerfanas(score_db):
    score_db.raw["canonical_tables"].insert_many(
        [{"_id": f"t{i}", "projectId": P, "flgactive": True, "physicalName": f"T{i}"} for i in range(3)])
    assert asyncio.run(rep_views.scorecard(P))["orphanTables"] == 3


def test_h7_los_pipelines_del_scorecard_compilan_en_lakebase(score_db, monkeypatch):
    """FakeDb (mongomock) no evalúa expresiones dentro de un array de `$project`
    (el pipeline viejo contaba los literales «$sourceTableId»): lo que corre en
    Lakebase lo decide su traductor — cada pipeline debe compilar sin
    `NotImplementedError`."""
    _scorecard_world(score_db)
    seen: list[list] = []
    real = rep_views._agg

    async def spy(coll, pipeline, limit=None):
        seen.append(pipeline)
        return await real(coll, pipeline, limit)

    monkeypatch.setattr(rep_views, "_agg", spy)
    asyncio.run(rep_views.scorecard(P))
    assert len(seen) == 3                     # doc 105 (ronda 4): columnas por tabla, tablas, relaciones
    for pipeline in seen:
        compile_pipeline(pipeline, "t", Sql())
