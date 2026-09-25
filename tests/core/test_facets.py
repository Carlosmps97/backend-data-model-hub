"""Doc 69 §4.9: contrato ÚNICO de facetas lógico/físico (puro)."""
from __future__ import annotations

from app.core import facets as fx


def test_normalize_view_fuerza_physical_en_view_y_canvas():
    assert fx.normalize_udp_view("view", "logical") == "physical"
    assert fx.normalize_udp_view("canvas", None) == "physical"


def test_normalize_view_respeta_table_column_y_default_physical():
    assert fx.normalize_udp_view("table", "logical") == "logical"
    assert fx.normalize_udp_view("column", "physical") == "physical"
    assert fx.normalize_udp_view("column", None) == "physical"      # defs pre-doc 69
    assert fx.normalize_udp_view("table", "garbage") == "physical"


def test_display_name_fisica_plana_y_logica_con_sufijo():
    assert fx.udp_display_name({"id": "u1", "name": "Clasificacion del Dato", "level": "column"}) == "Clasificacion del Dato"
    assert fx.udp_display_name({"id": "u2", "name": "Clasificacion del Dato", "level": "column",
                                "view": "logical"}) == "Clasificacion del Dato (Logical)"


def test_display_names_acepta_dumps_y_docs_crudos():
    names = fx.udp_display_names([{"id": "u1", "name": "A", "level": "table"},
                                  {"_id": "u2", "name": "B", "level": "table", "view": "logical"},
                                  {"name": "sin id"}])
    assert names == {"u1": "A", "u2": "B (Logical)"}


def test_physical_udp_defs_filtra_logicas():
    defs = [{"id": "p", "level": "column"}, {"id": "l", "level": "column", "view": "logical"},
            {"id": "v", "level": "view", "view": "logical"}]   # view ⇒ physical aunque diga logical
    assert [d["id"] for d in fx.physical_udp_defs(defs)] == ["p", "v"]


def test_contratos_sin_campos_repetidos_entre_facetas():
    for contract in (fx.TABLE_FACET_FIELDS, fx.COLUMN_FACET_FIELDS, fx.DOMAIN_FACET_FIELDS):
        seen: set[str] = set()
        for fields in contract.values():
            assert not (seen & set(fields)), fields
            seen |= set(fields)
    assert "dataType" in fx.COLUMN_FACET_FIELDS["physical"]
    assert "logicalDataType" in fx.COLUMN_FACET_FIELDS["logical"]
    # Doc 74: un solo orden de columnas, compartido por ambas facetas.
    # Doc 94 D1: sin orden de llave aparte (`pkPosition` retirado).
    assert "ordinal" in fx.COLUMN_FACET_FIELDS["shared"] and "pkPosition" not in fx.COLUMN_FACET_FIELDS["shared"]
    assert "defaultDataType" in fx.DOMAIN_FACET_FIELDS["physical"]


def test_dominio_y_columna_facetas_doc85():
    """Doc 85 §3.2: el dominio declara nombre/tipo/descripción POR faceta; la
    columna suma la descripción física (Comment de Erwin)."""
    assert set(fx.DOMAIN_FACET_FIELDS["logical"]) == {"name", "logicalDataType", "description"}
    assert set(fx.DOMAIN_FACET_FIELDS["physical"]) == {"physicalName", "defaultDataType", "physicalDescription"}
    assert {"id", "projectId", "namingTerm", "inheritsName", "udpValues"} <= set(fx.DOMAIN_FACET_FIELDS["shared"])
    assert "physicalDescription" in fx.COLUMN_FACET_FIELDS["physical"]
    assert "description" in fx.COLUMN_FACET_FIELDS["shared"]      # la definición funcional sigue compartida en el CONTRATO
