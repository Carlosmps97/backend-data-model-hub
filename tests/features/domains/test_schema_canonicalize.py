"""Doc 62: los DTOs que escriben `defaultDataType` homologan la grafía al
catálogo de la plataforma EN EL BORDE (validator Pydantic) — un dominio nunca
vuelve a quedar con `Array` / `BIG INTEGER` / `DECIMAL (22,4)` al guardarse
por el router de domains ni por el apply de Data Standards."""
from __future__ import annotations

from app.features.data_standards.schemas import DomainEdit
from app.features.domains.schemas import ParentDomainBody


def test_parent_domain_body_canonicaliza_el_default():
    assert ParentDomainBody(name="ColArray", defaultDataType="Array").defaultDataType == "ARRAY<>"
    assert ParentDomainBody(name="BigInt", defaultDataType="BIG INTEGER").defaultDataType == "BIGINT"
    assert ParentDomainBody(name="Monto", defaultDataType="DECIMAL (22,4)").defaultDataType == "DECIMAL(22,4)"


def test_domain_edit_canonicaliza_el_default():
    assert DomainEdit(name="ColArray", defaultDataType="Array").defaultDataType == "ARRAY<>"
    assert DomainEdit(name="Rate", defaultDataType="NUMBER(10,6)").defaultDataType == "NUMBER(10,6)"


def test_lo_desconocido_queda_verbatim():
    assert DomainEdit(name="X", defaultDataType="Tipo Raro").defaultDataType == "Tipo Raro"
