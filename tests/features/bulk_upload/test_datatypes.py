"""Gramática de tipos de dato de la carga masiva (doc 55 §5.5): el MISMO
catálogo cerrado del combobox del front (typePick.ts) + argumentos por tipo +
complejos STRUCT/ARRAY/MAP + los defaults de parent domains como tipos extra.

Doc 62: el catálogo suma VARIANT (JSON libre) y los tipos Oracle de la data
real (NUMBER, VARCHAR2), y nace `canonicalize_default_type` — homologación de
los `defaultDataType` de parent domains a la grafía canónica de la plataforma
(`Array` → `ARRAY<>`, `BIG INTEGER` → `BIGINT`, `DECIMAL (22,4)` →
`DECIMAL(22,4)`); lo que no se reconoce queda VERBATIM (jamás se inventa).
"""
from __future__ import annotations

import pytest

from app.features.bulk_upload.datatypes import (
    CATALOG,
    canonical_type,
    canonicalize_default_type,
)


def test_catalogo_es_el_del_front_en_mayusculas():
    assert {"STRING", "BIGINT", "DECIMAL", "TIMESTAMP", "STRUCT", "ARRAY", "MAP"} <= CATALOG
    assert "INT" not in CATALOG


def test_catalogo_incluye_variant_y_tipos_oracle():
    # Doc 62: VARIANT (JSON libre) + NUMBER/VARCHAR2 (dominios de la data real).
    assert {"VARIANT", "NUMBER", "VARCHAR2"} <= CATALOG


def test_simple_case_insensitive():
    assert canonical_type("string") == "STRING"


def test_variant_es_tipo_simple_sin_argumentos():
    assert canonical_type("variant") == "VARIANT"
    assert canonical_type("VARIANT(10)") is None


def test_number_y_varchar2_con_argumentos():
    assert canonical_type("NUMBER(22,3)") == "NUMBER(22,3)"
    assert canonical_type("number (22, 3)") == "NUMBER(22,3)"
    assert canonical_type("NUMBER(5)") == "NUMBER(5)"
    assert canonical_type("varchar2(10)") == "VARCHAR2(10)"


def test_decimal_con_espacios_se_compacta():
    assert canonical_type("decimal (18, 2)") == "DECIMAL(18,2)"


def test_varchar_un_argumento():
    assert canonical_type("varchar(50)") == "VARCHAR(50)"


def test_decimal_solo_precision():
    assert canonical_type("DECIMAL(10)") == "DECIMAL(10)"


@pytest.mark.parametrize("bad", ["VARCHAR(1,2)", "DECIMAL(a)", "STRING(10)", "DECIMAL()",
                                 "DECIMAL(1,2,3)", "NUMBER(1,2,3)", "VARCHAR2(1,2)",
                                 "INT", "", None, "STRING extra"])
def test_rechazos(bad):
    assert canonical_type(bad) is None


def test_acepta_tipo_extra_de_dominio_con_su_grafia():
    # Un default de dominio fuera de catálogo sigue valiendo como extra.
    assert canonical_type("raw (16)", extra=["RAW(16)"]) == "RAW(16)"


def test_complejos_basicos():
    assert canonical_type("array<string>") == "ARRAY<STRING>"
    assert canonical_type("MAP<STRING, INTEGER>") == "MAP<STRING,INTEGER>"
    assert canonical_type("struct<a: integer, b: decimal(10,2)>") == "STRUCT<a:INTEGER,b:DECIMAL(10,2)>"


def test_complejo_anidado():
    assert canonical_type("ARRAY<STRUCT<x:STRING,y:ARRAY<BIGINT>>>") == "ARRAY<STRUCT<x:STRING,y:ARRAY<BIGINT>>>"


@pytest.mark.parametrize("bad", ["STRUCT<a:INT>", "STRUCT<a:INT", "STRUCT<>", "MAP<STRING>",
                                 "STRUCT<1a:STRING>", "ARRAY<STRING>>", "STRUCT<a>"])
def test_complejos_malformados(bad):
    assert canonical_type(bad) is None


def test_extra_tambien_vale_dentro_de_un_complejo():
    assert canonical_type("ARRAY<RAW(16)>", extra=["RAW(16)"]) == "ARRAY<RAW(16)>"


# ── canonicalize_default_type (doc 62): homologación de defaults de dominio ──


@pytest.mark.parametrize("raw, expected", [
    ("Array", "ARRAY<>"),            # el caso reportado: ColArray
    ("array<>", "ARRAY<>"),
    ("ARRAY <>", "ARRAY<>"),
    ("Struct", "STRUCT<>"),
    ("map", "MAP<>"),
    ("BIG INTEGER", "BIGINT"),       # alias con espacio (dominio BigInt)
    ("int", "INTEGER"),
    ("bool", "BOOLEAN"),
    ("DOUBLE PRECISION", "DOUBLE"),
    ("DECIMAL (22,4)", "DECIMAL(22,4)"),   # espacio antes del paréntesis
    ("decimal (19, 8)", "DECIMAL(19,8)"),
    ("NUMBER(10,6)", "NUMBER(10,6)"),      # ya canónico tras entrar al catálogo
    ("varchar2(60)", "VARCHAR2(60)"),
    ("variant", "VARIANT"),
    ("  string  ", "STRING"),
    ("ARRAY<STRING>", "ARRAY<STRING>"),    # complejo válido pasa intacto
    ("VARCHAR(120)", "VARCHAR(120)"),
])
def test_canonicalize_homologa(raw, expected):
    assert canonicalize_default_type(raw) == expected


@pytest.mark.parametrize("verbatim", ["Tipo Raro", "RAW(16)", "STRUCT<a:INT>"])
def test_canonicalize_lo_desconocido_queda_verbatim(verbatim):
    # Jamás se inventa: si no se reconoce, se conserva la grafía original.
    assert canonicalize_default_type(verbatim) == verbatim


def test_canonicalize_vacios():
    assert canonicalize_default_type("") == ""
    assert canonicalize_default_type("   ") == ""
    assert canonicalize_default_type(None) == ""
