"""Doc 61 §2.4: parser/validador puro del SQL custom de una vista.

Reglas: 1 statement; raíz SELECT (WITH y set-ops descienden al SELECT final);
anti-`SELECT *`; expresión compleja sin alias → error; salida = columnas
nominadas + tablas referenciadas (sin CTEs)."""
from __future__ import annotations

import pytest

from app.features.views.custom_sql import CustomSqlError, parse_custom_sql


def test_simple_select():
    out = parse_custom_sql("SELECT id, nombre FROM core.m_cliente")
    assert out["columns"] == [{"name": "id"}, {"name": "nombre"}]
    assert out["tables"] == ["core.m_cliente"]


def test_aliases_and_expressions():
    out = parse_custom_sql(
        "SELECT id AS codigo, UPPER(nombre) AS nombre_up, monto FROM t1")
    assert out["columns"] == [
        {"name": "codigo"},
        {"name": "nombre_up", "expression": "UPPER(nombre)"},
        {"name": "monto"},
    ]


def test_with_cte_and_join():
    sql = """WITH base AS (
      SELECT c.id, c.nombre, s.saldo
      FROM core.m_cliente c
      INNER JOIN core.m_saldo s ON s.cliente_id = c.id
    )
    SELECT id, nombre, saldo AS saldo_total FROM base WHERE saldo > 0"""
    out = parse_custom_sql(sql)
    assert [c["name"] for c in out["columns"]] == ["id", "nombre", "saldo_total"]
    # `base` es CTE: NO cuenta como tabla referenciada.
    assert out["tables"] == ["core.m_cliente", "core.m_saldo"]


def test_union_uses_first_select_projection():
    out = parse_custom_sql("SELECT a FROM t1 UNION ALL SELECT b FROM t2")
    assert out["columns"] == [{"name": "a"}]
    assert out["tables"] == ["t1", "t2"]


def test_select_star_rejected():
    with pytest.raises(CustomSqlError):
        parse_custom_sql("SELECT * FROM t1")


def test_qualified_star_rejected():
    with pytest.raises(CustomSqlError):
        parse_custom_sql("SELECT t.* FROM t1 t")


def test_expression_without_alias_rejected():
    with pytest.raises(CustomSqlError) as ei:
        parse_custom_sql("SELECT SUM(monto) FROM t1")
    assert "alias" in ei.value.message.lower()


def test_duplicate_output_names_rejected():
    with pytest.raises(CustomSqlError) as ei:
        parse_custom_sql("SELECT id, monto AS id FROM t1")
    assert "duplicate" in ei.value.message.lower() or "duplicad" in ei.value.message.lower()


def test_multi_statement_rejected():
    with pytest.raises(CustomSqlError):
        parse_custom_sql("SELECT a FROM t; SELECT b FROM u")


def test_non_select_rejected():
    with pytest.raises(CustomSqlError):
        parse_custom_sql("DELETE FROM t1")


def test_empty_rejected():
    with pytest.raises(CustomSqlError):
        parse_custom_sql("   ")


def test_syntax_error_has_position():
    with pytest.raises(CustomSqlError) as ei:
        parse_custom_sql("SELECT id FROM WHERE x = 1 GROUP BY")
    assert ei.value.line >= 1 and ei.value.col >= 1
