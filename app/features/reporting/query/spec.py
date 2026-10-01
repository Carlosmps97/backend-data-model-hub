"""`QuerySpec` — el IR (contrato JSON) del motor de consulta del reporting.

Es la ÚNICA fuente de verdad: lo producen el query-builder visual y el parser
SQL (F2), y lo consumen el compiler (→ pipeline Mongo), la grilla y el export.
El cliente NUNCA manda paths de Mongo: manda una `field` KEY pública que el
compiler resuelve contra el Field Catalog (`schema.py`). `op` es un enum cerrado.
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Operadores soportados (enum cerrado → cero inyección; el value se castea al
# tipo del campo). `contains`/`startsWith` usan re.escape en el compiler.
OPS = ("eq", "ne", "in", "nin", "contains", "startsWith",
       "gt", "gte", "lt", "lte", "between", "exists", "isnull")
FROMS = ("columns", "tables", "relationships", "views", "view_columns", "models")
AGG_FNS = ("count", "countDistinct", "sum", "avg", "min", "max")
# Tamaño máximo de una página y tope máximo del resultado TOTAL (`maxRows`).
MAX_PAGE = 5000
MAX_ROWS = 10_000_000
# Doc 105 (ronda 5): niveles máximos de grupos anidados del WHERE. Más allá, la
# recursión de pydantic/compilador/traductor congelaba el event loop o daba 500.
MAX_WHERE_DEPTH = 50
TOO_DEEP = f"The filter is nested too deeply (more than {MAX_WHERE_DEPTH} levels)."


def where_depth(node, limit: int = MAX_WHERE_DEPTH) -> int:
    """Niveles de grupos del WHERE (dict del JSON o modelos), SIN recursión;
    corta apenas pasa `limit`. Puro."""
    deepest, stack = 0, [(node, 1)]
    while stack:
        current, depth = stack.pop()
        conditions = (current.get("conditions") if isinstance(current, dict)
                      else getattr(current, "conditions", None))
        if not isinstance(conditions, (list, tuple)):     # lo que no es lista lo rechaza pydantic (422)
            continue
        deepest = max(deepest, depth)
        if deepest > limit:
            return deepest
        stack.extend((c, depth + 1) for c in conditions if isinstance(c, (dict, BaseModel)))
    return deepest


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    op: Literal[OPS] = "eq"  # type: ignore[valid-type]
    value: Any = None


class WhereGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["and", "or", "not"] = "and"
    # Unión: nodo = Condition {field,op,value} o subgrupo {op,conditions}.
    conditions: list[Union["Condition", "WhereGroup"]] = Field(default_factory=list)


class Aggregation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    fn: Literal[AGG_FNS] = "count"  # type: ignore[valid-type]
    field: str | None = None
    as_: str = Field(default="value", alias="as")


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    dir: Literal["asc", "desc"] = "asc"


class QuerySpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    projectId: str = Field(min_length=1)      # doc 75: toda consulta es de UN proyecto
    from_: Literal[FROMS] = Field(default="columns", alias="from")  # type: ignore[valid-type]
    select: list[str] = Field(default_factory=list)
    where: WhereGroup | None = None
    groupBy: list[str] = Field(default_factory=list)
    aggregations: list[Aggregation] = Field(default_factory=list)
    orderBy: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=MAX_PAGE)        # tamaño de PÁGINA
    # Doc 105: tope del resultado TOTAL (lo arma el editor SQL con `LIMIT n`):
    # las páginas siguientes y el export se detienen ahí. None = todo el
    # resultado (el constructor pagina con `limit`).
    maxRows: int | None = Field(default=None, ge=1, le=MAX_ROWS)
    # Doc 105 (ronda 5): columnas del resultado AGRUPADO, en orden (las arma el
    # editor SQL con su SELECT): dimensiones y agregados intercalados, y una
    # dimensión agrupada que no está aquí no sale. None = groupBy + agregados.
    resultColumns: list[str] | None = None
    cursor: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _where_not_too_deep(cls, data: Any) -> Any:
        """Antes de construir los modelos anidados (y su recursión)."""
        if isinstance(data, dict) and where_depth(data.get("where")) > MAX_WHERE_DEPTH:
            raise ValueError(TOO_DEEP)
        return data

    @property
    def is_grouped(self) -> bool:
        return bool(self.groupBy or self.aggregations)


WhereGroup.model_rebuild()
