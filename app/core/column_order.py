"""Orden ÚNICO de las columnas de una tabla (doc 81 → doc 94) — espejo Python de
`web/src/features/modeler/lib/columnOrder.ts` (`orderColumns`): PRIMERO las PK y
DESPUÉS el resto, cada bloque por `ordinal`. No hay orden de llave aparte (doc 94
D1: `pkPosition` se retiró; un valor viejo se ignora). Es lo que dibuja el canvas
y lo que emite el DDL. Lo consumen la carga masiva (estructura de las vistas
`_vu`, doc 87 §3.1) y el motor del Export DDL. Puro: no muta la entrada; sort
estable."""
from __future__ import annotations


def _ordinal(col: dict) -> int:
    return int(col.get("ordinal") or 0)


def display_order(cols: list[dict]) -> list[dict]:
    """Columnas de UNA tabla en su orden único: PK primero y cada bloque por
    `ordinal`. `cols` debe venir acotado a una sola tabla."""
    pks = sorted((c for c in cols if c.get("isPrimaryKey")), key=_ordinal)
    rest = sorted((c for c in cols if not c.get("isPrimaryKey")), key=_ordinal)
    return pks + rest
