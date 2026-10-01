"""Doc 106: el catálogo de tipos suma Databricks SQL / Hive, Oracle y SQL Server.
La plataforma no valida por motor (un tipo que el motor no acepta es del
modelador): la carga Excel acepta lo mismo que el selector, los argumentos
aceptan texto (`MAX`, `30 CHAR`, `*`) y `INT` deja de homologarse a `INTEGER`."""
from __future__ import annotations

import pytest

from app.core.datatypes import canonical_type, canonicalize_default_type, is_complete_simple_type


@pytest.mark.parametrize("raw, expected", [
    # Databricks SQL / Hive
    ("int", "INT"),
    ("timestamp_ntz", "TIMESTAMP_NTZ"),
    ("double precision", "DOUBLE PRECISION"),
    ("geography", "GEOGRAPHY"),
    ("geometry(4326)", "GEOMETRY(4326)"),
    # Oracle
    ("nvarchar2(50)", "NVARCHAR2(50)"),
    ("nchar(10)", "NCHAR(10)"),
    ("binary_float", "BINARY_FLOAT"),
    ("binary_double", "BINARY_DOUBLE"),
    ("long", "LONG"),
    ("long  raw", "LONG RAW"),
    ("raw(16)", "RAW(16)"),
    ("rowid", "ROWID"),
    ("urowid(4000)", "UROWID(4000)"),
    ("clob", "CLOB"),
    ("nclob", "NCLOB"),
    ("bfile", "BFILE"),
    ("xmltype", "XMLTYPE"),
    ("vector(1536, float32)", "VECTOR(1536,FLOAT32)"),
    ("timestamp with time zone", "TIMESTAMP WITH TIME ZONE"),
    ("Timestamp  With Local Time Zone", "TIMESTAMP WITH LOCAL TIME ZONE"),
    ("interval year to month", "INTERVAL YEAR TO MONTH"),
    ("INTERVAL DAY TO SECOND", "INTERVAL DAY TO SECOND"),
    ("float(126)", "FLOAT(126)"),
    ("timestamp(6)", "TIMESTAMP(6)"),
    # SQL Server
    ("bit", "BIT"),
    ("smallmoney", "SMALLMONEY"),
    ("datetime2(7)", "DATETIME2(7)"),
    ("datetimeoffset(3)", "DATETIMEOFFSET(3)"),
    ("smalldatetime", "SMALLDATETIME"),
    ("ntext", "NTEXT"),
    ("image", "IMAGE"),
    ("uniqueidentifier", "UNIQUEIDENTIFIER"),
    ("sql_variant", "SQL_VARIANT"),
    ("hierarchyid", "HIERARCHYID"),
    ("rowversion", "ROWVERSION"),
    ("time(7)", "TIME(7)"),
    ("binary(16)", "BINARY(16)"),
])
def test_doc106_tipos_de_los_cuatro_motores_valen_en_la_carga(raw, expected):
    assert canonical_type(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("varchar(max)", "VARCHAR(MAX)"),            # RDV: 6 columnas
    ("NVARCHAR( max )", "NVARCHAR(MAX)"),        # RDV: 1 columna
    ("varbinary(MAX)", "VARBINARY(MAX)"),
    ("varchar2(30 char)", "VARCHAR2(30 CHAR)"),
    ("VARCHAR2(30   BYTE)", "VARCHAR2(30 BYTE)"),
    ("char(10 char)", "CHAR(10 CHAR)"),
    ("number(*,2)", "NUMBER(*,2)"),
    ("NUMBER(5,-2)", "NUMBER(5,-2)"),
    ("vector(*, *)", "VECTOR(*,*)"),
])
def test_doc106_argumentos_de_texto(raw, expected):
    assert canonical_type(raw) == expected


@pytest.mark.parametrize("bad", [
    "VARCHAR('x')", 'VARCHAR("x")', "VARCHAR(1;2)", "VARCHAR(1.5)", "VARCHAR(30 CHAR BYTE)",
    "VARCHAR(--1)",
    "INT(11)", "DATETIME(3)", "STRING(10)", "VARCHAR(1,2)", "DECIMAL()",
    # los tipos de varias palabras van sin argumentos (Oracle usa su precisión por default)
    "TIMESTAMP(6) WITH TIME ZONE", "LONG RAW(10)",
    "TIMESTAMP WITH ZONE", "CHARACTER VARYING(10)",
    # revisión: la unidad es una PALABRA (`30 CHAR`), no otro número; y se valida antes de pasar a MAYÚSCULA
    "DECIMAL(18 2)", "VARCHAR(25 5)", "DECIMAL(1 0,2)", "VARCHAR(ſ)", "VARCHAR(ﬀ)", "VARCHAR(ß)",
])
def test_doc106_rechazos(bad):
    assert canonical_type(bad) is None


@pytest.mark.parametrize("raw, expected", [
    ("int", "INT"),                                   # antes: INTEGER (sinónimo del doc 62)
    ("INT", "INT"),
    ("double precision", "DOUBLE PRECISION"),         # antes: DOUBLE
    ("struct<a:int>", "STRUCT<a:INT>"),
    ("varchar(max)", "VARCHAR(MAX)"),                 # antes: verbatim, en minúscula
    ("BIG INTEGER", "BIGINT"),                        # los sinónimos que no son tipos siguen
    ("bool", "BOOLEAN"),
])
def test_doc106_homologacion_deja_int_tal_cual(raw, expected):
    assert canonicalize_default_type(raw) == expected


def test_doc106_varchar_max_es_un_tipo_simple_completo():
    assert is_complete_simple_type("VARCHAR(MAX)") is True
    assert is_complete_simple_type("TIMESTAMP WITH TIME ZONE") is True
    assert is_complete_simple_type("TIMESTAMP WITH") is False
