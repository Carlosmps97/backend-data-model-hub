"""Doc 105 — standards/apply (segundo seguimiento):

1. Un upsert con el id de un estándar que existe FUERA de los activos del
   proyecto (en otro proyecto, o borrado en este) responde 409 ANTES de
   escribir nada — antes caía al alta, chocaba por clave duplicada (500) y
   dejaba hechas las escrituras previas del lote.
2. P1-bis en dominios, UDP, reglas DDL y config DDL: un upsert que el apply
   grabaría IGUAL a lo vigente (comparando el documento normalizado que
   efectivamente escribiría, no el body crudo) se descarta; si no queda nada,
   422. Casos límite: orden de claves, opcionales ausentes vs null, espacios."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import service
from app.features.data_standards.schemas import ApplyBody, DdlConfigPatch, DdlRuleEdit, DomainEdit, UdpEdit
from app.features.ddl_rules.models import DdlRuleDoc
from app.features.domains.models import ParentDomainDoc
from app.features.udp.models import UdpDefinitionDoc
from tests.features.data_standards.test_standards import _mock_apply

NO_CHANGES = "There are no changes to apply."
DOM = ParentDomainDoc(id="d1", projectId="p1", name="Fecha", defaultDataType="DATE",
                      udpValues={"u1": "a", "u2": "b"}).model_dump()
UDP = UdpDefinitionDoc(id="u1", projectId="p1", name="Clase", level="table", dataType="string").model_dump()
REPORT = {"state": "valid", "checks": [{"name": "Condition syntax", "ok": True, "detail": ""}],
          "errors": [], "warnings": [], "udpRefs": [], "condition": "true"}
RULE = DdlRuleDoc(id="r1", projectId="p1", name="tags_tabla", target="table", condition="true",
                  action={"tags": {"k": "v"}}, appliesTo=["ddl.tabla_fisica"], updatedBy="otro",
                  validationReport={k: REPORT[k] for k in ("state", "checks", "errors", "warnings")}).model_dump()
CONFIG = {"id": "p1", "projectId": "p1", "lookups": {"a": {"values": {"x": 1}}, "b": {"default": None}},
          "functions": [{"name": "f", "params": [], "body": "1"}], "output": {"typeCase": "upper"}}


def _mock(monkeypatch, *, domains=(), udp=(), rules=(), config=None, owners=None):
    inserted = _mock_apply(monkeypatch, before_domains=list(domains))
    monkeypatch.setattr(service.udp_repo, "list_udp", AsyncMock(return_value=list(udp)))
    monkeypatch.setattr(service.rules_repo, "list_rules", AsyncMock(return_value=list(rules)))
    monkeypatch.setattr(service.rules_repo, "get_config", AsyncMock(return_value=config or CONFIG))
    monkeypatch.setattr(service.ddl_validate, "validate_rule", lambda *a, **k: dict(REPORT))
    monkeypatch.setattr(service.repository, "owners", AsyncMock(return_value=owners or {}))
    return inserted


def _no_changes(body):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert (exc.value.status_code, exc.value.detail) == (422, NO_CHANGES)
    service.repository.insert_version_next_seq.assert_not_awaited()
    for repo, fn in ((service.dom_repo, "update_domain"), (service.udp_repo, "update_udp"),
                     (service.rules_repo, "update_rule"), (service.rules_repo, "set_config")):
        getattr(repo, fn).assert_not_awaited()


# ── 1. Ids de otro proyecto (o borrados en este) ─────────────────────────────

@pytest.mark.parametrize("body, label", [
    (ApplyBody(termsUpsert=[{"id": "x", "term": "otra", "abbrev": "OTR", "scope": "column"}]), "The term 'otra'"),
    (ApplyBody(domainsUpsert=[DomainEdit(id="x", name="Otro", defaultDataType="DATE")]), "The domain 'Otro'"),
    (ApplyBody(udpUpsert=[UdpEdit(id="x", name="Otra")]), "The UDP 'Otra'"),
    (ApplyBody(rulesUpsert=[DdlRuleEdit(id="x", name="otra_regla")]), "The rule 'otra_regla'"),
])
def test_upsert_con_id_de_otro_proyecto_409_antes_de_escribir(monkeypatch, body, label):
    _mock(monkeypatch, owners={"x": {"projectId": "p2", "flgactive": True}})
    body = body.model_copy(update={"termsDelete": ["t-propio"]})
    monkeypatch.setattr(service.dict_repo, "list_entries",
                        AsyncMock(return_value=[{"id": "t-propio", "term": "cuenta", "abbrev": "CTA", "scope": "column"}]))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert (exc.value.status_code, exc.value.detail) == (409, f"{label} belongs to another project.")
    service.dict_repo.delete_entry.assert_not_awaited()          # la baja del lote tampoco se hizo
    service.repository.insert_version_next_seq.assert_not_awaited()


def test_upsert_con_id_borrado_en_el_proyecto_409_legible(monkeypatch):
    _mock(monkeypatch, owners={"d9": {"projectId": "p1", "flgactive": False}})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", ApplyBody(domainsUpsert=[DomainEdit(id="d9", name="Vieja",
                                                                                   defaultDataType="DATE")])))
    assert exc.value.status_code == 409
    assert exc.value.detail == "The domain 'Vieja' was deleted; reload the standards and try again."


def test_upsert_con_un_id_nuevo_del_cliente_sigue_siendo_un_alta(monkeypatch):
    _mock(monkeypatch)                                           # el id no existe en ningún proyecto
    asyncio.run(service.apply("ana", "p1", ApplyBody(domainsUpsert=[DomainEdit(id="nuevo", name="Hora",
                                                                               defaultDataType="TIMESTAMP")])))
    service.dom_repo.create_domain.assert_awaited_once()
    service.repository.owners.assert_awaited_once_with("parent_domains", ["nuevo"])


# ── 2. Upserts idénticos por bloque ─────────────────────────────────────────

def test_same_domain_compara_el_doc_normalizado():
    same = DomainEdit(id="d1", name="Fecha", defaultDataType="date",          # canonicaliza a DATE
                      udpValues={"u2": "b", "u1": "a"},                       # otro orden de claves
                      physicalName="  ", description=None)                     # blanco ⇒ derivado; null ⇒ no se escribe
    assert service.same_domain(same, DOM)
    assert not service.same_domain(same.model_copy(update={"name": "Fecha "}), DOM)   # el nombre no se recorta
    assert not service.same_domain(same.model_copy(update={"inheritsName": True}), DOM)
    assert not service.same_domain(same.model_copy(update={"udpValues": {"u1": "a"}}), DOM)
    assert not service.same_domain(same, None)


def test_same_udp_compara_el_doc_normalizado():
    same = UdpEdit(id="u1", name="Clase", level="table", dataType="string",
                   allowedValues=["x"])                                         # no-lista ⇒ se graba []
    assert service.same_udp(same, UDP)
    assert not service.same_udp(UdpEdit(id="u1", name="Clase"), UDP)            # level ausente ⇒ «column»
    assert not service.same_udp(same.model_copy(update={"description": "d"}), UDP)
    lista = {**UDP, "dataType": "list", "allowedValues": ["a", "b"]}
    assert not service.same_udp(same.model_copy(update={"dataType": "list", "allowedValues": ["b", "a"]}), lista)


def test_same_rule_ignora_quien_guardo_pero_no_el_estado_de_validacion():
    edit = DdlRuleEdit(id="r1", name="tags_tabla", target="table", condition=" true ",
                       action={"tags": {"k": "v"}}, appliesTo=["ddl.tabla_fisica"])
    assert service.same_rule(edit, REPORT, "valid", RULE)                       # updatedBy distinto no cuenta
    assert not service.same_rule(edit, REPORT, "valid", {**RULE, "validationState": "stale"})   # revalida
    assert not service.same_rule(edit.model_copy(update={"priority": 50}), REPORT, "valid", RULE)


def test_apply_dominio_identico_422_sin_cascada(monkeypatch):
    _mock(monkeypatch, domains=[DOM])
    _no_changes(ApplyBody(domainsUpsert=[DomainEdit(id="d1", name="Fecha", defaultDataType="DATE",
                                                    udpValues={"u2": "b", "u1": "a"}, physicalName="")]))
    service.dom_svc.count_retype.assert_not_awaited()


def test_apply_udp_identico_422_sin_revalidar_reglas(monkeypatch):
    _mock(monkeypatch, udp=[UDP], rules=[RULE])
    _no_changes(ApplyBody(udpUpsert=[UdpEdit(id="u1", name="Clase", level="table")]))


def test_apply_regla_identica_422(monkeypatch):
    _mock(monkeypatch, rules=[RULE])
    _no_changes(ApplyBody(rulesUpsert=[DdlRuleEdit(id="r1", name="tags_tabla", target="table", condition="true",
                                                   action={"tags": {"k": "v"}}, appliesTo=["ddl.tabla_fisica"])]))


def test_apply_config_identica_422_orden_de_claves_y_claves_desconocidas(monkeypatch):
    _mock(monkeypatch)
    _no_changes(ApplyBody(ddlConfigPatch=DdlConfigPatch(
        lookups={"b": {"default": None}, "a": {"values": {"x": 1}}},            # otro orden
        functions=[{"name": "f", "params": [], "body": "1"}],
        output={"typeCase": "upper", "clave_rara": 1})))                        # la normalización la descarta


def test_apply_config_escribe_solo_los_bloques_que_cambian(monkeypatch):
    inserted = _mock(monkeypatch)
    asyncio.run(service.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
        lookups=dict(CONFIG["lookups"]), output={"typeCase": "lower"}))))
    service.rules_repo.set_config.assert_awaited_once_with("p1", lookups=None, functions=None,
                                                           output={"typeCase": "lower"})
    assert inserted["diff"]["edited"] == ["DDL output settings"]


def test_apply_mixto_registra_solo_lo_que_cambia(monkeypatch):
    inserted = _mock(monkeypatch, domains=[DOM], udp=[UDP], rules=[RULE])
    body = ApplyBody(domainsUpsert=[DomainEdit(id="d1", name="Fecha", defaultDataType="DATE")],
                     udpUpsert=[UdpEdit(id="u1", name="Clase", level="table", description="nueva")],
                     rulesUpsert=[DdlRuleEdit(id="r1", name="tags_tabla", target="table", condition="true",
                                              action={"tags": {"k": "v"}}, appliesTo=["ddl.tabla_fisica"])])
    asyncio.run(service.apply("ana", "p1", body))
    service.dom_repo.update_domain.assert_not_awaited()
    service.rules_repo.update_rule.assert_not_awaited()
    service.udp_repo.update_udp.assert_awaited_once()
    assert inserted["diff"] == {"added": [], "edited": ["UDP Clase"], "removed": []}
