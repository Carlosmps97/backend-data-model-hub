"""Smoke tests offline del procesamiento determinista (post-LLM).

NO llama al LLM. Verifica:
- audit_columns: inyección desde guidelines, deduplicación, exclusión por
  tipo de tabla (r*, t*).
- ddl_generator: render para databricks_sql + postgresql.
- response_builder.assemble_tables: pipeline completo de mapeo + audit
  + ddl a partir de un JSON crudo del LLM.

Run:
    /path/to/.venv/bin/python tests/test_processing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.api.response_builder import (
    _map_column,
    _map_table,
    assemble_tables,
)
from src.processing.audit_columns import (
    build_audit_columns,
    get_audit_column_names,
    inject_audit_columns,
    should_inject_audit,
)
from src.processing.ddl_generator import render_table_ddl
from src.schemas import ColumnAPI, TableAPI


# ─── Guidelines fixture (subset del JSON real del usuario) ─────────────


GUIDELINES_FIXTURE = {
    "audit_columns": {
        "required": True,
        "applies_to": "ALL tables except static reference tables and temporary tables",
        "columns": [
            {"column_name": "tscreated", "data_type": "TIMESTAMP", "nullable": False,
             "description": "Timestamp when the record was created, in UTC"},
            {"column_name": "tsupdated", "data_type": "TIMESTAMP", "nullable": False,
             "description": "Timestamp of the last update, in UTC"},
            {"column_name": "usrcreatedby", "data_type": "STRING", "nullable": False,
             "description": "User that created the record"},
            {"column_name": "usrupdatedby", "data_type": "STRING", "nullable": False,
             "description": "User that last updated the record"},
            {"column_name": "flgactive", "data_type": "BOOLEAN", "nullable": False,
             "default": "true", "description": "Logical delete flag"},
            {"column_name": "srcbatchid", "data_type": "STRING", "nullable": True,
             "description": "Batch identifier"},
        ],
    },
}


# ─── Tests audit_columns ───────────────────────────────────────────────


def test_audit_names() -> None:
    names = get_audit_column_names(GUIDELINES_FIXTURE)
    assert names == {"tscreated", "tsupdated", "usrcreatedby", "usrupdatedby",
                     "flgactive", "srcbatchid"}, names
    print("  ok audit_names")


def test_build_audit_columns() -> None:
    cols = build_audit_columns(GUIDELINES_FIXTURE)
    assert len(cols) == 6
    assert cols[0].column_name == "tscreated"
    assert cols[0].data_type == "TIMESTAMP"
    assert cols[0].nullable is False
    assert cols[4].column_name == "flgactive"
    assert "default=true" in cols[4].observations
    assert cols[5].nullable is True
    print("  ok build_audit_columns")


def test_should_inject_audit() -> None:
    assert should_inject_audit("hd_traces") is True
    assert should_inject_audit("md_agents_catalog") is True
    assert should_inject_audit("hrt_spans") is True
    assert should_inject_audit("r_country_codes") is False  # reference
    assert should_inject_audit("td_temp_processing") is False  # temporary
    assert should_inject_audit("") is True  # conservador
    print("  ok should_inject_audit")


def test_inject_audit_no_duplicates() -> None:
    # Si el LLM ya emitió flgactive (caso atípico pero posible), no se
    # duplica al inyectar.
    user_cols = [
        ColumnAPI(column_name="codagent", data_type="STRING", nullable=False, is_pk=True,
                  functional_definition="código del agente"),
        ColumnAPI(column_name="flgactive", data_type="BOOLEAN", nullable=False,
                  functional_definition="ya emitido por el LLM"),
    ]
    out = inject_audit_columns(user_cols, "md_agents", GUIDELINES_FIXTURE)
    names = [c.column_name for c in out]
    assert names.count("flgactive") == 1, names
    # Las otras 5 audit columns sí se agregan.
    assert "tscreated" in names
    assert "srcbatchid" in names
    print("  ok inject_audit_no_duplicates")


def test_inject_audit_skipped_for_reference() -> None:
    user_cols = [
        ColumnAPI(column_name="codcountry", data_type="STRING", nullable=False, is_pk=True,
                  functional_definition="ISO country code"),
    ]
    out = inject_audit_columns(user_cols, "r_countries", GUIDELINES_FIXTURE)
    names = [c.column_name for c in out]
    assert names == ["codcountry"], names
    print("  ok inject_audit_skipped_for_reference")


def test_inject_audit_no_guidelines() -> None:
    user_cols = [
        ColumnAPI(column_name="codagent", data_type="STRING", nullable=False, is_pk=True,
                  functional_definition="x"),
    ]
    # Sin guidelines, retorna las columnas tal cual.
    out = inject_audit_columns(user_cols, "md_agents", None)
    assert len(out) == 1
    out2 = inject_audit_columns(user_cols, "md_agents", {})
    assert len(out2) == 1
    print("  ok inject_audit_no_guidelines")


# ─── Tests ddl_generator ───────────────────────────────────────────────


def test_ddl_databricks_basic() -> None:
    table = TableAPI(
        table_name="md_agents_catalog",
        table_description="Catálogo de agentes",
        columns=[
            ColumnAPI(column_name="codagent", data_type="STRING", nullable=False, is_pk=True,
                      functional_definition="Código único"),
            ColumnAPI(column_name="nmagent", data_type="STRING", nullable=True,
                      functional_definition="Nombre del agente"),
            ColumnAPI(column_name="flgactive", data_type="BOOLEAN", nullable=False,
                      functional_definition="Activo",
                      observations="default=true | audit column"),
        ],
    )
    ddl = render_table_ddl(table, "databricks_sql")
    assert "CREATE TABLE md_agents_catalog" in ddl
    assert "codagent STRING NOT NULL" in ddl
    assert "nmagent STRING" in ddl
    assert "flgactive BOOLEAN NOT NULL DEFAULT TRUE" in ddl
    assert "CONSTRAINT pk_md_agents_catalog PRIMARY KEY (codagent)" in ddl
    assert "USING DELTA" in ddl
    assert "COMMENT 'Catálogo de agentes'" in ddl
    print("  ok ddl_databricks_basic")


def test_ddl_postgres_basic() -> None:
    table = TableAPI(
        table_name="md_agents",
        columns=[
            ColumnAPI(column_name="codagent", data_type="VARCHAR(50)", nullable=False, is_pk=True,
                      functional_definition="Code"),
            ColumnAPI(column_name="qtytotal", data_type="INTEGER", nullable=True,
                      functional_definition="total"),
        ],
    )
    ddl = render_table_ddl(table, "postgresql")
    assert "CREATE TABLE md_agents" in ddl
    assert "PRIMARY KEY (codagent)" in ddl
    assert "COMMENT ON COLUMN md_agents.codagent IS 'Code';" in ddl
    print("  ok ddl_postgres_basic")


def test_ddl_engine_fallback() -> None:
    # Motor desconocido → cae en databricks_sql.
    table = TableAPI(
        table_name="x_test",
        columns=[ColumnAPI(column_name="codtest", data_type="STRING", nullable=False, is_pk=True,
                           functional_definition="t")],
    )
    ddl = render_table_ddl(table, "no_existe")
    assert "USING DELTA" in ddl
    print("  ok ddl_engine_fallback")


# ─── Tests response_builder mapeo ──────────────────────────────────────


def test_map_column_defaults() -> None:
    raw = {
        "column_name": "codagent",
        "data_type": "STRING",
        "is_pk": True,
        "functional_definition": "Código del agente",
    }
    c = _map_column(raw)
    assert c.column_name == "codagent"
    assert c.data_type == "STRING"
    assert c.is_pk is True
    assert c.nullable is False  # PK fuerza nullable=False
    assert c.functional_definition == "Código del agente"
    print("  ok map_column_defaults")


def test_map_column_legacy_keys() -> None:
    # Si el LLM usa claves legacy (is_primary_key, is_nullable), siguen
    # siendo aceptadas.
    raw = {
        "column_name": "codagent",
        "data_type": "STRING",
        "is_primary_key": True,
        "is_nullable": False,
        "functional_definition": "x",
    }
    c = _map_column(raw)
    assert c.is_pk is True
    assert c.nullable is False
    print("  ok map_column_legacy_keys")


def test_map_table_basic() -> None:
    raw = {
        "table_name": "md_agents",
        "table_description": "Catálogo",
        "columns": [
            {"column_name": "codagent", "data_type": "STRING", "is_pk": True,
             "functional_definition": "Código"},
            {"column_name": "nmagent", "data_type": "STRING",
             "functional_definition": "Nombre"},
        ],
    }
    t = _map_table(raw)
    assert t.table_name == "md_agents"
    assert t.table_description == "Catálogo"
    assert len(t.columns) == 2
    assert t.ddl == ""  # aún no se generó
    print("  ok map_table_basic")


# ─── Test end-to-end de assemble_tables ────────────────────────────────


def test_assemble_tables_end_to_end() -> None:
    # JSON crudo simulado del LLM (1 tabla maestra + 1 referencia).
    raw_tables = [
        {
            "table_name": "md_agents_catalog",
            "table_description": "Catálogo maestro de agentes",
            "columns": [
                {"column_name": "codagent", "data_type": "STRING", "is_pk": True,
                 "nullable": False, "functional_definition": "Código del agente"},
                {"column_name": "nmagent", "data_type": "STRING", "is_pk": False,
                 "nullable": True, "functional_definition": "Nombre del agente"},
            ],
        },
        {
            "table_name": "r_country_codes",
            "table_description": "Catálogo estático de países",
            "columns": [
                {"column_name": "codcountry", "data_type": "STRING", "is_pk": True,
                 "nullable": False, "functional_definition": "ISO country"},
            ],
        },
    ]
    tables = assemble_tables(raw_tables, "databricks_sql", GUIDELINES_FIXTURE)

    # Tabla maestra: 2 user cols + 6 audit = 8.
    assert tables[0].table_name == "md_agents_catalog"
    assert len(tables[0].columns) == 8, [c.column_name for c in tables[0].columns]
    assert tables[0].columns[-1].column_name == "srcbatchid"
    assert "USING DELTA" in tables[0].ddl
    assert "tscreated TIMESTAMP NOT NULL" in tables[0].ddl

    # Tabla reference: NO recibe audit columns (1 col).
    assert tables[1].table_name == "r_country_codes"
    assert len(tables[1].columns) == 1, [c.column_name for c in tables[1].columns]
    assert "tscreated" not in tables[1].ddl

    print("  ok assemble_tables_end_to_end")


def test_assemble_skips_table_without_name() -> None:
    raw_tables = [
        {"table_name": "", "columns": [{"column_name": "x", "data_type": "STRING",
                                         "functional_definition": "y"}]},
        {"table_name": "md_real", "columns": [{"column_name": "codreal", "data_type": "STRING",
                                                "is_pk": True, "functional_definition": "z"}]},
    ]
    tables = assemble_tables(raw_tables, "databricks_sql", GUIDELINES_FIXTURE)
    assert len(tables) == 1
    assert tables[0].table_name == "md_real"
    print("  ok assemble_skips_table_without_name")


def main() -> int:
    tests = [
        test_audit_names,
        test_build_audit_columns,
        test_should_inject_audit,
        test_inject_audit_no_duplicates,
        test_inject_audit_skipped_for_reference,
        test_inject_audit_no_guidelines,
        test_ddl_databricks_basic,
        test_ddl_postgres_basic,
        test_ddl_engine_fallback,
        test_map_column_defaults,
        test_map_column_legacy_keys,
        test_map_table_basic,
        test_assemble_tables_end_to_end,
        test_assemble_skips_table_without_name,
    ]
    print(f"Running {len(tests)} tests...")
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print()
    if failures:
        print(f"❌ {failures}/{len(tests)} tests failed")
        return 1
    print(f"✅ {len(tests)}/{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
