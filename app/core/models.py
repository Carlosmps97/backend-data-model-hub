"""Piezas de modelo Pydantic compartidas entre features.

`DOC_CONFIG` es la config base de todo documento persistido (`extra="ignore"`
descarta campos internos del store —`flgactive`, `deletedAt`— y campos legacy
silenciosamente; `populate_by_name` permite alias como `schema`).

`TagDoc` + `coerce_tags` son etiquetas key/value de gobierno usadas por varias
entidades (dominios, tablas, columnas), por eso viven en `core` y no en una
feature concreta.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

DOC_CONFIG = ConfigDict(extra="ignore", populate_by_name=True)


class TagDoc(BaseModel):
    """Etiqueta de metadato key/value adjunta a dominios/tablas/columnas.
    Espeja la interfaz TS `Tag`. `key` y `value` son texto libre. Entradas
    legacy `list[str]` se normalizan a `{key: "", value: <str>}` en
    `coerce_tags`."""

    model_config = DOC_CONFIG

    key: str = ""
    value: str = ""


def coerce_tags(raw: Any) -> Any:
    """Acepta la forma nueva `list[TagDoc]` o la legacy `list[str]` de
    documentos persistidos heredados."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        return raw  # deja que Pydantic levante su error de validación habitual
    out: list[Any] = []
    for item in raw:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append({"key": "", "value": stripped})
        else:
            out.append(item)
    return out
