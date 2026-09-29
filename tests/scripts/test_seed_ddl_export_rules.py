"""Doc 73 §11.3 / doc 76: el seed de reglas DDL decide qué UDP auto-crear
mirando SOLO la faceta física (las reglas enlazan UDP físicos); un homónimo
lógico no cuenta. Los UDP de origen de los lookups y el `partitionOrderUdp`
del layout cuentan como requeridos. Doc 101: los proyectos Oracle no llevan
ruleset — solo las Output settings con Oracle por default."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.data_standards import service as std_service
from app.features.ddl_rules import repository as rules_repo
from app.features.ddl_rules import service as rules_svc
from app.features.ddl_rules.output import ORACLE_DEFAULTS, normalize_output
from app.features.ddl_rules.templates import SEED_LOOKUPS, SEED_OUTPUT, SEED_RULES
from scripts import seed_ddl_export_rules as seed
from scripts.seed_ddl_export_rules import _AUTO_UDPS, _referenced_udps, physical_udp_keys, select_projects

DEFS = [
    {"id": "1", "name": "Tipo de Vista", "level": "table", "view": "logical"},    # kit (catálogo Erwin)
    {"id": "2", "name": "Tipo de Vista", "level": "view", "view": "physical"},    # doc 61
    {"id": "3", "name": "Clasificacion del Dato", "level": "column"},             # sin view = físico (pre doc 69)
    {"id": "4", "name": "Frecuencia Vacuum", "level": "table", "view": "physical"},
]


def test_solo_defs_fisicas_cuentan_como_presentes():
    keys = physical_udp_keys(DEFS)
    assert ("table", "Tipo de Vista") not in keys          # sólo existe la lógica
    assert ("view", "Tipo de Vista") in keys
    assert ("column", "Clasificacion del Dato") in keys     # ausencia de `view` = física
    assert ("table", "Frecuencia Vacuum") in keys


def test_auto_udps_solo_frecuencia_vacuum_en_la_faceta_fisica():
    """Doc 76: la vista técnica ya no depende de «Tipo de Vista» → no se auto-crea."""
    assert [(a["name"], a["level"], a["view"]) for a in _AUTO_UDPS] == [("Frecuencia Vacuum", "table", "physical")]


def test_referenced_udps_cubre_condiciones_lookups_y_layout():
    refs = _referenced_udps(SEED_RULES, SEED_LOOKUPS)
    assert ("column", "Clasificacion del Dato") in refs      # condiciones LIKE 'DAC-%' + dac_map
    assert ("table", "Clasificacion del Dato") in refs       # generadores DAC + dac_flag_map
    assert ("table", "Frecuencia Vacuum") in refs            # vacuum_map / update_frequency_map
    assert ("column", "Particion") in refs                   # layout.partitionUdp
    assert ("table", "Tipo de Vista") not in refs            # ya no se usa


def test_select_projects_por_nombre_o_todos():
    """Doc 75: el ruleset se siembra POR PROYECTO — uno por nombre o todos."""
    projects = [{"id": "a", "name": "Modelo DDV"}, {"id": "b", "name": "UDV INT FISICO"}]
    assert select_projects(projects, project="udv int fisico", all_projects=False) == [{"id": "b", "name": "UDV INT FISICO"}]
    assert select_projects(projects, project=None, all_projects=True) == projects
    with pytest.raises(SystemExit):
        select_projects(projects, project="nope", all_projects=False)
    with pytest.raises(SystemExit):
        select_projects(projects, project=None, all_projects=False)


# ── Doc 101: proyectos Oracle — sin ruleset, Oracle por default ────────────

def test_is_oracle_project_por_nombre_sin_distinguir_mayusculas():
    assert seed.ORACLE_PROJECTS == ("MODELO RDV DataEntry",)
    assert seed.is_oracle_project("MODELO RDV DataEntry")
    assert seed.is_oracle_project("  modelo rdv dataentry ")
    assert not seed.is_oracle_project("MODELO DDV")
    assert not seed.is_oracle_project("UDV INT FISICO")
    assert not seed.is_oracle_project("")
    assert not seed.is_oracle_project(None)


def test_oracle_output_es_la_semilla_con_oracle_por_default():
    out = seed.oracle_output(SEED_OUTPUT)
    assert out["dialect"] == "oracle" and out["oracle"] == ORACLE_DEFAULTS
    assert (out["oracle"]["includeComments"], out["oracle"]["includeKeys"], out["oracle"]["typeCase"]) \
        == (False, True, "upper")
    assert SEED_OUTPUT["dialect"] == "databricks"                    # la semilla no se ensucia
    assert normalize_output(out) == out                               # el apply no la rechaza (422)


def _mock_seed(monkeypatch, *, rules=(), output=None):
    apply = AsyncMock(return_value={"label": "v2", "title": "t"})
    monkeypatch.setattr(std_service, "apply", apply)
    monkeypatch.setattr(rules_repo, "list_rules", AsyncMock(return_value=list(rules)))
    monkeypatch.setattr(rules_repo, "get_config", AsyncMock(return_value={
        "lookups": {}, "functions": [], "output": output or {}}))
    templates = AsyncMock(side_effect=AssertionError("un proyecto Oracle no pide el ruleset"))
    monkeypatch.setattr(rules_svc, "templates_payload", templates)
    return apply


def test_seed_proyecto_oracle_registra_solo_output_sin_reglas(monkeypatch):
    apply = _mock_seed(monkeypatch)
    asyncio.run(seed.seed_one("pid-rdv", "MODELO RDV DataEntry", apply=True))
    apply.assert_awaited_once()
    actor, pid, body = apply.await_args.args
    assert (actor, pid, body.kind) == ("system", "pid-rdv", "ddl")
    assert body.rulesUpsert == [] and body.udpUpsert == []
    assert body.ddlConfigPatch.lookups is None and body.ddlConfigPatch.functions is None
    assert body.ddlConfigPatch.output["dialect"] == "oracle"
    assert body.ddlConfigPatch.output["oracle"] == ORACLE_DEFAULTS
    assert "Oracle" in body.title


def test_seed_proyecto_oracle_dry_run_no_escribe(monkeypatch):
    apply = _mock_seed(monkeypatch)
    asyncio.run(seed.seed_one("pid-rdv", "MODELO RDV DataEntry", apply=False))
    apply.assert_not_awaited()


def test_seed_proyecto_oracle_se_salta_si_ya_eligio_dialecto_o_tiene_reglas(monkeypatch):
    apply = _mock_seed(monkeypatch, output={"dialect": "databricks"})
    asyncio.run(seed.seed_one("pid-rdv", "MODELO RDV DataEntry", apply=True))
    apply.assert_not_awaited()
    apply = _mock_seed(monkeypatch, rules=[{"name": "drop_comentado", "kind": "rule"}])
    asyncio.run(seed.seed_one("pid-rdv", "MODELO RDV DataEntry", apply=True))
    apply.assert_not_awaited()


def _physical_defs_for_seed() -> list[dict]:
    """Las defs FÍSICAS que el ruleset base necesita (como las deja el kit)."""
    return [{"id": f"u-{lvl}-{name}", "name": name, "level": lvl, "view": "physical",
             "dataType": "list", "allowedValues": []}
            for lvl, name in sorted(_referenced_udps(SEED_RULES, SEED_LOOKUPS))]


def test_revision_doc101_seed_databricks_conserva_dialecto_y_oracle_ya_elegidos(monkeypatch):
    """Revisión independiente #1: un proyecto que NO está en ORACLE_PROJECTS, sin
    reglas, pero que ya eligió Oracle (copiado del RDV o con las reglas borradas):
    sembrar el ruleset base no le pisa el dialecto ni la config de Oracle — igual
    que «Restore the base rule set» del front."""
    from app.features.udp import repository as udp_repo
    apply = AsyncMock(return_value={"label": "v2", "title": "t"})
    monkeypatch.setattr(std_service, "apply", apply)
    monkeypatch.setattr(rules_repo, "list_rules", AsyncMock(return_value=[]))
    saved_oracle = {"includeComments": True, "defaultSchema": "BCP_UDV"}
    monkeypatch.setattr(rules_repo, "get_config", AsyncMock(return_value={
        "lookups": {}, "functions": [], "output": {"dialect": "oracle", "oracle": saved_oracle}}))
    monkeypatch.setattr(udp_repo, "list_udp", AsyncMock(return_value=_physical_defs_for_seed()))
    asyncio.run(seed.seed_one("pid-x", "OTRO PROYECTO", apply=True))
    body = apply.await_args.args[2]
    assert len(body.rulesUpsert) == len(SEED_RULES)                    # el ruleset base sí se siembra
    out = body.ddlConfigPatch.output
    assert out["dialect"] == "oracle" and out["oracle"] == saved_oracle
    assert {k: v for k, v in out.items() if k not in ("dialect", "oracle")} == \
        {k: v for k, v in SEED_OUTPUT.items() if k not in ("dialect", "oracle")}
    # sin nada elegido: la semilla tal cual
    monkeypatch.setattr(rules_repo, "get_config", AsyncMock(return_value={"lookups": {}, "functions": [], "output": {}}))
    asyncio.run(seed.seed_one("pid-y", "OTRO PROYECTO", apply=True))
    assert apply.await_args.args[2].ddlConfigPatch.output == SEED_OUTPUT


def test_revision_doc101_seed_avisa_proyecto_oracle_con_reglas_y_nombres_sin_proyecto(monkeypatch, capsys):
    """Revisión independiente #4: señales claras en los dos casos borde."""
    _mock_seed(monkeypatch, rules=[{"name": "drop_comentado", "kind": "rule"}])
    asyncio.run(seed.seed_one("pid-rdv", "MODELO RDV DataEntry", apply=True))
    assert "Oracle" in capsys.readouterr().out
    projects = [{"id": "a", "name": "MODELO DDV"}, {"id": "b", "name": "modelo rdv dataentry"}]
    assert seed.unmatched_oracle_projects(projects) == []
    assert seed.unmatched_oracle_projects([{"id": "a", "name": "MODELO DDV"}]) == ["MODELO RDV DataEntry"]
