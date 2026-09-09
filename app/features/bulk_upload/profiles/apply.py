"""Del workbook crudo al `ParsedWorkbook` según el PERFIL (doc 78 §5). PURO.

Pasos por hoja del perfil: ubicar la hoja (`norm_name`) · fila de cabecera
(`headerRow` o búsqueda de la cabecera CLAVE) · cabeceras por `header_key` ·
política de cabeceras desconocidas · reglas por columna · filas tipadas con
sus defaults (el planner los aplica SOLO a entidades nuevas, doc 78 S5)."""
from __future__ import annotations

from ..normalize import clean_text, is_pk_mark, norm_name
from ..parser import ColumnRow, ParsedWorkbook, TableRow, header_key
from ..report import DEFAULT_SHEET_NAMES, SHEET_WORKBOOK, Issue
from ..schemas import RawSheet, UploadWorkbookBody
from .models import FIELDS_BY_ROLE, SHEET_ROLES, key_field
from .rules import check_rules

HEADER_SCAN_ROWS = 30
_ROW_CLASS = {"tables": TableRow, "columns": ColumnRow}


def _find_header(raw: RawSheet, spec: dict, key_hk: str) -> tuple[int | None, bool]:
    """(índice en raw.rows, movida). Primero `headerRow`; si esa fila no trae
    la cabecera clave, se busca en las primeras filas."""
    want = spec.get("headerRow")
    if want is not None:
        for i, r in enumerate(raw.rows):
            if r.row == want:
                if any(header_key(c) == key_hk for c in r.cells):
                    return i, False
                break
    for i, r in enumerate(raw.rows[:HEADER_SCAN_ROWS]):
        if any(header_key(c) == key_hk for c in r.cells):
            return i, want is not None
    return None, False


def apply_profile(body: UploadWorkbookBody, profile: dict, udp_defs: list[dict]) -> ParsedWorkbook:
    defs_by_id = {str(d.get("id")): d for d in udp_defs if d.get("id")}
    sheets_spec = profile.get("sheets") or {}
    parsed = ParsedWorkbook(
        sheet_names={role: clean_text(sheets_spec[role].get("name")) for role in SHEET_ROLES if role in sheets_spec},
        profile_ref={"id": profile.get("id"), "name": profile.get("name")},
    )
    wb_name = DEFAULT_SHEET_NAMES[SHEET_WORKBOOK]
    by_name: dict[str, RawSheet] = {}
    for s in body.sheets:
        by_name.setdefault(norm_name(s.name), s)
    policy = (profile.get("policies") or {}).get("unknownHeaders", "warn")

    for role in SHEET_ROLES:
        spec = sheets_spec.get(role)
        if spec is None:
            continue
        sname = parsed.sheet_names[role]
        raw = by_name.get(norm_name(sname))
        info = {"role": role, "name": sname, "found": raw is not None, "headerRow": None, "rows": 0}
        parsed.sheets_info.append(info)
        if raw is None:
            if spec.get("required", True):
                parsed.issues.append(Issue("error", wb_name, None, None, "missing-sheet",
                                           f"The workbook has no sheet named '{sname}'."))
                parsed.fatal = True
            else:
                parsed.issues.append(Issue("warning", wb_name, None, None, "sheet-skipped",
                                           f"Sheet '{sname}' is not in the workbook — skipped."))
            continue
        setattr(parsed, f"has_{role}_sheet", True)
        mappings = [m for m in (spec.get("mappings") or []) if clean_text(m.get("header"))]
        key_mapping = next((m for m in mappings if (m.get("target") or {}).get("field") == key_field(role)),
                           mappings[0] if mappings else None)
        key_header = clean_text(key_mapping["header"]) if key_mapping else ""
        idx, moved = _find_header(raw, spec, header_key(key_header))
        if idx is None:
            where = f" (expected at row {spec['headerRow']})" if spec.get("headerRow") else ""
            parsed.issues.append(Issue("error", sname, None, key_header or None, "missing-header",
                                       f"Sheet '{sname}' has no header row with '{key_header or '?'}'{where}."))
            parsed.fatal = True
            continue
        header_row = raw.rows[idx]
        info["headerRow"] = header_row.row
        if moved:
            parsed.issues.append(Issue("warning", sname, header_row.row, None, "header-row-moved",
                                       f"Header row expected at row {spec['headerRow']} but found at row {header_row.row}."))
        columns: dict[str, tuple[int, str]] = {}
        for ci, cell in enumerate(header_row.cells):
            hk = header_key(cell)
            if hk and hk not in columns:
                columns[hk] = (ci, clean_text(cell))
        data_rows = [r for r in raw.rows[idx + 1:] if any(clean_text(c) for c in r.cells)]
        info["rows"] = len(data_rows)

        # ── Cabeceras del perfil vs. archivo ───────────────────────────────
        located: list[tuple[dict, int]] = []
        mapped_hks: set[str] = set()
        for m in mappings:
            hk = header_key(m["header"])
            mapped_hks.add(hk)
            target = m.get("target") or {}
            if hk in columns:
                located.append((m, columns[hk][0]))
                if target.get("kind") == "field":
                    parsed.headers.setdefault(role, {})[target["field"]] = clean_text(m["header"])
                continue
            if target.get("kind") == "ignore":
                continue
            fspec = FIELDS_BY_ROLE[role].get(target.get("field") or "") if target.get("kind") == "field" else None
            required = bool(fspec and fspec["required"]) or any(r.get("type") == "required" for r in m.get("rules") or [])
            if required:
                parsed.issues.append(Issue("error", sname, None, clean_text(m["header"]), "missing-header",
                                           f"Sheet '{sname}' is missing the required column '{m['header']}'."))
                parsed.fatal = True
            else:
                parsed.issues.append(Issue("warning", sname, None, clean_text(m["header"]), "header-not-found",
                                           f"Column '{m['header']}' is not in the sheet — its values are left empty."))
        if policy != "ignore":
            for hk, (_, text) in columns.items():
                if hk in mapped_hks:
                    continue
                if policy == "reject":
                    parsed.issues.append(Issue("error", sname, None, text, "unknown-header",
                                               f"Column '{text}' is not in the profile (the profile rejects unknown columns)."))
                    parsed.fatal = True
                else:
                    parsed.issues.append(Issue("warning", sname, None, text, "unknown-header",
                                               f"Column '{text}' is not in the profile and was ignored."))

        # ── Reglas + valores por fila (grafía canónica de allowedValues) ───
        values: dict[int, dict[str, str]] = {r.row: {} for r in data_rows}
        for m, ci in located:
            cells = [(r.row, r.cells[ci] if ci < len(r.cells) else "") for r in data_rows]
            issues, canonical = check_rules(m, cells, role, sname)
            parsed.issues.extend(issues)
            for row, text in cells:
                values[row][m["header"]] = clean_text(canonical.get(row, text))

        # ── Filas tipadas ──────────────────────────────────────────────────
        rows_out = []
        for r in data_rows:
            obj = _ROW_CLASS[role](row=r.row)
            for m, _ in located:
                target = m.get("target") or {}
                text = values[r.row].get(m["header"], "")
                default = clean_text(m.get("defaultValue"))
                if target.get("kind") == "field":
                    fs = FIELDS_BY_ROLE[role][target["field"]]
                    if fs["attr"] == "pk":
                        obj.pk = is_pk_mark(text)
                    else:
                        setattr(obj, fs["attr"], text)
                    if default and not text:
                        obj.defaults[fs["attr"]] = default
                elif target.get("kind") == "udp":
                    obj.udp[m["header"]] = text
                    if default and not text:
                        obj.udp_defaults[m["header"]] = default
            rows_out.append(obj)
        udp_map = {m["header"]: [defs_by_id[u] for u in ((m.get("target") or {}).get("udpIds") or []) if u in defs_by_id]
                   for m, _ in located if (m.get("target") or {}).get("kind") == "udp"}
        if role == "tables":
            parsed.tables, parsed.table_udp = rows_out, udp_map
        else:
            parsed.columns, parsed.column_udp = rows_out, udp_map

    if not parsed.fatal and not parsed.tables and not parsed.columns:
        parsed.issues.append(Issue("error", wb_name, None, None, "empty-workbook",
                                   "The workbook has no data rows in the profile's sheets."))
        parsed.fatal = True
    return parsed
