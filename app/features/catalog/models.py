"""Modelos de `catalog` (tablas y columnas canónicas universales)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from app.core.colors import clean_object_color
from app.core.models import DOC_CONFIG


class CanonicalTableDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Doc 75 D1: alcance por proyecto — obligatorio, lo estampa el servidor.
    projectId: str
    physicalName: str
    logicalName: str
    sql_schema: str | None = Field(default=None, alias="schema")
    # Doc 68: físico editado a mano (o heredado de la BD real) — rephysicalize
    # NO lo re-deriva. Espejo del patrón `typeOverridden` de columnas.
    physicalNameOverridden: bool = False
    # Doc 69 §4.9 (facetas): objeto que existe en UNA sola vista de Erwin
    # (`Is_Logical_Only` / `Is_Physical_Only`). Fase 2 los gatea en canvas/DDL.
    logicalOnly: bool = False
    physicalOnly: bool = False
    description: str | None = None
    # UDP: valores de las etiquetas key-value asignadas a esta tabla
    # ({udpDefId: value}). Aditivo (invariante §2.6): las keys se definen en Data
    # Standards; acá viven los valores elegidos para clasificar el dato.
    udpValues: dict[str, str] = Field(default_factory=dict)
    # Doc 109: color de la tabla en TODOS sus canvases ('theme:<id>' o
    # '#RRGGBB'; None = sin color). Un canvas puede pintarla distinto
    # (`subject_areas.colors`). Lectura tolerante: un valor inválido = sin color.
    color: str | None = None

    _clean_color = field_validator("color", mode="before")(clean_object_color)


class CanonicalColumnDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Doc 75 D1: alcance por proyecto — obligatorio, lo estampa el servidor.
    projectId: str
    tableId: str
    physicalName: str
    logicalName: str
    parentDomainId: str | None = None
    dataType: str
    typeOverridden: bool = False
    # Doc 69 §4.9 — faceta LÓGICA del tipo: Erwin trae Logical_Data_Type y
    # Physical_Data_Type (28-36 % de las columnas difieren: INTEGER/INT,
    # VARCHAR(20)/VARCHAR(30), DATE/TIMESTAMP). `dataType` sigue siendo el
    # FÍSICO. None = no informado (la UI muestra el físico como fallback).
    logicalDataType: str | None = None
    # Override manual del tipo lógico respecto al `logicalDataType` del dominio
    # (espejo de `typeOverridden`, que es de la faceta física).
    logicalTypeOverridden: bool = False
    # Existencia en una sola faceta (Is_Logical_Only / Is_Physical_Only).
    logicalOnly: bool = False
    physicalOnly: bool = False
    # Doc 68: override manual del NOMBRE físico (paridad con typeOverridden,
    # que cubre solo el tipo). Debe existir acá Y en el TS CanonicalColumn.
    physicalNameOverridden: bool = False
    # Doc 94 D1: la llave NO tiene un orden aparte (`pkPosition` se retiró; los
    # docs viejos que lo traen lo pierden al leer): las PK van primero en el
    # `ordinal` y entre ellas manda el `ordinal`.
    isPrimaryKey: bool | None = None
    isForeignKey: bool | None = None
    # Aditivos (invariante §2.6): nulabilidad, columna de partición y
    # descripción (def funcional a nivel columna).
    isNullable: bool = True
    isPartition: bool = False
    description: str | None = None
    # Doc 85: descripción FÍSICA (Comment de Erwin; el DDL la emite como
    # COMMENT con fallback a `description`). None = igual a la lógica.
    physicalDescription: str | None = None
    # Orden ÚNICO de la columna en la tabla (doc 74): el mismo en el modelo
    # lógico y en el físico — canvas, paneles, reporting y DDL ordenan por él.
    # La migración Erwin lo hereda como «llaves primero + Column order».
    ordinal: int = 0
    # UDP: valores de las etiquetas key-value asignadas a esta columna.
    udpValues: dict[str, str] = Field(default_factory=dict)
