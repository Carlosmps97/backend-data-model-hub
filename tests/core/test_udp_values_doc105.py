"""Doc 105 (ronda 5) — `app.core.udp_values.normalize_boolean`: un UDP booleano
se guarda como TEXTO; las grafías reconocidas se escriben «true»/«false» para
que el GROUP BY del Reporting no parta un mismo valor en varios grupos. Las
grafías son las MISMAS que reconoce el motor del Reporting al filtrar
(`reporting/query/compiler._UDP_TRUE/_UDP_FALSE`), sin distinguir mayúsculas
ni espacios de borde."""
from __future__ import annotations

import re

import pytest

from app.core.udp_values import UDP_FALSE_PATTERN, UDP_TRUE_PATTERN, normalize_boolean

TRUE = ["true", "TRUE", " True ", "1", "si", "Si", "SI", "sí", "Sí", "SÍ", "sÍ", "yes", "YES", "verdadero",
        "Verdadero", "\tsi\n"]
FALSE = ["false", "FALSE", " False ", "0", "no", "No", "NO", "falso", "FALSO", " no\t"]
UNKNOWN = ["quizás", "tal vez", "", "   ", "2", "y", "n", "s", "sii", "true false", "1.0", "verdad", None]


@pytest.mark.parametrize("text", TRUE)
def test_las_grafias_de_verdadero(text):
    assert normalize_boolean(text) == "true"


@pytest.mark.parametrize("text", FALSE)
def test_las_grafias_de_falso(text):
    assert normalize_boolean(text) == "false"


@pytest.mark.parametrize("text", UNKNOWN)
def test_lo_desconocido_es_none(text):
    assert normalize_boolean(text) is None


def test_booleanos_y_enteros_de_json():
    assert [normalize_boolean(v) for v in (True, False, 1, 0)] == ["true", "false", "true", "false"]


def test_las_grafias_deciden_lo_mismo_que_su_expresion():
    """`normalize_boolean` decide con las MISMAS expresiones con las que el motor
    filtra `udp."X" = TRUE` (el motor las importa de este módulo)."""
    for text in TRUE + FALSE + [u for u in UNKNOWN if u is not None]:
        esperado = ("true" if re.search(UDP_TRUE_PATTERN, text, re.IGNORECASE)
                    else "false" if re.search(UDP_FALSE_PATTERN, text, re.IGNORECASE) else None)
        assert normalize_boolean(text) == esperado, text


# ── Número y fecha (ronda 5, decisión del coordinador) ─────────────────────
# Número: finito (rechaza nan/inf y el texto); fecha: ISO YYYY-MM-DD real.

from app.core.udp_values import is_finite_number, is_iso_date  # noqa: E402


@pytest.mark.parametrize("text", ["0", "12.5", " 7 ", "-3", "1e3", "1E-2", "+4", ".5"])
def test_numeros_finitos(text):
    assert is_finite_number(text) is True


@pytest.mark.parametrize("text", ["nan", "NaN", "inf", "-inf", "Infinity", "1e400", "abc", "", "1,5", "0x10",
                                  "12 kg", None,
                                  # doc 105 (ronda 5): `float()` los acepta, pero el Reporting no los
                                  # encontraría con `udp."X" = 1000` (se guardan tal cual se escribieron)
                                  "1_000", "١٢", "１２", "12.5e", "."])
def test_no_numeros_o_no_finitos(text):
    assert is_finite_number(text) is False


@pytest.mark.parametrize("text", ["2024-02-29", "2026-12-31", " 2000-01-01 "])
def test_fechas_iso_reales(text):
    assert is_iso_date(text) is True


@pytest.mark.parametrize("text", ["31/02/2024", "2024-02-30", "2023-02-29", "1/2/24", "2024-2-3", "20240203",
                                  "2024-W05-1", "2024-02-03T10:00", "hoy", "", "２０２４-０１-０１", None])
def test_fechas_invalidas_o_no_iso(text):
    assert is_iso_date(text) is False


# ── Ronda 6 (R16/H2): el número se graba en su forma CANÓNICA ─────────────
# El motor busca el literal y su forma canónica; si los escritores graban lo
# tipeado («10.50», «1e3»), `= 10.5` y `= 10.50` devolvían filas distintas.

from app.core.udp_values import canonical_number, iso_date  # noqa: E402

CANONICAL = [("10.50", "10.5"), ("1e3", "1000"), ("10.0", "10"), (" 7 ", "7"), ("-0", "0"), ("+5", "5"),
             ("007", "7"), (".5", "0.5"), ("5.", "5"), ("1.0e2", "100"), ("-3.25", "-3.25"),
             ("9007199254740993", "9007199254740993"), ("9007199254740993.0", "9007199254740992.0"),
             ("1e20", "1e+20"), ("0.1", "0.1")]


@pytest.mark.parametrize("text, expected", CANONICAL)
def test_forma_canonica_de_un_numero(text, expected):
    assert canonical_number(text) == expected


def test_el_motor_usa_esta_misma_forma_canonica_y_grafias():
    """Contrato (ronda 7): el motor del Reporting IMPORTA esta forma canónica y
    estas grafías — no una copia que pueda divergir de lo que graban los
    escritores."""
    from app.features.reporting.query import compiler
    assert compiler._canonical_number is canonical_number
    assert (compiler._UDP_TRUE, compiler._UDP_FALSE) == (UDP_TRUE_PATTERN, UDP_FALSE_PATTERN)


@pytest.mark.parametrize("text", ["1_000", "١٢", "１２", "12 kg", "0x10"])
def test_sin_forma_canonica_lo_que_no_es_un_numero_ascii(text):
    """Ronda 7: como `is_finite_number` y la forma canónica del front
    (`src/lib/udpNumber.ts`): `int()`/`float()` aceptan «1_000» y dígitos de otros
    alfabetos, que no son un valor de UDP."""
    assert canonical_number(text) is None


# ── Ronda 6 (R16/B1): fecha con hora → sólo la fecha ───────────────────────

@pytest.mark.parametrize("text, expected", [
    ("2024-01-15", "2024-01-15"), (" 2024-01-15 ", "2024-01-15"),
    ("2024-01-15 10:30", "2024-01-15"), ("2024-01-15 10:30:59", "2024-01-15"),
    ("2024-01-15T10:30", "2024-01-15"), ("2024-01-15T00:00:00", "2024-01-15"),
])
def test_fecha_o_fecha_hora_iso_da_la_fecha(text, expected):
    assert iso_date(text) == expected


@pytest.mark.parametrize("text", ["2024-02-30 10:30", "2024-01-15 25:00", "2024-01-15 10:60", "2024-01-15 10:30:60",
                                  "2024-01-15 10", "2024-01-15T", "2024-01-15T10:30Z", "2024-01-15T10:30+05:00",
                                  "2024-01-15 10:30:15.5", "2024-01-15  10:30", "31/12/2026", "hoy", "", None])
def test_fecha_hora_invalida_o_con_zona_es_none(text):
    assert iso_date(text) is None


# Doc 105 (ronda 6): el panel (front) canonicaliza el número antes de grabarlo
# (`web-data-model-hub/src/lib/udpNumber.ts`) donde Python y JS lo escriben
# igual. Ésta es la MISMA tabla de su test: si una de las dos formas cambia, el
# Reporting dejaría de encontrar lo que el panel grabó.
@pytest.mark.parametrize("typed, canonical", [
    ("10.50", "10.5"), ("1e3", "1000"), ("10.0", "10"), ("+007", "7"), ("-0", "0"), (" 12 ", "12"),
    ("12345678901234567890", "12345678901234567890"), ("-3.25", "-3.25"), (".5", "0.5"), ("1E-2", "0.01"),
])
def test_la_forma_canonica_del_panel_es_la_del_backend(typed, canonical):
    assert canonical_number(typed) == canonical
