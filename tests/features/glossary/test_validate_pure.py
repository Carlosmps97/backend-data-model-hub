"""F2 #1: validación pura — regex de frase completa + duplicado exacto."""
from __future__ import annotations

import re

from app.features.glossary.service import corpus_regex, find_glossary_duplicate


def _matches(term: str, logical: str) -> bool:
    return re.search(corpus_regex(term), logical, re.IGNORECASE) is not None


# ── corpus_regex: frase completa, contigua, case-insensitive ───────────────


def test_matchea_palabra_al_inicio_medio_y_final():
    assert _matches("codigo", "codigo cuenta en soles")
    assert _matches("cuenta", "codigo cuenta en soles")
    assert _matches("soles", "codigo cuenta en soles")


def test_no_matchea_subpalabra():
    assert not _matches("codigo", "codigos de barras")   # 'codigos' ≠ 'codigo'
    assert not _matches("cod", "codigo cuenta")          # prefijo no cuenta


def test_frase_multi_palabra_contigua():
    assert _matches("codigo cuenta", "codigo cuenta en soles")
    assert not _matches("codigo soles", "codigo cuenta en soles")  # no contigua


def test_case_insensitive_y_acentos_como_letra():
    assert _matches("CODIGO", "Codigo Cuenta")
    # ñ/acentos son letra: no cortan palabra → 'año' NO matchea dentro de 'añosco'
    assert not _matches("año", "añosco")
    assert _matches("año", "año fiscal")


def test_regex_escapa_metacaracteres():
    # Un término con paréntesis no debe romper el regex ni matchear de más.
    assert _matches("monto (neto)", "el monto (neto) final")
    assert not _matches("monto (neto)", "el monto neto final")


# ── find_glossary_duplicate: exacto case-insensitive, excluye self ─────────


ENTRIES = [
    {"id": "t1", "term": "Codigo", "abbrev": "COD"},
    {"id": "t2", "term": "cuenta", "abbrev": "CTA"},
]


def test_duplicado_exacto_case_insensitive():
    dup = find_glossary_duplicate("codigo", ENTRIES)
    assert dup == {"id": "t1", "term": "Codigo", "abbrev": "COD"}


def test_sin_duplicado_devuelve_none():
    assert find_glossary_duplicate("codigo de analisis", ENTRIES) is None


def test_exclude_id_no_choca_contra_si_mismo():
    assert find_glossary_duplicate("codigo", ENTRIES, exclude_id="t1") is None
