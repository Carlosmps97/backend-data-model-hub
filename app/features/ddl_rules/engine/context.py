"""Contextos de evaluación (spec doc 30 §6.3) desde docs canónicos o payloads
del render. PURO — los valores UDP llegan keyed por defId (`udpValues`, como
viven en `canonical_tables`/`canonical_columns`) y acá se traducen a nombre
usando el catálogo de definiciones.
"""
from __future__ import annotations

from app.core.facets import is_logical_udp


def _udp_by_name(udp_values: dict | None, name_by_id: dict[str, str]) -> dict:
    """{defId: valor} → {nombreUDP: valor}. Ids sin definición se ignoran.

    OJO (decisión owner 07-21, enfoque B): el motor lee SOLO los valores
    EXPLÍCITOS — fiel a lo que guardó el XML. El `defaultValue` de la def NO se
    inyecta acá; el "sin valor → usar default" se declara en la REGLA (condición
    abierta + `default` del lookup, spec §6.7). Ver
    `plan-implementacion/30b-HALLAZGOS-UDP-DEFAULTS.md`."""
    out: dict[str, str] = {}
    for def_id, value in (udp_values or {}).items():
        name = name_by_id.get(def_id)
        if name is not None:
            out[name] = value
    return out


def names_by_id(udp_defs: list[dict]) -> dict[str, str]:
    """{defId: nombre} SOLO de defs FÍSICAS (doc 69): el motor DDL nunca lee un
    UDP de la faceta lógica — un homónimo lógico pisaría el valor físico."""
    return {d["id"]: d["name"] for d in udp_defs
            if d.get("id") and d.get("name") and not is_logical_udp(d)}


def table_ctx(table: dict, name_by_id: dict[str, str]) -> dict:
    """Contexto `tabla.*`. Acepta el doc canónico (physicalName/schema/
    description/udpValues) o el payload del render (name/schema). `tipo` es
    'physical' para toda tabla del modelo (las vistas no pasan por acá)."""
    return {
        "nombre": table.get("physicalName") or table.get("name") or "",
        "esquema": table.get("schema") or table.get("sql_schema") or None,
        "catalogo": table.get("catalog") or None,
        "tipo": table.get("tipo") or "physical",
        "comentario": table.get("description") or None,
        "udp": _udp_by_name(table.get("udpValues"), name_by_id),
    }


def column_ctx(col: dict, name_by_id: dict[str, str],
               domain_name_by_id: dict[str, str] | None = None) -> dict:
    """Contexto `columna.*`. `dominio` = NOMBRE del Parent Domain (resuelto de
    `parentDomainId`, o `domainName` si el payload ya lo trae)."""
    domain = col.get("domainName")
    if domain is None and col.get("parentDomainId"):
        domain = (domain_name_by_id or {}).get(col["parentDomainId"])
    return {
        "nombre": col.get("physicalName") or col.get("name") or "",
        "tipo": col.get("dataType") or col.get("type") or "",
        "nulable": bool(col.get("isNullable", True)),
        "pk": bool(col.get("isPrimaryKey") or False),
        "orden": col.get("ordinal") if col.get("ordinal") is not None else 0,
        "comentario": col.get("description") or None,
        "dominio": domain,
        "udp": _udp_by_name(col.get("udpValues"), name_by_id),
    }


def model_ctx(model: dict | None, name_by_id: dict[str, str]) -> dict:
    """Contexto `modelo.*` (= el canvas/subject area en la plataforma)."""
    model = model or {}
    return {
        "nombre": model.get("name") or model.get("nombre") or "",
        "udp": _udp_by_name(model.get("udpValues"), name_by_id),
    }
