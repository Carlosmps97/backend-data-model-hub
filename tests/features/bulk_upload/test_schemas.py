"""DTOs del body de la carga masiva (doc 55 §7)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.bulk_upload.schemas import MAX_COLUMN_ROWS, MAX_TABLE_ROWS, UploadWorkbookBody


def test_body_minimo_y_defaults():
    body = UploadWorkbookBody.model_validate({"sheets": {"tables": {"headers": ["TABLA_LOGICO"],
                                                                   "rows": [{"row": 3, "cells": {"TABLA_LOGICO": "A"}}]}}})
    assert body.fileName == "workbook.xlsx"
    assert body.sheets.columns is None
    assert body.sheets.tables.rows[0].cells == {"TABLA_LOGICO": "A"}


def test_fila_debe_ser_positiva():
    with pytest.raises(ValidationError):
        UploadWorkbookBody.model_validate({"sheets": {"tables": {"headers": [], "rows": [{"row": 0, "cells": {}}]}}})


def test_topes_de_filas_son_los_del_diseno():
    assert (MAX_TABLE_ROWS, MAX_COLUMN_ROWS) == (5000, 20000)
