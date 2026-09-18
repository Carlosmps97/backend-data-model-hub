"""Doc 92 D6: tipos complejos multilínea de Erwin — homologación con sinónimos
anidados, plegado a una línea y gramática estricta intacta para la carga."""
from __future__ import annotations

from app.core.datatypes import canonical_type, canonicalize_default_type, fold_type_whitespace

ERWIN = "Array \n<\n\tstruct <\n\tcodcampania: varchar(30),\n\tnumorden: int,\n\tfecsolicitud: date\n\t>\n>"


def test_canonicalize_pliega_y_homologa_sinonimos_anidados():
    assert canonicalize_default_type(ERWIN) == "ARRAY<STRUCT<codcampania:VARCHAR(30),numorden:INTEGER,fecsolicitud:DATE>>"
    assert canonicalize_default_type("Array\n< struct <\n\tv:map<varchar(120), string> > >") == "ARRAY<STRUCT<v:MAP<VARCHAR(120),STRING>>>"


def test_canonical_type_estricto_sigue_rechazando_sinonimos_anidados():
    # La carga masiva no acepta `int` (ni arriba ni adentro) — doc 62.
    assert canonical_type("int") is None
    assert canonical_type("array<struct<a:int>>") is None
    assert canonical_type("array<struct<a:int>>", aliases=True) == "ARRAY<STRUCT<a:INTEGER>>"


def test_complejo_que_no_parsea_sale_verbatim_en_una_linea():
    raw = "Struct <\n@param1: String,\n@param2: String\n>"
    out = canonicalize_default_type(raw)
    assert out == "Struct<@param1:String,@param2:String>"
    assert "\n" not in out and "\t" not in out


def test_fold_type_whitespace():
    assert fold_type_whitespace("Array \n<\n\tstruct < a : int >\n>") == "Array<struct<a:int>>"
    assert fold_type_whitespace("  DECIMAL ( 10 , 2 ) ") == "DECIMAL(10,2)"
    assert fold_type_whitespace(None) == ""
