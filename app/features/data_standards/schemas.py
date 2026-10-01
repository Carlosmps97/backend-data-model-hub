"""DTOs de Data Standards (apply/rollback)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.datatypes import canonicalize_default_type
from app.features.settings.service import _validate_case, _validate_scope


class TermEdit(BaseModel):
    id: str | None = None            # None = nuevo término
    term: str
    abbrev: str
    scope: str                        # 'column' | 'table'


class DomainEdit(BaseModel):
    id: str | None = None            # None = dominio nuevo
    name: str
    defaultDataType: str
    # Doc 69: tipo LÓGICO del dominio (faceta Entidad/Atributo); opcional.
    logicalDataType: str | None = None
    namingTerm: str | None = None
    description: str | None = None
    inheritsName: bool | None = None  # Doc 79 (ver ParentDomainBody).
    physicalName: str | None = None          # doc 85 (ver ParentDomainBody)
    physicalDescription: str | None = None
    udpValues: dict[str, str] | None = None

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
    # Límite de caracteres del físico (tabla/columna); 0 = límite DESACTIVADO
    # (así lo leen el guard de longitud del changeset y la carga Excel). Estricto:
    # un `true` no pasa por 1.
    maxLength: int = Field(150, ge=0, strict=True)

    # Doc 105 (R2): desde D1b el apply es el ÚNICO camino de escritura del
    # naming (el PUT de /settings responde 409). Valida lo mismo que validaba
    # el PUT, con la misma regla de `settings` → 422 antes de escribir nada.
    # Antes un `case` desconocido se grababa y el re-derivado, el physicalize y
    # el alta de columnas reventaban (ValueError del motor → 500).
    @field_validator("case")
    @classmethod
    def _known_case(cls, v: str) -> str:
        _validate_case(v)
        return v


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
    completo (payload chico; sin deltas). `output` = Output settings (doc 93)."""
    lookups: dict | None = None
    functions: list | None = None
    output: dict | None = None


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
    ddlConfigPatch: DdlConfigPatch | None = None  # lookups/functions/output del ruleset

    # Doc 105 (R2): un scope desconocido se grababa como un doc más de
    # `naming_config` (y versionaba). Misma regla que el PUT cerrado.
    @field_validator("namingConfig")
    @classmethod
    def _known_scopes(cls, v: dict[str, NamingEdit]) -> dict[str, NamingEdit]:
        for scope in v:
            _validate_scope(scope)
        return v


class RollbackBody(BaseModel):
    """Restaura el estado de estándares al de `targetSeq` (la versión a la que se
    revierte). El rollback se registra como una versión NUEVA (15g)."""
    targetSeq: int


class CopyFromBody(BaseModel):
    """Doc 75 D15: bloques de estándares que un proyecto NUEVO copia de otro."""
    projectId: str
    blocks: list[Literal["glossary", "domains", "udp", "naming", "ddl"]] = Field(min_length=1)
