"""Doc 102: nombre del archivo de las plantillas de hoja Excel — marcadores de
fecha/hora como dato; la QA_MODELO del one-shot sale `QA_REPORTE_…`."""
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.core.db import client as db_client
from app.features.reporting.sheet_templates import service
from app.features.reporting.sheet_templates.builtin import QA_MODELO
from app.features.reporting.sheet_templates.models import SheetTemplateBody, clean_file_name
from scripts.seed_sheet_templates import plan_lines
from tests.support.fakedb import FakeDb

BODY = {"name": "Mi formato", "sheetName": "HOJA_1",
        "columns": [{"header": "TABLA", "source": "table.physicalName"}]}
QA_PATTERN = "QA_REPORTE_{yyyy}-{MM}-{dd} {HH}{mm}{ss}"


@pytest.mark.parametrize("raw, clean", [
    (None, None), ("", None), ("   ", None), (".xlsx", None), (" .XLSX ", None),
    (f"  {QA_PATTERN}.xlsx ", QA_PATTERN), ("Reporte {yyyy}", "Reporte {yyyy}"),
    ("SIN_MARCADORES", "SIN_MARCADORES"),
])
def test_clean_file_name_normaliza(raw, clean):
    assert clean_file_name(raw) == clean


@pytest.mark.parametrize("raw, msg", [
    ("A/B", "can't contain"), ('A"B', "can't contain"), ("A:B", "can't contain"),
    ("x" * 121, "at most 120"),
    ("QA_{fecha}", "Unknown placeholder {fecha}"), ("QA_{YYYY}", "Unknown placeholder {YYYY}"),
    ("QA_{yyyy", "Unbalanced braces"), ("QA_}", "Unbalanced braces"),
])
def test_file_name_invalido_es_error_de_forma(raw, msg):
    with pytest.raises(ValidationError) as exc:
        SheetTemplateBody.model_validate({**BODY, "fileName": raw})
    assert msg in str(exc.value)


def test_el_body_sin_file_name_sigue_valido():
    assert SheetTemplateBody.model_validate(BODY).fileName is None


def test_qa_modelo_trae_el_nombre_qa_reporte():
    assert SheetTemplateBody.model_validate(QA_MODELO).fileName == QA_PATTERN


def test_el_plan_del_seed_muestra_el_archivo():
    assert any(f"{QA_PATTERN}.xlsx" in line for line in plan_lines(QA_MODELO))


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    return fake


def test_create_default_persiste_el_nombre_y_la_edicion_lo_cambia_o_lo_quita(db):
    doc = asyncio.run(service.create_default("system", "p1"))
    assert doc["fileName"] == QA_PATTERN
    (listed,) = asyncio.run(service.list_templates("p1", "ana"))
    assert listed["fileName"] == QA_PATTERN
    otro = SheetTemplateBody.model_validate({**QA_MODELO, "shared": True, "fileName": "OTRO_{yyyy}"})
    assert asyncio.run(service.update_template("jefa", True, "p1", doc["id"], otro))["fileName"] == "OTRO_{yyyy}"
    vacio = SheetTemplateBody.model_validate({**QA_MODELO, "shared": True, "fileName": "  "})
    assert asyncio.run(service.update_template("jefa", True, "p1", doc["id"], vacio))["fileName"] is None
