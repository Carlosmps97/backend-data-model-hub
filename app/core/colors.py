"""Doc 109 — referencias de color de tablas, vistas y canvases (puro).

Un color se guarda como REFERENCIA (`ColorRef`, un string), espejo de
`web-data-model-hub/src/lib/colors.ts`:

  'theme:<id>'  un theme del proyecto (Data Standards → Themes): si cambia el
                color del theme, cambian todas las cajas que lo usan
  '#RRGGBB'     un color fijo
  'none'        «sin color» — sólo como EXCEPCIÓN de un canvas (la tabla tiene
                color pero en este canvas se ve sin color)

Dónde vive: `canonical_tables.color` / `views.color` (todos los canvases de la
tabla/vista; null = sin color) y `subject_areas.colors` = {id de tabla o vista:
ColorRef} (excepciones de ESE canvas). Precedencia, como en Erwin: excepción
del canvas → color de la tabla → sin color.

Lectura TOLERANTE (un valor inválido se descarta: la caja se ve sin color y el
canvas abre igual) y escritura ESTRICTA en la entrada del cliente (mensaje
legible), mismo patrón que los trazos de wires (doc 99).
"""
from __future__ import annotations

import re

NONE = "none"
MAX_CANVAS_COLORS = 5_000      # excepciones por canvas (sólo escritura)
MAX_ID_LEN = 200

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_THEME_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_THEME = re.compile(r"^theme:[A-Za-z0-9_.:-]{1,64}$")


def theme_id_ok(theme_id: object) -> bool:
    """¿Sirve como id de theme (cabe en una referencia `theme:<id>`)? Puro."""
    return isinstance(theme_id, str) and bool(_THEME_ID.match(theme_id))


def normal_color(value: object, *, allow_none: bool) -> str | None:
    """La referencia NORMALIZADA (hex en mayúsculas) o None si no es válida.
    `allow_none`: si vale la palabra 'none' (excepción de canvas). Puro."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if _HEX.match(v):
        return v.upper()
    if _THEME.match(v):
        return v
    if allow_none and v == NONE:
        return NONE
    return None


def clean_object_color(value: object) -> str | None:
    """Color de una tabla/vista al LEER: válido o None. Puro."""
    return normal_color(value, allow_none=False)


def with_clean_color(doc: dict) -> dict:
    """La tabla/vista con su `color` saneado (la misma si no trae la llave o ya
    es válido). Las lecturas que el cliente usa para armar su próximo guardado
    pasan por acá: un color corrupto en la BD no vuelve como payload (lo
    rechazaría la escritura estricta y la tabla no se podría editar). Puro."""
    if "color" not in doc:
        return doc
    clean = clean_object_color(doc.get("color"))
    return doc if clean == doc.get("color") else {**doc, "color": clean}


def _id_ok(key: object) -> bool:
    return (isinstance(key, str) and bool(key.strip()) and len(key) <= MAX_ID_LEN
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in key))


def clean_colors(raw: object) -> dict[str, str]:
    """Excepciones de un canvas al LEER: sólo las entradas válidas (no muta la
    entrada). Puro."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        ref = normal_color(value, allow_none=True)
        if _id_ok(key) and ref is not None:
            out[key] = ref
    return out


def colors_error(raw: object) -> str | None:
    """Escritura ESTRICTA de las excepciones de un canvas: mensaje legible si
    algo no es válido, None si lo es (ausente/None = sin excepciones). Puro."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return "colors must be an object {tableOrViewId: color}"
    if len(raw) > MAX_CANVAS_COLORS:
        return f"colors can have at most {MAX_CANVAS_COLORS} entries per canvas"
    for key, value in raw.items():
        if not _id_ok(key):
            return f"colors: invalid id (empty, longer than {MAX_ID_LEN} characters or with control characters)"
        if normal_color(value, allow_none=True) is None:
            return f"colors.{key}: '{value}' is not a color (use #RRGGBB, theme:<id> or none)"
    return None


def object_color_error(value: object) -> str | None:
    """Escritura ESTRICTA del color de una tabla/vista (null = sin color)."""
    if value is None or normal_color(value, allow_none=False) is not None:
        return None
    return f"color: '{value}' is not a color (use #RRGGBB or theme:<id>)"
