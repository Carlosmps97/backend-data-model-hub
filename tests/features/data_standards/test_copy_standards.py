"""Doc 75 D15 — proyecto nuevo con copia de bloques de estándares de otro."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import service


SRC_UDP = [{"id": "u-src", "name": "Clasificacion del Dato", "level": "column", "view": "physical",
            "dataType": "list", "defaultValue": "No Definido", "allowedValues": ["No Definido", "DAC"], "description": None}]
SRC_RULES = [{"id": "r-src", "name": "tags", "kind": "rule", "target": "column", "sourceArtifact": None,
              "condition": 'columna.udp["Clasificacion del Dato"] <> \'No Definido\'',
              "udpRefs": [{"udpId": "u-src", "level": "column"}],
              "action": {"tags": {"c": "{udp:Clasificacion del Dato}"}}, "appliesTo": ["ddl.tabla_fisica"],
              "priority": 50, "enabled": True, "validationState": "valid", "validationReport": {}, "updatedBy": None}]


def _mock(monkeypatch):
    created = {"domains": [], "terms": [], "udp": [], "rules": [], "config": [], "naming": []}
    monkeypatch.setattr(service.dom_repo, "list_domains",
                        AsyncMock(return_value=[{"id": "d-src", "name": "Codigo", "defaultDataType": "VARCHAR(20)",
                                                 "logicalDataType": None, "namingTerm": None, "description": None}]))
    monkeypatch.setattr(service.dict_repo, "list_entries",
                        AsyncMock(return_value=[{"id": "t-src", "term": "codigo", "abbrev": "COD", "scope": "column",
                                                 "wordType": None, "locked": True, "lockedBy": "admin", "lockedAt": "x"}]))
    monkeypatch.setattr(service.udp_repo, "list_udp", AsyncMock(return_value=SRC_UDP))
    monkeypatch.setattr(service.rules_repo, "list_rules", AsyncMock(return_value=SRC_RULES))
    monkeypatch.setattr(service.rules_repo, "get_config",
                        AsyncMock(return_value={"id": "src", "projectId": "src", "functions": [],
                                                "lookups": {"m": {"fromUdpId": "u-src", "fromLevel": "column", "values": {}, "default": None}}}))
    monkeypatch.setattr(service.set_svc, "get_naming",
                        AsyncMock(return_value={"column": {"separator": "_", "case": "lower", "maxLength": 90},
                                                "table": {"separator": "", "case": "upper", "maxLength": 150}}))
    monkeypatch.setattr(service.dom_repo, "create_domain", AsyncMock(side_effect=lambda pid, d: created["domains"].append((pid, d)) or {**d, "id": "d-new"}))
    monkeypatch.setattr(service.dict_repo, "create_entry", AsyncMock(side_effect=lambda pid, d: created["terms"].append((pid, d)) or {**d, "id": "t-new"}))
    monkeypatch.setattr(service.udp_repo, "create_udp", AsyncMock(side_effect=lambda pid, d: created["udp"].append((pid, d)) or {**d, "id": "u-new"}))
    monkeypatch.setattr(service.rules_repo, "create_rule", AsyncMock(side_effect=lambda pid, d: created["rules"].append((pid, d)) or {**d, "id": "r-new"}))
    monkeypatch.setattr(service.rules_repo, "set_config", AsyncMock(side_effect=lambda pid, **kw: created["config"].append((pid, kw)) or {}))
    monkeypatch.setattr(service.set_repo, "upsert", AsyncMock(side_effect=lambda pid, sc, d: created["naming"].append((pid, sc, d)) or {}))
    monkeypatch.setattr(service, "current_snapshot", AsyncMock(return_value={"domains": [], "dict": [], "namingConfig": {}}))
    monkeypatch.setattr(service.repository, "insert_version_next_seq",
                        AsyncMock(side_effect=lambda pid, f: {"id": "v", "seq": 1, "label": "v1", "projectId": pid, **f}))
    monkeypatch.setattr(service, "audit", AsyncMock())
    return created


def test_copia_total_remapea_udp_en_reglas_y_lookups_y_quita_locks(monkeypatch):
    created = _mock(monkeypatch)
    v = asyncio.run(service.copy_standards("ana", "dst", "src", list(service.COPY_BLOCKS), "DDV"))
    assert v["kind"] == "copy" and v["projectId"] == "dst" and "DDV" in v["title"]
    assert created["domains"][0][0] == "dst" and "id" not in created["domains"][0][1]
    term = created["terms"][0][1]
    assert term["locked"] is False and term.get("lockedBy") is None
    assert created["udp"][0][0] == "dst"
    rule = created["rules"][0][1]
    assert rule["udpRefs"] == [{"udpId": "u-new", "level": "column"}] and rule["validationState"] == "valid"
    assert created["config"][0][1]["lookups"]["m"]["fromUdpId"] == "u-new"
    assert created["naming"] == [("dst", "column", {"separator": "_", "case": "lower", "maxLength": 90}),
                                 ("dst", "table", {"separator": "", "case": "upper", "maxLength": 150})]


def test_copia_de_reglas_sin_udp_las_deja_stale(monkeypatch):
    created = _mock(monkeypatch)
    asyncio.run(service.copy_standards("ana", "dst", "src", ["ddl"]))
    rule = created["rules"][0][1]
    assert rule["validationState"] == "stale" and rule["udpRefs"] == []
    assert created["config"][0][1]["lookups"]["m"]["fromUdpId"] is None
    assert created["udp"] == []


def test_bloque_desconocido_422(monkeypatch):
    _mock(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.copy_standards("ana", "dst", "src", ["glossary", "otro"]))
    assert exc.value.status_code == 422


def test_bootstrap_sin_copia_registra_baseline_vacio(monkeypatch):
    _mock(monkeypatch)
    v = asyncio.run(service.bootstrap_project("ana", "dst", None))
    assert v["kind"] == "baseline" and v["projectId"] == "dst"
