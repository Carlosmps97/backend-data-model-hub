"""`QuerySpec` — el IR (contrato JSON) del motor de consulta del reporting.

Es la ÚNICA fuente de verdad: lo producen el query-builder visual y el parser
SQL (F2), y lo consumen el compiler (→ pipeline Mongo), la grilla y el export.
El cliente NUNCA manda paths de Mongo: manda una `field` KEY pública que el
compiler resuelve contra el Field Catalog (`schema.py`). `op` es un enum cerrado.
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# Operadores soportados (enum cerrado → cero inyección; el value se castea al
# tipo del campo). `contains`/`startsWith` usan re.escape en el compiler.
OPS = ("eq", "ne", "in", "nin", "contains", "startsWith",
       "gt", "gte", "lt", "lte", "between", "exists", "isnull")
FROMS = ("columns", "tables", "relationships", "views")
AGG_FNS = ("count", "countDistinct", "sum", "avg", "min", "max")


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
    from_: Literal[FROMS] = Field(default="columns", alias="from")  # type: ignore[valid-type]
    select: list[str] = Field(default_factory=list)
    where: WhereGroup | None = None
    groupBy: list[str] = Field(default_factory=list)
    aggregations: list[Aggregation] = Field(default_factory=list)
    orderBy: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=5000)
    cursor: str | None = None

    @property
    def is_grouped(self) -> bool:
        return bool(self.groupBy or self.aggregations)


WhereGroup.model_rebuild()
