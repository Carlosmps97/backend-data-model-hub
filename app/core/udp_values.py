"""Valores de UDP compartidos por quienes los ESCRIBEN y el motor del Reporting
que los LEE (doc 105, ronda 5).

Un UDP booleano se guarda como TEXTO: las grafías reconocidas se escriben
«true»/«false» (la carga Excel lo hace con `normalize_boolean`), así el GROUP BY
del Reporting no parte un mismo valor en varios grupos. Las grafías son las
MISMAS con las que el motor filtra `udp."X" = TRUE`
(`app/features/reporting/query/compiler._UDP_TRUE/_UDP_FALSE`, copiadas
exactas): sin distinguir mayúsculas ni espacios de borde. Un test las ata.
Número: finito, y se graba en su forma CANÓNICA (`canonical_number`, la del
motor: ronda 6). Fecha: ISO `YYYY-MM-DD` real; una fecha-hora ISO graba sólo la
fecha (`iso_date`, ronda 6).
"""
from __future__ import annotations

import math
import re
from datetime import date, time

UDP_TRUE_PATTERN = r"^\s*(true|1|s[iíIÍ]|yes|verdadero)\s*$"
UDP_FALSE_PATTERN = r"^\s*(false|0|no|falso)\s*$"
_TRUE = re.compile(UDP_TRUE_PATTERN, re.IGNORECASE)
_FALSE = re.compile(UDP_FALSE_PATTERN, re.IGNORECASE)


def normalize_boolean(text) -> str | None:
    """«true» / «false» si `text` es una grafía booleana reconocida; None si no
    (o si es None). Un bool o un entero de JSON se leen por su texto. Puro."""
    if text is None:
        return None
    value = str(text)
    if _TRUE.search(value):
        return "true"
    if _FALSE.search(value):
        return "false"
    return None


_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def is_finite_number(text) -> bool:
    """¿`text` es un número FINITO escrito con dígitos ASCII? `float()` acepta
    además «nan», «inf», «1_000» y dígitos de otros alfabetos («١٢», «１２»),
    que no son un valor de UDP (no tienen forma canónica y el Reporting no los
    encontraría con `udp."X" = 1000`). Puro."""
    if text is None:
        return False
    value = str(text).strip()
    return bool(_NUMBER.fullmatch(value)) and math.isfinite(float(value))


def list_key(text) -> str:
    """Clave para comparar un valor de UDP de lista con sus `allowedValues`:
    sin bordes, espacios internos colapsados y sin distinguir mayúsculas — la
    misma tolerancia de la carga Excel (`bulk_upload/normalize.norm_enum`)."""
    return " ".join(str(text).split()).casefold()


def is_iso_date(text) -> bool:
    """¿`text` es una fecha real en ISO `YYYY-MM-DD`? («2024-02-30»,
    «31/02/2024» o «1/2/24», no). Puro."""
    if text is None:
        return False
    value = str(text).strip()
    if not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def canonical_number(value) -> str | None:
    """Forma canónica de un número (texto): un entero, EXACTO (sin pasar por
    float); un float, sin «.0» sólo si es entero y menor que 2^53. None si no es
    un número escrito con dígitos ASCII (ronda 7: como `is_finite_number` y la
    forma del front, `src/lib/udpNumber.ts`). Los escritores graban esta forma y
    el motor del Reporting la importa (ronda 6): «10.50» y «10.5», o «1e3» y
    «1000», son el mismo valor. Puro."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    text = value.strip() if isinstance(value, str) else None
    if text is not None:
        if re.fullmatch(r"[+-]?[0-9]{1,300}", text):
            return str(int(text))
        if not _NUMBER.fullmatch(text):
            return None
    try:
        f = float(text if text is not None else value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(f):
        return None
    return str(int(f)) if f.is_integer() and abs(f) < 2 ** 53 else repr(f)


_ISO_DATETIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:[ T]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?)?")


def iso_date(text) -> str | None:
    """La fecha «YYYY-MM-DD» de un texto ISO de fecha o de fecha-hora
    (`YYYY-MM-DD HH:MM[:SS]`, con espacio o `T`, sin zona ni fracciones): así
    llega de la carga Excel una celda de fecha con hora (ronda 6); se graba
    sólo la fecha. None si la fecha no existe o la hora no es válida. Puro."""
    if text is None:
        return None
    match = _ISO_DATETIME.fullmatch(str(text).strip())
    if not match:
        return None
    day, hh, mm, ss = match.groups()
    try:
        date.fromisoformat(day)
        if hh is not None:
            time(int(hh), int(mm), int(ss or 0))
    except ValueError:
        return None
    return day
