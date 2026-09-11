"""Orden de DISPLAY de las columnas de una tabla (doc 81) — espejo Python de
`web/src/features/modeler/lib/columnOrder.ts` (`orderColumns`): PRIMERO el
bloque de llaves en orden de llave (`pkPosition`; una PK sin posición explícita
cae DESPUÉS de las explícitas, por `ordinal`) y DESPUÉS el resto por `ordinal`.
Es exactamente lo que dibuja el canvas y lo que emite el DDL. Acá lo consume la
carga masiva para replicar la estructura de una tabla en sus vistas `_vu`
(doc 87 §3.1). Puro: no muta la entrada; sort estable."""
from __future__ import annotations

_LAST = float("inf")


def _ordinal(col: dict) -> int:
    return int(col.get("ordinal") or 0)


def _key_position(col: dict) -> tuple[float, int]:
    pos = col.get("pkPosition")
    return (float(pos) if pos is not None else _LAST, _ordinal(col))


def display_order(cols: list[dict]) -> list[dict]:
    """Columnas de UNA tabla en su orden visible: PK primero (orden de llave),
    luego el resto por `ordinal`. `cols` debe venir acotado a una sola tabla."""
    pks = sorted((c for c in cols if c.get("isPrimaryKey")), key=_key_position)
    rest = sorted((c for c in cols if not c.get("isPrimaryKey")), key=_ordinal)
    return pks + rest
