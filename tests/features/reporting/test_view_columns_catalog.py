"""view_columns (F5): entidad virtual del reporting — catálogo + contrato spec.

El camino de ejecución (aggregate+unwind con fallback de description) se valida
en vivo; acá se fija el CONTRATO puro que no debe romperse."""
from __future__ import annotations

from app.features.reporting.query.schema import build_catalog
from app.features.reporting.query.spec import FROMS, QuerySpec


def test_view_columns_is_a_valid_from():
    assert "view_columns" in FROMS
    spec = QuerySpec.model_validate({"from": "view_columns", "projectId": "p1",
                                     "select": ["viewName", "description"]})
    assert spec.from_ == "view_columns"


def test_view_columns_catalog_has_definition_on_source_path():
    cat = build_catalog("view_columns", [])
    assert set(cat) >= {"viewName", "schema", "outputName", "sourceColumn",
                        "castType", "expression", "description"}
    # la definición de columna-de-vista vive en el array `sources` (unwind) y
    # no se puede agrupar (el builder no debe ofrecer groupBy en esta entidad).
    assert cat["description"].path == "sources.description"
    assert cat["description"].groupable is False
    assert all(fd.groupable is False for fd in cat.values())


def test_view_level_definition_is_reportable():
    # F5: la definición funcional de la VISTA también entra al catálogo `views`.
    assert "description" in build_catalog("views", [])
