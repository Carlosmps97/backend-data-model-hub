"""Generación determinista de DDL desde un `TableAPI`.

Antes el LLM emitía el DDL como texto. Esto era costoso (200-400 tokens
por tabla, mucho del techo de output) y propenso a errores de sintaxis.

Ahora el LLM solo decide nombres + tipos + nullability + PK. El DDL se
construye en código con templates por motor. Resultado:
- 0 errores de sintaxis (template controlado).
- 0 tokens del LLM gastados en DDL.
- DDL siempre consistente con los nombres del modelo.

Motores soportados (los mismos que listaba la API):
- databricks_sql (default — coincide con el área "databricks" del JSON)
- postgresql
- mysql
- sqlserver
- cosmosdb (devuelve un container definition JSON)

Si el motor no coincide con ninguno, cae al template de databricks_sql.
"""

from __future__ import annotations

from src.schemas import ColumnAPI, TableAPI


def _quote_default(value: str) -> str:
    """Citado mínimo para defaults en DDL.

    Si el valor parece numérico/booleano/función, lo dejamos sin comillas;
    en otro caso lo citamos como string SQL.
    """
    v = value.strip()
    if not v:
        return ""
    lower = v.lower()
    if lower in ("true", "false", "null", "current_timestamp", "now()"):
        return v.upper() if lower in ("true", "false", "null") else v
    if lower.replace(".", "").replace("-", "").isdigit():
        return v
    return f"'{v}'"


def _render_column_databricks(c: ColumnAPI) -> str:
    """Render de una columna en sintaxis Databricks SQL."""
    parts: list[str] = [c.column_name, c.data_type or "STRING"]
    if not c.nullable:
        parts.append("NOT NULL")
    # default desde observations (formato `default=X`)
    obs = c.observations or ""
    if "default=" in obs:
        for chunk in obs.split("|"):
            chunk = chunk.strip()
            if chunk.startswith("default="):
                default = chunk.split("=", 1)[1].strip()
                rendered = _quote_default(default)
                if rendered:
                    parts.append(f"DEFAULT {rendered}")
                break
    if c.functional_definition:
        comment = c.functional_definition.replace("'", "''")
        parts.append(f"COMMENT '{comment}'")
    return "  " + " ".join(parts)


def _render_table_databricks(t: TableAPI) -> str:
    """Genera CREATE TABLE para Databricks (Delta)."""
    if not t.columns:
        return ""
    col_lines = [_render_column_databricks(c) for c in t.columns]
    pk_cols = [c.column_name for c in t.columns if c.is_pk]
    inner = ",\n".join(col_lines)
    if pk_cols:
        inner += f",\n  CONSTRAINT pk_{t.table_name} PRIMARY KEY ({', '.join(pk_cols)})"

    head = f"CREATE TABLE {t.table_name} ("
    body = f"\n{inner}\n)"
    using = "\nUSING DELTA"
    if t.table_description:
        comment = t.table_description.replace("'", "''")
        using += f"\nCOMMENT '{comment}'"
    return head + body + using + ";"


def _render_column_postgres(c: ColumnAPI) -> str:
    parts: list[str] = [c.column_name, c.data_type or "TEXT"]
    if not c.nullable:
        parts.append("NOT NULL")
    obs = c.observations or ""
    if "default=" in obs:
        for chunk in obs.split("|"):
            chunk = chunk.strip()
            if chunk.startswith("default="):
                default = chunk.split("=", 1)[1].strip()
                rendered = _quote_default(default)
                if rendered:
                    parts.append(f"DEFAULT {rendered}")
                break
    return "  " + " ".join(parts)


def _render_table_postgres(t: TableAPI) -> str:
    if not t.columns:
        return ""
    col_lines = [_render_column_postgres(c) for c in t.columns]
    pk_cols = [c.column_name for c in t.columns if c.is_pk]
    inner = ",\n".join(col_lines)
    if pk_cols:
        inner += f",\n  PRIMARY KEY ({', '.join(pk_cols)})"
    out = f"CREATE TABLE {t.table_name} (\n{inner}\n);"
    # Comments separados (Postgres no permite COMMENT inline)
    extras: list[str] = []
    if t.table_description:
        d = t.table_description.replace("'", "''")
        extras.append(f"COMMENT ON TABLE {t.table_name} IS '{d}';")
    for c in t.columns:
        if c.functional_definition:
            d = c.functional_definition.replace("'", "''")
            extras.append(
                f"COMMENT ON COLUMN {t.table_name}.{c.column_name} IS '{d}';"
            )
    return out + ("\n" + "\n".join(extras) if extras else "")


def _render_table_mysql(t: TableAPI) -> str:
    if not t.columns:
        return ""
    col_lines: list[str] = []
    for c in t.columns:
        parts: list[str] = [f"`{c.column_name}`", c.data_type or "VARCHAR(255)"]
        if not c.nullable:
            parts.append("NOT NULL")
        obs = c.observations or ""
        if "default=" in obs:
            for chunk in obs.split("|"):
                chunk = chunk.strip()
                if chunk.startswith("default="):
                    default = chunk.split("=", 1)[1].strip()
                    rendered = _quote_default(default)
                    if rendered:
                        parts.append(f"DEFAULT {rendered}")
                    break
        if c.functional_definition:
            comment = c.functional_definition.replace("'", "''")
            parts.append(f"COMMENT '{comment}'")
        col_lines.append("  " + " ".join(parts))
    pk_cols = [f"`{c.column_name}`" for c in t.columns if c.is_pk]
    inner = ",\n".join(col_lines)
    if pk_cols:
        inner += f",\n  PRIMARY KEY ({', '.join(pk_cols)})"
    table_comment = ""
    if t.table_description:
        d = t.table_description.replace("'", "''")
        table_comment = f" COMMENT='{d}'"
    return (
        f"CREATE TABLE `{t.table_name}` (\n{inner}\n)"
        f" ENGINE=InnoDB DEFAULT CHARSET=utf8mb4{table_comment};"
    )


def _render_table_sqlserver(t: TableAPI) -> str:
    if not t.columns:
        return ""
    col_lines: list[str] = []
    for c in t.columns:
        parts: list[str] = [f"[{c.column_name}]", c.data_type or "NVARCHAR(MAX)"]
        if not c.nullable:
            parts.append("NOT NULL")
        else:
            parts.append("NULL")
        obs = c.observations or ""
        if "default=" in obs:
            for chunk in obs.split("|"):
                chunk = chunk.strip()
                if chunk.startswith("default="):
                    default = chunk.split("=", 1)[1].strip()
                    rendered = _quote_default(default)
                    if rendered:
                        parts.append(f"DEFAULT {rendered}")
                    break
        col_lines.append("  " + " ".join(parts))
    pk_cols = [f"[{c.column_name}]" for c in t.columns if c.is_pk]
    inner = ",\n".join(col_lines)
    if pk_cols:
        inner += (
            f",\n  CONSTRAINT [pk_{t.table_name}] PRIMARY KEY ({', '.join(pk_cols)})"
        )
    return f"CREATE TABLE [dbo].[{t.table_name}] (\n{inner}\n);"


def _render_table_cosmosdb(t: TableAPI) -> str:
    """Cosmos DB no usa DDL — devolvemos un container definition JSON."""
    pk = next((c.column_name for c in t.columns if c.is_pk), None)
    indent = "  "
    parts = [
        "{",
        f'{indent}"id": "{t.table_name}",',
        f'{indent}"partitionKey": "/{pk}",' if pk else "",
        f'{indent}"comment": "{(t.table_description or "").replace(chr(34), chr(39))}"',
        "}",
    ]
    return "\n".join(p for p in parts if p)


_RENDERERS = {
    "databricks_sql": _render_table_databricks,
    "postgresql": _render_table_postgres,
    "postgres": _render_table_postgres,
    "mysql": _render_table_mysql,
    "sqlserver": _render_table_sqlserver,
    "mssql": _render_table_sqlserver,
    "cosmosdb": _render_table_cosmosdb,
}


def render_table_ddl(table: TableAPI, engine: str) -> str:
    """Genera el DDL de UNA tabla para el motor indicado.

    Si el motor no está soportado, se aplica el renderer de Databricks
    como default (es lo que pide el área "databricks" del JSON real).
    """
    renderer = _RENDERERS.get(engine.lower(), _render_table_databricks)
    return renderer(table).strip()


def render_export_sql(tables: list[TableAPI], engine: str) -> str:
    """Concatena el DDL de todas las tablas separados por header de tabla."""
    chunks: list[str] = []
    for t in tables:
        ddl = render_table_ddl(t, engine)
        if ddl:
            chunks.append(f"-- ── Tabla: {t.table_name} ──\n{ddl}")
    return "\n\n".join(chunks)
