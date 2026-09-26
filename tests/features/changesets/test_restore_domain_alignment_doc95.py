"""Doc 95 D10: «Restore to vN» no le devuelve a una columna un tipo que su
dominio ya cambió — Data Standards manda sobre el tipo de las columnas que
siguen a un dominio; el resto de la columna se restaura igual."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import service

TARGET = {"id": "v1", "projectId": "p1", "status": "approved", "appliedAt": "2026-01-01", "versionLabel": "v1"}
AFTER = [{"id": "v2", "appliedAt": "2026-02-01", "versionLabel": "v2"}]


def _mock(monkeypatch, changes, published):
    gets = {"v1": TARGET, "d9": {"id": "d9"}}
    monkeypatch.setattr(service.repository, "get", AsyncMock(side_effect=lambda cid: gets.get(cid)))
    monkeypatch.setattr(service.repository, "applied_after", AsyncMock(return_value=AFTER))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(side_effect=lambda cid, cols=None: changes.get(cid, {})))
    monkeypatch.setattr(service.repository, "list_summaries",
                        AsyncMock(return_value=[{"versionLabel": "v1"}, {"versionLabel": "v2"}]))
    monkeypatch.setattr(service.repository, "create", AsyncMock(return_value={"id": "d9"}))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=published))
    monkeypatch.setattr(service.dom_repo, "types_by_ids", AsyncMock(return_value={"pd": ("UUID", "TEXT")}))
    payloads: dict = {}
    monkeypatch.setattr(service.repository, "set_change",
                        AsyncMock(side_effect=lambda did, coll, eid, op, payload: payloads.update({eid: payload})))
    return payloads


def test_restore_alinea_la_columna_que_hoy_sigue_a_su_dominio(monkeypatch):
    before = {"id": "c1", "tableId": "t1", "physicalName": "CODCLI", "parentDomainId": "pd",
              "dataType": "INTEGER", "logicalDataType": "NUMBER"}
    changes = {"v2": {"canonical_columns": {"c1": {"op": "upsert", "beforeAt": "x", "before": before,
                                                   "payload": {**before, "physicalName": "CODCLIENTE"}}}}}
    today = [{"id": "c1", "tableId": "t1", "parentDomainId": "pd", "dataType": "UUID", "logicalDataType": "TEXT"}]
    payloads = _mock(monkeypatch, changes, today)
    asyncio.run(service.rollback("v1", "ana"))
    assert payloads["c1"]["physicalName"] == "CODCLI"                    # el resto se restaura
    assert (payloads["c1"]["dataType"], payloads["c1"]["logicalDataType"]) == ("UUID", "TEXT")
    service.repository.published.assert_awaited_once_with("canonical_columns", {"_id": {"$in": ["c1"]}})
    assert service.dom_repo.types_by_ids.await_args.args[0] == ["pd"]


def test_restore_sin_columnas_con_dominio_no_consulta_nada(monkeypatch):
    changes = {"v2": {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "x",
                                                  "before": {"id": "t1", "physicalName": "VIEJO"},
                                                  "payload": {"physicalName": "NUEVO"}}}}}
    payloads = _mock(monkeypatch, changes, [])
    asyncio.run(service.rollback("v1", "ana"))
    assert payloads["t1"] == {"physicalName": "VIEJO"}
    service.repository.published.assert_not_awaited()
    service.dom_repo.types_by_ids.assert_not_awaited()
