"""`effective` con búsqueda server-side (`q`/`limit`) — repository mockeado.

Semántica: publicado filtrado+capado en Mongo, cambios del changeset filtrados
por el MISMO criterio, overlay, re-filtro post-overlay (un upsert puede renombrar
la entidad y sacarla/meterla del match) y orden por nombre.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import service


def _mock_repo(monkeypatch, published, changes):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "draft", "owner": "ana"}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=published))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(return_value={"canonical_tables": changes}))


def test_q_incluye_entidades_nuevas_del_changeset(monkeypatch):
    """Una tabla NUEVA del changeset cuyo nombre matchea `q` aparece en la
    búsqueda aunque no exista publicada."""
    _mock_repo(
        monkeypatch,
        published=[{"id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente"}],
        changes={"t9": {"op": "upsert", "payload": {"physicalName": "CLIENTENUEVO", "logicalName": "cliente nuevo"}}},
    )
    out = asyncio.run(service.effective("c1", "canonical_tables", q="clien", limit=50))
    assert {d["id"] for d in out} == {"t1", "t9"}


def test_q_excluye_renombradas_fuera_del_match(monkeypatch):
    """Si el changeset renombró la entidad y ya no matchea `q`, el re-filtro
    post-overlay la saca (el publicado viejo matcheaba, el efectivo no)."""
    _mock_repo(
        monkeypatch,
        published=[{"id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente"}],
        changes={"t1": {"op": "upsert", "payload": {"physicalName": "PERSONA", "logicalName": "persona"}}},
    )
    out = asyncio.run(service.effective("c1", "canonical_tables", q="clien", limit=50))
    assert out == []


def test_q_aplica_deletes_del_changeset(monkeypatch):
    """Una tabla publicada que matchea pero fue borrada en el changeset no
    aparece en la búsqueda."""
    _mock_repo(
        monkeypatch,
        published=[
            {"id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente"},
            {"id": "t2", "physicalName": "CLIENTEVIP", "logicalName": "cliente vip"},
        ],
        changes={"t2": {"op": "delete", "at": "2026-07-04T00:00:00+00:00"}},
    )
    out = asyncio.run(service.effective("c1", "canonical_tables", q="cliente", limit=50))
    assert {d["id"] for d in out} == {"t1"}


def test_q_ordena_y_capea(monkeypatch):
    _mock_repo(
        monkeypatch,
        published=[
            {"id": "t2", "physicalName": "CTAB", "logicalName": "b"},
            {"id": "t1", "physicalName": "CTAA", "logicalName": "a"},
            {"id": "t3", "physicalName": "CTAC", "logicalName": "c"},
        ],
        changes={},
    )
    out = asyncio.run(service.effective("c1", "canonical_tables", q="cta", limit=2))
    assert [d["physicalName"] for d in out] == ["CTAA", "CTAB"]


def test_sin_q_conserva_contrato_original(monkeypatch):
    """Sin `q` no hay re-filtro ni cap: overlay completo (compat listDomains etc.)."""
    _mock_repo(
        monkeypatch,
        published=[{"id": "t1", "physicalName": "CLIENTE"}],
        changes={"t9": {"op": "upsert", "payload": {"physicalName": "OTRA"}}},
    )
    out = asyncio.run(service.effective("c1", "canonical_tables"))
    assert {d["id"] for d in out} == {"t1", "t9"}
