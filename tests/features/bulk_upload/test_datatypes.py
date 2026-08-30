"""Gramática de tipos de dato de la carga masiva (doc 55 §5.5): el MISMO
catálogo cerrado del combobox del front (typePick.ts) + argumentos por tipo +
complejos STRUCT/ARRAY/MAP + los defaults de parent domains como tipos extra."""
from __future__ import annotations

import pytest

from app.features.bulk_upload.datatypes import CATALOG, canonical_type


def test_catalogo_es_el_del_front_en_mayusculas():
    assert {"STRING", "BIGINT", "DECIMAL", "TIMESTAMP", "STRUCT", "ARRAY", "MAP"} <= CATALOG
    assert "INT" not in CATALOG


def test_simple_case_insensitive():
    assert canonical_type("string") == "STRING"


def test_decimal_con_espacios_se_compacta():
    assert canonical_type("decimal (18, 2)") == "DECIMAL(18,2)"


def test_varchar_un_argumento():
    assert canonical_type("varchar(50)") == "VARCHAR(50)"


def test_decimal_solo_precision():
    assert canonical_type("DECIMAL(10)") == "DECIMAL(10)"


@pytest.mark.parametrize("bad", ["VARCHAR(1,2)", "DECIMAL(a)", "STRING(10)", "DECIMAL()",
                                 "DECIMAL(1,2,3)", "NUMBER(22,3)", "INT", "", None, "STRING extra"])
def test_rechazos(bad):
    assert canonical_type(bad) is None


def test_acepta_tipo_extra_de_dominio_con_su_grafia():
    # "Tipo existente en la plataforma": el default de un parent domain vale
    # aunque no esté en el catálogo (data real: NUMBER(22,3), VARCHAR2(10)).
    assert canonical_type("number (22,3)", extra=["NUMBER(22,3)"]) == "NUMBER(22,3)"
    assert canonical_type("varchar2(10)", extra=["VARCHAR2(10)"]) == "VARCHAR2(10)"


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
    assert canonical_type("ARRAY<NUMBER(22,3)>", extra=["NUMBER(22,3)"]) == "ARRAY<NUMBER(22,3)>"
