"""Doc 92 D8: regla de caracteres de los nombres lógicos (espejo del front)."""
from __future__ import annotations

from app.core.naming.logical import has_special_chars, sanitize_logical_name


def test_conserva_letras_digitos_espacio_y_guion_bajo():
    assert sanitize_logical_name("Código Único Cliente 2 _x") == "Código Único Cliente 2 _x"
    assert sanitize_logical_name("Año Ñandú") == "Año Ñandú"


def test_quita_puntuacion_y_simbolos_y_colapsa_espacios():
    assert sanitize_logical_name("Cuenta (activa)") == "Cuenta activa"
    assert sanitize_logical_name("E/3360") == "E3360"
    assert sanitize_logical_name("%AttDomain") == "AttDomain"
    assert sanitize_logical_name("a-b.c,d|e;f:g\"h'i") == "abcdefghi"
    assert sanitize_logical_name("  x  @  y  ") == "x y"


def test_none_y_no_texto():
    assert sanitize_logical_name(None) == ""
    assert sanitize_logical_name(12) == "12"


def test_has_special_chars():
    assert has_special_chars("Cuenta (activa)") is True
    assert has_special_chars("Cuenta activa") is False
    assert has_special_chars(None) is False
