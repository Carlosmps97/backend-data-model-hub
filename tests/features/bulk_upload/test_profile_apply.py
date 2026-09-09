"""Doc 78 §5: hoja por nombre, fila de cabecera (explícita / movida / ausente),
cabeceras por header_key, políticas de cabeceras desconocidas, ignore, mapeo a
campos y a varios UDP, defaults, reglas."""
from __future__ import annotations

from app.features.bulk_upload.profiles.apply import apply_profile
from app.features.bulk_upload.schemas import RawRow, RawSheet, UploadWorkbookBody

DEFS = [{"id": "t-p", "name": "Tipo de Entidad", "level": "table", "view": "physical", "dataType": "list", "allowedValues": ["No Definido", "Sub-Tipo"]},
        {"id": "t-l", "name": "Tipo de Entidad", "level": "table", "view": "logical", "dataType": "list", "allowedValues": ["No Definido", "Sub-Tipo"]},
        {"id": "c-p", "name": "Particion", "level": "column", "view": "physical", "dataType": "list", "allowedValues": ["No Definido", "PART_01"]}]

PROFILE = {"id": "pf", "name": "P", "sheets": {
    "tables": {"name": "Cargar_Tablas", "required": True, "headerRow": 5, "mappings": [
        {"header": "ESQUEMA", "target": {"kind": "field", "field": "schema"}, "defaultValue": "bcp_ddv"},
        {"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "logicalName"}, "rules": [{"type": "required"}]},
        {"header": "UDP_Tipo_de_Entidad", "target": {"kind": "udp", "udpIds": ["t-p", "t-l"]}, "defaultValue": "Sub-Tipo"},
        {"header": "LOGICO", "target": {"kind": "ignore"}}]},
    "columns": {"name": "Cargar_Campos", "required": False, "headerRow": 5, "mappings": [
        {"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "tableRef"}},
        {"header": "CAMPO_LOGICO", "target": {"kind": "field", "field": "logicalName"}},
        {"header": "PK", "target": {"kind": "field", "field": "pk"}},
        {"header": "UDP Particion", "target": {"kind": "udp", "udpIds": ["c-p"]}}]}},
    "policies": {"onExistingTable": "update", "onExistingColumn": "update", "unknownHeaders": "warn"}}


def _sheet(name, header_row, headers, *rows):
    out = [RawRow(row=4, cells=["", "(help)"]), RawRow(row=header_row, cells=[""] + headers)]
    for i, r in enumerate(rows):
        out.append(RawRow(row=header_row + 1 + i, cells=[""] + list(r)))
    return RawSheet(name=name, rows=out)


def _body(*sheets):
    return UploadWorkbookBody(profileId="pf", sheets=list(sheets))


def _codes(parsed):
    return [(i.severity, i.code, i.sheet, i.row, i.column) for i in parsed.issues]


def test_mapea_campos_udp_defaults_e_ignora_control():
    body = _body(_sheet("cargar_tablas", 5, ["ESQUEMA", "TABLA_LOGICO", "UDP_Tipo_de_Entidad", "LOGICO", "EXTRA"],
                        ["", "Cliente", "sub-tipo", "7", "x"]),
                 _sheet("Cargar_Campos", 5, ["TABLA_LOGICO", "CAMPO_LOGICO", "PK", "UDP Particion"], ["Cliente", "Codigo", "X", "PART_01"]))
    parsed = apply_profile(body, PROFILE, DEFS)
    assert parsed.fatal is False and (parsed.has_tables_sheet, parsed.has_columns_sheet) == (True, True)
    (t,) = parsed.tables
    assert (t.row, t.schema, t.logical, t.udp, t.defaults, t.udp_defaults) == (
        6, "", "Cliente", {"UDP_Tipo_de_Entidad": "sub-tipo"}, {"schema": "bcp_ddv"}, {})
    assert [d["id"] for d in parsed.table_udp["UDP_Tipo_de_Entidad"]] == ["t-p", "t-l"]
    (c,) = parsed.columns
    assert (c.table_logical, c.logical, c.pk, c.udp) == ("Cliente", "Codigo", True, {"UDP Particion": "PART_01"})
    assert parsed.headers["tables"]["schema"] == "ESQUEMA" and parsed.headers["columns"]["tableRef"] == "TABLA_LOGICO"
    assert parsed.sheet_names == {"tables": "Cargar_Tablas", "columns": "Cargar_Campos"}
    assert _codes(parsed) == [("warning", "unknown-header", "Cargar_Tablas", None, "EXTRA")]
    assert parsed.sheets_info[0] == {"role": "tables", "name": "Cargar_Tablas", "found": True, "headerRow": 5, "rows": 1}
    assert parsed.profile_ref == {"id": "pf", "name": "P"}


def test_udp_default_solo_cuando_la_celda_viene_vacia():
    body = _body(_sheet("Cargar_Tablas", 5, ["ESQUEMA", "TABLA_LOGICO", "UDP_Tipo_de_Entidad"], ["ddv", "Cliente", ""]))
    (t,) = apply_profile(body, PROFILE, DEFS).tables
    assert t.udp == {"UDP_Tipo_de_Entidad": ""} and t.udp_defaults == {"UDP_Tipo_de_Entidad": "Sub-Tipo"} and t.defaults == {}


def test_hoja_requerida_ausente_es_fatal_y_opcional_solo_avisa():
    parsed = apply_profile(_body(_sheet("Cargar_Campos", 5, ["TABLA_LOGICO", "CAMPO_LOGICO"], ["A", "B"])), PROFILE, DEFS)
    assert parsed.fatal is True and ("error", "missing-sheet", "Workbook", None, None) in _codes(parsed)
    assert parsed.sheets_info[0]["found"] is False
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 5, ["ESQUEMA", "TABLA_LOGICO"], ["s", "A"])), PROFILE, DEFS)
    assert parsed.fatal is False and ("warning", "sheet-skipped", "Workbook", None, None) in _codes(parsed)
    assert parsed.has_columns_sheet is False


def test_fila_de_cabecera_movida_y_ausente():
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 7, ["ESQUEMA", "TABLA_LOGICO"], ["s", "A"])), PROFILE, DEFS)
    assert ("warning", "header-row-moved", "Cargar_Tablas", 7, None) in _codes(parsed) and parsed.tables[0].row == 8
    assert parsed.sheets_info[0]["headerRow"] == 7
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 5, ["ESQUEMA", "OTRA"], ["s", "A"])), PROFILE, DEFS)
    assert parsed.fatal is True and ("error", "missing-header", "Cargar_Tablas", None, "TABLA_LOGICO") in _codes(parsed)


def test_header_row_null_solo_busca():
    prof = {**PROFILE, "sheets": {**PROFILE["sheets"], "tables": {**PROFILE["sheets"]["tables"], "headerRow": None}}}
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 9, ["ESQUEMA", "TABLA_LOGICO"], ["s", "A"])), prof, DEFS)
    assert parsed.tables[0].row == 10 and not any(c[1] == "header-row-moved" for c in _codes(parsed))


def test_cabecera_opcional_ausente_avisa_y_politicas_de_desconocidas():
    reject = {**PROFILE, "policies": {**PROFILE["policies"], "unknownHeaders": "reject"}}
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 5, ["TABLA_LOGICO", "EXTRA"], ["A", "x"])), reject, DEFS)
    assert ("warning", "header-not-found", "Cargar_Tablas", None, "ESQUEMA") in _codes(parsed)
    assert ("warning", "header-not-found", "Cargar_Tablas", None, "UDP_Tipo_de_Entidad") in _codes(parsed)
    assert ("error", "unknown-header", "Cargar_Tablas", None, "EXTRA") in _codes(parsed) and parsed.fatal
    assert not any(c[4] == "LOGICO" for c in _codes(parsed))          # ignore ausente: silencio
    ignore = {**PROFILE, "policies": {**PROFILE["policies"], "unknownHeaders": "ignore"}}
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 5, ["TABLA_LOGICO", "EXTRA"], ["A", "x"])), ignore, DEFS)
    assert not any(c[1] == "unknown-header" for c in _codes(parsed)) and parsed.fatal is False


def test_cabecera_obligatoria_por_regla_required_ausente_es_fatal():
    parsed = apply_profile(_body(_sheet("Cargar_Campos", 5, ["TABLA_LOGICO", "PK"], ["A", "X"]),
                                 _sheet("Cargar_Tablas", 5, ["TABLA_LOGICO"], ["A"])), PROFILE, DEFS)
    assert ("error", "missing-header", "Cargar_Campos", None, "CAMPO_LOGICO") in _codes(parsed) and parsed.fatal


def test_reglas_corren_sobre_las_celdas_y_canonizan():
    prof = {**PROFILE, "sheets": {**PROFILE["sheets"], "tables": {**PROFILE["sheets"]["tables"], "mappings": [
        {"header": "ESQUEMA", "target": {"kind": "field", "field": "schema"}, "rules": [{"type": "allowedValues", "value": ["bcp_ddv"]}]},
        {"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "logicalName"}, "rules": [{"type": "required"}, {"type": "uniqueInFile"}]}]}}}
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 5, ["ESQUEMA", "TABLA_LOGICO"], ["BCP_DDV", "A"], ["x", ""], ["bcp_ddv", "a"])), prof, DEFS)
    assert parsed.tables[0].schema == "bcp_ddv"
    assert ("error", "rule-allowed-values", "Cargar_Tablas", 7, "ESQUEMA") in _codes(parsed)
    assert ("error", "rule-required", "Cargar_Tablas", 7, "TABLA_LOGICO") in _codes(parsed)
    assert ("error", "rule-unique", "Cargar_Tablas", 8, "TABLA_LOGICO") in _codes(parsed)
    assert parsed.fatal is False


def test_workbook_sin_filas_es_fatal_y_filas_cortas_no_revientan():
    parsed = apply_profile(_body(_sheet("Cargar_Tablas", 5, ["ESQUEMA", "TABLA_LOGICO"])), PROFILE, DEFS)
    assert parsed.fatal is True and any(c[1] == "empty-workbook" for c in _codes(parsed))
    corta = RawSheet(name="Cargar_Tablas", rows=[RawRow(row=5, cells=["", "ESQUEMA", "TABLA_LOGICO"]), RawRow(row=6, cells=["", "ddv"])])
    parsed = apply_profile(_body(corta), PROFILE, DEFS)
    assert parsed.tables[0].logical == "" and parsed.tables[0].schema == "ddv"
