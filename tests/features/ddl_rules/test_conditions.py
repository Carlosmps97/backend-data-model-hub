"""DSL de condiciones (doc 30 §6): parse + eval puros, semántica SQL estricta.
Incluye la trampa del catálogo real: 'No DAC' contiene 'DAC' (spec §3.3-b)."""
from __future__ import annotations

import pytest

from app.features.ddl_rules.engine import conditions as c

CTX = {
    "tabla": {"nombre": "tbl_cliente", "esquema": "core", "catalogo": "main",
              "tipo": "physical", "comentario": None,
              "udp": {"Clasificacion del Dato": "DAC", "Tipo de Vista": "Regular"}},
    "columna": {"nombre": "nom_cliente", "tipo": "STRING", "nulable": True, "pk": False,
                "orden": 2, "comentario": "Nombre", "dominio": "Descripcion",
                "udp": {"Clasificacion del Dato": "DAC-NOMBRE", "Particion": "No Definido"}},
    "modelo": {"nombre": "DDV", "udp": {"Database": "ddv"}},
}


def ev(text: str, ctx: dict = CTX) -> bool:
    return c.eval_condition(c.parse_condition(text), ctx, text)


# ── Operadores ─────────────────────────────────────────────────────────────

def test_eq_neq_sobre_udp_bracket():
    assert ev('columna.udp["Clasificacion del Dato"] = \'DAC-NOMBRE\'')
    assert not ev('columna.udp["Clasificacion del Dato"] = \'DAC-DOCUMENTO\'')
    assert ev('columna.udp["Particion"] <> \'PART_01\'')


def test_bracket_acepta_comillas_simples_y_dobles():
    assert ev("tabla.udp['Clasificacion del Dato'] = 'DAC'")
    assert ev('tabla.udp["Clasificacion del Dato"] = \'DAC\'')


def test_udp_dot_form_para_identificadores():
    assert ev("modelo.udp.Database = 'ddv'")


def test_in_y_not_in():
    assert ev("columna.dominio IN ('Descripcion', 'Importe')")
    assert not ev("columna.dominio IN ('Importe')")
    assert ev("columna.dominio NOT IN ('Importe', 'Saldo')")


def test_like_la_trampa_dac():
    """spec §3.3-b: el LIKE anclado es lo correcto; '%DAC%' matchea 'No DAC'."""
    ctx_no_dac = {**CTX, "columna": {**CTX["columna"], "udp": {"Clasificacion del Dato": "No DAC"}}}
    assert ev('columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'')            # DAC-NOMBRE ✓
    assert not ev('columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'', ctx_no_dac)
    assert ev('columna.udp["Clasificacion del Dato"] LIKE \'%DAC%\'', ctx_no_dac)  # la trampa


def test_like_case_sensitive_y_guion_bajo():
    assert not ev("columna.nombre LIKE 'NOM%'")     # case-sensitive (spec §6.4)
    assert ev("columna.nombre LIKE 'nom_cliente'")  # _ = un carácter ('_' matchea '_')
    assert ev("columna.nombre LIKE 'no__cliente' OR columna.nombre LIKE 'nom_cliente'")
    assert ev("columna.nombre NOT LIKE 'aux%'")


def test_starts_ends_with_desugar():
    assert ev("columna.nombre STARTS WITH 'nom'")
    assert not ev("columna.nombre STARTS WITH 'NOM'")
    assert ev("columna.nombre ENDS WITH 'cliente'")


def test_null_semantics():
    """UDP no asignado = NULL: no compara con nada; IS NULL lo detecta."""
    assert not ev('columna.udp["Tabla Referencia"] = \'x\'')
    assert not ev('columna.udp["Tabla Referencia"] <> \'x\'')   # NULL tampoco es <>
    assert ev('columna.udp["Tabla Referencia"] IS NULL')
    assert ev('columna.udp["Clasificacion del Dato"] IS NOT NULL')
    assert ev("tabla.comentario IS NULL")


def test_booleanos_y_numeros():
    assert ev("columna.pk = false and columna.nulable = true")
    assert ev("columna.orden >= 2 AND columna.orden < 10")
    assert not ev("columna.orden > 2")


def test_logica_and_or_not_parentesis():
    assert ev("tabla.tipo = 'physical' AND (columna.pk = true OR columna.orden = 2)")
    assert ev("NOT (tabla.esquema = 'stg')")


def test_condicion_vacia_y_true_aplican_siempre():
    assert ev("")
    assert ev("   ")
    assert ev("true")


# ── Errores del DSL ────────────────────────────────────────────────────────

def test_campo_desconocido_da_error_con_columna():
    with pytest.raises(c.CondError) as e:
        ev("columna.nombrez = 'x'")
    assert "not a field of columna" in e.value.message and e.value.col >= 1


def test_objeto_desconocido():
    with pytest.raises(c.CondError) as e:
        ev("col.nombre = 'x'")
    assert "Unknown object 'col'" in e.value.message


def test_funciones_y_subqueries_rechazadas():
    with pytest.raises(c.CondError):
        ev("upper(columna.nombre) = 'X'")
    with pytest.raises(c.CondError):
        ev("columna.nombre IN (SELECT 1)")


def test_bracket_sobre_no_udp_rechazado():
    with pytest.raises(c.CondError) as e:
        ev('columna.dominio["x"] = \'y\'')
    assert "udp" in e.value.message


def test_parse_error_trae_posicion():
    with pytest.raises(c.CondError) as e:
        c.parse_condition("columna.udp[ = 'x'")
    assert e.value.line >= 1 and e.value.col >= 1


# ── Extracción para el validador ───────────────────────────────────────────

def test_udp_names_used_y_literal_comparisons():
    text = ('columna.udp["Clasificacion del Dato"] = \'DAC\' '
            'AND tabla.udp["Tipo de Vista"] IN (\'Regular\', \'Personalizada\') '
            'AND columna.udp["Clasificacion del Dato"] IS NOT NULL')
    ast = c.parse_condition(text)
    assert c.udp_names_used(ast, text) == [("columna", "Clasificacion del Dato"),
                                           ("tabla", "Tipo de Vista")]
    comps = c.literal_comparisons(ast, text)
    assert ("columna", "Clasificacion del Dato", "=", ["DAC"]) in comps
    assert ("tabla", "Tipo de Vista", "IN", ["Regular", "Personalizada"]) in comps
