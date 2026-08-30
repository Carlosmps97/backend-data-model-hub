"""Parser del workbook (doc 55 §4): del JSON por hoja que manda el front a
filas tipadas. Cabeceras tolerantes (caso/espacios/tildes), UDP por prefijo,
hojas/cabeceras faltantes → issues. Puro."""
from __future__ import annotations

from app.features.bulk_upload.parser import parse_workbook
from app.features.bulk_upload.schemas import Sheet, SheetRow, UploadSheets, UploadWorkbookBody


def _body(tables: Sheet | None = None, columns: Sheet | None = None) -> UploadWorkbookBody:
    return UploadWorkbookBody(fileName="x.xlsx", sheets=UploadSheets(tables=tables, columns=columns))


def _sheet(headers: list[str], *rows: dict, start: int = 3) -> Sheet:
    return Sheet(headers=headers, rows=[SheetRow(row=start + i, cells=r) for i, r in enumerate(rows)])


def _codes(issues, severity=None):
    return [i.code for i in issues if severity is None or i.severity == severity]


def test_detecta_cabeceras_con_espacios_minusculas_y_tildes():
    body = _body(tables=_sheet(["Project", "tabla lógico", "TABLA_FISICA", "Esquema"],
                               {"Project": "P1", "tabla lógico": "Cliente", "TABLA_FISICA": "", "Esquema": "ddv"}))
    parsed = parse_workbook(body)
    assert parsed.issues == []
    (t,) = parsed.tables
    assert (t.row, t.project, t.logical, t.physical, t.schema) == (3, "P1", "Cliente", "", "ddv")


def test_udp_headers_se_conservan_con_su_texto_original():
    body = _body(tables=_sheet(["TABLA_LOGICO", "UDP_Tipo_Vista", "UDP_Universal"],
                               {"TABLA_LOGICO": "Cliente", "UDP_Tipo_Vista": "Regular", "UDP_Universal": ""}))
    parsed = parse_workbook(body)
    assert parsed.table_udp_headers == ["UDP_Tipo_Vista", "UDP_Universal"]
    assert parsed.tables[0].udp == {"UDP_Tipo_Vista": "Regular", "UDP_Universal": ""}


def test_cabecera_desconocida_es_warning_y_se_ignora():
    body = _body(tables=_sheet(["TABLA_LOGICO", "COMENTARIO"], {"TABLA_LOGICO": "A", "COMENTARIO": "x"}))
    parsed = parse_workbook(body)
    (w,) = parsed.issues
    assert (w.severity, w.sheet, w.code, w.column, w.row) == ("warning", "Tablas", "unknown-header", "COMENTARIO", None)
    assert parsed.tables[0].logical == "A"


def test_tablas_sin_tabla_logico_es_error_y_no_parsea_filas():
    body = _body(tables=_sheet(["PROJECT", "TABLA_FISICA"], {"PROJECT": "P", "TABLA_FISICA": "T"}))
    parsed = parse_workbook(body)
    assert _codes(parsed.issues, "error") == ["missing-header"]
    assert parsed.issues[0].column == "TABLA_LOGICO"
    assert parsed.tables == []


def test_atributos_exige_tabla_y_campo_logico():
    body = _body(columns=_sheet(["TABLA_LOGICO", "CAMPO_FISICO"], {"TABLA_LOGICO": "A", "CAMPO_FISICO": "X"}))
    parsed = parse_workbook(body)
    assert _codes(parsed.issues, "error") == ["missing-header"]
    assert parsed.issues[0].sheet == "Atributos" and parsed.issues[0].column == "CAMPO_LOGICO"


def test_sin_hojas_es_error():
    parsed = parse_workbook(_body())
    assert _codes(parsed.issues) == ["missing-sheet"]
    assert parsed.has_tables_sheet is False and parsed.has_columns_sheet is False


def test_hojas_presentes_pero_vacias_es_error():
    parsed = parse_workbook(_body(tables=Sheet(headers=["TABLA_LOGICO"]), columns=Sheet(headers=["TABLA_LOGICO", "CAMPO_LOGICO"])))
    assert _codes(parsed.issues) == ["empty-workbook"]


def test_solo_atributos_es_valido():
    body = _body(columns=_sheet(["TABLA_LOGICO", "CAMPO_LOGICO"], {"TABLA_LOGICO": "A", "CAMPO_LOGICO": "c"}))
    parsed = parse_workbook(body)
    assert parsed.issues == []
    assert parsed.has_tables_sheet is False and parsed.has_columns_sheet is True
    assert parsed.columns[0].table_logical == "A"


def test_filas_completamente_vacias_se_saltan():
    body = _body(tables=_sheet(["TABLA_LOGICO", "DEF_TABLA"],
                               {"TABLA_LOGICO": "A"}, {"TABLA_LOGICO": "  ", "DEF_TABLA": ""}, {"TABLA_LOGICO": "B"}))
    parsed = parse_workbook(body)
    assert [t.row for t in parsed.tables] == [3, 5]


def test_definiciones_conservan_saltos_y_recortan_borde():
    body = _body(tables=_sheet(["TABLA_LOGICO", "DEF_TABLA"], {"TABLA_LOGICO": "A", "DEF_TABLA": "  año\r\nñ "}))
    assert parse_workbook(body).tables[0].description == "año\nñ"


def test_columna_completa_con_pk():
    body = _body(columns=_sheet(
        ["TABLA_LOGICO", "CAMPO_LOGICO", "CAMPO_FISICO", "DEF_ATRIBUTO", "PARENT_DOMAIN", "TIPO_DATO", "UDP_Particion", "PK"],
        {"TABLA_LOGICO": "A", "CAMPO_LOGICO": "Codigo", "CAMPO_FISICO": "COD", "DEF_ATRIBUTO": "d",
         "PARENT_DOMAIN": "Codigo", "TIPO_DATO": "string", "UDP_Particion": "PART_01", "PK": "X"},
        {"TABLA_LOGICO": "A", "CAMPO_LOGICO": "Otro", "PK": ""},
    ))
    parsed = parse_workbook(body)
    c1, c2 = parsed.columns
    assert (c1.table_logical, c1.logical, c1.physical, c1.description, c1.domain, c1.data_type, c1.pk) == \
        ("A", "Codigo", "COD", "d", "Codigo", "string", True)
    assert c1.udp == {"UDP_Particion": "PART_01"}
    assert c2.pk is False and c2.physical == ""
    assert parsed.column_udp_headers == ["UDP_Particion"]
