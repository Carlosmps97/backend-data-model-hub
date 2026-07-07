"""Field Catalog — el ESQUEMA VIRTUAL consultable por el motor de reporting.

Cada vista (`columns`/`tables`/`relationships`/`views`) expone una lista de
`FieldDef` (estáticos + UDP dinámicos derivados de `udp_definitions`). Es el
contrato único que:
- valida las queries (whitelist de campos + ops por tipo),
- traduce la `key` pública al `path` de Mongo (el cliente nunca manda paths),
- dice si un filtro/orden es `indexed`/`sortable` (para el planner de escala),
- alimenta el query-builder, el autocompletado SQL y los headers de la grilla.

Crear un UDP def agrega columnas seleccionables/filtrables/agrupables SIN tocar
código: "más metadata para filtrar" sale del catálogo dinámico.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

# Ops válidas por tipo de campo.
OPS_BY_TYPE = {
    "string": ("eq", "ne", "in", "nin", "contains", "startsWith", "exists", "isnull"),
    "enum": ("eq", "ne", "in", "nin", "exists", "isnull"),
    "number": ("eq", "ne", "in", "gt", "gte", "lt", "lte", "between", "exists", "isnull"),
    "boolean": ("eq", "ne", "exists"),
    "date": ("eq", "gt", "gte", "lt", "lte", "between", "exists", "isnull"),
}


@dataclass
class FieldDef:
    key: str                       # key pública (estable): p.ej. 'physicalName' | 'udp.<defId>'
    label: str
    path: str                      # path de Mongo en la colección de la vista
    type: str = "string"           # string | enum | number | boolean | date
    sortable: bool = False         # hay índice que soporta sort/keyset por este campo
    groupable: bool = True
    indexed: bool = False          # un $match por este campo usa índice (pushdown barato)
    enumValues: list[str] | None = None
    hydrate: str | None = None     # resolución de nombre post-fetch: 'domain'|'udp'|'fkref'|'derived'
    udpDefId: str | None = None
    entity: str | None = None      # vista cruzada (p.ej. columns.schema viene de la tabla)

    @property
    def ops(self) -> tuple[str, ...]:
        return OPS_BY_TYPE.get(self.type, OPS_BY_TYPE["string"])

    def to_public(self) -> dict:
        return {
            "key": self.key, "label": self.label, "type": self.type, "ops": list(self.ops),
            "sortable": self.sortable, "groupable": self.groupable, "indexed": self.indexed,
            "enumValues": self.enumValues, "udp": self.udpDefId is not None,
            "hydrated": self.hydrate is not None,
        }


def _s(key, label, **kw): return FieldDef(key, label, kw.pop("path", key), **kw)


# ── Catálogos estáticos por vista ────────────────────────────────────────────
_COLUMNS = [
    _s("physicalName", "Physical name", sortable=True, indexed=True),
    _s("logicalName", "Logical name"),
    _s("tableId", "Table id", indexed=True),
    _s("schema", "Schema", indexed=True, entity="table", sortable=False),          # cross-entity (pre-resuelto a tableIds)
    _s("dataType", "Data type", type="string", indexed=True),
    _s("parentDomainId", "Parent domain", type="string", indexed=True, hydrate="domain"),
    _s("typeOverridden", "Type overridden", type="boolean"),
    _s("isPrimaryKey", "Is PK", type="boolean"),
    _s("isForeignKey", "Is FK", type="boolean"),
    _s("isNullable", "Nullable", type="boolean"),
    _s("isPartition", "Partition", type="boolean"),
    _s("ordinal", "Ordinal", type="number", groupable=False),
    _s("description", "Description"),
]
_TABLES = [
    _s("physicalName", "Physical name", sortable=True, indexed=True),
    _s("logicalName", "Logical name"),
    _s("schema", "Schema", indexed=True),
    _s("description", "Description"),
]
_RELATIONSHIPS = [
    _s("sourceTableId", "Source table id", indexed=True),
    _s("targetTableId", "Target table id", indexed=True),
    _s("sourceCardinality", "Source cardinality", type="enum", enumValues=["one", "many"]),
    _s("targetCardinality", "Target cardinality", type="enum", enumValues=["one", "many"]),
    _s("identifying", "Identifying", type="boolean"),
]
_VIEWS = [
    _s("name", "Name", sortable=False),
    _s("schema", "Schema", indexed=True),
    _s("tableId", "Base table id", indexed=True),
]
_STATIC = {"columns": _COLUMNS, "tables": _TABLES, "relationships": _RELATIONSHIPS, "views": _VIEWS}

# En qué vista aparece cada nivel de UDP.
_UDP_VIEW_BY_LEVEL = {"column": "columns", "table": "tables"}


def _udp_type(dt: str) -> str:
    return {"list": "enum", "number": "number", "boolean": "boolean", "date": "date"}.get(dt, "string")


def udp_fields(from_: str, udp_defs: list[dict]) -> list[FieldDef]:
    """Campos dinámicos por cada UDP def del nivel que corresponde a la vista.
    key='udp.<defId>' (estable, sin acentos), path='udpValues.<defId>'. Cubiertos
    por el índice wildcard `udpValues.$**` (equality/$in/$exists = seek)."""
    out = []
    for d in udp_defs:
        if _UDP_VIEW_BY_LEVEL.get(d.get("level")) != from_:
            continue
        did = d.get("id")
        out.append(FieldDef(
            key=f"udp.{did}", label=d.get("name") or did, path=f"udpValues.{did}",
            type=_udp_type(d.get("dataType") or "string"),
            enumValues=(d.get("allowedValues") or None) if d.get("dataType") == "list" else None,
            sortable=False, groupable=True, indexed=True, hydrate="udp", udpDefId=did,
        ))
    return out


def build_catalog(from_: str, udp_defs: list[dict]) -> dict[str, FieldDef]:
    """Mapa key→FieldDef para la vista (estáticos + UDP dinámicos)."""
    fields = list(_STATIC.get(from_, [])) + udp_fields(from_, udp_defs)
    return {f.key: f for f in fields}
