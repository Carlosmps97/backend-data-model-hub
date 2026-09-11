"""Doc 85: faceta FÍSICA del dominio (nombre, descripción) + UDP por defecto.
Aditivos, round-trip, `''` = volver al derivado / limpiar (se lee como None)."""
from __future__ import annotations

from app.core.facets import DOMAIN_FACET_FIELDS
from app.features.data_standards.schemas import DomainEdit
from app.features.domains.models import ParentDomainDoc
from app.features.domains.schemas import ParentDomainBody


def test_defaults_no_breaking():
    d = ParentDomainDoc.model_validate({"projectId": "p1", "name": "Codigo", "defaultDataType": "VARCHAR(30)"}).model_dump()
    assert (d["physicalName"], d["physicalDescription"], d["udpValues"]) == (None, None, None)


def test_roundtrip_y_blank_se_lee_como_none():
    raw = {"projectId": "p1", "id": "pd1", "name": "Codigo Clave", "defaultDataType": "VARCHAR(30)",
           "physicalName": "CodigoClave", "physicalDescription": "Clave fisica.",
           "udpValues": {"u-log": "No", "u-phys": "No DAC"}, "flgactive": True}
    d = ParentDomainDoc.model_validate(raw).model_dump()
    assert (d["physicalName"], d["physicalDescription"]) == ("CodigoClave", "Clave fisica.")
    assert d["udpValues"] == {"u-log": "No", "u-phys": "No DAC"}
    assert "flgactive" not in d
    blank = ParentDomainDoc.model_validate({**raw, "physicalName": "", "physicalDescription": "  "}).model_dump()
    assert (blank["physicalName"], blank["physicalDescription"]) == (None, None)


def test_body_y_edit_exponen_los_campos_y_conservan_blank():
    b = ParentDomainBody.model_validate({"name": "X", "defaultDataType": "INT", "physicalName": "X_F", "udpValues": {}})
    assert (b.physicalName, b.udpValues, b.physicalDescription) == ("X_F", {}, None)
    e = DomainEdit.model_validate({"name": "X", "defaultDataType": "INT"})
    assert (e.physicalName, e.physicalDescription, e.udpValues) == (None, None, None)
    # '' viaja tal cual (exclude_none NO lo descarta): así un update vuelve al derivado
    assert DomainEdit.model_validate({"name": "X", "defaultDataType": "INT", "physicalName": ""}).physicalName == ""
    assert ParentDomainBody.model_validate({"name": "X", "defaultDataType": "INT", "physicalDescription": ""}).physicalDescription == ""


def test_contrato_de_facetas_del_dominio():
    assert set(DOMAIN_FACET_FIELDS["logical"]) == {"name", "logicalDataType", "description"}
    assert set(DOMAIN_FACET_FIELDS["physical"]) == {"physicalName", "defaultDataType", "physicalDescription"}
