"""Doc 73 §11.3 / doc 76: el seed de reglas DDL decide qué UDP auto-crear
mirando SOLO la faceta física (las reglas enlazan UDP físicos); un homónimo
lógico no cuenta. Los UDP de origen de los lookups y el `partitionOrderUdp`
del layout cuentan como requeridos."""
from __future__ import annotations

import pytest

from app.features.ddl_rules.templates import SEED_LOOKUPS, SEED_RULES
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
