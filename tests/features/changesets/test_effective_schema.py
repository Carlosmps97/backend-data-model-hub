"""`effective` con filtro por `schema` (Database Explorer draft-aware, doc 25).

Semántica: publicado del esquema (bounded en Mongo) + overlay del draft; a los
`changes` sólo entran los que ya están en el slice o cuyo payload cae en el
esquema (tabla nueva/movida). El POST-filtro por `schema` sobre el overlay saca
lo que el draft sacó del esquema (rename de `schema`). Orden por nombre + cap.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import service


def _mock_repo(monkeypatch, collection, published, changes):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"projectId": "p1", "id": "c1", "status": "draft", "owner": "ana"}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=published))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(return_value={collection: changes}))


def test_schema_incluye_tabla_nueva_del_draft(monkeypatch):
    """Una tabla creada en el draft dentro del esquema aparece aunque NO esté
    publicada (el bug reportado por el owner)."""
    _mock_repo(monkeypatch, "canonical_tables",
               published=[{"id": "t1", "physicalName": "CLIENTE", "schema": "CORE"}],
               changes={"t9": {"op": "upsert", "payload": {"physicalName": "NUEVA", "schema": "CORE"}}})
    out = asyncio.run(service.effective("c1", "canonical_tables", schema="CORE"))
    assert {d["id"] for d in out} == {"t1", "t9"}


def test_schema_excluye_tabla_movida_a_otro_esquema(monkeypatch):
    """Si el draft le cambió el `schema` a una tabla, sale del esquema viejo
    (el publicado matcheaba, el efectivo no → POST-filtro)."""
    _mock_repo(monkeypatch, "canonical_tables",
               published=[{"id": "t1", "physicalName": "CLIENTE", "schema": "CORE"}],
               changes={"t1": {"op": "upsert", "payload": {"physicalName": "CLIENTE", "schema": "STAGE"}}})
    out = asyncio.run(service.effective("c1", "canonical_tables", schema="CORE"))
    assert out == []


def test_schema_aplica_deletes_del_draft(monkeypatch):
    """Una tabla publicada del esquema borrada en el draft no aparece."""
    _mock_repo(monkeypatch, "canonical_tables",
               published=[{"id": "t1", "physicalName": "CLIENTE", "schema": "CORE"},
                          {"id": "t2", "physicalName": "CUENTA", "schema": "CORE"}],
               changes={"t2": {"op": "delete", "at": "2026-07-19T00:00:00+00:00"}})
    out = asyncio.run(service.effective("c1", "canonical_tables", schema="CORE"))
    assert {d["id"] for d in out} == {"t1"}


def test_schema_ordena_por_nombre_y_capea(monkeypatch):
    _mock_repo(monkeypatch, "canonical_tables",
               published=[{"id": "t2", "physicalName": "BETA", "schema": "CORE"},
                          {"id": "t1", "physicalName": "ALFA", "schema": "CORE"},
                          {"id": "t3", "physicalName": "GAMMA", "schema": "CORE"}],
               changes={})
    out = asyncio.run(service.effective("c1", "canonical_tables", schema="CORE", limit=2))
    assert [d["physicalName"] for d in out] == ["ALFA", "BETA"]


def test_schema_en_vistas_ordena_por_name(monkeypatch):
    """Para `views` el orden/lookup usa `name` (no `physicalName`)."""
    _mock_repo(monkeypatch, "views",
               published=[{"id": "v1", "name": "VW_CLIENTE", "schema": "CORE"}],
               changes={"v9": {"op": "upsert", "payload": {"name": "VW_NUEVA", "schema": "CORE"}}})
    out = asyncio.run(service.effective("c1", "views", schema="CORE"))
    assert [d["name"] for d in out] == ["VW_CLIENTE", "VW_NUEVA"]


# ── schema + q combinados (modal "New table · Import existing") ──────────────
# El modal pasa schema + q + limit JUNTOS: buscar por nombre DENTRO del esquema
# elegido. Antes la rama `schema` ignoraba `q` (todo el esquema salía y el filtro
# por nombre no aplicaba en el path draft-aware — el usado al importar tablas).


def test_schema_y_q_filtran_juntos(monkeypatch):
    """Dentro del esquema, sólo las tablas cuyo nombre matchea `q`."""
    _mock_repo(monkeypatch, "canonical_tables",
               published=[{"id": "t1", "physicalName": "CLIENTE", "schema": "CORE"},
                          {"id": "t2", "physicalName": "CUENTA", "schema": "CORE"}],
               changes={})
    out = asyncio.run(service.effective("c1", "canonical_tables", schema="CORE", q="clie", limit=50))
    assert {d["id"] for d in out} == {"t1"}


def test_schema_y_q_incluye_nueva_del_draft_y_excluye_no_match(monkeypatch):
    """Tabla NUEVA del draft en el esquema que matchea `q` aparece; otra del
    mismo esquema que NO matchea `q`, no."""
    _mock_repo(monkeypatch, "canonical_tables",
               published=[{"id": "t1", "physicalName": "CLIENTE", "schema": "CORE"}],
               changes={
                   "t9": {"op": "upsert", "payload": {"physicalName": "CLIENTEVIP", "schema": "CORE"}},
                   "t8": {"op": "upsert", "payload": {"physicalName": "PRODUCTO", "schema": "CORE"}},
               })
    out = asyncio.run(service.effective("c1", "canonical_tables", schema="CORE", q="clie", limit=50))
    assert {d["id"] for d in out} == {"t1", "t9"}


def test_schema_sin_q_conserva_todo_el_esquema(monkeypatch):
    """Sin `q` (Database Explorer) el filtro por esquema devuelve el esquema
    completo — el `q` opcional no rompe el contrato existente."""
    _mock_repo(monkeypatch, "canonical_tables",
               published=[{"id": "t1", "physicalName": "CLIENTE", "schema": "CORE"},
                          {"id": "t2", "physicalName": "CUENTA", "schema": "CORE"}],
               changes={})
    out = asyncio.run(service.effective("c1", "canonical_tables", schema="CORE"))
    assert {d["id"] for d in out} == {"t1", "t2"}
