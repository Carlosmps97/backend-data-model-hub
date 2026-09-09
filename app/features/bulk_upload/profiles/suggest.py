"""Mapeo SUGERIDO por nombre (doc 78 §3.3). `norm_key` absorbe tildes, `_`/`-`,
stopwords y el prefijo `udp`; los alias de campo cubren la plantilla histórica
(doc 55) y nombres obvios en inglés. Lo guardado es lo que el usuario confirma
en el editor: en runtime el mapeo es siempre explícito. PURO."""
from __future__ import annotations

from ..normalize import norm_key
from .models import LEVEL_OF_ROLE

FIELD_ALIASES: dict[str, dict[str, str]] = {
    "tables": {
        "tabla logico": "logicalName", "logical name": "logicalName", "nombre logico": "logicalName", "entity": "logicalName",
        "tabla fisica": "physicalName", "physical name": "physicalName", "nombre fisico": "physicalName", "table name": "physicalName",
        "def tabla": "description", "definicion": "description", "description": "description", "definition": "description",
        "esquema": "schema", "schema": "schema",
        "subject": "subject", "carpeta": "subject", "folder": "subject",
        "space": "space", "espacio": "space",
        "diagrama": "diagram", "diagram": "diagram", "canvas": "diagram",
        "project": "project", "proyecto": "project",
    },
    "columns": {
        "tabla logico": "tableRef", "tabla": "tableRef", "table": "tableRef", "entity": "tableRef",
        "campo logico": "logicalName", "atributo": "logicalName", "attribute": "logicalName",
        "column name": "logicalName", "logical name": "logicalName",
        "campo fisico": "physicalName", "physical name": "physicalName", "column": "physicalName",
        "def atributo": "description", "definicion": "description", "description": "description", "definition": "description",
        "parent domain": "parentDomain", "dominio": "parentDomain", "domain": "parentDomain",
        "tipo dato": "dataType", "data type": "dataType", "tipo": "dataType", "type": "dataType",
        "pk": "pk", "primary key": "pk", "llave primaria": "pk", "clave primaria": "pk",
    },
}


def suggest(role: str, headers: list[str], udp_defs: list[dict]) -> list[dict]:
    """[{header, target, matched: 'field' | 'udp' | None}] en el orden recibido."""
    level = LEVEL_OF_ROLE[role]
    by_udp: dict[str, list[str]] = {}
    for d in udp_defs:
        if (d.get("level") or "column") == level and d.get("id"):
            by_udp.setdefault(norm_key(d.get("name")), []).append(str(d["id"]))
    out: list[dict] = []
    for h in headers:
        key = norm_key(h)
        field = FIELD_ALIASES[role].get(key)
        if field:
            out.append({"header": h, "target": {"kind": "field", "field": field}, "matched": "field"})
        elif key in by_udp:
            out.append({"header": h, "target": {"kind": "udp", "udpIds": list(by_udp[key])}, "matched": "udp"})
        else:
            out.append({"header": h, "target": {"kind": "ignore"}, "matched": None})
    return out
