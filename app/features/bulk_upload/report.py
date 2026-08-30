"""Reporte de validación de la carga masiva (doc 55 §5). Puro.

Dos severidades: `error` bloquea el Upload; `warning` informa (entidades
existentes que se modifican, renames, etc.). Cada incidencia lleva hoja, fila
y columna del Excel para que el modelador ubique la celda. Se listan hasta
`SEVERITY_CAP` por severidad; los totales SIEMPRE viajan completos.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

SEVERITY_CAP = 500

SHEET_TABLES = "Tablas"
SHEET_COLUMNS = "Atributos"
SHEET_WORKBOOK = "Workbook"


@dataclass(frozen=True)
class Issue:
    severity: str            # 'error' | 'warning'
    sheet: str               # Tablas | Atributos | Workbook
    row: int | None
    column: str | None       # cabecera de la celda implicada (si aplica)
    code: str                # código estable para agrupar en la UI
    message: str


class ReportBuilder:
    """Acumula incidencias y arma el reporte final (`build`)."""

    def __init__(self) -> None:
        self._errors: list[Issue] = []
        self._warnings: list[Issue] = []
        self._by_row: dict[tuple[str, int], int] = {}
        self.error_count = 0
        self.warning_count = 0

    def issues_in(self, sheet: str, rows: list[int]) -> int:
        """Incidencias (ambas severidades) de esas filas de una hoja — el
        desglose por tabla del popup suma su fila + sus filas de columnas."""
        return sum(self._by_row.get((sheet, r), 0) for r in rows)

    def add(self, issue: Issue) -> None:
        if issue.row is not None:
            key = (issue.sheet, issue.row)
            self._by_row[key] = self._by_row.get(key, 0) + 1
        if issue.severity == "error":
            self.error_count += 1
            if len(self._errors) < SEVERITY_CAP:
                self._errors.append(issue)
        else:
            self.warning_count += 1
            if len(self._warnings) < SEVERITY_CAP:
                self._warnings.append(issue)

    def extend(self, issues) -> None:
        for issue in issues:
            self.add(issue)

    def error(self, sheet: str, code: str, message: str,
              row: int | None = None, column: str | None = None) -> None:
        self.add(Issue("error", sheet, row, column, code, message))

    def warning(self, sheet: str, code: str, message: str,
                row: int | None = None, column: str | None = None) -> None:
        self.add(Issue("warning", sheet, row, column, code, message))

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    def build(self, summary: dict, tables: list[dict]) -> dict:
        """Forma que consume el popup: conteos por entidad, desglose por tabla
        e incidencias (ordenadas por fila)."""
        key = (lambda i: (i.sheet, i.row if i.row is not None else -1))
        return {
            "summary": summary,
            "tables": tables,
            "errors": [asdict(i) for i in sorted(self._errors, key=key)],
            "warnings": [asdict(i) for i in sorted(self._warnings, key=key)],
            "errorCount": self.error_count,
            "warningCount": self.warning_count,
        }
