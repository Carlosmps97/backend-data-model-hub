"""Doc 69 §4.4: el dominio lleva su tipo FÍSICO (`defaultDataType`, lo que el
DDL necesita) y su tipo LÓGICO (`logicalDataType`); la cascada respeta el
override de CADA faceta por separado."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.features.domains import repository, service
from app.features.domains.models import ParentDomainDoc
from app.features.domains.schemas import ParentDomainBody
from app.features.domains.service import cascade_filter, logical_cascade_filter


def test_doc_default_none_y_roundtrip():
    assert ParentDomainDoc.model_validate({"projectId": "p1", "name": "Codigo", "defaultDataType": "VARCHAR(30)"}).model_dump()["logicalDataType"] is None
    d = ParentDomainDoc.model_validate({"projectId": "p1", "name": "Codigo", "defaultDataType": "VARCHAR(30)", "logicalDataType": "VARCHAR(20)"})
    assert d.model_dump()["logicalDataType"] == "VARCHAR(20)"


def test_body_canonicaliza_el_tipo_logico_como_el_fisico():
    b = ParentDomainBody.model_validate({"name": "Monto", "defaultDataType": "decimal (21,4)", "logicalDataType": "decimal (22,4)"})
    assert (b.defaultDataType, b.logicalDataType) == ("DECIMAL(21,4)", "DECIMAL(22,4)")
    assert ParentDomainBody.model_validate({"name": "X", "defaultDataType": "INT", "logicalDataType": ""}).logicalDataType is None


def test_logical_cascade_filter_usa_su_propio_override():
    assert logical_cascade_filter("pd") == {"parentDomainId": "pd", "logicalTypeOverridden": {"$ne": True},
                                            "flgactive": {"$ne": False}}
    assert cascade_filter("pd")["typeOverridden"] == {"$ne": True}   # la física no cambia


def test_propagate_aplica_ambas_facetas(monkeypatch):
    monkeypatch.setattr(service.repository, "get_domain_types", AsyncMock(return_value=("VARCHAR(30)", "VARCHAR(20)")))
    monkeypatch.setattr(service.repository, "propagate_type", AsyncMock(return_value=7))
    monkeypatch.setattr(service.repository, "propagate_field", AsyncMock(return_value=5))
    out = asyncio.run(service.propagate("pd"))
    assert out == {"updated": 7, "updatedLogical": 5}
    service.repository.propagate_type.assert_awaited_once_with(cascade_filter("pd"), "VARCHAR(30)")
    service.repository.propagate_field.assert_awaited_once_with(logical_cascade_filter("pd"), "logicalDataType", "VARCHAR(20)")


def test_propagate_sin_tipo_logico_no_toca_la_faceta_logica(monkeypatch):
    monkeypatch.setattr(service.repository, "get_domain_types", AsyncMock(return_value=("INT", None)))
    monkeypatch.setattr(service.repository, "propagate_type", AsyncMock(return_value=1))
    monkeypatch.setattr(service.repository, "propagate_field", AsyncMock(return_value=0))
    assert asyncio.run(service.propagate("pd")) == {"updated": 1, "updatedLogical": 0}
    service.repository.propagate_field.assert_not_awaited()


class _Coll:
    """Fake mínimo de colección: registra update_many; find_one/find_one_and_update
    devuelven el doc dado."""
    def __init__(self, doc=None):
        self.doc, self.updates = doc, []

    async def find_one(self, *_a, **_k):
        return dict(self.doc) if self.doc else None

    async def find_one_and_update(self, _f, upd, **_k):
        self.doc = {**self.doc, **upd["$set"]}
        return dict(self.doc)

    async def update_many(self, flt, upd):
        self.updates.append((flt, upd["$set"]))
        return SimpleNamespace(modified_count=1)


def test_update_domain_cascadea_cada_faceta_por_separado(monkeypatch):
    dom = _Coll({"_id": "pd", "projectId": "p1", "name": "Codigo", "defaultDataType": "VARCHAR(30)", "logicalDataType": "VARCHAR(20)"})
    cols = _Coll()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"parent_domains": dom, "canonical_columns": cols}))
    out = asyncio.run(repository.update_domain("pd", {"name": "Codigo", "defaultDataType": "VARCHAR(40)",
                                                       "logicalDataType": "VARCHAR(25)"}))
    assert out["defaultDataType"] == "VARCHAR(40)" and out["logicalDataType"] == "VARCHAR(25)"
    phys, log = cols.updates
    assert phys[0]["dataType"] == "VARCHAR(30)" and phys[0]["typeOverridden"] == {"$ne": True} and phys[1]["dataType"] == "VARCHAR(40)"
    assert log[0]["logicalDataType"] == "VARCHAR(20)" and log[0]["logicalTypeOverridden"] == {"$ne": True}
    assert log[1]["logicalDataType"] == "VARCHAR(25)"
