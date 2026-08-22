"""Negocio de `schemas` (doc 18): validación de nombre (pura), unicidad
case-insensitive y delete-solo-si-vacío. Repository mockeado (patrón
test_publish_duplicates.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.schemas import service
from app.features.schemas.schemas import SchemaBody
from app.features.schemas.service import (
    DuplicateSchemaError,
    InvalidSchemaNameError,
    name_error,
)


def test_name_error_acepta_nombres_validos():
    # legacy con mayúsculas (No_Definido) DEBE validar
    for name in ("bcp_ddv_clientes", "No_Definido", "core", "s1", "vistas_vu"):
        assert name_error(name) is None, name


def test_name_error_rechaza_invalidos():
    for name in ("", "  ", "1core", "core cliente", "core.cliente", "core-x", "$core", "esquema!"):
        assert name_error(name) is not None, repr(name)


def test_create_schema_valida_nombre():
    with pytest.raises(InvalidSchemaNameError):
        asyncio.run(service.create_schema(SchemaBody(name="mal nombre")))


def test_create_schema_rechaza_duplicado_case_insensitive(monkeypatch):
    monkeypatch.setattr(service.repository, "find_by_name",
                        AsyncMock(return_value={"id": "s1", "name": "core"}))
    with pytest.raises(DuplicateSchemaError):
        asyncio.run(service.create_schema(SchemaBody(name="CORE")))


def test_create_schema_trimea_y_delega(monkeypatch):
    monkeypatch.setattr(service.repository, "find_by_name", AsyncMock(return_value=None))
    create = AsyncMock(return_value={"id": "s1", "name": "core", "description": None})
    monkeypatch.setattr(service.repository, "create_schema", create)
    asyncio.run(service.create_schema(SchemaBody(name="  core  ")))
    assert create.await_args.args[0]["name"] == "core"


def test_update_schema_permite_mismo_id_con_mismo_nombre(monkeypatch):
    # renombrar a su PROPIO nombre (o cambiar solo description) no es duplicado
    monkeypatch.setattr(service.repository, "find_by_name",
                        AsyncMock(return_value={"id": "s1", "name": "core"}))
    upd = AsyncMock(return_value={"id": "s1", "name": "core", "description": "x"})
    monkeypatch.setattr(service.repository, "update_schema", upd)
    out = asyncio.run(service.update_schema("s1", SchemaBody(name="core", description="x")))
    assert out["description"] == "x"


def test_delete_schema_en_uso_devuelve_in_use(monkeypatch):
    monkeypatch.setattr(service.repository, "get_schema",
                        AsyncMock(return_value={"id": "s1", "name": "core"}))
    monkeypatch.setattr(service.repository, "usage_count", AsyncMock(return_value=3))
    assert asyncio.run(service.delete_schema("s1")) == "in-use"


def test_delete_schema_vacio_borra(monkeypatch):
    monkeypatch.setattr(service.repository, "get_schema",
                        AsyncMock(return_value={"id": "s1", "name": "core"}))
    monkeypatch.setattr(service.repository, "usage_count", AsyncMock(return_value=0))
    monkeypatch.setattr(service.repository, "delete_schema", AsyncMock(return_value=True))
    assert asyncio.run(service.delete_schema("s1")) is True


# ── kind del esquema (doc 44): 'tables' | 'views', opcional ────────────────


def test_schema_doc_roundtrip_conserva_kind():
    from app.features.schemas.models import SchemaDoc
    d = SchemaDoc.model_validate({"id": "s1", "name": "core_vu", "kind": "views"})
    assert d.model_dump()["kind"] == "views"
    # sin kind (docs pre-doc-44): None, no desaparece la clave
    assert SchemaDoc.model_validate({"id": "s2", "name": "core"}).model_dump()["kind"] is None


def test_schema_body_rechaza_kind_invalido():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SchemaBody(name="core", kind="tabla")


def test_create_schema_delega_kind(monkeypatch):
    monkeypatch.setattr(service.repository, "find_by_name", AsyncMock(return_value=None))
    create = AsyncMock(return_value={"id": "s1", "name": "core_vu",
                                     "description": None, "kind": "views"})
    monkeypatch.setattr(service.repository, "create_schema", create)
    asyncio.run(service.create_schema(SchemaBody(name="core_vu", kind="views")))
    assert create.await_args.args[0]["kind"] == "views"


def test_update_schema_con_kind_lo_actualiza(monkeypatch):
    monkeypatch.setattr(service.repository, "find_by_name", AsyncMock(return_value=None))
    upd = AsyncMock(return_value={"id": "s1", "name": "core", "description": None,
                                  "kind": "tables"})
    monkeypatch.setattr(service.repository, "update_schema", upd)
    asyncio.run(service.update_schema("s1", SchemaBody(name="core", kind="tables")))
    assert upd.await_args.args[1]["kind"] == "tables"


def test_update_schema_sin_kind_no_lo_pisa(monkeypatch):
    # un PATCH que no manda kind NO debe escribirlo (preserva el valor en BD)
    monkeypatch.setattr(service.repository, "find_by_name", AsyncMock(return_value=None))
    upd = AsyncMock(return_value={"id": "s1", "name": "core", "description": None})
    monkeypatch.setattr(service.repository, "update_schema", upd)
    asyncio.run(service.update_schema("s1", SchemaBody(name="core")))
    assert "kind" not in upd.await_args.args[1]
