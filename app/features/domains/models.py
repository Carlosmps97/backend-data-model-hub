"""Modelos de la feature `domains` (Parent Domains: tipo por defecto + cascada)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class ParentDomainDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Doc 75 D1: alcance por proyecto — obligatorio, lo estampa el servidor.
    projectId: str
    name: str
    defaultDataType: str
    # Doc 69 §4.9: `defaultDataType` = tipo FÍSICO por defecto (lo que emite el
    # DDL; el kit lo siembra desde Physical_Data_Type). `logicalDataType` =
    # Logical_Data_Type de Erwin (el dominio «Codigo» es VARCHAR(20) lógico y
    # VARCHAR(30) físico). None = no informado.
    logicalDataType: str | None = None
    # R5b: término de naming sugerido por el dominio (lo muestra el diseño p4).
    # Aditivo (invariante de persistencia): el seed ya lo siembra y antes se
    # descartaba al leer por `extra="ignore"`. Default None = no-breaking.
    namingTerm: str | None = None
    description: str | None = None
    # Doc 79: dominio "atributo estándar" (Erwin `Attribute_Definition`) — al
    # asignarlo, el atributo hereda su nombre y definición; los genéricos de
    # tipo (Codigo, Fecha, Number…) no la llevan. Aditivo, default False.
    inheritsName: bool = False
