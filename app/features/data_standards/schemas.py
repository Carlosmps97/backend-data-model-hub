"""DTOs de Data Standards (apply/rollback)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.datatypes import canonicalize_default_type


class TermEdit(BaseModel):
    id: str | None = None            # None = nuevo término
    term: str
    abbrev: str
    scope: str                        # 'column' | 'table'
    wordType: str | None = None


class DomainEdit(BaseModel):
    id: str | None = None            # None = dominio nuevo
    name: str
    defaultDataType: str
    # Doc 69: tipo LÓGICO del dominio (faceta Entidad/Atributo); opcional.
    logicalDataType: str | None = None
    namingTerm: str | None = None
    description: str | None = None

    # Doc 62: misma homologación que ParentDomainBody — el apply de Standards
    # es el otro camino de escritura de dominios.
    @field_validator("defaultDataType")
    @classmethod
    def _canonicalize(cls, v: str) -> str:
        return canonicalize_default_type(v)

    # Doc 69: misma homologación para el tipo lógico ('' ⇒ None).
    @field_validator("logicalDataType")
    @classmethod
    def _canonicalize_logical(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return canonicalize_default_type(v) or None


class NamingEdit(BaseModel):
    separator: str
    case: str
    maxLength: int = 150  # límite de caracteres del físico (tabla/columna)


class UdpEdit(BaseModel):
    id: str | None = None            # None = definición UDP nueva
    name: str
    level: str = "column"            # Class: 'table' | 'column' | 'canvas' | 'view'
    view: str = "physical"           # faceta (doc 69): 'logical' | 'physical'
    dataType: str = "string"         # string | number | boolean | date | list
    defaultValue: str | None = None
    allowedValues: list[str] = []    # para dataType='list' (enum)
    description: str | None = None


class DdlRuleEdit(BaseModel):
    """Una regla de DDL Export (doc 30). `udpRefs` los deriva/valida el
    validador server-side (F2) a partir de `condition`/`action`; se aceptan del
    cliente para el guard de borrado mientras tanto."""
    id: str | None = None            # None = regla nueva
    name: str
    description: str | None = None
    kind: str = "rule"               # 'rule' | 'generator'
    target: str | None = "column"    # 'column' | 'table' — solo kind='rule'
    sourceArtifact: str | None = None  # solo kind='generator'
    condition: str = ""
    udpRefs: list[dict] = []         # [{udpId, level}]
    action: dict = {}
    appliesTo: list[str] = []
    priority: int = 100
    enabled: bool = True
    validationState: str = "valid"
    validationReport: dict = {}


class DdlConfigPatch(BaseModel):
    """Patch del ruleset config: cada bloque que venga no-None REEMPLAZA el set
    completo (payload chico; sin deltas)."""
    lookups: dict | None = None
    functions: list | None = None


class ApplyBody(BaseModel):
    """Batch de cambios de estándares que se aplican como UNA versión (15d/15e)."""
    kind: str = "batch"               # glossary | udp | domain | naming | ddl | batch
    title: str | None = None
    description: str | None = None
    termsUpsert: list[TermEdit] = []
    termsDelete: list[str] = []
    namingConfig: dict[str, NamingEdit] = {}   # {"column": {...}, "table": {...}}
    domainsUpsert: list[DomainEdit] = []
    domainsDelete: list[str] = []
    udpUpsert: list[UdpEdit] = []     # definiciones UDP a crear/editar
    udpDelete: list[str] = []         # ids de definiciones UDP a borrar
    rulesUpsert: list[DdlRuleEdit] = []   # reglas de DDL Export a crear/editar
    rulesDelete: list[str] = []           # ids de reglas a borrar
    ddlConfigPatch: DdlConfigPatch | None = None  # lookups/functions del ruleset


class RollbackBody(BaseModel):
    """Restaura el estado de estándares al de `targetSeq` (la versión a la que se
    revierte). El rollback se registra como una versión NUEVA (15g)."""
    targetSeq: int


class CopyFromBody(BaseModel):
    """Doc 75 D15: bloques de estándares que un proyecto NUEVO copia de otro."""
    projectId: str
    blocks: list[Literal["glossary", "domains", "udp", "naming", "ddl"]] = Field(min_length=1)
