"""Motor de conversión nombre lógico ↔ físico (puro, data-driven)."""
from __future__ import annotations

import pytest

from app.core.naming.engine import physicalize

DICT = {"monto": "MTO", "deuda": "DEU", "dólares": "USD", "tipo de cambio": "TPC"}
# Mapa per-table del diseño (screen 08b): cuenta riesgo → CTARIESGO.
TABLE_DICT = {"cuenta": "CTA"}


def test_physicalize_basico():
    assert physicalize("monto deuda dólares", DICT) == "MTO_DEU_USD"


def test_physicalize_longest_match_multi_palabra():
    assert physicalize("tipo de cambio monto", DICT) == "TPC_MTO"


def test_physicalize_token_no_mapeado_va_en_mayuscula():
    assert physicalize("monto neto", DICT) == "MTO_NETO"


def test_physicalize_vacio():
    assert physicalize("", DICT) == ""


# ── case + separator configurables (R1c) ─────────────────────────────────


def test_physicalize_case_default_es_upper():
    # Default sin pasar case/separator == comportamiento histórico.
    assert physicalize("monto deuda dólares", DICT) == "MTO_DEU_USD"


def test_physicalize_case_lower():
    assert physicalize("monto deuda dólares", DICT, case="lower") == "mto_deu_usd"


def test_physicalize_separator_custom():
    assert physicalize("monto deuda", DICT, separator="-") == "MTO-DEU"


def test_physicalize_per_table_sin_separador_y_longest_match():
    # screen 08b: separador "(none)" → join sin separador, UPPER.
    assert physicalize("cuenta riesgo", TABLE_DICT, separator="", case="upper") == "CTARIESGO"


def test_physicalize_camel_ignora_separator():
    # camelCase: primera palabra lower, siguientes Capitalized, sin separador.
    out = physicalize("monto deuda dólares", DICT, separator="_", case="camel")
    assert out == "mtoDeuUsd"


def test_physicalize_camel_con_tokens_no_mapeados():
    out = physicalize("monto neto final", DICT, case="camel")
    assert out == "mtoNetoFinal"


def test_physicalize_camel_longest_match():
    out = physicalize("tipo de cambio monto", DICT, case="camel")
    assert out == "tpcMto"


def test_physicalize_vacio_con_case():
    assert physicalize("", DICT, separator="", case="camel") == ""


def test_physicalize_case_invalido_levanta():
    with pytest.raises(ValueError):
        physicalize("monto", DICT, case="title")
