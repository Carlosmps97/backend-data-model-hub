"""Constructores compartidos por los tests del planner de la carga masiva."""
from __future__ import annotations

from app.features.bulk_upload.context import UploadContext
from app.features.bulk_upload.parser import ColumnRow, ParsedWorkbook, TableRow


def naming(max_len: int = 150) -> dict[str, dict]:
    return {scope: {"scope": scope, "separator": "", "case": "upper", "maxLength": max_len}
            for scope in ("table", "column")}


def ctx(**kw) -> UploadContext:
    """Contexto vacío con naming corporativo (join + UPPER, 150) y sin glosario."""
    base = dict(project_id="p1", project_name="P", naming=naming(), glossary={"table": {}, "column": {}})
    base.update(kw)
    return UploadContext(**base)


def parsed(tables: list[TableRow] | None = None, columns: list[ColumnRow] | None = None,
           table_udp: dict[str, list[dict]] | None = None, column_udp: dict[str, list[dict]] | None = None,
           headers: dict[str, dict[str, str]] | None = None) -> ParsedWorkbook:
    """Workbook YA interpretado por un perfil (doc 78): `table_udp`/`column_udp`
    son el mapeo resuelto cabecera → defs; los nombres de hoja son los de la
    plantilla histórica (`Tablas`/`Atributos`)."""
    return ParsedWorkbook(
        tables=list(tables or []), columns=list(columns or []),
        table_udp=dict(table_udp or {}), column_udp=dict(column_udp or {}),
        headers=dict(headers or {}),
        has_tables_sheet=tables is not None, has_columns_sheet=columns is not None,
        sheet_names={"tables": "Tablas", "columns": "Atributos"},
    )


def trow(row: int, logical: str, **kw) -> TableRow:
    return TableRow(row=row, logical=logical, **kw)


def crow(row: int, table_logical: str, logical: str, **kw) -> ColumnRow:
    return ColumnRow(row=row, table_logical=table_logical, logical=logical, **kw)


def by_coll(plan) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for ch in plan.changes:
        out.setdefault(ch["collection"], []).append(ch)
    return out


def codes(plan, severity: str) -> list[str]:
    return [i["code"] for i in plan.report[severity + "s"]]


def seq_ids():
    """Generador determinista de ids nuevos (n1, n2, …)."""
    counter = {"n": 0}

    def _next() -> str:
        counter["n"] += 1
        return f"n{counter['n']}"

    return _next
