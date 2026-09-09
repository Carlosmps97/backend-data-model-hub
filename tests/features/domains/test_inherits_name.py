"""Doc 79: `inheritsName` aditivo en ParentDomain (invariante de persistencia).

Marca el dominio "atributo estándar" (Erwin `Attribute_Definition`) que hereda
nombre + definición al atributo al asignarse. Default False = no-breaking; el
body/DomainEdit lo exponen como `bool | None` para que un edit que lo OMITA no
lo pise (exclude_none) — solo cuando se envía explícito se actualiza.
"""
from __future__ import annotations

from app.core.facets import DOMAIN_FACET_FIELDS
from app.features.data_standards.schemas import DomainEdit
from app.features.domains.models import ParentDomainDoc
from app.features.domains.schemas import ParentDomainBody


def test_inherits_name_default_false():
    d = ParentDomainDoc.model_validate({"projectId": "p1", "name": "Codigo", "defaultDataType": "VARCHAR(20)"})
    assert d.model_dump()["inheritsName"] is False


def test_inherits_name_roundtrips():
    raw = {"projectId": "p1", "id": "pd1", "name": "FecRutina", "defaultDataType": "DATE",
           "inheritsName": True, "flgactive": True}
    dumped = ParentDomainDoc.model_validate(raw).model_dump()
    assert dumped["inheritsName"] is True
    assert "flgactive" not in dumped


def test_body_and_edit_expose_inherits_name_optional():
    b = ParentDomainBody.model_validate({"name": "FecRutina", "defaultDataType": "DATE", "inheritsName": True})
    assert b.inheritsName is True
    # omitido = None ⇒ exclude_none lo descarta ⇒ el update NO pisa el flag vigente
    assert ParentDomainBody.model_validate({"name": "X", "defaultDataType": "INT"}).inheritsName is None
    assert DomainEdit.model_validate({"name": "X", "defaultDataType": "INT"}).inheritsName is None


def test_inherits_name_es_campo_compartido_de_faceta():
    assert "inheritsName" in DOMAIN_FACET_FIELDS["shared"]
