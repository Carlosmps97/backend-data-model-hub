"""Doc 92 D8 — nombres LÓGICOS (entidad / atributo) sin caracteres especiales.

Permitidos: letras Unicode (tildes, ñ), dígitos, espacio y guion bajo. Espejo
de `src/lib/logicalName.ts` del front (que lo aplica al tipear); acá se aplica
en el choke point de changesets para TODO escritor (paneles, popup, CTAS,
paste, bulk upload, API), con el mismo criterio que el case del físico
(doc 83): lo legacy se sana al escribir. Puro.
"""
from __future__ import annotations

import re

# `\w` en Python 3 es Unicode: letras, dígitos y `_`.
_FORBIDDEN = re.compile(r"[^\w ]", re.UNICODE)
_MULTI_SPACE = re.compile(r" {2,}")


def sanitize_logical_name(value) -> str:
    """Quita los caracteres no permitidos, colapsa espacios repetidos y recorta
    los bordes. None/no-texto → ""."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return _MULTI_SPACE.sub(" ", _FORBIDDEN.sub("", text)).strip()


def has_special_chars(value) -> bool:
    return bool(_FORBIDDEN.search(value if isinstance(value, str) else ""))
