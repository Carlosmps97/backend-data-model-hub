"""Doc 61 ronda 2: catálogo FIJO de UDPs + matching CI de valores del XML.
Doc 69: cada definición declara su faceta (`view`) — 25 defs."""
from scripts.erwin_migration.policies import norm_enum
from scripts.erwin_migration.standard_udps import (ALIASES, FIXED_UDPS,
                                                   fixed_lookup, match_value)


def _fixed(level, name):
    """Def FÍSICA por (level, nombre) — las aserciones históricas son de la faceta física."""
    return next(d for d in FIXED_UDPS if d["level"] == level and d["name"] == name and d["view"] == "physical")


def _fixed_view(level, view, name):
    return next(d for d in FIXED_UDPS if d["level"] == level and d["view"] == view and d["name"] == name)


def test_catalogo_niveles_y_facetas():
    assert len(FIXED_UDPS) == 25
    assert all(d["view"] in ("logical", "physical") for d in FIXED_UDPS)
    by = {}
    for d in FIXED_UDPS:
        by.setdefault((d["level"], d["view"]), []).append(d["name"])
    assert sorted(by["view", "physical"]) == ["Filtro Despliegue 2021", "Tipo de Vista"]
    vt = _fixed("view", "Tipo de Vista")
    assert (vt["dataType"], vt["allowedValues"], vt["defaultValue"]) == ("list", ["Regular", "Personalizada"], "Regular")
    fd = _fixed("view", "Filtro Despliegue 2021")
    assert (fd["dataType"], fd["allowedValues"], fd["defaultValue"]) == ("list", ["NO", "SI"], "NO")
    # View y Model: solo faceta física.
    assert all(d["view"] == "physical" for d in FIXED_UDPS if d["level"] in ("view", "canvas"))
    # «Tipo de Vista» a nivel tabla existe SOLO en la faceta lógica (imagen del estándar, doc 69 §5.1).
    assert all(d["view"] == "logical" for d in FIXED_UDPS if d["level"] == "table" and d["name"] == "Tipo de Vista")


def test_catalogo_sin_duplicados_por_nivel_faceta_nombre():
    keys = [(d["level"], d["view"], d["name"].upper()) for d in FIXED_UDPS]
    assert len(keys) == len(set(keys))
    assert len(fixed_lookup()) == len(FIXED_UDPS)
    assert ("table", "logical", norm_enum("Clasificacion del Dato")) in fixed_lookup()
    assert ("table", "physical", norm_enum("Clasificacion del Dato")) in fixed_lookup()


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
    # Owner 2026-09-09 (deroga la decisión 1 del doc 68): la grafía CANÓNICA es
    # «Snapshot» bien escrito. El typo «Snaptshot» del estándar corporativo —y
    # las variantes con el paréntesis pegado— convergen hacia ella, así los XML
    # ya cargados con el typo siguen resolviendo al re-sembrar.
    carga = _fixed("table", "Tipo de Carga")
    assert match_value(carga, "Tipo 4 (Historia Snapshot)") == "Tipo 4 (Historia Snapshot)"
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
    for (level, name_norm) in ALIASES:
        assert any(d["level"] == level and norm_enum(d["name"]) == name_norm for d in FIXED_UDPS)


# ── Doc 69: faceta LÓGICA (imágenes owner 2026-09-05) ─────────────────────

def test_catalogo_entidad_logica_owner_2026_09():
    ent = {d["name"]: d for d in FIXED_UDPS if d["level"] == "table" and d["view"] == "logical"}
    assert sorted(ent) == ["Clasificacion del Dato", "Dominio Principal", "Filtro Despliegue 2021",
                           "Tipo de Entidad", "Tipo de Vista", "Universal"]
    assert (ent["Filtro Despliegue 2021"]["dataType"], ent["Filtro Despliegue 2021"]["defaultValue"]) == ("string", "NO")
    assert ent["Tipo de Vista"]["allowedValues"] == ["Regular", "Personalizada"]
    assert ent["Clasificacion del Dato"]["allowedValues"] == ["No Definido", "No DAC", "DAC"]
    assert ent["Tipo de Entidad"]["allowedValues"] == _fixed_view("table", "physical", "Tipo de Entidad")["allowedValues"]
    for d in ent.values():
        if d["dataType"] == "list":
            assert d["defaultValue"] == d["allowedValues"][0], d["name"]


def test_catalogo_atributo_logico_owner_2026_09():
    att = {d["name"]: d for d in FIXED_UDPS if d["level"] == "column" and d["view"] == "logical"}
    assert sorted(att) == ["Atributo Cross", "Clasificacion del Dato"]
    assert att["Atributo Cross"]["allowedValues"] == ["No Definido", "Si", "No"]
    assert att["Clasificacion del Dato"]["allowedValues"] == _fixed_view("column", "physical", "Clasificacion del Dato")["allowedValues"]


def test_catalogo_fisico_identico_al_doc_68():
    tabla = sorted(d["name"] for d in FIXED_UDPS if d["level"] == "table" and d["view"] == "physical")
    col = sorted(d["name"] for d in FIXED_UDPS if d["level"] == "column" and d["view"] == "physical")
    assert tabla == ["Clasificacion del Dato", "Dominio Principal", "Estado Cloud", "Frecuencia Vacuum",
                     "Tabla Cross", "Tipo de Carga", "Tipo de Entidad", "Universal"]
    assert col == ["Campo Cross", "Clasificacion del Dato", "Exclusivo Cloud", "Particion", "Tabla Referencia"]


# ── Doc 68: catálogo EXACTO de las imágenes del owner (2026-09-03) ─────────


def test_catalogo_tabla_owner_2026_09():
    tabla = {d["name"]: d for d in FIXED_UDPS if d["level"] == "table" and d["view"] == "physical"}
    assert sorted(tabla) == ["Clasificacion del Dato", "Dominio Principal",
                             "Estado Cloud", "Frecuencia Vacuum", "Tabla Cross",
                             "Tipo de Carga", "Tipo de Entidad", "Universal"]
    # «Exclusivo Cloud» ya NO es de tabla (solo columna, imagen #2).
    assert "Exclusivo Cloud" not in tabla
    assert tabla["Tipo de Carga"]["allowedValues"] == [
        "No Definido", "Tipo 1 (No Historia)", "Tipo 2 (Historia Vigencia)",
        "Tipo 4 (Historia Snapshot)"]
    assert tabla["Tipo de Entidad"]["allowedValues"] == [
        "No Definido", "Super-Tipo", "Sub-Tipo", "Asociacion", "Referencia",
        "Dependiente"]
    # Default = SIEMPRE el primer valor de la lista (regla del owner).
    for d in tabla.values():
        if d["dataType"] == "list":
            assert d["defaultValue"] == d["allowedValues"][0], d["name"]
    assert tabla["Frecuencia Vacuum"]["defaultValue"] == "CUSTOM_90 days"
    assert tabla["Dominio Principal"]["dataType"] == "string"


def test_catalogo_columna_owner_2026_09():
    col = {d["name"]: d for d in FIXED_UDPS if d["level"] == "column" and d["view"] == "physical"}
    assert sorted(col) == ["Campo Cross", "Clasificacion del Dato",
                           "Exclusivo Cloud", "Particion", "Tabla Referencia"]
    assert col["Clasificacion del Dato"]["allowedValues"] == [
        "No Definido", "No DAC", "DAC-DOCUMENTO", "DAC-NOMBRE", "DAC-DIRECCION",
        "DAC-TELEFONO", "DAC-CUENTA", "DAC-TARJETA", "DAC-EMAIL",
        "DAC-BIOMETRICO", "DAC-IMAGENVOZ", "DAC-FIRMA", "DAC-GLOSADAC", "DAC"]
    assert col["Particion"]["allowedValues"] == [
        "No Definido", "PART_01", "PART_02", "PART_03"]
    for d in col.values():
        if d["dataType"] == "list":
            assert d["defaultValue"] == d["allowedValues"][0], d["name"]
    assert col["Tabla Referencia"]["dataType"] == "string"


def test_catalogo_model_owner_2026_09():
    canvas = {d["name"]: d for d in FIXED_UDPS if d["level"] == "canvas"}
    assert sorted(canvas) == ["Archivo Base", "Database"]
    assert canvas["Database"]["dataType"] == "string"
    assert canvas["Database"]["defaultValue"] is None
    assert canvas["Archivo Base"]["dataType"] == "string"
    assert canvas["Archivo Base"]["defaultValue"] == \
        "DDV Modelo de Datos Fisico Planeamiento Banca Minorista"
