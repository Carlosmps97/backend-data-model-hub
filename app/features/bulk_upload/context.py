"""Contexto de validación de la carga masiva (doc 55): la foto EFECTIVA del
changeset (publicado + overlay) y los Data Standards vivos, tal como los
consume el planner (puro). Lo arma `loader.py`; los tests lo construyen a mano.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UploadContext:
    projects: list[dict] = field(default_factory=list)
    folders: list[dict] = field(default_factory=list)
    canvases: list[dict] = field(default_factory=list)       # subject_areas
    schemas: list[dict] = field(default_factory=list)
    # Pool de tablas efectivo, PROYECTADO (id, physicalName, logicalName,
    # schema, description, udpValues) — resuelve identidades por nombre.
    tables: list[dict] = field(default_factory=list)
    # Columnas efectivas SOLO de las tablas existentes referenciadas por el
    # workbook (`loader.load_columns`), agrupadas por tableId.
    columns_by_table: dict[str, list[dict]] = field(default_factory=dict)
    udp_defs: list[dict] = field(default_factory=list)
    domains: list[dict] = field(default_factory=list)
    # naming_config por scope ('table' | 'column'): separator, case, maxLength.
    naming: dict[str, dict] = field(default_factory=dict)
    # Glosario por scope: {término: abreviatura} (entrada de `physicalize`).
    glossary: dict[str, dict[str, str]] = field(default_factory=dict)
