"""Data Standards: diff/snapshot (puro) + apply/rollback (repos mockeados)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.data_standards import service
from app.features.data_standards.schemas import ApplyBody, DomainEdit, NamingEdit, TermEdit, UdpEdit


# ── snapshot_of / build_diff (puro) ───────────────────────────────────────

def test_snapshot_of_limpia_campos():
    snap = service.snapshot_of(
        domains=[{"id": "d1", "name": "Importe", "defaultDataType": "DECIMAL(18,2)", "extra": "x"}],
        terms=[{"id": "t1", "term": "monto", "abbrev": "MTO", "scope": "column", "wordType": "prime", "junk": 1}],  # wordType: retirado (doc 94)
        naming={"column": {"separator": "_", "case": "upper"}, "table": {"separator": "", "case": "upper"}},
    )
    assert snap["domains"][0] == {"id": "d1", "name": "Importe", "defaultDataType": "DECIMAL(18,2)",
                                   "namingTerm": None, "description": None, "logicalDataType": None,
                                   "inheritsName": None,                       # doc 79: aditivo al snapshot
                                   "physicalName": None, "physicalDescription": None, "udpValues": None}  # doc 85
    assert "junk" not in snap["dict"][0] and "wordType" not in snap["dict"][0]
    assert snap["namingConfig"]["column"] == {"separator": "_", "case": "upper", "maxLength": None}


def test_snapshot_of_conserva_view_de_udp_y_tipo_logico_de_dominio():
    # Doc 69: sin `view` en el snapshot un rollback perdería la faceta.
    snap = service.snapshot_of(
        domains=[{"id": "d1", "name": "Codigo", "defaultDataType": "VARCHAR(30)", "logicalDataType": "VARCHAR(20)"}],
        terms=[], naming={},
        udp=[{"id": "u1", "name": "Atributo Cross", "level": "column", "view": "logical", "dataType": "list",
              "allowedValues": ["No Definido", "Si", "No"], "junk": 1}],
    )
    assert snap["domains"][0]["logicalDataType"] == "VARCHAR(20)"
    assert snap["udp"][0]["view"] == "logical" and "junk" not in snap["udp"][0]


def test_udp_edit_default_view_physical_y_domain_edit_canonicaliza_logico():
    assert UdpEdit(name="X").view == "physical"
    assert UdpEdit(name="X", view="logical").view == "logical"
    d = DomainEdit(name="Monto", defaultDataType="decimal (21,4)", logicalDataType="decimal (22,4)")
    assert (d.defaultDataType, d.logicalDataType) == ("DECIMAL(21,4)", "DECIMAL(22,4)")


def test_build_diff_clasifica_add_edit_remove():
    body = ApplyBody(
        termsUpsert=[TermEdit(term="dólares", abbrev="USD", scope="column"),      # nuevo
                     TermEdit(id="t1", term="monto", abbrev="MTO", scope="column")],  # editado
        termsDelete=["t2"],
        domainsUpsert=[DomainEdit(id="d1", name="Importe", defaultDataType="DECIMAL(20,4)")],  # cambio de tipo
        namingConfig={"table": NamingEdit(separator="", case="upper")},
    )
    before_terms = {"t1": {"term": "monto"}, "t2": {"term": "viejo"}}
    before_domains = {"d1": {"name": "Importe", "defaultDataType": "DECIMAL(18,2)"}}
    diff = service.build_diff(body, before_domains, before_terms)
    assert "Term dólares → USD" in diff["added"]
    assert any("monto" in e for e in diff["edited"])
    assert any("DECIMAL(18,2) → DECIMAL(20,4)" in e for e in diff["edited"])
    assert "Term viejo" in diff["removed"]


# ── apply (mockeado) ──────────────────────────────────────────────────────

def _mock_apply(monkeypatch, *, before_domains=None, before_terms=None, rephys=None, impact_willupdate=0):
    monkeypatch.setattr(service.dom_repo, "list_domains", AsyncMock(return_value=before_domains or []))
    monkeypatch.setattr(service.dict_repo, "list_entries", AsyncMock(return_value=before_terms or []))
    monkeypatch.setattr(service.dict_svc, "ensure_term_valid", AsyncMock())
    monkeypatch.setattr(service.dom_svc, "impact", AsyncMock(return_value={"willUpdate": impact_willupdate}))
    monkeypatch.setattr(service.dict_repo, "delete_entry", AsyncMock())
    monkeypatch.setattr(service.dict_repo, "create_entry", AsyncMock())
    monkeypatch.setattr(service.dict_repo, "update_entry", AsyncMock())
    monkeypatch.setattr(service.set_repo, "upsert", AsyncMock())
    monkeypatch.setattr(service.dom_repo, "delete_domain", AsyncMock())
    monkeypatch.setattr(service.dom_repo, "create_domain", AsyncMock())
    monkeypatch.setattr(service.dom_repo, "update_domain", AsyncMock())
    monkeypatch.setattr(service.udp_repo, "list_udp", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.udp_repo, "create_udp", AsyncMock())
    monkeypatch.setattr(service.udp_repo, "update_udp", AsyncMock())
    monkeypatch.setattr(service.udp_repo, "delete_udp", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "list_rules", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.rules_repo, "get_config",
                        AsyncMock(return_value={"id": "global", "lookups": {}, "functions": []}))
    monkeypatch.setattr(service.rules_repo, "create_rule", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "update_rule", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "delete_rule", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "set_config", AsyncMock())
    monkeypatch.setattr(service.dict_svc, "rephysicalize",
                        AsyncMock(return_value={"updated": rephys or {"tables": 0, "columns": 0}}))
    monkeypatch.setattr(service, "current_snapshot", AsyncMock(return_value={"domains": [], "dict": [], "namingConfig": {}}))
    inserted = {}
    async def _ins(pid, fields):  # el repo asigna seq/label; acá simulamos v17
        inserted.update(fields); return {**fields, "seq": 17, "label": "v17", "id": "v-new"}
    monkeypatch.setattr(service.repository, "insert_version_next_seq", AsyncMock(side_effect=_ins))
    monkeypatch.setattr(service, "audit", AsyncMock())
    return inserted


def test_apply_registra_version_con_seq_incremental_e_impacto(monkeypatch):
    inserted = _mock_apply(
        monkeypatch,
        before_domains=[{"id": "d1", "name": "Importe", "defaultDataType": "DECIMAL(18,2)"}],
        before_terms=[],
        rephys={"tables": 3, "columns": 12},
        impact_willupdate=43,
    )
    body = ApplyBody(
        termsUpsert=[TermEdit(term="dólares", abbrev="USD", scope="column")],
        domainsUpsert=[DomainEdit(id="d1", name="Importe", defaultDataType="DECIMAL(20,4)")],
    )
    v = asyncio.run(service.apply("maria.rojas", "p1", body))
    assert v["seq"] == 17 and v["label"] == "v17"           # max_seq(16)+1
    assert v["author"] == "maria.rojas" and v["status"] == "applied"
    # impacto = rephys.columns (12) + dominio willUpdate (43); tables de rephys.
    assert inserted["impact"] == {"tables": 3, "columns": 55}
    assert "Term dólares → USD" in inserted["diff"]["added"]


def test_apply_sin_cambios_de_udp_no_rephysicaliza(monkeypatch):
    _mock_apply(monkeypatch, before_domains=[{"id": "d1", "name": "X", "defaultDataType": "T"}])
    body = ApplyBody(domainsDelete=["d1"])  # solo dominios → no re-deriva nombres
    asyncio.run(service.apply("ana", "p1", body))
    service.dict_svc.rephysicalize.assert_not_awaited()
    service.dom_repo.delete_domain.assert_awaited_once()


# ── rollback (mockeado) ───────────────────────────────────────────────────

def test_rollback_restaura_snapshot_y_registra_version_nueva(monkeypatch):
    target = {"seq": 16, "label": "v16", "snapshot": {
        "domains": [{"id": "d1", "name": "Importe", "defaultDataType": "DECIMAL(18,2)"}],
        "dict": [{"id": "t1", "term": "monto", "abbrev": "MTO", "scope": "column"}],
        "namingConfig": {"column": {"separator": "_", "case": "upper"}},
    }}
    monkeypatch.setattr(service.repository, "get_version", AsyncMock(return_value=target))
    rd = AsyncMock(); rdi = AsyncMock(); rn = AsyncMock()
    monkeypatch.setattr(service.repository, "restore_domains", rd)
    monkeypatch.setattr(service.repository, "restore_dict", rdi)
    monkeypatch.setattr(service.repository, "restore_naming", rn)
    monkeypatch.setattr(service.udp_repo, "restore_udp", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "restore_rules", AsyncMock())
    monkeypatch.setattr(service.rules_repo, "restore_config", AsyncMock())
    monkeypatch.setattr(service.dict_svc, "rephysicalize", AsyncMock(return_value={"updated": {"tables": 4, "columns": 12}}))
    monkeypatch.setattr(service.dom_svc, "propagate", AsyncMock(return_value={"updated": 5}))
    monkeypatch.setattr(service, "current_snapshot", AsyncMock(return_value={}))
    inserted = {}
    async def _ins(pid, fields):  # el repo asigna seq/label; acá simulamos v19
        inserted.update(fields); return {**fields, "seq": 19, "label": "v19", "id": "v19"}
    monkeypatch.setattr(service.repository, "insert_version_next_seq", AsyncMock(side_effect=_ins))
    monkeypatch.setattr(service, "audit", AsyncMock())

    v = asyncio.run(service.rollback("mr", "p1", 16))
    assert v["seq"] == 19 and v["kind"] == "rollback" and v["revertsSeq"] == 16
    rd.assert_awaited_once(); rdi.assert_awaited_once(); rn.assert_awaited_once()
    # impacto = rephys.columns (12) + propagate (5 × 1 dominio) ; tables rephys.
    assert inserted["impact"] == {"tables": 4, "columns": 17}


def test_rollback_version_inexistente_es_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get_version", AsyncMock(return_value=None))
    assert asyncio.run(service.rollback("mr", "p1", 999)) is None


# ── Doc 80 §4: la marca «atributo estándar» es un cambio auditable ────────

def test_build_diff_nombra_el_cambio_de_inherits_name():
    """Prender/apagar la herencia de nombre+definición cambia qué se estampa en
    CADA columna que adopte el dominio: la versión de estándares debe decirlo,
    no salir como un "Domain X" mudo (así se lee en el historial y en el
    rollback qué se prendió y cuándo)."""
    before = {"d1": {"name": "FecRutina", "defaultDataType": "DATE", "inheritsName": False},
              "d2": {"name": "CodMes", "defaultDataType": "VARCHAR(6)", "inheritsName": True},
              "d3": {"name": "Codigo", "defaultDataType": "VARCHAR(30)", "inheritsName": False}}
    body = ApplyBody(domainsUpsert=[
        DomainEdit(id="d1", name="FecRutina", defaultDataType="DATE", inheritsName=True),      # ON
        DomainEdit(id="d2", name="CodMes", defaultDataType="VARCHAR(6)", inheritsName=False),  # OFF
        DomainEdit(id="d3", name="Codigo", defaultDataType="VARCHAR(30)"),                     # sin tocar
    ])
    diff = service.build_diff(body, before, {})
    assert "Domain FecRutina · inherits name + definition ON" in diff["edited"]
    assert "Domain CodMes · inherits name + definition OFF" in diff["edited"]
    assert "Domain Codigo" in diff["edited"]      # un cliente que no manda el flag no lo toca


def test_build_diff_reporta_tipo_e_inherits_name_juntos():
    before = {"d1": {"name": "FecRutina", "defaultDataType": "DATE", "inheritsName": False}}
    body = ApplyBody(domainsUpsert=[
        DomainEdit(id="d1", name="FecRutina", defaultDataType="TIMESTAMP", inheritsName=True)])
    diff = service.build_diff(body, before, {})
    assert "Domain FecRutina · DATE → TIMESTAMP · inherits name + definition ON" in diff["edited"]


# ── Doc 85: faceta física y UDP por defecto del dominio son cambios auditables ──

def test_build_diff_nombra_fisico_y_udp_defaults_doc85():
    before = {"d1": {"name": "Codigo Clave", "defaultDataType": "VARCHAR(30)", "physicalName": None, "udpValues": {}}}
    body = ApplyBody(domainsUpsert=[DomainEdit(id="d1", name="Codigo Clave", defaultDataType="VARCHAR(30)",
                                               physicalName="CodigoClave", udpValues={"u1": "No"})])
    diff = service.build_diff(body, before, {})
    assert "Domain Codigo Clave · physical name (derived) → CodigoClave · UDP defaults changed" in diff["edited"]


def test_build_diff_volver_al_derivado_y_sin_cambios_de_udp():
    before = {"d1": {"name": "Codigo Clave", "defaultDataType": "VARCHAR(30)", "physicalName": "CodigoClave", "udpValues": {"u1": "No"}}}
    body = ApplyBody(domainsUpsert=[DomainEdit(id="d1", name="Codigo Clave", defaultDataType="VARCHAR(30)",
                                               physicalName="", udpValues={"u1": "No"})])
    diff = service.build_diff(body, before, {})
    assert "Domain Codigo Clave · physical name CodigoClave → (derived)" in diff["edited"]


def test_remap_udp_values_doc85():
    assert service.remap_udp_values({"u-src": "No", "u-gone": "x"}, {"u-src": "u-new"}) == {"u-new": "No"}
    assert service.remap_udp_values({"u-gone": "x"}, {"u-src": "u-new"}) is None
    assert service.remap_udp_values(None, {}) is None and service.remap_udp_values({}, {}) is None
