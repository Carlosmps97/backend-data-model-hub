"""Reporte de validación de la carga masiva (doc 55 §5 · doc 78 §6). Puro.

Dos severidades: `error` bloquea el Upload; `warning` informa. Cada incidencia
lleva hoja, fila y columna del Excel para que el modelador ubique la celda.
Los planners hablan en ROLES (`tables` / `columns` / `workbook` / `profile`) y
el builder los traduce al nombre REAL de la hoja del perfil (`Issue.sheet`);
las incidencias que ya vienen con nombre real (`profiles/apply`) se aceptan
tal cual. Se listan hasta `SEVERITY_CAP` por severidad; los totales SIEMPRE
viajan completos.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

SEVERITY_CAP = 500

SHEET_TABLES = "tables"
SHEET_COLUMNS = "columns"
SHEET_WORKBOOK = "workbook"
SHEET_PROFILE = "profile"
DEFAULT_SHEET_NAMES = {SHEET_TABLES: "Tablas", SHEET_COLUMNS: "Atributos",
                       SHEET_WORKBOOK: "Workbook", SHEET_PROFILE: "Profile"}


@dataclass(frozen=True)
class Issue:
    severity: str            # 'error' | 'warning'
    sheet: str               # nombre REAL de la hoja | Workbook | Profile
    row: int | None
    column: str | None       # cabecera de la celda implicada (si aplica)
    code: str                # código estable para agrupar en la UI
    message: str


class ReportBuilder:
    """Acumula incidencias y arma el reporte final (`build`)."""

    def __init__(self, sheet_names: dict[str, str] | None = None) -> None:
        self._names = {**DEFAULT_SHEET_NAMES, **{k: v for k, v in (sheet_names or {}).items() if v}}
        self._roles = {v: k for k, v in self._names.items()}
        self._errors: list[Issue] = []
        self._warnings: list[Issue] = []
        self._by_row: dict[tuple[str, int], int] = {}
        self.error_count = 0
        self.warning_count = 0

    def sheet_name(self, role: str) -> str:
        return self._names.get(role, role)

    def _role(self, sheet: str) -> str:
        return self._roles.get(sheet, sheet)

    def issues_in(self, role: str, rows: list[int]) -> int:
        """Incidencias (ambas severidades) de esas filas de una hoja — el
        desglose por tabla del popup suma su fila + sus filas de columnas."""
        r = self._role(role)
        return sum(self._by_row.get((r, row), 0) for row in rows)

    def add(self, issue: Issue) -> None:
        if issue.row is not None:
            key = (self._role(issue.sheet), issue.row)
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

    def issue(self, severity: str, role: str, code: str, message: str,
              row: int | None = None, column: str | None = None) -> None:
        self.add(Issue(severity, self.sheet_name(role), row, column, code, message))

    def error(self, role: str, code: str, message: str,
              row: int | None = None, column: str | None = None) -> None:
        self.issue("error", role, code, message, row, column)

    def warning(self, role: str, code: str, message: str,
                row: int | None = None, column: str | None = None) -> None:
        self.issue("warning", role, code, message, row, column)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    def build(self, summary: dict, tables: list[dict], profile: dict | None = None,
              sheets: list[dict] | None = None) -> dict:
        """Forma que consume el popup: conteos por entidad, desglose por tabla,
        incidencias (ordenadas por hoja y fila), perfil usado y hojas halladas."""
        key = (lambda i: (i.sheet, i.row if i.row is not None else -1))
        return {
            "summary": summary,
            "tables": tables,
            "errors": [asdict(i) for i in sorted(self._errors, key=key)],
            "warnings": [asdict(i) for i in sorted(self._warnings, key=key)],
            "errorCount": self.error_count,
            "warningCount": self.warning_count,
            "profile": profile,
            "sheets": list(sheets or []),
        }
