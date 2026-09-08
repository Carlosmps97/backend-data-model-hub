"""Conversión nombre lógico ↔ físico vía un diccionario de abreviaturas.

Puro y data-driven (sin reglas hardcodeadas): el diccionario es dato. Soporta
términos multi-palabra con *longest-match* (p. ej. "tipo de cambio" → "TPC").

El físico se arma en 3 pasos configurables por *scope* (columna vs tabla):
1. abreviar cada palabra/frase vía el mapa (longest-match),
2. aplicar `case` a cada segmento,
3. unir los segmentos con `separator`.

`case ∈ {'upper','lower','camel'}` (default 'upper' = comportamiento histórico).
- 'upper'/'lower': cada segmento va en MAYÚSCULA/minúscula y se une con `separator`.
- 'camel': lowerCamelCase — primer segmento en minúscula, los siguientes con la
  inicial en mayúscula; SIEMPRE se unen sin separador (se ignora `separator`),
  porque camelCase ya delimita por capitalización. Las abreviaturas mapeadas se
  re-capitalizan según su posición (no se respeta el casing del mapa) para que
  el resultado sea camelCase consistente.
"""
from __future__ import annotations

_VALID_CASES = ("upper", "lower", "camel")


def _abbreviate(logical: str, mappings: dict[str, str]) -> list[str]:
    """Tokeniza `logical` y reemplaza por abreviatura (longest-match multi-palabra).

    Devuelve los segmentos crudos (sin aplicar case): abreviatura tal cual del
    mapa, o la palabra original si no hay match. Puro."""
    lc = {k.lower(): v for k, v in mappings.items()}
    words = logical.split()
    if not words:
        return []
    max_n = max((len(k.split()) for k in lc), default=1)
    out: list[str] = []
    i = 0
    while i < len(words):
        matched = False
        for n in range(min(max_n, len(words) - i), 0, -1):
            phrase = " ".join(words[i : i + n]).lower()
            if phrase in lc:
                out.append(lc[phrase])
                i += n
                matched = True
                break
        if not matched:
            out.append(words[i])
            i += 1
    return out


def physicalize(
    logical: str,
    mappings: dict[str, str],
    separator: str = "_",
    case: str = "upper",
) -> str:
    """'monto deuda dólares' + {monto:MTO,…} → 'MTO_DEU_USD'.

    Tokens no mapeados se conservan y se les aplica `case`. Une los segmentos
    con `separator` (salvo 'camel', que ignora `separator` y une por
    capitalización). Ver semántica de `case` en el docstring del módulo.

    Ejemplos:
      physicalize('monto deuda dólares', m)                       -> 'MTO_DEU_USD'
      physicalize('cuenta riesgo', m, separator='')               -> 'CTARIESGO'
      physicalize('monto deuda', m, separator='', case='lower')   -> 'mtodeu'
      physicalize('monto deuda usd', m, case='camel')             -> 'mtoDeuUsd'
    """
    if case not in _VALID_CASES:
        raise ValueError(f"case debe ser uno de {_VALID_CASES}, no {case!r}")
    segments = _abbreviate(logical, mappings)
    if not segments:
        return ""
    if case == "camel":
        head, *tail = segments
        return head.lower() + "".join(s[:1].upper() + s[1:].lower() for s in tail)
    transform = str.upper if case == "upper" else str.lower
    return separator.join(transform(s) for s in segments)
