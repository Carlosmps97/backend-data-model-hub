"""Derivación de una columna canónica: físico + dataType heredado del dominio."""
from __future__ import annotations

from app.features.catalog.service import derive_column


def test_derive_hereda_tipo_del_dominio():
    d = derive_column(logical="monto deuda", physical="MTO_DEU",
                      domain_default="decimal(24,4)", override=None)
    assert d == {"physicalName": "MTO_DEU", "dataType": "decimal(24,4)", "typeOverridden": False}


def test_derive_respeta_override_manual():
    d = derive_column(logical="monto", physical="MTO",
                      domain_default="decimal(24,4)", override="decimal(10,2)")
    assert d == {"physicalName": "MTO", "dataType": "decimal(10,2)", "typeOverridden": True}
