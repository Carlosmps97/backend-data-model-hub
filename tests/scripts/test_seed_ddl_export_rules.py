"""Doc 73 §11.3: el seed de reglas DDL decide qué UDP auto-crear mirando SOLO
la faceta física (las reglas enlazan UDP físicos); un homónimo lógico no cuenta."""
from __future__ import annotations

import pytest

from scripts.seed_ddl_export_rules import _AUTO_UDPS, physical_udp_keys, select_projects

DEFS = [
    {"id": "1", "name": "Tipo de Vista", "level": "table", "view": "logical"},    # kit (catálogo Erwin)
    {"id": "2", "name": "Tipo de Vista", "level": "view", "view": "physical"},    # doc 61
    {"id": "3", "name": "Clasificacion del Dato", "level": "column"},             # sin view = físico (pre doc 69)
    {"id": "4", "name": "Frecuencia Vacuum", "level": "table", "view": "physical"},
]


def test_solo_defs_fisicas_cuentan_como_presentes():
    keys = physical_udp_keys(DEFS)
    assert ("table", "Tipo de Vista") not in keys          # sólo existe la lógica → se auto-crea la física
    assert ("view", "Tipo de Vista") in keys
    assert ("column", "Clasificacion del Dato") in keys     # ausencia de `view` = física
    assert ("table", "Frecuencia Vacuum") in keys


def test_auto_udps_nacen_en_la_faceta_fisica():
    assert all(a["view"] == "physical" and a["level"] == "table" for a in _AUTO_UDPS)
    assert {a["name"] for a in _AUTO_UDPS} == {"Tipo de Vista", "Frecuencia Vacuum"}


def test_select_projects_por_nombre_o_todos():
    """Doc 75: el ruleset se siembra POR PROYECTO — uno por nombre o todos."""
    projects = [{"id": "a", "name": "Modelo DDV"}, {"id": "b", "name": "UDV INT FISICO"}]
    assert select_projects(projects, project="udv int fisico", all_projects=False) == [{"id": "b", "name": "UDV INT FISICO"}]
    assert select_projects(projects, project=None, all_projects=True) == projects
    with pytest.raises(SystemExit):
        select_projects(projects, project="nope", all_projects=False)
    with pytest.raises(SystemExit):
        select_projects(projects, project=None, all_projects=False)
