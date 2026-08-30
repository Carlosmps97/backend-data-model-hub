"""Normalización de texto de la carga masiva (doc 55): celdas, claves de
cabecera/UDP, enums, marca de PK y correlativo de partición. Todo puro."""
from __future__ import annotations

import pytest

from app.features.bulk_upload.normalize import (
    clean_text, is_pk_mark, norm_ci, norm_enum, norm_key, norm_type,
    partition_correlative,
)


def test_clean_text_preserva_saltos_de_linea_y_tildes():
    # DEF_TABLA / DEF_ATRIBUTO viajan tal cual: solo se recorta el borde y se
    # normaliza CRLF → LF (Excel en Windows mete \r\n).
    assert clean_text("  Código\r\nde año y ñ \n") == "Código\nde año y ñ"


def test_clean_text_none_y_no_texto():
    assert clean_text(None) == ""
    assert clean_text(42) == "42"


def test_norm_ci_colapsa_espacios_y_baja_a_minusculas():
    assert norm_ci("  Tabla   Uno ") == "tabla uno"


def test_norm_key_ignora_tildes_guiones_bajos_y_stopwords():
    assert norm_key("UDP_Clasificacion_del_Dato") == "clasificacion dato"
    assert norm_key("Clasificación del Dato") == "clasificacion dato"


def test_norm_key_tipo_vista_equivale_a_tipo_de_vista():
    # La plantilla dice UDP_Tipo_Vista; la definición viva se llama "Tipo de Vista".
    assert norm_key("UDP_Tipo_Vista") == norm_key("Tipo de Vista")


def test_norm_key_prefijo_udp_opcional():
    assert norm_key("Universal") == norm_key("UDP_Universal") == "universal"


def test_norm_enum_como_el_kit_erwin():
    assert norm_enum("  no   dac ") == "NO DAC"
    assert norm_enum(None) == ""


@pytest.mark.parametrize("mark", ["X", "x", " x ", "Si", "sí", "SI", "yes", "1", "true", "PK"])
def test_is_pk_mark_true(mark):
    assert is_pk_mark(mark) is True


@pytest.mark.parametrize("mark", ["", None, "no", "0", "false", "N"])
def test_is_pk_mark_false(mark):
    assert is_pk_mark(mark) is False


def test_partition_correlative():
    assert partition_correlative("PART_01") == 1
    assert partition_correlative("part-3") == 3
    assert partition_correlative("No Definido") is None
    assert partition_correlative("") is None


def test_norm_type_ignora_caso_y_espacios():
    assert norm_type("decimal (18, 2)") == "DECIMAL(18,2)"
    assert norm_type(None) == ""


def test_norm_name_ignora_tildes_caso_y_espacios():
    # Nombres de dominios/proyectos/carpetas/canvases/lógicos: "Código Clave"
    # y "codigo  clave" son el mismo nombre para la carga.
    from app.features.bulk_upload.normalize import norm_name
    assert norm_name("Código  Clave SHA2 512") == norm_name("codigo clave sha2 512") == "codigo clave sha2 512"
