"""DTOs del body de la carga masiva (doc 78 §5): hojas crudas + perfil."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.bulk_upload.schemas import MAX_CELLS_PER_ROW, MAX_ROWS_PER_SHEET, MAX_SHEETS, UploadWorkbookBody


def test_body_crudo_por_hoja():
    b = UploadWorkbookBody.model_validate({"profileId": "pf", "sheets": [
        {"name": "Cargar_Tablas", "rows": [{"row": 5, "cells": ["", "SUBJECT", "TABLA_LOGICO"]}]}]})
    assert b.fileName == "workbook.xlsx" and b.profileId == "pf"
    assert b.sheets[0].name == "Cargar_Tablas" and b.sheets[0].rows[0].cells[1] == "SUBJECT"


def test_profile_id_es_obligatorio_y_fila_positiva():
    with pytest.raises(ValidationError):
        UploadWorkbookBody.model_validate({"sheets": []})
    with pytest.raises(ValidationError):
        UploadWorkbookBody.model_validate({"profileId": "pf", "sheets": [{"name": "x", "rows": [{"row": 0, "cells": []}]}]})


def test_topes_son_los_del_diseno():
    assert (MAX_SHEETS, MAX_ROWS_PER_SHEET, MAX_CELLS_PER_ROW) == (30, 20000, 200)
