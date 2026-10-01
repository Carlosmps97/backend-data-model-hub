"""Doc 92 D6: tipos complejos multilínea de Erwin — homologación con sinónimos
anidados, plegado a una línea y gramática estricta intacta para la carga."""
from __future__ import annotations

from app.core.datatypes import canonical_type, canonicalize_default_type, fold_type_whitespace

ERWIN = "Array \n<\n\tstruct <\n\tcodcampania: varchar(30),\n\tnumorden: int,\n\tactivo: bool,\n\tfecsolicitud: date\n\t>\n>"


def test_canonicalize_pliega_y_homologa_sinonimos_anidados():
    # doc 106: INT es del catálogo (queda INT); `bool` sigue siendo un sinónimo
    assert canonicalize_default_type(ERWIN) == (
        "ARRAY<STRUCT<codcampania:VARCHAR(30),numorden:INT,activo:BOOLEAN,fecsolicitud:DATE>>")
    assert canonicalize_default_type("Array\n< struct <\n\tv:map<varchar(120), string> > >") == "ARRAY<STRUCT<v:MAP<VARCHAR(120),STRING>>>"


def test_canonical_type_estricto_sigue_rechazando_sinonimos_anidados():
    # La carga masiva no acepta un sinónimo como `bool` (ni arriba ni adentro) — doc 62.
    assert canonical_type("bool") is None
    assert canonical_type("array<struct<a:bool>>") is None
    assert canonical_type("array<struct<a:bool>>", aliases=True) == "ARRAY<STRUCT<a:BOOLEAN>>"


def test_complejo_que_no_parsea_sale_verbatim_en_una_linea():
    raw = "Struct <\n@param1: String,\n@param2: String\n>"
    out = canonicalize_default_type(raw)
    assert out == "Struct<@param1:String,@param2:String>"
    assert "\n" not in out and "\t" not in out


def test_fold_type_whitespace():
    assert fold_type_whitespace("Array \n<\n\tstruct < a : int >\n>") == "Array<struct<a:int>>"
    assert fold_type_whitespace("  DECIMAL ( 10 , 2 ) ") == "DECIMAL(10,2)"
    assert fold_type_whitespace(None) == ""


# ── Doc 96 D8: un tipo complejo es el mismo en las dos facetas ──

from app.core.datatypes import has_complex_content, mirror_complex  # noqa: E402


def test_complejo_con_contenido_vs_generico_doc96():
    assert has_complex_content("ARRAY<STRING>") and has_complex_content("Array\n< struct <\n\ta:int>>")
    assert has_complex_content("Struct<@param1:String>")
    assert not has_complex_content("ARRAY<>") and not has_complex_content("Array")
    assert not has_complex_content("CHAR(18)") and not has_complex_content(None)


def test_mirror_complex_el_complejo_manda_doc96():
    full = "ARRAY<STRUCT<a:VARCHAR(30)>>"
    assert mirror_complex(full, "ARRAY<>") == full          # Erwin: lógico `Array` sin estructura
    assert mirror_complex(full, "CHAR(18)") == full         # default de Erwin
    assert mirror_complex(full, "VARCHAR(256)") == full     # decisión del owner: complejo = igual en ambas
    assert mirror_complex(full, None) == full
    assert mirror_complex(full, "ARRAY<STRING>") == "ARRAY<STRING>"   # el otro ya es un complejo completo
    assert mirror_complex("VARCHAR(20)", "CHAR(18)") == "CHAR(18)"    # simples: independientes


from app.core.datatypes import synced_other_facet  # noqa: E402


def test_synced_other_facet_espejo_del_front_final_review():
    full = "ARRAY<STRUCT<a:STRING>>"
    assert synced_other_facet("ARRAY<STRING>", full, full) == "ARRAY<STRING>"   # complejo nuevo: la otra igual
    assert synced_other_facet("STRING", full, full) == "STRING"                 # salen juntas del complejo
    assert synced_other_facet("STRING", "S", "S") == "STRING"                   # estado intermedio: siguen enlazadas
    assert synced_other_facet("BIGINT", "INT", "INT") == "INT"                  # simples: independientes
    assert synced_other_facet("STRING", full, "CHAR(18)") == "CHAR(18)"         # no estaban enlazadas
    assert synced_other_facet("STRING", "", "") == ""                           # vacías: no se arrastra
