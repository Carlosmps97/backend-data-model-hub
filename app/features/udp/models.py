"""Modelos de la feature `udp` (User Defined Properties): etiquetas key-value
DEFINIDAS en Data Standards y asignadas a tablas/columnas para clasificar el dato.

Una *definición* es la KEY (name) + su tipo + valor default + valores permitidos
(enum) + a qué niveles aplica. Los *valores* asignados viven embebidos en cada
tabla/columna (`udpValues: {defId: value}`), no acá.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG

# Tipos de dato soportados para una key UDP.
UDP_TYPES = ("string", "number", "boolean", "date", "list")
# Nivel/jerarquía ("Class" en Erwin) al que pertenece una key UDP. Cada UDP vive
# en UN nivel (segmentado, no multi-nivel). 'canvas' = el Modelo de Datos
# completo (la subject area) — F5, spec 10 §10.
UDP_LEVELS = ("table", "column", "canvas", "view")


class UdpDefinitionDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str                              # la KEY, p.ej. "Clasificación del Dato"
    level: str = "column"                  # Class: 'table' | 'column' | 'canvas' | 'view'
    dataType: str = "string"               # string | number | boolean | date | list
    defaultValue: str | None = None        # valor por defecto al asignar
    allowedValues: list[str] = []          # para dataType='list' (enum: [DAC, NO DAC, ...])
    description: str | None = None
