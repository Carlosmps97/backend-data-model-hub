"""Los 5 checks del validador (doc 30 §9) con el catálogo REAL de UDPs:
nombres exactos de UI, waiting en cascada, Levenshtein, la trampa DAC y el
warning obligatorio de '%DAC%'. + wiring en el apply de standards."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import service as std_service
from app.features.data_standards.schemas import ApplyBody, DdlRuleEdit
from app.features.ddl_rules.engine import validate as v

# Subconjunto REAL del catálogo (verificado contra la BD, doc 30 A4).
DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC-DOCUMENTO", "DAC-NOMBRE", "DAC-DIRECCION",
                       "DAC-TELEFONO", "DAC-CUENTA", "DAC-TARJETA", "DAC-EMAIL", "DAC-BIOMETRICO",
                       "DAC-IMAGENVOZ", "DAC-FIRMA", "DAC-GLOSADAC"]},
    {"id": "u-dac-tab", "name": "Clasificacion del Dato", "level": "table", "dataType": "list",
     "allowedValues": ["No DAC", "No Definido", "DAC"]},
    {"id": "u-vac", "name": "Frecuencia Vacuum", "level": "table", "dataType": "list",
     "allowedValues": ["CUSTOM_90 days", "DAILY_15 days"]},
    {"id": "u-tv", "name": "Tipo de Vista", "level": "table", "dataType": "list",
     "allowedValues": ["Regular", "Personalizada"]},
    {"id": "u-dom", "name": "Dominio Principal", "level": "table", "dataType": "string",
     "allowedValues": []},
]
CONFIG = {"lookups": {"vacuum_map": {"fromUdpId": "u-vac", "values": {}}},
          "functions": [{"name": "enmascarar", "params": ["col", "nivel"], "body": "CASE END"}]}
ARTS = ["ddl.tabla_fisica", "ddl.vista_negocio", "ddl.vista_tecnica"]


def rule(**kw) -> dict:
    base = {"id": None, "name": "r_test", "kind": "rule", "target": "column",
            "sourceArtifact": None, "condition": "", "udpRefs": [], "action": {},
            "appliesTo": ["ddl.vista_tecnica"], "priority": 100, "enabled": True}
    return {**base, **kw}


def names_ok(report):
    return {c["name"]: c["ok"] for c in report["checks"]}


# ── Los 5 checks ───────────────────────────────────────────────────────────

def test_regla_valida_pasa_los_5_checks():
    r = rule(condition='columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
             action={"expression": "sha2({col}, 512)", "alias": "{columna.nombre}"})
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    assert rep["state"] == "valid" and rep["errors"] == []
    assert list(names_ok(rep).values()) == [True, True, True, True, True]
    assert [c["name"] for c in rep["checks"]] == list(v.CHECK_NAMES)
    assert rep["udpRefs"] == [{"udpId": "u-dac-col", "level": "column"}]


def test_typo_de_udp_sugiere_levenshtein_y_pone_waiting():
    r = rule(condition='columna.udp["Clasificacion del Dat"] LIKE \'DAC-%\'')
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    ok = names_ok(rep)
    assert rep["state"] == "invalid"
    assert ok["Condition syntax"] is True
    assert ok["UDP exists in catalog"] is False
    assert ok["Value allowed for UDP"] is None          # waiting (16c·2)
    err = next(e for e in rep["errors"] if e["check"] == "UDP exists in catalog")
    assert err["suggestion"] == "Clasificacion del Dato"
    assert "Did you mean 'Clasificacion del Dato'?" in err["message"]
    assert err["snippet"] and "^" in err["snippet"]      # caret al token exacto


def test_sintaxis_rota_pone_waiting_en_2_y_3():
    rep = v.validate_rule(rule(condition="columna.udp[ = 'x'"), DEFS, CONFIG, ARTS)
    ok = names_ok(rep)
    assert ok["Condition syntax"] is False
    assert ok["UDP exists in catalog"] is None and ok["Value allowed for UDP"] is None


def test_trampa_dac_igual_plano_ensena_like_anclado():
    """spec §9: '= 'DAC'' a nivel columna → mensaje que enseña LIKE 'DAC-%'."""
    r = rule(condition='columna.udp["Clasificacion del Dato"] = \'DAC\'')
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    err = next(e for e in rep["errors"] if e["check"] == "Value allowed for UDP")
    assert "prefix family" in err["message"]
    assert "LIKE 'DAC-%'" in err["message"] and err["suggestion"] == "LIKE 'DAC-%'"


def test_valor_no_permitido_sugiere_cercano():
    r = rule(kind="rule", target="table",
             condition='tabla.udp["Tipo de Vista"] = \'Regularr\'')
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    err = next(e for e in rep["errors"] if e["check"] == "Value allowed for UDP")
    assert err["suggestion"] == "Regular"


def test_warning_obligatorio_porciento_dac():
    r = rule(condition='columna.udp["Clasificacion del Dato"] LIKE \'%DAC%\'')
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    assert rep["state"] == "valid"                       # warning, NO error (spec §9)
    assert any("'No DAC'" in w for w in rep["warnings"])


def test_expresion_databricks_invalida_falla_check_4():
    r = rule(condition="", action={"expression": "sha2({col}, 512"})   # paréntesis sin cerrar
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    assert names_ok(rep)["Expression syntax (Databricks)"] is False


def test_placeholders_col_solo_en_reglas_de_columna():
    r = rule(target="table", condition="tabla.udp[\"Dominio Principal\"] IS NOT NULL",
             action={"expression": "sha2({col}, 512)"})
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    err = next(e for e in rep["errors"] if e["check"] == "Placeholders resolved")
    assert "{col}" in err["message"]


def test_placeholder_udp_lookup_funcion():
    ok_rule = rule(target="table", condition="",
                   action={"tags": {"dominio": "{udp:Dominio Principal}"},
                           "tblproperties": {"x": "{lookup:vacuum_map}"},
                           "expression": "{enmascarar(col, 'DAC')}"})
    rep = v.validate_rule(ok_rule, DEFS, CONFIG, ARTS)
    assert names_ok(rep)["Placeholders resolved"] is True
    bad = rule(target="table", condition="",
               action={"tags": {"a": "{udp:Dominio Principall}", "b": "{lookup:vacum_map}",
                                "c": "{enmascararr(col)}"}})
    rep2 = v.validate_rule(bad, DEFS, CONFIG, ARTS)
    msgs = " · ".join(e["message"] for e in rep2["errors"])
    assert "Dominio Principal" in msgs and "vacuum_map" in msgs and "enmascarar" in msgs


def test_columna_en_regla_de_tabla_no_existe():
    r = rule(target="table", condition="columna.pk = true")
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    assert rep["state"] == "invalid"
    assert "columna" in rep["errors"][0]["message"]


def test_generator_estructura():
    g = {"name": "g", "kind": "generator", "target": None, "sourceArtifact": None,
         "condition": "true", "udpRefs": [], "action": {"emit": {"artifact": "tabla_rej"}},
         "appliesTo": [], "priority": 200, "enabled": True}
    rep = v.validate_rule(g, DEFS, CONFIG, ARTS)
    msgs = " · ".join(e["message"] for e in rep["errors"])
    assert "source artifact" in msgs and "'ddl.' prefix" in msgs
    g2 = {**g, "sourceArtifact": "ddl.tabla_fisica",
          "action": {"emit": {"artifact": "ddl.tabla_fisica"}}}
    rep2 = v.validate_rule(g2, DEFS, CONFIG, ARTS)
    assert any("cycle" in e["message"] for e in rep2["errors"])


def test_applies_to_desconocido_y_vacio_son_warnings():
    """Artefacto sin generador todavía = warning (se puede autorar la regla
    antes que su generador), igual que una regla sin destinos."""
    rep = v.validate_rule(rule(appliesTo=["ddl.no_existe"]), DEFS, CONFIG, ARTS)
    assert rep["state"] == "valid"
    assert any("No generator declares 'ddl.no_existe'" in w for w in rep["warnings"])
    rep2 = v.validate_rule(rule(appliesTo=[]), DEFS, CONFIG, ARTS)
    assert any("no target artifacts" in w for w in rep2["warnings"])


def test_recanonize_tras_rename_de_udp():
    """Renombrar el UDP no rompe la regla (binding por id): el texto guardado
    con el nombre viejo se re-escribe al vigente y valida."""
    r = rule(condition='columna.udp["Clasificacion del Dato VIEJO"] LIKE \'DAC-%\'',
             udpRefs=[{"udpId": "u-dac-col", "level": "column"}])
    rep = v.validate_rule(r, DEFS, CONFIG, ARTS)
    assert rep["state"] == "valid"
    assert 'udp["Clasificacion del Dato"]' in rep["condition"]


def test_passive_state_stale_solo_por_valores():
    rep_val = {"state": "invalid", "errors": [{"check": "Value allowed for UDP"}]}
    rep_udp = {"state": "invalid", "errors": [{"check": "UDP exists in catalog"}]}
    rep_ok = {"state": "valid", "errors": []}
    assert v.passive_state(rep_val) == "stale"
    assert v.passive_state(rep_udp) == "invalid"
    assert v.passive_state(rep_ok) == "valid"


# ── Wiring en el apply de standards ────────────────────────────────────────

def _mock_apply(monkeypatch, *, before_rules=None, udp=None):
    s = std_service
    monkeypatch.setattr(s.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(s.dict_repo, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(s.udp_repo, "list_udp", AsyncMock(return_value=udp or DEFS))
    monkeypatch.setattr(s.rules_repo, "list_rules", AsyncMock(return_value=before_rules or []))
    monkeypatch.setattr(s.rules_repo, "get_config", AsyncMock(return_value={"id": "global", **CONFIG}))
    for fn in ("create_rule", "update_rule", "delete_rule", "set_config"):
        monkeypatch.setattr(s.rules_repo, fn, AsyncMock())
    monkeypatch.setattr(s, "current_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(s.repository, "insert_version_next_seq",
                        AsyncMock(side_effect=lambda pid, f: {**f, "seq": 5, "label": "v5", "id": "v5"}))
    monkeypatch.setattr(s, "audit", AsyncMock())


def test_apply_regla_nueva_invalida_400(monkeypatch):
    _mock_apply(monkeypatch)
    body = ApplyBody(rulesUpsert=[DdlRuleEdit(
        name="mala", condition='columna.udp["No Existe"] = \'x\'')])
    with pytest.raises(HTTPException) as e:
        asyncio.run(std_service.apply("mr", "p1", body))
    assert e.value.status_code == 400 and "mala" in e.value.detail
    std_service.rules_repo.create_rule.assert_not_awaited()


def test_apply_persiste_reporte_y_udprefs_derivados(monkeypatch):
    _mock_apply(monkeypatch)
    body = ApplyBody(rulesUpsert=[DdlRuleEdit(
        name="enmascarar_dac",
        condition='columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
        action={"expression": "sha2({col}, 512)"}, appliesTo=["ddl.vista_tecnica"],
        udpRefs=[{"udpId": "hack-cliente", "level": "column"}])])   # el server NO confía en esto
    asyncio.run(std_service.apply("mr", "p1", body))
    saved = std_service.rules_repo.create_rule.await_args.args[1]
    assert saved["validationState"] == "valid"
    assert saved["udpRefs"] == [{"udpId": "u-dac-col", "level": "column"}]  # derivado server-side
    assert saved["validationReport"]["state"] == "valid"


def test_apply_toggle_de_regla_ya_invalida_se_permite(monkeypatch):
    """Editar props sueltas (enabled/priority) de una regla YA rota no bloquea:
    el export la salta igual. Solo se bloquea guardar un CORE editado inválido."""
    prev = {"id": "r1", "name": "rota", "kind": "rule", "target": "column",
            "sourceArtifact": None, "condition": 'columna.udp["Ya No Existe"] = \'x\'',
            "udpRefs": [], "action": {}, "appliesTo": ["ddl.vista_tecnica"],
            "priority": 70, "enabled": True, "validationState": "invalid",
            "validationReport": {}, "updatedBy": "mr"}
    _mock_apply(monkeypatch, before_rules=[prev])
    body = ApplyBody(rulesUpsert=[DdlRuleEdit(**{**prev, "enabled": False,
                                                 "validationReport": {}})])
    asyncio.run(std_service.apply("mr", "p1", body))       # no levanta
    saved = std_service.rules_repo.update_rule.await_args.args[1]
    assert saved["enabled"] is False and saved["validationState"] == "invalid"


def test_apply_quitar_valor_de_lista_marca_stale_las_no_tocadas(monkeypatch):
    """spec §4: un valor eliminado de la lista de un UDP marca las reglas
    afectadas como 'stale' (siguen guardadas, avisan)."""
    prev = {"id": "r1", "name": "usa_regular", "kind": "rule", "target": "table",
            "sourceArtifact": None, "condition": "tabla.udp[\"Tipo de Vista\"] = 'Regular'",
            "udpRefs": [{"udpId": "u-tv", "level": "table"}], "action": {},
            "appliesTo": ["ddl.vista_negocio"], "priority": 50, "enabled": True,
            "validationState": "valid", "validationReport": {}, "updatedBy": "mr"}
    _mock_apply(monkeypatch, before_rules=[prev])
    from app.features.data_standards.schemas import UdpEdit
    body = ApplyBody(udpUpsert=[UdpEdit(id="u-tv", name="Tipo de Vista", level="table",
                                        dataType="list", allowedValues=["Personalizada"])])
    monkeypatch.setattr(std_service.udp_repo, "create_udp", AsyncMock())
    monkeypatch.setattr(std_service.udp_repo, "update_udp", AsyncMock())
    monkeypatch.setattr(std_service.udp_repo, "delete_udp", AsyncMock())
    asyncio.run(std_service.apply("mr", "p1", body))
    args = std_service.rules_repo.update_rule.await_args
    assert args.args[0] == "r1" and args.args[1]["validationState"] == "stale"


# ── Doc 71 H4 · acción incompatible con el tipo de artefacto + add_columns ──

KINDS = {"ddl.tabla_fisica": "table", "ddl.vista_negocio": "view", "ddl.vista_tecnica": "view"}


def test_expression_sobre_tabla_y_tags_sobre_vista_avisan():
    rep = v.validate_rule(rule(appliesTo=["ddl.tabla_fisica"],
                               action={"expression": "sha2({col}, 512)"}), DEFS, CONFIG, ARTS, KINDS)
    assert rep["state"] == "valid"
    assert any("table artifact" in w and "expression" in w for w in rep["warnings"])
    # Doc 76 D5: los tags SÍ aplican a vistas (ALTER VIEW) — sin aviso; lo que
    # sigue sin sentido sobre una vista es TBLPROPERTIES.
    rep_tags = v.validate_rule(rule(target="table", appliesTo=["ddl.vista_negocio"],
                                    action={"tags": {"x": "y"}}), DEFS, CONFIG, ARTS, KINDS)
    assert not any("view artifact" in w for w in rep_tags["warnings"])
    rep2 = v.validate_rule(rule(target="table", appliesTo=["ddl.vista_negocio"],
                                action={"tblproperties": {"x": "y"}}), DEFS, CONFIG, ARTS, KINDS)
    assert any("view artifact" in w and "TBLPROPERTIES" in w for w in rep2["warnings"])
    # exclude sobre una tabla: mismo aviso que expression
    rep_ex = v.validate_rule(rule(appliesTo=["ddl.tabla_fisica"],
                                  action={"exclude": True}), DEFS, CONFIG, ARTS, KINDS)
    assert any("table artifact" in w and "exclude" in w for w in rep_ex["warnings"])
    # combinación coherente: sin aviso
    rep3 = v.validate_rule(rule(appliesTo=["ddl.vista_tecnica"],
                                action={"expression": "sha2({col}, 512)"}), DEFS, CONFIG, ARTS, KINDS)
    assert not any("artifact —" in w for w in rep3["warnings"])


def test_generator_add_columns_incompletas_son_error():
    gen = {"name": "g", "kind": "generator", "target": None, "sourceArtifact": "ddl.tabla_fisica",
           "condition": "true", "udpRefs": [],
           "action": {"emit": {"artifact": "ddl.tabla_rej", "type": "table",
                               "add_columns": [{"name": "ok", "type": "STRING"}, {"name": "", "type": "STRING"},
                                               {"name": "sin_tipo", "type": ""}]}},
           "appliesTo": [], "priority": 200, "enabled": True}
    rep = v.validate_rule(gen, DEFS, CONFIG, ARTS, KINDS)
    msgs = [e["message"] for e in rep["errors"]]
    assert rep["state"] == "invalid"
    assert any("#2" in m for m in msgs) and any("#3" in m for m in msgs)
