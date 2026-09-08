"""Doc 70 §3: snapshot de una versión publicada como changeset VIRTUAL
`asof:<versionId>` — inverso compuesto de las versiones posteriores, servido
por `repository.changes_map` a todos los lectores changeset-aware."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import asof, repository, service
from app.main import _asof_unavailable_handler


# ── helpers puros ────────────────────────────────────────────────────────

def test_ids_virtuales():
    assert asof.asof_id("v1") == "asof:v1"
    assert asof.asof_version_id("asof:v1") == "v1"
    assert asof.asof_version_id("c1") is None
    assert asof.asof_version_id(None) is None
    assert asof.asof_version_id("asof:") is None


def test_compose_inverse_gana_la_version_mas_cercana_a_la_objetivo():
    v3 = {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "y",
                                      "before": {"id": "t1", "physicalName": "B"},
                                      "payload": {"physicalName": "C"}}}}
    v2 = {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "x",
                                      "before": {"id": "t1", "physicalName": "A"},
                                      "payload": {"physicalName": "B"}},
                               "t3": {"op": "upsert", "beforeAt": "x", "before": None,
                                      "payload": {"physicalName": "NUEVA"}}},
          "views": {"v1": {"op": "delete", "beforeAt": "x", "before": {"id": "v1", "name": "V"}}}}
    composed, missing = asof.compose_inverse([v3, v2])          # latest → oldest
    # t1: el «antes» de v2 (la más cercana a la objetivo) = estado en la objetivo
    assert composed["canonical_tables"]["t1"] == {"op": "upsert", "payload": {"physicalName": "A"}}
    # t3 creada después → en el snapshot no existe
    assert composed["canonical_tables"]["t3"] == {"op": "delete"}
    # v1 borrada después → en el snapshot vuelve con su doc previo
    assert composed["views"]["v1"] == {"op": "upsert", "payload": {"name": "V"}}
    assert missing == []


def test_compose_inverse_sin_imagen_previa_va_a_faltantes():
    composed, missing = asof.compose_inverse([{"canonical_tables": {"t1": {"op": "upsert",
                                                                           "payload": {"physicalName": "B"}}}}])
    assert composed == {}
    assert missing == ["canonical_tables/t1"]


# ── repository.changes_map despacha el id virtual ────────────────────────

def _wire(monkeypatch, target, later, ledgers):
    monkeypatch.setattr(repository, "get", AsyncMock(return_value=target))
    monkeypatch.setattr(repository, "applied_after", AsyncMock(return_value=later))
    reads: list[tuple] = []

    async def _ledger(cs_id, collections=None):
        reads.append((cs_id, collections))
        return ledgers.get(cs_id, {})
    monkeypatch.setattr(repository, "_ledger_map", _ledger)
    return reads


def test_changes_map_asof_compone_las_posteriores_y_acota_colecciones(monkeypatch):
    reads = _wire(
        monkeypatch,
        target={"id": "v1", "projectId": "p1", "status": "approved", "appliedAt": "2026-01-01"},
        later=[{"id": "v3", "appliedAt": "2026-03-01"}, {"id": "v2", "appliedAt": "2026-02-01"}],
        ledgers={
            "v3": {"canonical_tables": {"t3": {"op": "upsert", "beforeAt": "x", "before": None,
                                               "payload": {"physicalName": "NUEVA"}}}},
            "v2": {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "x",
                                               "before": {"id": "t1", "physicalName": "VIEJO"},
                                               "payload": {"physicalName": "NUEVO"}}}},
        })
    out = asyncio.run(repository.changes_map("asof:v1", ["canonical_tables"]))
    assert out == {"canonical_tables": {"t3": {"op": "delete"},
                                        "t1": {"op": "upsert", "payload": {"physicalName": "VIEJO"}}}}
    assert reads == [("v3", ["canonical_tables"]), ("v2", ["canonical_tables"])]
    repository.applied_after.assert_awaited_once_with("p1", "2026-01-01")


def test_changes_map_asof_de_la_produccion_actual_es_vacio(monkeypatch):
    _wire(monkeypatch, {"id": "v9", "projectId": "p1", "status": "approved", "appliedAt": "2026-09-01"}, [], {})
    assert asyncio.run(repository.changes_map("asof:v9")) == {}


def test_changes_map_id_real_sigue_el_lector_crudo(monkeypatch):
    reads = _wire(monkeypatch, None, [], {"c1": {"views": {"v1": {"op": "delete"}}}})
    assert asyncio.run(repository.changes_map("c1", ["views"])) == {"views": {"v1": {"op": "delete"}}}
    assert reads == [("c1", ["views"])]
    repository.get.assert_not_awaited()


@pytest.mark.parametrize("target, later, ledgers, reason", [
    (None, [], {}, "not-found"),
    ({"id": "d1", "status": "draft", "appliedAt": None}, [], {}, "not-applied"),
    ({"id": "v1", "projectId": "p1", "status": "approved", "appliedAt": "2026-01-01"},
     [{"id": "v2", "appliedAt": "2026-02-01"}],
     {"v2": {"canonical_tables": {"t1": {"op": "upsert", "payload": {"physicalName": "X"}}}}},
     "no-before"),
])
def test_changes_map_asof_errores(monkeypatch, target, later, ledgers, reason):
    _wire(monkeypatch, target, later, ledgers)
    with pytest.raises(asof.AsOfUnavailable) as exc:
        asyncio.run(repository.changes_map("asof:v1"))
    assert exc.value.reason == reason
    if reason == "no-before":
        assert exc.value.missing == ["canonical_tables/t1"]


# ── service: effective/get aceptan el id virtual ─────────────────────────

def test_effective_asof_no_exige_doc_de_changeset(monkeypatch):
    get = AsyncMock(return_value=None)
    monkeypatch.setattr(service.repository, "get", get)
    monkeypatch.setattr(service.repository, "published",
                        AsyncMock(return_value=[{"id": "t1", "physicalName": "NUEVO"},
                                                {"id": "t3", "physicalName": "NUEVA"}]))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={
        "canonical_tables": {"t1": {"op": "upsert", "payload": {"physicalName": "VIEJO"}},
                             "t3": {"op": "delete"}}}))
    out = asyncio.run(service.effective("asof:v1", "canonical_tables", ids=["t1", "t3"]))
    assert out == [{"id": "t1", "physicalName": "VIEJO"}]
    get.assert_not_awaited()                     # el id virtual no tiene doc
    service.repository.changes_map.assert_awaited_once_with("asof:v1", ["canonical_tables"])


def test_get_asof_devuelve_la_cabecera_de_la_version(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={"projectId": "p1", 
        "id": "v1", "status": "approved", "appliedAt": "2026-01-01", "versionLabel": "v1", "title": "Base"}))
    out = asyncio.run(service.get("asof:v1"))
    assert out["id"] == "asof:v1" and out["versionLabel"] == "v1" and out["diff"] == {}
    assert out["asOf"] == {"versionId": "v1", "versionLabel": "v1", "appliedAt": "2026-01-01"}
    service.repository.get.assert_awaited_once_with("v1")


# ── handler HTTP ─────────────────────────────────────────────────────────

def test_handler_mapea_404_y_409_con_detail():
    r = asyncio.run(_asof_unavailable_handler(None, asof.AsOfUnavailable("no-before", "v1", ["x/y"])))
    assert r.status_code == 409
    assert "can't be reconstructed" in json.loads(r.body)["detail"]
    r = asyncio.run(_asof_unavailable_handler(None, asof.AsOfUnavailable("not-found", "v1")))
    assert r.status_code == 404
