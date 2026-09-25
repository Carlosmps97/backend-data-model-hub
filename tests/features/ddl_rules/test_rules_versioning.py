"""DDL Export Rules F1 (doc 30): las reglas entran al versionado de Data
Standards (snapshot/apply/rollback) + guard de borrado de UDP referenciado +
helpers puros del service."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import service
from app.features.data_standards.schemas import ApplyBody, DdlConfigPatch, DdlRuleEdit
from app.features.ddl_rules import service as rules_svc

RULE = {
    "id": "r1", "name": "enmascarar_dac", "description": None, "kind": "rule",
    "target": "column", "sourceArtifact": None,
    "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
    "udpRefs": [{"udpId": "u-dac-col", "level": "column"}],
    "action": {"expression": "sha2({col}, 512)", "alias": "{columna.nombre}"},
    "appliesTo": ["ddl.vista_tecnica"], "priority": 100, "enabled": True,
    "validationState": "valid", "validationReport": {}, "updatedBy": "mr",
}
GEN = {
    "id": "g1", "name": "tabla_rechazos", "description": None, "kind": "generator",
    "target": None, "sourceArtifact": "ddl.tabla_fisica", "condition": "true",
    "udpRefs": [], "action": {"emit": {"artifact": "ddl.tabla_rej"}},
    "appliesTo": [], "priority": 200, "enabled": True,
    "validationState": "valid", "validationReport": {}, "updatedBy": "mr",
}


# ── Puro: snapshot / diff / helpers ────────────────────────────────────────

def test_snapshot_of_incluye_reglas_y_config():
    snap = service.snapshot_of(
        domains=[], terms=[], naming={},
        udp=[{"id": "u1", "name": "Clasificacion del Dato", "level": "column",
              "dataType": "list", "allowedValues": ["No DAC"]}],
        ddl_rules=[{**RULE, "junk": "x"}],
        ddl_config={"lookups": {"vacuum_map": {"fromUdpId": "u9", "values": {}}},
                    "functions": [{"name": "enmascarar", "params": [], "body": "..."}]},
    )
    r = snap["ddlRules"][0]
    assert r["name"] == "enmascarar_dac" and "junk" not in r
    assert r["udpRefs"] == [{"udpId": "u-dac-col", "level": "column"}]
    assert snap["ddlConfig"]["lookups"]["vacuum_map"]["fromUdpId"] == "u9"
    assert len(snap["ddlConfig"]["functions"]) == 1


def test_snapshot_of_sin_reglas_emite_vacios():
    snap = service.snapshot_of(domains=[], terms=[], naming={})
    assert snap["ddlRules"] == [] and snap["ddlConfig"] == {"lookups": {}, "functions": [], "output": {}}


def test_build_diff_reglas_y_config():
    body = ApplyBody(
        rulesUpsert=[DdlRuleEdit(name="tags_tabla", kind="rule", target="table"),      # nueva
                     DdlRuleEdit(id="r1", name="enmascarar_dac")],                      # editada
        rulesDelete=["g1", "r-fantasma"],
        ddlConfigPatch=DdlConfigPatch(lookups={"vacuum_map": {}}),
    )
    diff = service.build_diff(body, {}, {}, {}, {"r1": RULE, "g1": GEN})
    assert "Rule tags_tabla" in diff["added"]
    assert "Rule enmascarar_dac" in diff["edited"]
    assert "Generator tabla_rechazos" in diff["removed"]
    assert "Rule r-fantasma" in diff["removed"]
    assert any(e.startswith("DDL lookups") for e in diff["edited"])


def test_rules_referencing_y_lookups_referencing():
    hits = rules_svc.rules_referencing([RULE, GEN], {"u-dac-col"})
    assert hits == [{"id": "r1", "name": "enmascarar_dac"}]
    assert rules_svc.rules_referencing([RULE], {"otro"}) == []
    lk = {"vacuum_map": {"fromUdpId": "u-vac"}, "otro_map": {"fromUdpId": "x"}}
    assert rules_svc.lookups_referencing(lk, {"u-vac"}) == ["vacuum_map"]


def test_artifact_catalog_raices_mas_generadores():
    cat = rules_svc.artifact_catalog([RULE, GEN])
    ids = [a["id"] for a in cat]
    assert ids[:2] == ["ddl.tabla_fisica", "ddl.vista_negocio"]      # raíces primero
    assert "ddl.tabla_rej" in ids                                     # declarado por g1
    rej = next(a for a in cat if a["id"] == "ddl.tabla_rej")
    assert rej["generatedBy"] == "tabla_rechazos" and rej["root"] is False


# ── apply (mockeado) ──────────────────────────────────────────────────────

def _mock_apply(monkeypatch, *, before_rules=None, config=None):
    monkeypatch.setattr(service.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.dict_repo, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.udp_repo, "list_udp", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.udp_repo, "delete_udp", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "list_rules", AsyncMock(return_value=before_rules or []))
    monkeypatch.setattr(service.rules_repo, "get_config",
                        AsyncMock(return_value=config or {"id": "global", "lookups": {}, "functions": []}))
    created, updated, deleted = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(service.rules_repo, "create_rule", created)
    monkeypatch.setattr(service.rules_repo, "update_rule", updated)
    monkeypatch.setattr(service.rules_repo, "delete_rule", deleted)
    set_cfg = AsyncMock()
    monkeypatch.setattr(service.rules_repo, "set_config", set_cfg)
    monkeypatch.setattr(service, "current_snapshot", AsyncMock(return_value={}))
    inserted = {}
    async def _ins(pid, fields):
        inserted.update(fields); return {**fields, "seq": 9, "label": "v9", "id": "v-new"}
    monkeypatch.setattr(service.repository, "insert_version_next_seq", AsyncMock(side_effect=_ins))
    monkeypatch.setattr(service, "audit", AsyncMock())
    return inserted, created, updated, deleted, set_cfg


def test_apply_reglas_crea_version_y_muta(monkeypatch):
    inserted, created, updated, deleted, set_cfg = _mock_apply(monkeypatch, before_rules=[RULE])
    body = ApplyBody(
        kind="ddl",
        rulesUpsert=[DdlRuleEdit(id="r1", name="enmascarar_dac", condition=RULE["condition"],
                                 action=RULE["action"], appliesTo=RULE["appliesTo"]),
                     DdlRuleEdit(name="tags_tabla", kind="rule", target="table")],
        rulesDelete=[],
        ddlConfigPatch=DdlConfigPatch(functions=[{"name": "f", "params": [], "body": "1"}]),
    )
    v = asyncio.run(service.apply("maria.rojas", "p1", body))
    assert v["seq"] == 9 and inserted["kind"] == "ddl"
    updated.assert_awaited_once()                       # r1 existente → update
    created.assert_awaited_once()                       # tags_tabla nueva → create
    assert created.await_args.args[1]["updatedBy"] == "maria.rojas"
    set_cfg.assert_awaited_once_with("p1", lookups=None, functions=[{"name": "f", "params": [], "body": "1"}],
                                     output=None)
    assert "Rule tags_tabla" in inserted["diff"]["added"]
    deleted.assert_not_awaited()                        # el batch no traía rulesDelete


def test_apply_udp_delete_referenciado_por_regla_409(monkeypatch):
    _, created, _, deleted, _ = _mock_apply(monkeypatch, before_rules=[RULE])
    with pytest.raises(HTTPException) as e:
        asyncio.run(service.apply("mr", "p1", ApplyBody(udpDelete=["u-dac-col"])))
    assert e.value.status_code == 409 and "enmascarar_dac" in e.value.detail
    deleted.assert_not_awaited()                        # fail-fast: nada mutado
    service.udp_repo.delete_udp.assert_not_awaited()


def test_apply_udp_delete_referenciado_por_lookup_409(monkeypatch):
    _mock_apply(monkeypatch, config={"id": "global",
                                     "lookups": {"vacuum_map": {"fromUdpId": "u-vac"}},
                                     "functions": []})
    with pytest.raises(HTTPException) as e:
        asyncio.run(service.apply("mr", "p1", ApplyBody(udpDelete=["u-vac"])))
    assert e.value.status_code == 409 and "vacuum_map" in e.value.detail


def test_apply_udp_delete_ok_si_la_regla_cae_en_el_mismo_batch(monkeypatch):
    """El guard evalúa el estado POST-batch: si el batch borra también la regla
    que referenciaba el UDP, no hay nada que proteger."""
    inserted, *_ = _mock_apply(monkeypatch, before_rules=[RULE])
    v = asyncio.run(service.apply("mr", "p1", ApplyBody(udpDelete=["u-dac-col"], rulesDelete=["r1"])))
    assert v["seq"] == 9
    service.udp_repo.delete_udp.assert_awaited_once_with("u-dac-col")
    service.rules_repo.delete_rule.assert_awaited_once_with("r1")


def test_apply_nombre_duplicado_409(monkeypatch):
    _mock_apply(monkeypatch, before_rules=[RULE])
    # colisión con una existente
    with pytest.raises(HTTPException) as e1:
        asyncio.run(service.apply("mr", "p1", ApplyBody(rulesUpsert=[DdlRuleEdit(name="enmascarar_dac")])))
    assert e1.value.status_code == 409
    # duplicado intra-batch (dos altas nuevas con el mismo name)
    with pytest.raises(HTTPException) as e2:
        asyncio.run(service.apply("mr", "p1", ApplyBody(
            rulesUpsert=[DdlRuleEdit(name="x_rule"), DdlRuleEdit(name="x_rule")])))
    assert e2.value.status_code == 409
    # renombrar la MISMA regla a su propio nombre no choca
    v = asyncio.run(service.apply("mr", "p1", ApplyBody(
        rulesUpsert=[DdlRuleEdit(id="r1", name="enmascarar_dac")])))
    assert v["seq"] == 9


# ── rollback (mockeado) ───────────────────────────────────────────────────

def _mock_rollback(monkeypatch, snapshot: dict):
    monkeypatch.setattr(service.repository, "get_version",
                        AsyncMock(return_value={"seq": 3, "label": "v3", "snapshot": snapshot}))
    for fn in ("restore_domains", "restore_dict", "restore_naming"):
        monkeypatch.setattr(service.repository, fn, AsyncMock())
    monkeypatch.setattr(service.udp_repo, "restore_udp", AsyncMock())
    rr, rc = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service.rules_repo, "restore_rules", rr)
    monkeypatch.setattr(service.rules_repo, "restore_config", rc)
    monkeypatch.setattr(service, "current_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(service.repository, "insert_version_next_seq",
                        AsyncMock(side_effect=lambda pid, f: {**f, "seq": 4, "label": "v4", "id": "v4"}))
    monkeypatch.setattr(service, "audit", AsyncMock())
    return rr, rc


def test_rollback_restaura_reglas_y_config(monkeypatch):
    cfg = {"lookups": {"vacuum_map": {"fromUdpId": "u-vac", "values": {}}}, "functions": []}
    rr, rc = _mock_rollback(monkeypatch, {"ddlRules": [RULE, GEN], "ddlConfig": cfg})
    v = asyncio.run(service.rollback("mr", "p1", 3))
    assert v["kind"] == "rollback" and v["revertsSeq"] == 3
    rr.assert_awaited_once_with("p1", [RULE, GEN])
    rc.assert_awaited_once_with("p1", cfg)


def test_rollback_snapshot_pre_feature_deja_catalogo_vacio(monkeypatch):
    """Snapshots anteriores al doc 30 no traen 'ddlRules'/'ddlConfig': el
    rollback restaura con vacío (mismo criterio que tuvo 'udp')."""
    rr, rc = _mock_rollback(monkeypatch, {"dict": [], "domains": []})
    asyncio.run(service.rollback("mr", "p1", 3))
    rr.assert_awaited_once_with("p1", [])
    rc.assert_awaited_once_with("p1", {})
