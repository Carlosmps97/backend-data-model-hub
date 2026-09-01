"""Doc 61 ronda 2: catálogo FIJO de UDPs + matching CI de valores del XML."""
from scripts.erwin_migration.standard_udps import (ALIASES, FIXED_UDPS,
                                                   fixed_lookup, match_value)


def _fixed(level, name):
    return next(d for d in FIXED_UDPS if d["level"] == level and d["name"] == name)


def test_catalogo_niveles_y_view_unico():
    by_level = {}
    for d in FIXED_UDPS:
        by_level.setdefault(d["level"], []).append(d["name"])
    assert sorted(by_level) == ["canvas", "column", "table", "view"]
    # View: UN solo UDP — Tipo de Vista, list, default Regular.
    assert by_level["view"] == ["Tipo de Vista"]
    vt = _fixed("view", "Tipo de Vista")
    assert vt["dataType"] == "list"
    assert vt["allowedValues"] == ["Regular", "Personalizada"]
    assert vt["defaultValue"] == "Regular"
    # Tipo de Vista NO existe a nivel tabla (se movió a view).
    assert all(d["name"] != "Tipo de Vista" for d in FIXED_UDPS if d["level"] == "table")


def test_catalogo_sin_duplicados_por_nivel_nombre():
    keys = [(d["level"], d["name"].upper()) for d in FIXED_UDPS]
    assert len(keys) == len(set(keys))
    assert len(fixed_lookup()) == len(FIXED_UDPS)


def test_match_case_insensitive_asigna_grafia_canonica():
    f = _fixed("table", "Clasificacion del Dato")
    assert match_value(f, "no dac") == "No DAC"
    assert match_value(f, "  DAC ") == "DAC"
    assert match_value(f, "NO DEFINIDO") == "No Definido"


def test_match_colapsa_espacios_internos():
    f = _fixed("table", "Frecuencia Vacuum")
    assert match_value(f, "CUSTOM_90   days") == "CUSTOM_90 days"
    assert match_value(f, "daily_15 DAYS") == "DAILY_15 days"


def test_match_aliases_typos_y_tildes():
    carga = _fixed("table", "Tipo de Carga")
    assert match_value(carga, "Tipo 4 (Historia Snaptshot)") == "Tipo 4 (Historia Snapshot)"
    assert match_value(carga, "TIPO 4(HISTORIA SNAPTSHOT)") == "Tipo 4 (Historia Snapshot)"
    assert match_value(carga, "Tipo 4(Historia Snapshot)") == "Tipo 4 (Historia Snapshot)"
    ent = _fixed("table", "Tipo de Entidad")
    assert match_value(ent, "Sub - Tipo") == "Sub-Tipo"
    assert match_value(ent, "sub tipo") == "Sub-Tipo"
    assert match_value(ent, "Asociación") == "Asociacion"


def test_sin_coincidencia_es_none_default():
    f = _fixed("column", "Particion")
    assert match_value(f, "PART_09") is None
    assert match_value(f, "") is None
    assert match_value(f, None) is None


def test_string_pasa_tal_cual():
    f = _fixed("table", "Dominio Principal")
    assert match_value(f, "  Riesgos  ") == "Riesgos"
    assert match_value(_fixed("canvas", "Database"), "DDV_PROD") == "DDV_PROD"


def test_aliases_solo_de_defs_existentes():
    lk = fixed_lookup()
    for (level, name_norm) in ALIASES:
        assert (level, name_norm) in lk
