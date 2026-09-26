"""Doc 95 D8/D9: el rollback de Data Standards re-tipa SOLO los dominios cuyo
tipo cambia y con la regla de la cascada de ida (las columnas que siguen al
dominio vuelven; las divorciadas no se tocan); vista previa antes de confirmar;
historial de UN dominio para revertirlo solo a él."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.data_standards import service

D1_V1 = {"id": "d1", "name": "Codigo Clave", "defaultDataType": "INTEGER", "logicalDataType": "NUMBER"}
D2 = {"id": "d2", "name": "Monto", "defaultDataType": "DECIMAL(18,2)", "logicalDataType": None}


# ── domain_type_changes (puro) ──

def test_solo_cambian_los_dominios_con_otro_tipo():
    current = {"d1": ("UUID", "NUMBER"), "d2": ("DECIMAL(18,2)", None)}
    assert service.domain_type_changes([D1_V1, D2], current) == [
        {"id": "d1", "name": "Codigo Clave", "physical": ("UUID", "INTEGER"), "logical": None}]


def test_sin_estado_actual_no_re_tipa_y_nunca_cascadea_a_vacio():
    assert service.domain_type_changes([D1_V1], {}) == []
    sin_logico = {**D1_V1, "logicalDataType": None}
    assert service.domain_type_changes([sin_logico], {"d1": ("INTEGER", "TEXT")}) == []


# ── rollback (mockeado) ──

def _mock_rollback(monkeypatch, snapshot, current_types, retyped=5):
    monkeypatch.setattr(service.repository, "get_version",
                        AsyncMock(return_value={"seq": 1, "label": "v1", "snapshot": snapshot}))
    monkeypatch.setattr(service, "current_snapshot",
                        AsyncMock(return_value={"domains": [], "dict": [], "namingConfig": {}}))
    for name in ("restore_domains", "restore_dict", "restore_naming"):
        monkeypatch.setattr(service.repository, name, AsyncMock())
    monkeypatch.setattr(service.udp_repo, "restore_udp", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "restore_rules", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "restore_config", AsyncMock())
    monkeypatch.setattr(service.dict_svc, "rephysicalize",
                        AsyncMock(return_value={"updated": {"tables": 0, "columns": 0}}))
    monkeypatch.setattr(service.dom_repo, "types_by_ids", AsyncMock(return_value=current_types))
    monkeypatch.setattr(service.dom_repo, "retype", AsyncMock(return_value=retyped))
    monkeypatch.setattr(service.dom_svc, "propagate", AsyncMock())
    inserted = {}

    async def _ins(pid, fields):
        inserted.update(fields)
        return {**fields, "seq": 3, "label": "v3", "id": "v3"}

    monkeypatch.setattr(service.repository, "insert_version_next_seq", AsyncMock(side_effect=_ins))
    monkeypatch.setattr(service, "audit", AsyncMock())
    return inserted


def test_rollback_retipa_solo_el_dominio_que_cambio_con_la_regla_de_ida(monkeypatch):
    snap = {"domains": [D1_V1, D2], "dict": [], "namingConfig": {}}
    inserted = _mock_rollback(monkeypatch, snap, {"d1": ("UUID", "NUMBER"), "d2": ("DECIMAL(18,2)", None)},
                              retyped=30000)
    asyncio.run(service.rollback("ana", "p1", 1))
    service.dom_repo.retype.assert_awaited_once_with("d1", "physical", "UUID", "INTEGER")
    service.dom_svc.propagate.assert_not_awaited()          # adiós al re-tipo de TODO
    assert inserted["impact"]["columns"] == 30000
    # el tipo actual se lee ANTES de restaurar, con los borrados incluidos
    assert service.dom_repo.types_by_ids.await_args.kwargs == {"include_deleted": True}


def test_rollback_que_revive_un_dominio_borrado_retipa_desde_su_ultimo_tipo(monkeypatch):
    snap = {"domains": [D1_V1], "dict": [], "namingConfig": {}}
    _mock_rollback(monkeypatch, snap, {"d1": ("BIGINT", "NUMBER")})   # hoy borrado con BIGINT
    asyncio.run(service.rollback("ana", "p1", 1))
    service.dom_repo.retype.assert_awaited_once_with("d1", "physical", "BIGINT", "INTEGER")


def test_rollback_con_ambas_facetas_cuenta_cada_columna_una_vez(monkeypatch):
    snap = {"domains": [D1_V1], "dict": [], "namingConfig": {}}
    inserted = _mock_rollback(monkeypatch, snap, {"d1": ("UUID", "TEXT")}, retyped=12)
    asyncio.run(service.rollback("ana", "p1", 1))
    assert service.dom_repo.retype.await_count == 2
    assert inserted["impact"]["columns"] == 12


# ── vista previa ──

def test_preview_lista_dominios_con_columnas_y_si_re_deriva_nombres(monkeypatch):
    snap = {"domains": [D1_V1, D2], "namingConfig": {},
            "dict": [{"id": "t1", "term": "monto", "abbrev": "MTO", "scope": "column"}]}
    monkeypatch.setattr(service.repository, "get_version",
                        AsyncMock(return_value={"seq": 1, "label": "v1", "snapshot": snap}))
    monkeypatch.setattr(service.dom_repo, "types_by_ids",
                        AsyncMock(return_value={"d1": ("UUID", "NUMBER"), "d2": ("DECIMAL(18,2)", None)}))
    monkeypatch.setattr(service.dom_svc, "count_changes", AsyncMock(return_value=177))
    monkeypatch.setattr(service, "current_snapshot",
                        AsyncMock(return_value={"domains": [], "dict": [], "namingConfig": {}}))
    out = asyncio.run(service.rollback_preview("p1", 1))
    assert out == {"target": {"seq": 1, "label": "v1"}, "namesRederived": True,
                   "domains": [{"id": "d1", "name": "Codigo Clave", "physical": {"from": "UUID", "to": "INTEGER"},
                                "logical": None, "columns": 177}]}
    service.dom_svc.count_changes.assert_awaited_once_with(
        "d1", {"physical": "UUID", "logical": "NUMBER"}, {"physical": "INTEGER"})


def test_preview_de_version_inexistente_es_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get_version", AsyncMock(return_value=None))
    assert asyncio.run(service.rollback_preview("p1", 99)) is None


# ── historial de UN dominio (puro) ──

def _v(seq, domains):
    return {"seq": seq, "label": f"v{seq}", "title": f"t{seq}", "author": "ana",
            "createdAt": f"2026-09-{seq:02d}", "kind": "domain", "snapshot": {"domains": domains}}


def test_historial_solo_donde_el_dominio_cambia_mas_reciente_primero():
    d1_uuid = {**D1_V1, "defaultDataType": "UUID"}
    versions = [_v(1, [D1_V1]), _v(2, [D1_V1, D2]), _v(3, [d1_uuid, D2]), _v(4, [D2])]
    out = service.domain_history(versions, "d1")
    assert [e["label"] for e in out] == ["v4", "v3", "v1"]
    assert out[0]["state"] is None                                           # borrado en v4
    assert out[1]["state"]["defaultDataType"] == "UUID" and out[1]["changed"] == ["defaultDataType"]
    assert out[2]["changed"] == [] and out[2]["state"]["name"] == "Codigo Clave"   # creado en v1


def test_historial_de_un_dominio_que_nunca_existio_es_vacio():
    assert service.domain_history([_v(1, [D2])], "d1") == []


# ── rutas ──

def test_rutas_de_vista_previa_e_historial(project_client, monkeypatch):
    monkeypatch.setattr(service, "rollback_preview", AsyncMock(return_value=None))
    assert project_client.get("/api/projects/p1/standards/rollback-preview?targetSeq=4").status_code == 404
    monkeypatch.setattr(service, "domain_history_of", AsyncMock(return_value=[]))
    resp = project_client.get("/api/projects/p1/standards/domains/d1/history")
    assert resp.status_code == 200 and resp.json()["data"] == []
    service.domain_history_of.assert_awaited_once_with("p1", "d1")
