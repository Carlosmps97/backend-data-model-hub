"""Modelos de `ddl_rules` (DDL Export Rules, doc 30): reglas que transforman el
TEXTO SQL del Export DDL según valores de UDP. Se autoran/versionan en Data
Standards y se aplican recién al exportar desde el canvas — nunca tocan el
modelo ni la data; los artefactos que generan viven solo en el `.sql`.

Versionado: NO tienen versionado propio — entran al snapshot de
`standards_versions` como UDP/Glossary/Parent Domains (doc 30 D1). Las
mutaciones van SOLO por `POST /api/standards/apply` (rulesUpsert/rulesDelete/
ddlConfigPatch); el router de esta feature es de lectura.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG

RULE_KINDS = ("rule", "generator")
RULE_TARGETS = ("column", "table")
VALIDATION_STATES = ("valid", "invalid", "stale")

# Artefactos RAÍZ del export (siempre existen). Los generadores declaran los
# suyos vía `action.emit.artifact` — el catálogo es raíces + declarados, nunca
# un enum cerrado (spec §5).
ROOT_ARTIFACTS = (
    {"id": "ddl.tabla_fisica", "label": "Physical table", "root": True},
    {"id": "ddl.vista_negocio", "label": "Business view", "root": True},
)


class UdpRef(BaseModel):
    """Binding regla→UDP por ID (doc 30 A6): renombrar un UDP no rompe la regla
    (el nombre se resuelve al renderizar) y borrar uno referenciado se bloquea."""
    model_config = DOC_CONFIG

    udpId: str
    level: str  # 'table' | 'column' | 'canvas' (el "Model" del spec = canvas)


class DdlRuleDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str                              # slug único entre reglas activas (ej. enmascarar_dac)
    description: str | None = None
    kind: str = "rule"                     # 'rule' | 'generator'
    target: str | None = "column"          # 'column' | 'table' — solo kind='rule'
    sourceArtifact: str | None = None      # artefacto de entrada — solo kind='generator'
    condition: str = ""                    # DSL (SQL-like) en forma canónica con corchetes
    udpRefs: list[UdpRef] = []             # derivado de `condition`/`action` al validar
    action: dict = Field(default_factory=dict)   # expression | tags | tblproperties | emit (spec §6.6)
    appliesTo: list[str] = []              # artefactos destino — solo kind='rule'
    priority: int = 100                    # orden de ejecución: (priority DESC, name ASC)
    enabled: bool = True
    # Estado del último ciclo de validación (los 5 checks, spec §9). Las reglas
    # 'invalid'/'stale' se guardan igual; el export las salta y las reporta.
    validationState: str = "valid"         # 'valid' | 'invalid' | 'stale'
    validationReport: dict = Field(default_factory=dict)
    updatedBy: str | None = None


class DdlRulesetConfigDoc(BaseModel):
    """Config del ruleset global (singleton `_id='global'`): lookups (el BUSCARV
    de Excel, spec §6.7) y funciones reusables (spec §6.8). `scope` por-proyecto
    queda para más adelante (spec 15.2) — hoy solo existe 'global'."""
    model_config = DOC_CONFIG

    id: str = "global"
    # {nombre: {fromUdpId, fromLevel, values: {valorUdp: emitido|None}, default: str|None}}
    lookups: dict = Field(default_factory=dict)
    # [{name, params: [str], body: str}]
    functions: list = Field(default_factory=list)
