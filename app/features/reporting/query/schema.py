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
    "boolean": ("eq", "ne", "exists", "isnull"),     # doc 105 (ronda 4): IS NULL también
    "date": ("eq", "gt", "gte", "lt", "lte", "between", "exists", "isnull"),
}
UDP_NUMBER_OPS = ("eq", "ne", "in", "exists", "isnull")


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
        if self.udpDefId and self.type == "number":
            # Doc 105 (ronda 4): los UDP se guardan como TEXTO — un rango o un
            # orden compararía texto («10» < «5»): sólo igualdad.
            return UDP_NUMBER_OPS
        return OPS_BY_TYPE.get(self.type, OPS_BY_TYPE["string"])

    def to_public(self) -> dict:
        # Los campos `derived` (calculados post-fetch, p.ej. tableCount) NO existen
        # en Mongo: el compiler los rechaza en where/groupBy/agg (defensa en fondo).
        # Publicamos ops=[] + derived=True para que el builder NO los ofrezca como
        # filtro ni dispare facets (valores inútiles) — solo son seleccionables.
        derived = self.hydrate == "derived"
        return {
            "key": self.key, "label": self.label, "type": self.type,
            "ops": [] if derived else list(self.ops),
            "sortable": self.sortable, "groupable": self.groupable, "indexed": self.indexed,
            "enumValues": self.enumValues, "udp": self.udpDefId is not None,
            "hydrated": self.hydrate is not None, "derived": derived,
        }


def _s(key, label, **kw): return FieldDef(key, label, kw.pop("path", key), **kw)


# ── Catálogos estáticos por vista ────────────────────────────────────────────
_COLUMNS = [
    _s("physicalName", "Physical name", sortable=True, indexed=True),
    _s("logicalName", "Logical name"),
    _s("tableId", "Table id", indexed=True),
    # cross-entity (pre-resuelto a tableIds): filtrable con = / in, NO agrupable
    # ni agregable (vive en la tabla; doc 105, ronda 4).
    _s("schema", "Schema", indexed=True, entity="table", sortable=False, groupable=False),
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
_CARDINALITY_ENUM = ["one", "many", "one-only", "zero-one", "one-many", "zero-many"]
_RELATIONSHIPS = [
    _s("parentTableId", "Parent table id", indexed=True),
    _s("childTableId", "Child table id", indexed=True),
    _s("parentCardinality", "Parent cardinality", type="enum", enumValues=_CARDINALITY_ENUM),
    _s("childCardinality", "Child cardinality", type="enum", enumValues=_CARDINALITY_ENUM),
    _s("identifying", "Identifying", type="boolean"),
    _s("subcategory", "Subcategory", type="boolean"),
    # Doc 98: frases de relación (texto libre — sin índice, no agrupables).
    _s("parentToChildPhrase", "Parent-to-child phrase", groupable=False),
    _s("childToParentPhrase", "Child-to-parent phrase", groupable=False),
]
_VIEWS = [
    _s("name", "Name", sortable=False),
    _s("schema", "Schema", indexed=True),
    _s("tableId", "Base table id", indexed=True),
    _s("description", "Definition"),          # F5: definición funcional de la vista
]
# F5 — entidad virtual `view_columns`: 1 fila por COLUMNA de cada vista (unwind
# de `views.sources`). Expone la definición a nivel de columna-de-vista (override
# del origen) con FALLBACK a la def de la columna física. Se ejecuta por un
# camino DEDICADO (aggregate+unwind) en el executor — NO por el keyset genérico:
# por eso los campos van groupable=False (el builder no ofrece agrupar).
_VIEW_COLUMNS = [
    _s("viewName", "View", path="name", sortable=True, groupable=False),
    _s("schema", "Schema", path="schema", sortable=True, groupable=False),
    _s("outputName", "Column", path="sources.outputAlias", sortable=True, groupable=False),
    _s("sourceColumn", "Source column", path="sources.column", groupable=False),
    _s("sourceTableId", "Source table id", path="sources.tableId", groupable=False),
    _s("castType", "Cast", path="sources.castType", groupable=False),
    _s("expression", "Expression", path="sources.expression", groupable=False),
    _s("description", "Definition", path="sources.description", groupable=False),
]
# F5 — entidad `models` (Modelo de Datos = subject_areas). `tableCount` es
# CALCULADO post-fetch (len(tableIds) en el executor): path = la FUENTE
# proyectada; hydrate='derived' hace que el compiler lo rechace en
# where/groupBy/agregaciones (no existe como campo en Mongo).
_MODELS = [
    _s("name", "Model name", sortable=True, indexed=True),
    _s("folderId", "Folder id"),
    FieldDef("tableCount", "Table count", "tableIds", type="number",
             groupable=False, hydrate="derived"),
]
_STATIC = {"columns": _COLUMNS, "tables": _TABLES, "relationships": _RELATIONSHIPS,
           "views": _VIEWS, "view_columns": _VIEW_COLUMNS, "models": _MODELS}

# En qué vista aparece cada nivel de UDP.
_UDP_VIEW_BY_LEVEL = {"column": "columns", "table": "tables", "canvas": "models"}


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
