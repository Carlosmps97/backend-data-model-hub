"""Generación determinista del export Markdown del modelo.

Toma una lista de `TableAPI` y produce el documento Markdown completo
con secciones por tabla, tabla de columnas y bloque DDL embebido.

No requiere LLM. Es puramente formato.
"""

from __future__ import annotations

from src.schemas import TableAPI


def _md_escape(text: str) -> str:
    """Escapa pipes y newlines para no romper tablas Markdown."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_export_markdown(
    tables: list[TableAPI],
    engine: str,
    export_sql: str = "",
) -> str:
    """Genera el Markdown completo del modelo.

    Args:
        tables: tablas finales del modelo (ya con audit columns).
        engine: motor destino (se incluye en el header).
        export_sql: si se pasa, se anexa al final como un bloque único de
            SQL. Si está vacío, cada tabla muestra su propio DDL embebido.
    """
    lines: list[str] = ["# Modelo de Datos", ""]
    if engine:
        lines.append(f"**Motor:** {engine}")
        lines.append("")

    for t in tables:
        lines.append(f"## {t.table_name}")
        if t.table_description:
            lines.append("")
            lines.append(t.table_description)
        lines.append("")
        lines.append(
            "| Columna | Tipo | Nullable | PK | FK | Definición funcional | Observaciones |"
        )
        lines.append("|---|---|:-:|:-:|:-:|---|---|")
        for c in t.columns:
            row = (
                f"| {_md_escape(c.column_name)} "
                f"| {_md_escape(c.data_type)} "
                f"| {'✓' if c.nullable else '✗'} "
                f"| {'✓' if c.is_pk else ''} "
                f"| {'✓' if c.is_fk else ''} "
                f"| {_md_escape(c.functional_definition)} "
                f"| {_md_escape(c.observations)} |"
            )
            lines.append(row)
        lines.append("")
        if t.ddl.strip():
            lines.append("```sql")
            lines.append(t.ddl.strip())
            lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
