"""El diff de la revisión lee SIEMPRE con alcance de proyecto (doc 75 D1).

Regresión 2026-09-09: `service.diff` pedía las relaciones de las tablas tocadas
con `{"$or": [...]}` PELADO. `assert_scoped_filter` (doc 75, 2026-09-08) exige
`projectId`/`_id`/`tableId` en el PRIMER nivel del filtro, y `$or` no es
ninguno: cada request que tocara una tabla o una columna reventaba con
`MissingProjectError` → 500 sin manejar en `GET /changesets/{id}/diff`. La
pantalla de revisión pintaba "Request not found" y NO se podía aprobar nada.

El fake de `published` corre el guard REAL — el test viejo del diff mockeaba
`repository.published` entero, así que el guard nunca se ejecutaba y toda esta
clase de bug era invisible para la suite.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.scope import assert_scoped_filter
from app.features.changesets import service


def _published_fake(data: dict[str, list[dict]], seen: list[tuple[str, dict | None]]):
    """`repository.published` de mentira que CORRE EL GUARD REAL antes de responder."""
    async def _published(collection, flt=None, **_kw):
        seen.append((collection, flt))
        assert_scoped_filter(collection, flt)
        return list(data.get(collection, []))
    return _published


def _wire(monkeypatch, changes: dict, published: dict) -> list[tuple[str, dict | None]]:
    cs = {"id": "cs1", "projectId": "P1", "status": "submitted", "owner": "ana", "createdAt": "t0"}
    seen: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=cs))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes))
    monkeypatch.setattr(service.repository, "published", _published_fake(published, seen))
    return seen


def test_diff_con_tabla_tocada_lee_relaciones_con_alcance(monkeypatch):
    """Cambio en UNA tabla: la query de relaciones lleva el proyecto del changeset."""
    changes = {"canonical_tables": {"t1": {"op": "upsert", "at": "t1",
                                           "payload": {"physicalName": "M_CLIENTE", "projectId": "P1"}}}}
    published = {"canonical_tables": [{"id": "t1", "physicalName": "M_CLIENTE", "projectId": "P1"}]}
    seen = _wire(monkeypatch, changes, published)

    out = asyncio.run(service.diff("cs1"))

    assert out is not None
    rel = [flt for coll, flt in seen if coll == "relationships"]
    assert rel, "el diff no consultó relationships"
    assert all((f or {}).get("projectId") == "P1" for f in rel), rel


def test_diff_con_columna_tocada_lee_relaciones_con_alcance(monkeypatch):
    """Sólo cambia una COLUMNA: la tabla entra a `touched` por su `tableId`."""
    changes = {"canonical_columns": {"c1": {"op": "upsert", "at": "t1",
                                            "payload": {"tableId": "t1", "physicalName": "COD_CLI"}}}}
    seen = _wire(monkeypatch, changes, {})

    assert asyncio.run(service.diff("cs1")) is not None
    assert [flt for coll, flt in seen if coll == "relationships"]


def test_ninguna_lectura_del_diff_escapa_al_guard(monkeypatch):
    """Barrido: TODO filtro que el diff manda a `published` pasa el guard."""
    changes = {"canonical_tables": {"t1": {"op": "delete", "at": "t1"}},
               "canonical_columns": {"c1": {"op": "upsert", "at": "t1",
                                            "payload": {"tableId": "t2", "physicalName": "X"}}}}
    published = {"canonical_tables": [{"id": "t1", "physicalName": "VIEJA", "projectId": "P1"}],
                 "relationships": [{"id": "r1", "parentTableId": "t1", "childTableId": "t9"}]}
    seen = _wire(monkeypatch, changes, published)

    asyncio.run(service.diff("cs1"))   # el guard levanta desde dentro si algo escapa

    for coll, flt in seen:
        assert_scoped_filter(coll, flt)
    assert len(seen) >= 4
