"""Doc 105 — standards/apply: un lote sin cambios responde 422 sin registrar
versión (P1); una edición idéntica de un término (tras recortar) no es un cambio
ni dispara el re-derivado de los físicos de todo el proyecto, y los términos se
guardan recortados (P1-bis); con un término bloqueado, el 409 del bloqueo sale
antes que el 422 de campo vacío (C1)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import service
from app.features.data_standards.schemas import (
    ApplyBody, DdlConfigPatch, DdlRuleEdit, DomainEdit, NamingEdit, TermEdit, UdpEdit)
from tests.features.data_standards.test_standards import _mock_apply

NO_CHANGES = "There are no changes to apply."
T1 = {"id": "t1", "term": "monto", "abbrev": "MTO", "scope": "column", "locked": False}


# ── Puros ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    ApplyBody(),
    ApplyBody(kind="ddl", title="x", description="y"),
    ApplyBody(ddlConfigPatch=DdlConfigPatch()),
    ApplyBody(termsUpsert=[], termsDelete=[], namingConfig={}),
])
def test_empty_batch_sin_cambios(body):
    assert service._empty_batch(body)


@pytest.mark.parametrize("body", [
    ApplyBody(termsUpsert=[TermEdit(term="a", abbrev="A", scope="column")]),
    ApplyBody(termsDelete=["t1"]),
    ApplyBody(namingConfig={"column": NamingEdit(separator="_", case="upper")}),
    ApplyBody(domainsUpsert=[DomainEdit(name="D", defaultDataType="DATE")]),
    ApplyBody(domainsDelete=["d1"]),
    ApplyBody(udpUpsert=[UdpEdit(name="U")]),
    ApplyBody(udpDelete=["u1"]),
    ApplyBody(rulesUpsert=[DdlRuleEdit(name="r")]),
    ApplyBody(rulesDelete=["r1"]),
    ApplyBody(ddlConfigPatch=DdlConfigPatch(lookups={})),       # vaciar los lookups ES un cambio
    ApplyBody(ddlConfigPatch=DdlConfigPatch(functions=[])),
    ApplyBody(ddlConfigPatch=DdlConfigPatch(output={"typeCase": "upper"})),
])
def test_empty_batch_con_algun_cambio(body):
    assert not service._empty_batch(body)


def test_empty_batch_clasifica_todo_campo_del_lote():
    """Un campo nuevo en ApplyBody/DdlConfigPatch obliga a decidir si es cambio o metadato."""
    assert set(ApplyBody.model_fields) == {*service.BATCH_META, *service.BATCH_CHANGES, "ddlConfigPatch"}
    assert set(DdlConfigPatch.model_fields) == set(service.DDL_PATCH_BLOCKS)


def test_changed_terms_recorta_y_descarta_los_identicos():
    before = {"t1": T1, "t2": {**T1, "id": "t2", "term": "Codigo ", "abbrev": "COD"}}
    ups = [TermEdit(id="t1", term=" monto ", abbrev="MTO ", scope="column"),     # idéntico tras recortar
           TermEdit(id="t2", term="Codigo", abbrev="COD", scope="column"),      # idéntico (el guardado traía espacio)
           TermEdit(id="t1", term="monto", abbrev="MTO", scope="table"),        # cambia el scope
           TermEdit(id="t1", term="Monto", abbrev="MTO", scope="column"),       # cambia la grafía
           TermEdit(term=" dólares ", abbrev=" USD ", scope="column")]          # alta
    out = service.changed_terms(ups, before)
    assert [(t.id, t.term, t.abbrev, t.scope) for t in out] == [
        ("t1", "monto", "MTO", "table"), ("t1", "Monto", "MTO", "column"), (None, "dólares", "USD", "column")]
    assert ups[0].term == " monto "                                              # no muta la entrada


# ── apply (repos mockeados) ─────────────────────────────────────────────────

def _assert_no_escribio():
    service.repository.insert_version_next_seq.assert_not_awaited()
    service.dict_svc.rephysicalize.assert_not_awaited()
    for fn in ("create_entry", "update_entry", "delete_entry"):
        getattr(service.dict_repo, fn).assert_not_awaited()


@pytest.mark.parametrize("body", [ApplyBody(kind="glossary"), ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch())])
def test_apply_lote_vacio_422_sin_version(monkeypatch, body):
    _mock_apply(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert (exc.value.status_code, exc.value.detail) == (422, NO_CHANGES)
    _assert_no_escribio()


def test_apply_edicion_identica_422_sin_version_ni_rephysicalize(monkeypatch):
    _mock_apply(monkeypatch, before_terms=[T1])
    body = ApplyBody(kind="glossary", termsUpsert=[TermEdit(id="t1", term=" monto ", abbrev="MTO ", scope="column")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert (exc.value.status_code, exc.value.detail) == (422, NO_CHANGES)
    _assert_no_escribio()


def test_apply_edicion_identica_con_un_cambio_real_aplica_solo_el_real(monkeypatch):
    inserted = _mock_apply(monkeypatch, before_terms=[T1])
    body = ApplyBody(termsUpsert=[TermEdit(id="t1", term="monto", abbrev="MTO", scope="column")],
                     domainsUpsert=[DomainEdit(name="Fecha", defaultDataType="DATE")])
    asyncio.run(service.apply("ana", "p1", body))
    service.dict_repo.update_entry.assert_not_awaited()
    service.dict_svc.rephysicalize.assert_not_awaited()          # antes: re-derivaba todo el proyecto
    service.dom_repo.create_domain.assert_awaited_once()
    assert inserted["diff"] == {"added": ["Domain Fecha · DATE"], "edited": [], "removed": []}


def test_apply_guarda_termino_y_abreviatura_recortados(monkeypatch):
    inserted = _mock_apply(monkeypatch, before_terms=[T1])
    body = ApplyBody(termsUpsert=[TermEdit(term="  dólares ", abbrev=" USD ", scope="column"),
                                  TermEdit(id="t1", term="monto", abbrev=" MTS ", scope="column")])
    asyncio.run(service.apply("ana", "p1", body))
    service.dict_svc.ensure_term_valid.assert_awaited_once_with("p1", "dólares", "column")
    service.dict_repo.create_entry.assert_awaited_once_with("p1", {"term": "dólares", "abbrev": "USD", "scope": "column"})
    service.dict_repo.update_entry.assert_awaited_once_with("t1", {"term": "monto", "abbrev": "MTS", "scope": "column"})
    assert inserted["diff"]["added"] == ["Term dólares → USD"]
    assert inserted["diff"]["edited"] == ["Term monto → MTS"]


@pytest.mark.parametrize("abbrev", ["", "   "])
def test_c1_apply_bloqueado_sale_409_aunque_venga_vacio(monkeypatch, abbrev):
    _mock_apply(monkeypatch, before_terms=[{**T1, "locked": True}])
    body = ApplyBody(termsUpsert=[TermEdit(id="t1", term="monto", abbrev=abbrev, scope="column")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert exc.value.status_code == 409
    assert exc.value.detail == "The term 'monto' is locked by an admin; unlock it before editing it."
    _assert_no_escribio()


# ── Bajas de ids que no son del proyecto y naming idéntico (doc 105) ───────

NAMING = {"column": {"separator": "_", "case": "upper", "maxLength": 150},
          "table": {"separator": "", "case": "upper", "maxLength": 150}}


def test_existing_only_deja_solo_ids_del_proyecto():
    assert service.existing_only(["a", "x", "b"], {"a": {}, "b": {}}) == ["a", "b"]
    assert service.existing_only([], {"a": {}}) == []


def test_changed_naming_descarta_los_scopes_que_ya_estan_asi():
    edits = {"column": NamingEdit(separator="_", case="upper", maxLength=150),
             "table": NamingEdit(separator="_", case="upper", maxLength=150)}
    assert list(service.changed_naming(edits, NAMING)) == ["table"]
    assert service.changed_naming({"column": NamingEdit(separator="_", case="upper")}, NAMING) == {}
    assert list(service.changed_naming({"column": NamingEdit(separator="_", case="upper", maxLength=60)},
                                       NAMING)) == ["column"]


@pytest.mark.parametrize("body", [ApplyBody(termsDelete=["nope"]), ApplyBody(domainsDelete=["nope"]),
                                  ApplyBody(udpDelete=["nope"]), ApplyBody(rulesDelete=["nope"])])
def test_apply_baja_de_un_id_que_no_es_del_proyecto_422_sin_borrar(monkeypatch, body):
    _mock_apply(monkeypatch, before_terms=[T1])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert (exc.value.status_code, exc.value.detail) == (422, NO_CHANGES)
    _assert_no_escribio()
    for repo, fn in ((service.dom_repo, "delete_domain"), (service.udp_repo, "delete_udp"),
                     (service.rules_repo, "delete_rule")):
        getattr(repo, fn).assert_not_awaited()


def test_apply_baja_ajena_junto_a_un_cambio_real_no_se_registra_ni_borra(monkeypatch):
    inserted = _mock_apply(monkeypatch, before_terms=[T1])
    body = ApplyBody(termsDelete=["de-otro-proyecto"], domainsUpsert=[DomainEdit(name="Fecha", defaultDataType="DATE")])
    asyncio.run(service.apply("ana", "p1", body))
    service.dict_repo.delete_entry.assert_not_awaited()
    service.dict_svc.rephysicalize.assert_not_awaited()          # antes: re-derivaba todo el proyecto
    assert inserted["diff"] == {"added": ["Domain Fecha · DATE"], "edited": [], "removed": []}


def test_apply_naming_identico_422_sin_rephysicalize(monkeypatch):
    _mock_apply(monkeypatch)
    monkeypatch.setattr(service.set_svc, "get_naming_stored", AsyncMock(return_value=NAMING))   # lo guardado
    body = ApplyBody(kind="naming", namingConfig={"column": NamingEdit(separator="_", case="upper", maxLength=150)})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert (exc.value.status_code, exc.value.detail) == (422, NO_CHANGES)
    service.set_repo.upsert.assert_not_awaited()
    _assert_no_escribio()


def test_apply_naming_distinto_se_aplica_y_rederiva(monkeypatch):
    inserted = _mock_apply(monkeypatch)
    monkeypatch.setattr(service.set_svc, "get_naming_stored", AsyncMock(return_value=NAMING))   # lo guardado
    body = ApplyBody(namingConfig={"column": NamingEdit(separator="_", case="upper", maxLength=150),
                                   "table": NamingEdit(separator="_", case="upper", maxLength=150)})
    asyncio.run(service.apply("ana", "p1", body))
    service.set_repo.upsert.assert_awaited_once_with("p1", "table", {"separator": "_", "case": "upper", "maxLength": 150})
    service.dict_svc.rephysicalize.assert_awaited_once()
    assert inserted["diff"]["edited"] == ["Table naming · sep '_' · upper · max 150"]


def test_c1_apply_el_bloqueo_de_uno_gana_al_vacio_de_otro(monkeypatch):
    _mock_apply(monkeypatch, before_terms=[{**T1, "locked": True}])
    body = ApplyBody(termsUpsert=[TermEdit(term="nuevo", abbrev="", scope="column"),
                                  TermEdit(id="t1", term="monto", abbrev="MT2", scope="column")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", "p1", body))
    assert exc.value.status_code == 409
    _assert_no_escribio()
