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
# Faceta (vista Erwin) a la que pertenece la key: 'logical' (Entidad/Atributo)
# o 'physical' (Tabla/Columna/Vista/Model). Doc 69 §4.1: las defs homónimas de
# ambas facetas son documentos DISTINTOS (ids propios) — así `udpValues`
# {defId: value} separa los valores sin cambiar de forma. En level view/canvas
# la faceta es siempre physical (`app.core.facets.normalize_udp_view`).
UDP_VIEWS = ("logical", "physical")


class UdpDefinitionDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Doc 75 D1: alcance por proyecto — obligatorio, lo estampa el servidor.
    projectId: str
    name: str                              # la KEY, p.ej. "Clasificación del Dato"
    level: str = "column"                  # Class: 'table' | 'column' | 'canvas' | 'view'
    view: str = "physical"                 # faceta: 'logical' | 'physical' (doc 69)
    dataType: str = "string"               # string | number | boolean | date | list
    defaultValue: str | None = None        # valor por defecto al asignar
    allowedValues: list[str] = []          # para dataType='list' (enum: [DAC, NO DAC, ...])
    description: str | None = None
