"""Doc 110 — la subcarpeta de cada XML en Models es el NOMBRE DEL ARCHIVO.

Antes salía del dominio del Mart (`<Locator>`) y sólo sin Locator del archivo:
dos XML de la misma carpeta (DDV) quedaban nombrados con reglas distintas. Ahora
la decide quien nombra el `.xml`, igual que el proyecto de un XML suelto."""
from __future__ import annotations

import json

import pytest

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import migrate
from scripts.erwin_migration import policies as pol
from tests.erwin_migration.test_diagram_style_doc109 import FIXTURE
from tests.erwin_migration.test_subcategory_projection_doc100 import ProjectingDb


@pytest.mark.parametrize("path, want", [
    ("../folder_data/MODELO DDV/DDV - CPYBCA.xml", "DDV - CPYBCA"),
    ("/x/MODELO DDV/Matriz Variables.XML", "Matriz Variables"),
    ("UDV INT FISICO.xml", "UDV INT FISICO"),
    ("/x/MODELO DDV/ Otros .xml", "Otros"),
    ("C:\\data\\MODELO DDV\\Otros V0.214.xml", "Otros V0.214"),
    ("/x/MODELO DDV/.xml", ".xml"),          # sin nombre: no se queda sin carpeta
])
def test_la_subcarpeta_es_el_nombre_del_archivo_sin_extension(path, want):
    assert pol.source_folder_name(path) == want


def test_migrate_nombra_la_subcarpeta_con_el_archivo_aunque_el_xml_traiga_dominio_del_mart(
        tmp_path, monkeypatch):
    xml = FIXTURE.replace(
        "</ModelProps>",
        "</ModelProps>\n  <Locator>erwin://Mart://Mart/BCP/CPYBCA/Modelo Estilos?VNO=3</Locator>", 1)
    path = tmp_path / "Matriz Variables.xml"
    path.write_text(xml, encoding="utf-8")
    # precondición: con la regla anterior la subcarpeta habría sido «CPYBCA»
    assert pol.parse_mart_locator(ep.parse(str(path)).locator)["domain"] == "CPYBCA"

    db = ProjectingDb()
    monkeypatch.setattr("app.core.db.sync.get_sync_db", lambda: db)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    report = tmp_path / "r.json"
    rc = migrate.main([str(path), "--apply", "--force", "--project", "P", "--report", str(report)])

    assert rc == 0
    roots = [f["name"] for f in db.data["folders"].values() if f.get("parentFolderId") is None]
    assert roots == ["Matriz Variables"]
    assert json.loads(report.read_text(encoding="utf-8"))["project"] == "P"
