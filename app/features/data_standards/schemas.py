"""DTOs de Data Standards (apply/rollback)."""
from __future__ import annotations

from pydantic import BaseModel


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
    namingTerm: str | None = None
    description: str | None = None


class NamingEdit(BaseModel):
    separator: str
    case: str
    maxLength: int = 150  # límite de caracteres del físico (tabla/columna)


class UdpEdit(BaseModel):
    id: str | None = None            # None = definición UDP nueva
    name: str
    level: str = "column"            # Class: 'table' | 'column' | 'canvas'
    dataType: str = "string"         # string | number | boolean | date | list
    defaultValue: str | None = None
    allowedValues: list[str] = []    # para dataType='list' (enum)
    description: str | None = None


class ApplyBody(BaseModel):
    """Batch de cambios de estándares que se aplican como UNA versión (15d/15e)."""
    kind: str = "batch"               # glossary | udp | domain | naming | batch
    title: str | None = None
    description: str | None = None
    termsUpsert: list[TermEdit] = []
    termsDelete: list[str] = []
    namingConfig: dict[str, NamingEdit] = {}   # {"column": {...}, "table": {...}}
    domainsUpsert: list[DomainEdit] = []
    domainsDelete: list[str] = []
    udpUpsert: list[UdpEdit] = []     # definiciones UDP a crear/editar
    udpDelete: list[str] = []         # ids de definiciones UDP a borrar


class RollbackBody(BaseModel):
    """Restaura el estado de estándares al de `targetSeq` (la versión a la que se
    revierte). El rollback se registra como una versión NUEVA (15g)."""
    targetSeq: int
