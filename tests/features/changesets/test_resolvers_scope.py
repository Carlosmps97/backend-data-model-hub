"""Los tres resolvers de nombres piden los catálogos DEL PROYECTO (doc 80 §8).

`_detail_resolvers` (popup «Change details»), `_history_resolvers` (historial
del Object Inspector) y `_compare_resolvers` (compare de versiones) llamaban a
`udp_repo.list_udp()` / `dom_repo.list_domains()` SIN proyecto —firmas
pre-doc-75— y a `published("folders")` sin filtro. En vivo: `TypeError` y
`MissingProjectError` ⇒ 500 en las tres pantallas.

Los fakes de acá llevan la firma REAL y REGISTRAN el proyecto recibido: un fake
sin parámetro (como los que había) vuelve a esconder exactamente este bug.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.core.scope import assert_scoped_filter
from app.features.changesets import service


def _catalog_spies(monkeypatch) -> dict[str, list]:
    """Fakes con la firma real; guardan con qué proyecto los llamaron."""
    seen: dict[str, list] = {"udp": [], "domains": [], "published": []}

    async def _udp(project_id):
        seen["udp"].append(project_id)
        return [{"id": "u1", "name": "Sensibilidad"}]

    async def _domains(project_id):
        seen["domains"].append(project_id)
        return [{"id": "d1", "name": "Dominio Cliente"}]

    monkeypatch.setattr("app.features.udp.repository.list_udp", _udp)
    monkeypatch.setattr("app.features.domains.repository.list_domains", _domains)
    return seen


def _published_spy(seen: dict[str, list], data: dict[str, list[dict]] | None = None):
    """`published` que corre el GUARD REAL — un filtro sin alcance levanta acá."""
    async def _published(collection, flt=None, **_kw):
        seen["published"].append((collection, flt))
        assert_scoped_filter(collection, flt)
        return list((data or {}).get(collection, []))
    return _published


# ── popup «Change details» (doc 31) ───────────────────────────────────────

def test_diff_details_pide_los_catalogos_del_proyecto(monkeypatch):
    seen = _catalog_spies(monkeypatch)
    cs = {"id": "cs1", "projectId": "P1", "status": "submitted", "owner": "ana", "createdAt": "t0"}
    changes = {"canonical_columns": {"c1": {"op": "upsert", "at": "t1",
                                            "payload": {"tableId": "t1", "physicalName": "COD"}}},
               "subject_areas": {"sa1": {"op": "upsert", "at": "t1", "payload": {"name": "test"}}}}
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=cs))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes))
    monkeypatch.setattr(service.repository, "published", _published_spy(seen))

    out = asyncio.run(service.diff_details("cs1", [("canonical_columns", "c1"),
                                                   ("subject_areas", "sa1")]))

    assert out is not None and len(out["items"]) == 2
    assert seen["udp"] == ["P1"] and seen["domains"] == ["P1"]
    # `folders`/`projects` se resuelven para ubicar el canvas: con alcance.
    for coll, flt in seen["published"]:
        assert_scoped_filter(coll, flt)
    assert ("folders", {"projectId": "P1"}) in seen["published"]
    assert ("projects", {"_id": "P1"}) in seen["published"]


# ── historial del Object Inspector (doc 51) ───────────────────────────────

def _history_wire(monkeypatch, seen, *, header_project, doc_project):
    change = {"csId": "cs1", "collection": "canonical_tables", "entityId": "t1", "op": "upsert",
              "before": None, "beforeAt": "x", "at": "2026-08-01T00:00:00+00:00",
              "payload": {"physicalName": "HM_CUENTATRABAJO"}, "userId": "u9"}
    header = {"id": "cs1", "status": "approved", "appliedAt": "2026-08-02T00:00:00+00:00",
              "versionLabel": "v2", "title": "a", "owner": "u9", "approvals": {}}
    if header_project:
        header["projectId"] = header_project
    doc = {"id": "t1", "physicalName": "HM_CUENTATRABAJO", "createdAt": "2026-07-01T00:00:00+00:00"}
    if doc_project:
        doc["projectId"] = doc_project
    monkeypatch.setattr(service.repository, "entity_changes", AsyncMock(return_value=[change]))
    monkeypatch.setattr(service.repository, "changesets_by_ids", AsyncMock(return_value={"cs1": header}))
    monkeypatch.setattr(service.repository, "published", _published_spy(seen, {"canonical_tables": [doc]}))
    monkeypatch.setattr(service.repository, "earliest_applied", AsyncMock(return_value=None))
    monkeypatch.setattr("app.features.auth.repository.list_users", AsyncMock(return_value=[]))


def test_entity_history_resuelve_el_proyecto_desde_la_cabecera(monkeypatch):
    seen = _catalog_spies(monkeypatch)
    _history_wire(monkeypatch, seen, header_project="P1", doc_project="P1")

    out = asyncio.run(service.entity_history("canonical_tables", "t1"))

    assert out["items"], "el historial quedó vacío"
    assert seen["udp"] == ["P1"]


def test_entity_history_cae_al_doc_publicado_si_la_cabecera_no_trae_proyecto(monkeypatch):
    """Cabeceras legacy (pre-doc-75) sin `projectId`: manda el doc publicado."""
    seen = _catalog_spies(monkeypatch)
    _history_wire(monkeypatch, seen, header_project=None, doc_project="P9")

    asyncio.run(service.entity_history("canonical_tables", "t1"))

    assert seen["udp"] == ["P9"]


def test_entity_history_sin_proyecto_no_revienta_ni_consulta_catalogos(monkeypatch):
    """Sin proyecto por ningún lado: el historial se muestra igual, sólo sin
    resolver nombres de UDP/dominio. Un `list_udp(None)` sería un 500."""
    seen = _catalog_spies(monkeypatch)
    _history_wire(monkeypatch, seen, header_project=None, doc_project=None)

    out = asyncio.run(service.entity_history("canonical_tables", "t1"))

    assert out["items"]
    assert seen["udp"] == [] and seen["domains"] == []


# ── compare de versiones (doc 65) ─────────────────────────────────────────

def _compare_wire(monkeypatch, seen):
    older = {"id": "v1", "projectId": "P1", "status": "approved",
             "appliedAt": "2026-08-01T00:00:00+00:00", "versionLabel": "v1", "title": "v1"}
    newer = {"id": "v2", "projectId": "P1", "status": "approved",
             "appliedAt": "2026-08-05T00:00:00+00:00", "versionLabel": "v2", "title": "v2"}
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(side_effect=lambda cid: {"v1": older, "v2": newer}.get(cid)))
    monkeypatch.setattr(service.repository, "applied_after", AsyncMock(return_value=[newer]))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value={
        "canonical_columns": {"c1": {"op": "upsert", "before": None, "beforeAt": "x",
                                     "payload": {"tableId": "t1", "physicalName": "COD"}}},
        "subject_areas": {"sa1": {"op": "upsert", "before": None, "beforeAt": "x",
                                  "payload": {"name": "test"}}}}))
    monkeypatch.setattr(service.repository, "published", _published_spy(seen))


def test_compare_versions_pide_los_catalogos_del_proyecto(monkeypatch):
    seen = _catalog_spies(monkeypatch)
    _compare_wire(monkeypatch, seen)

    out = asyncio.run(service.compare_versions("v1", "v2"))

    assert isinstance(out, dict)
    assert seen["udp"] == ["P1"] and seen["domains"] == ["P1"]
    for coll, flt in seen["published"]:
        assert_scoped_filter(coll, flt)


def test_compare_details_pide_los_catalogos_del_proyecto(monkeypatch):
    seen = _catalog_spies(monkeypatch)
    _compare_wire(monkeypatch, seen)

    out = asyncio.run(service.compare_details("v1", "v2", [("canonical_columns", "c1")]))

    assert isinstance(out, dict) and len(out["items"]) == 1
    assert seen["udp"] == ["P1"] and seen["domains"] == ["P1"]
