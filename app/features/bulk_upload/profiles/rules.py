"""Motor de reglas por columna (doc 78 §3.5). PURO. `mustExist` no se evalúa
acá: lo aplican los resolvers del planner con `PlanOptions` (necesitan el
estado efectivo del proyecto)."""
from __future__ import annotations

import re

from ..normalize import clean_text, norm_ci, norm_enum
from ..report import Issue

_CODES = {"required": "rule-required", "maxLength": "rule-max-length", "pattern": "rule-pattern",
          "allowedValues": "rule-allowed-values", "uniqueInFile": "rule-unique"}


def check_rules(mapping: dict, cells: list[tuple[int, str]], role: str,
                sheet_name: str) -> tuple[list[Issue], dict[int, str]]:
    """`cells` = [(fila Excel, texto)] de la columna mapeada. Devuelve las
    incidencias (con la severidad de cada regla) y los valores CANÓNICOS por
    fila (`allowedValues` persiste la grafía de la lista)."""
    header = clean_text(mapping.get("header"))
    issues: list[Issue] = []
    canonical: dict[int, str] = {}

    def hit(rule: dict, row: int, message: str) -> None:
        issues.append(Issue(rule.get("severity") or "error", sheet_name, row, header, _CODES[rule["type"]], message))

    for rule in mapping.get("rules") or []:
        rtype = rule.get("type")
        if rtype == "required":
            for row, text in cells:
                if not clean_text(text):
                    hit(rule, row, f"'{header}' is required.")
        elif rtype == "maxLength":
            n = int(rule.get("value") or 0)
            for row, text in cells:
                t = clean_text(text)
                if len(t) > n:
                    hit(rule, row, f"'{header}' has {len(t)} characters, over the {n}-character limit.")
        elif rtype == "pattern":
            rx = re.compile(str(rule.get("value") or ""))
            for row, text in cells:
                t = clean_text(text)
                if t and rx.fullmatch(t) is None:
                    hit(rule, row, f"'{header}' value '{t}' doesn't match the pattern {rx.pattern}.")
        elif rtype == "allowedValues":
            allowed = {norm_enum(v): str(v) for v in (rule.get("value") or [])}
            for row, text in cells:
                t = clean_text(text)
                if not t:
                    continue
                canon = allowed.get(norm_enum(t))
                if canon is None:
                    hit(rule, row, f"'{header}' value '{t}' is not allowed (allowed: {', '.join(allowed.values())}).")
                else:
                    canonical[row] = canon
        elif rtype == "uniqueInFile":
            seen: dict[str, int] = {}
            for row, text in cells:
                t = clean_text(text)
                if not t:
                    continue
                key = norm_ci(canonical.get(row, t))
                if key in seen:
                    hit(rule, row, f"'{header}' value '{t}' is repeated (see row {seen[key]}).")
                else:
                    seen[key] = row
    return issues, canonical
