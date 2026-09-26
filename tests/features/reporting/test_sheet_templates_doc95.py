"""Doc 95 D11: plantillas de hoja Excel del Reporting — un formato FIJO guardado
como dato (nombre de hoja + columnas encabezado → dato), por proyecto."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.db import client as db_client
from app.core.identity.models import Principal
from app.core.scope import PROJECT_SCOPED
from app.features.reporting.sheet_templates import service
from app.features.reporting.sheet_templates.builtin import BUILTIN_ORIGIN, QA_MODELO
from app.features.reporting.sheet_templates.models import SheetTemplateBody
from app.features.reporting.sheet_templates.router import _actor
from app.main import app
from tests.support.fakedb import FakeDb

BODY = {"name": "Mi formato", "sheetName": "HOJA_1",
        "columns": [{"header": "TABLA", "source": "table.physicalName"},
                    {"header": "CLASIF", "source": "column.udp:Clasificacion del Dato"}]}


def test_body_valido_y_qa_modelo_valido():
    assert SheetTemplateBody.model_validate(BODY).columns[1].source == "column.udp:Clasificacion del Dato"
    qa = SheetTemplateBody.model_validate(QA_MODELO)
    assert qa.sheetName == "QA_MODELO" and len(qa.columns) == 14
    assert [c.header for c in qa.columns][:4] == ["DATABASE", "TABLA_FISICA", "ORDEN_FISICO", "CAMPO_FISICO"]


@pytest.mark.parametrize("patch, msg", [
    ({"sheetName": "A/B"}, "sheet name"),
    ({"sheetName": "X" * 32}, "at most 31"),
    ({"columns": []}, "at least 1"),
    ({"columns": [{"header": "A", "source": "table.x"}, {"header": "a", "source": "column.y"}]}, "repeated"),
    ({"columns": [{"header": "A", "source": "tabla.x"}]}, "Unknown data source"),
    ({"columns": [{"header": "  ", "source": "table.x"}]}, "header"),
])
def test_body_invalido(patch, msg):
    with pytest.raises(ValidationError) as exc:
        SheetTemplateBody.model_validate({**BODY, **patch})
    assert msg in str(exc.value)


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    return fake


def _body(**over) -> SheetTemplateBody:
    return SheetTemplateBody.model_validate({**BODY, **over})


def _run(coro):
    return asyncio.run(coro)


def test_nombre_repetido_entre_las_que_ve_quien_escribe(db):
    _run(service.create_template("ana", "p1", _body(name="Formato")))            # privada de ana
    _run(service.create_template("luis", "p1", _body(name="formato")))           # luis no la ve: vale
    _run(service.create_template("ana", "p1", _body(name="Común", shared=True)))
    with pytest.raises(service.TemplateNameTaken):
        _run(service.create_template("luis", "p1", _body(name="COMÚN")))          # la ve: 409


def test_update_puede_conservar_su_propio_nombre(db):
    t = _run(service.create_template("ana", "p1", _body()))
    out = _run(service.update_template("ana", False, "p1", t["id"], _body()))
    assert out["name"] == "Mi formato" and out["updatedBy"] == "ana"


# ── Final review #1 · spec D11: «dueño + compartida, como los saved reports» ──

def test_cada_uno_ve_las_suyas_y_las_compartidas_del_proyecto(db):
    _run(service.create_template("ana", "p1", _body(name="Privada de ana")))
    _run(service.create_template("ana", "p1", _body(name="Compartida de ana", shared=True)))
    _run(service.create_template("luis", "p1", _body(name="De luis")))
    _run(service.create_template("ana", "p2", _body(name="Otro proyecto", shared=True)))

    def names(user):
        return [t["name"] for t in _run(service.list_templates("p1", user))]

    assert names("ana") == ["Compartida de ana", "Privada de ana"]
    assert names("luis") == ["Compartida de ana", "De luis"]
    comp = _run(service.list_templates("p1", "luis"))[0]
    assert comp["owner"] == "ana" and comp["shared"] is True


def test_solo_el_dueno_edita_y_borra(db):
    t = _run(service.create_template("ana", "p1", _body(shared=True)))
    assert _run(service.update_template("luis", False, "p1", t["id"], _body(name="Pisada"))) is None
    assert _run(service.delete_template("luis", False, "p1", t["id"])) is False
    out = _run(service.update_template("ana", False, "p1", t["id"], _body(name="Renombrada", shared=False)))
    assert out["name"] == "Renombrada" and out["shared"] is False and out["owner"] == "ana"
    assert _run(service.delete_template("ana", False, "p1", t["id"])) is True


def test_admin_gobierna_las_compartidas_y_siguen_compartidas(db):
    qa = _run(service.create_default("system", "p1"))
    assert qa["origin"] == BUILTIN_ORIGIN and qa["owner"] == "system" and qa["shared"] is True
    out = _run(service.update_template("jefa", True, "p1", qa["id"], _body(name="QA_MODELO", shared=False)))
    assert out["shared"] is True and out["owner"] == "system" and out["updatedBy"] == "jefa"
    privada = _run(service.create_template("ana", "p1", _body(name="Solo mía")))
    assert _run(service.update_template("jefa", True, "p1", privada["id"], _body())) is None
    assert _run(service.delete_template("jefa", True, "p1", privada["id"])) is False


@pytest.mark.parametrize("tpl, user, admin, ok", [
    ({"owner": "ana", "shared": False}, "ana", False, True),      # la dueña
    ({"owner": "ana", "shared": False}, "luis", False, False),    # privada ajena
    ({"owner": "ana", "shared": True}, "luis", False, False),     # compartida ajena: solo la usa
    ({"owner": "system", "shared": True}, "jefa", True, True),    # admin: las compartidas (QA_MODELO)
    ({"owner": "ana", "shared": False}, "jefa", True, False),     # ni el admin toca una privada ajena
])
def test_can_edit(tpl, user, admin, ok):
    assert service.can_edit(tpl, user, admin) is ok


def test_create_default_siembra_una_sola_vez_aunque_no_se_vea(db):
    qa = _run(service.create_default("ana", "p1"))
    _run(service.update_template("ana", False, "p1", qa["id"], _body(name="QA_MODELO", shared=False)))
    assert _run(service.create_default("luis", "p1")) is None      # luis no la ve, pero el proyecto ya la tiene


def test_coleccion_con_alcance_de_proyecto():
    assert "sheet_templates" in PROJECT_SCOPED


@pytest.fixture
def api(project_client):
    app.dependency_overrides[_actor] = lambda: ("ana", False)
    try:
        yield project_client
    finally:
        app.dependency_overrides.pop(_actor, None)


def test_actor_escribe_cualquiera_en_sesion_y_admin_es_admin_manage():
    p = Principal(email="ana@x.pe", username="ana", display_name="Ana", source="local")
    assert _run(_actor(p, {"permissions": {"admin.manage": True}})) == ("ana", True)
    assert _run(_actor(p, {"permissions": {"model.edit": True}})) == ("ana", False)
    assert _run(_actor(p, None)) == ("ana", False)


def test_rutas_crud_y_default(api, monkeypatch):
    monkeypatch.setattr(service, "list_templates", AsyncMock(return_value=[]))
    assert api.get("/api/projects/p1/sheet-templates").json()["data"] == []
    monkeypatch.setattr(service, "create_default", AsyncMock(return_value=None))
    assert api.post("/api/projects/p1/sheet-templates/default").status_code == 409
    monkeypatch.setattr(service, "create_template", AsyncMock(side_effect=service.TemplateNameTaken("x")))
    assert api.post("/api/projects/p1/sheet-templates", json=BODY).status_code == 409
    assert api.post("/api/projects/p1/sheet-templates", json={**BODY, "sheetName": "A/B"}).status_code == 422
    monkeypatch.setattr(service, "update_template", AsyncMock(return_value=None))
    assert api.put("/api/projects/p1/sheet-templates/zz", json=BODY).status_code == 404
    monkeypatch.setattr(service, "delete_template", AsyncMock(return_value=False))
    assert api.delete("/api/projects/p1/sheet-templates/zz").status_code == 404
