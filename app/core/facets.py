"""Contrato ÚNICO de facetas lógico/físico (doc 69 §4.9).

Erwin modela UN objeto con DOS vistas (Logical/Physical): mismo id, llaves y
relaciones compartidas, y propiedades que pertenecen a una faceta u otra. Este
módulo es la única fuente de verdad de a qué faceta pertenece cada campo y de
cómo se etiqueta un UDP homónimo («Clasificacion del Dato» existe en ambas
facetas con ids distintos). Lo consumen reporting, DDL rules, bulk upload,
changesets (detalle de cambios) y el kit Erwin. PURO (sin FastAPI ni BD).
"""
from __future__ import annotations

from typing import Iterable

LOGICAL = "logical"
PHYSICAL = "physical"
FACETS = (LOGICAL, PHYSICAL)

# Niveles (Class Erwin) cuyos UDP son SIEMPRE físicos: `View.Physical.*` y
# `Model.Physical.*` — Erwin no define UDPs lógicos para vistas ni modelo.
PHYSICAL_ONLY_UDP_LEVELS = frozenset({"view", "canvas"})

TABLE_FACET_FIELDS: dict[str, tuple[str, ...]] = {
    LOGICAL: ("logicalName", "logicalOnly"),
    PHYSICAL: ("physicalName", "physicalNameOverridden", "schema", "physicalOnly"),
    "shared": ("id", "projectId", "description", "udpValues"),
}
COLUMN_FACET_FIELDS: dict[str, tuple[str, ...]] = {
    LOGICAL: ("logicalName", "logicalDataType", "logicalTypeOverridden", "logicalOnly"),
    # Doc 85: `physicalDescription` = Comment de Erwin (el DDL lo emite como COMMENT).
    PHYSICAL: ("physicalName", "physicalNameOverridden", "dataType", "typeOverridden",
               "isNullable", "isPartition", "physicalOnly", "physicalDescription"),
    # Doc 74: `ordinal` es el orden ÚNICO de la columna (lógico = físico),
    # como `pkPosition` es el único orden de la llave.
    "shared": ("id", "projectId", "tableId", "parentDomainId", "description", "isPrimaryKey",
               "isForeignKey", "pkPosition", "ordinal", "udpValues"),
}
# Doc 85 §3.1: el dominio tiene UN objeto con dos facetas, como Erwin
# (`Name`/`Physical_Name`, `Logical_Data_Type`/`Physical_Data_Type`,
# `Definition`/`Comment`). `udpValues` son los UDP por DEFECTO de
# atributo/columna (ambas facetas) que la columna hereda al asignar el dominio.
DOMAIN_FACET_FIELDS: dict[str, tuple[str, ...]] = {
    LOGICAL: ("name", "logicalDataType", "description"),
    PHYSICAL: ("physicalName", "defaultDataType", "physicalDescription"),
    "shared": ("id", "projectId", "namingTerm", "inheritsName", "udpValues"),
}


def normalize_udp_view(level: str | None, view: str | None) -> str:
    """Faceta EFECTIVA de una def UDP: view/canvas ⇒ physical siempre; en
    table/column vale lo declarado; desconocido/ausente ⇒ physical (todas las
    defs anteriores al doc 69 son físicas — catálogo del doc 68)."""
    if level in PHYSICAL_ONLY_UDP_LEVELS:
        return PHYSICAL
    return view if view in FACETS else PHYSICAL


def udp_view(defn: dict) -> str:
    return normalize_udp_view(defn.get("level"), defn.get("view"))


def is_logical_udp(defn: dict) -> bool:
    return udp_view(defn) == LOGICAL


def _def_id(defn: dict) -> str | None:
    raw = defn.get("id") if defn.get("id") is not None else defn.get("_id")
    return str(raw) if raw is not None else None


def udp_display_name(defn: dict) -> str:
    """Nombre legible ÚNICO por faceta: la física conserva el nombre plano (los
    reportes/Excel existentes no cambian); la lógica lleva sufijo «(Logical)»."""
    name = str(defn.get("name") or _def_id(defn) or "")
    return f"{name} (Logical)" if is_logical_udp(defn) else name


def udp_display_names(defs: Iterable[dict]) -> dict[str, str]:
    """{defId: nombre legible} para traducir `udpValues`. Acepta dumps (`id`)
    y docs crudos de la BD (`_id`)."""
    out: dict[str, str] = {}
    for d in defs:
        did = _def_id(d)
        if did:
            out[did] = udp_display_name(d)
    return out


def physical_udp_defs(defs: Iterable[dict]) -> list[dict]:
    """Solo las defs de la faceta física (el DDL es físico por naturaleza)."""
    return [d for d in defs if not is_logical_udp(d)]
