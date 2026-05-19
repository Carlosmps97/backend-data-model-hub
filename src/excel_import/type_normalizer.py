"""Normalización robusta de tipos de datos provenientes del Excel.

Pipeline determinista en cuatro pasos — diseñado para mantenerse sin
crecer un árbol de `if` cada vez que aparece un alias nuevo:

    raw → tokenize → exact lookup → alias lookup → fuzzy fallback

1. *Tokenize*: separa la base (`decimal`) de sus parámetros (`10, 2`).
   La regex acepta espacios, mayúsculas y formatos comunes
   (`VARCHAR(50)`, `decimal( 10 , 2 )`, `character varying(120)`).
2. *Exact*: si la base coincide con un tipo canónico, terminó.
3. *Alias*: tabla pequeña de sinónimos no ambiguos (`int → integer`,
   `bool → boolean`). NO se incluyen variantes con typo aquí — esas
   las resuelve el paso 4.
4. *Fuzzy*: `rapidfuzz.process.extractOne` contra el universo canónico
   + alias. Si la similitud supera `FUZZY_THRESHOLD` se acepta; si no,
   se devuelve el token original con `matched_via="unknown"` y
   `confidence=0` para que el frontend lo pueda mostrar en crudo y el
   usuario decida.

El módulo no asume un engine concreto: las constantes incluyen los
tipos canónicos que el resto del app maneja (`COLUMN_DATA_TYPES` en
`types/model.ts`) más los nombres nativos de los engines soportados
(Databricks/Spark: `string`; Postgres: `timestamptz`, `jsonb`; etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process


# ─── Constantes ────────────────────────────────────────────────────────────

#: Tipos canónicos reconocidos. Cualquier valor fuera de este set será
#: tratado como sospechoso por la capa de UI (badge "unknown"), pero no
#: bloquea la importación — el usuario lo puede corregir en el preview.
CANONICAL_TYPES: tuple[str, ...] = (
    # Números enteros
    "integer", "bigint", "smallint", "tinyint",
    # Números reales
    "float", "double", "real", "decimal", "numeric", "money",
    # Texto
    "varchar", "char", "text", "nvarchar", "string",
    # Booleano
    "boolean",
    # Tiempo
    "date", "datetime", "timestamp", "timestamptz", "time",
    # Binario
    "binary", "varbinary", "blob", "bytes",
    # Semi-estructurado
    "json", "jsonb", "xml", "uuid", "variant",
    # Estructurado (Spark/Databricks)
    "array", "map", "struct"
)

#: Aliases NO ambiguos. Solo sinónimos directos — los typos se atrapan
#: en el paso fuzzy. Mantener esta tabla corta evita drift silencioso.
ALIASES: dict[str, str] = {
    # Enteros
    "int": "integer",
    "int2": "smallint",
    "int4": "integer",
    "int8": "bigint",
    "long": "bigint",
    "short": "smallint",
    "byte": "tinyint",
    # Booleano
    "bool": "boolean",
    # Texto
    "str": "string",
    "character": "char",
    "character varying": "varchar",
    "char varying": "varchar",
    "nchar": "char",
    # Tiempo
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    # Binario
    "bytea": "binary",
}

#: Umbral mínimo (0-100) para aceptar un match fuzzy. Calibrado para
#: que `decximam → decimal` (≈86) entre, pero `foobarbaz → ???` no.
FUZZY_THRESHOLD: float = 75.0

#: `decimal(10, 2)` → base=`decimal`, args=`10, 2`.
#: `character varying(120)` → base=`character varying`, args=`120`.
_TYPE_TOKEN_RE = re.compile(
    r"""
    ^\s*
    (?P<base>[A-Za-z][A-Za-z0-9 _]*?)   # nombre del tipo (acepta espacios internos)
    \s*
    (?:\(\s*(?P<args>[^)]*)\s*\))?       # parámetros opcionales entre paréntesis
    \s*$
    """,
    re.VERBOSE,
)

MatchKind = Literal["exact", "alias", "fuzzy", "unknown", "empty"]


@dataclass(frozen=True, slots=True)
class NormalizedType:
    """Resultado de normalizar una cadena de tipo de dato.

    Atributos:
        canonical:    tipo canónico (`decimal`, `varchar`, ...). Cuando
                      `matched_via == "unknown"` contiene la base tal cual la
                      escribió el usuario, en minúsculas — el frontend lo
                      muestra y permite corregirlo.
        raw:          input original sin tocar, útil para mostrar al usuario
                      qué se interpretó.
        length:       primer argumento entre paréntesis si aplica (`varchar(50)` → 50).
        scale:        segundo argumento entre paréntesis (`decimal(10, 2)` → 2).
        confidence:   0-100. 100 si fue match exacto o por alias; el score de
                      rapidfuzz si fue fuzzy; 0 si no hubo match.
        matched_via:  qué paso del pipeline produjo este resultado.
    """

    canonical: str
    raw: str
    length: int | None
    scale: int | None
    confidence: float
    matched_via: MatchKind


# ─── API pública ───────────────────────────────────────────────────────────


def normalize_type(raw: str | None, *, threshold: float = FUZZY_THRESHOLD) -> NormalizedType:
    """Normaliza un tipo de dato recibido del Excel.

    No lanza excepciones — incluso para entradas vacías o basura
    devuelve un `NormalizedType` válido para que el preview siempre
    pueda renderizarse.
    """
    raw_clean = (raw or "").strip()
    if not raw_clean:
        return NormalizedType(
            canonical="varchar",
            raw=raw or "",
            length=None,
            scale=None,
            confidence=0.0,
            matched_via="empty",
        )

    base, length, scale = _tokenize(raw_clean)

    # Paso 1: match exacto contra canónicos.
    if base in CANONICAL_TYPES:
        return NormalizedType(base, raw_clean, length, scale, 100.0, "exact")

    # Paso 2: alias directo.
    if base in ALIASES:
        return NormalizedType(ALIASES[base], raw_clean, length, scale, 100.0, "alias")

    # Paso 3: fuzzy contra el universo (canónicos + alias) — devuelve el match
    # más cercano sobre el threshold.
    canonical, confidence = _fuzzy_match(base, threshold=threshold)
    if canonical is not None:
        return NormalizedType(canonical, raw_clean, length, scale, confidence, "fuzzy")

    # Paso 4: desconocido. Se conserva el token tal cual para que el usuario
    # lo edite en el modal de previsualización.
    return NormalizedType(base, raw_clean, length, scale, 0.0, "unknown")


# ─── Internos ──────────────────────────────────────────────────────────────


def _tokenize(raw: str) -> tuple[str, int | None, int | None]:
    """Separa `decimal(10, 2)` en base/length/scale.

    Si la cadena no matchea el patrón esperado (cosa rara, ya que
    `_TYPE_TOKEN_RE` es muy permisiva), devuelve la cadena entera en
    minúsculas como base, sin parámetros. Cualquier argumento no
    numérico se ignora silenciosamente.
    """
    match = _TYPE_TOKEN_RE.match(raw)
    if not match:
        return raw.lower(), None, None

    base = _collapse_whitespace(match.group("base")).lower()
    args_str = match.group("args")
    if not args_str:
        return base, None, None

    numbers: list[int] = []
    for part in args_str.split(","):
        part = part.strip()
        if part.isdigit():
            numbers.append(int(part))

    length = numbers[0] if len(numbers) >= 1 else None
    scale = numbers[1] if len(numbers) >= 2 else None
    return base, length, scale


def _fuzzy_match(base: str, *, threshold: float) -> tuple[str | None, float]:
    """Devuelve `(canonical, score)` o `(None, 0)` si nada supera el umbral.

    Busca contra el universo canónicos+alias. Cuando el match cae sobre
    un alias, lo resolvemos a su canónico antes de devolverlo, de modo
    que el resto del sistema solo ve nombres canónicos.

    Usamos `fuzz.ratio` (Levenshtein puro normalizado) y no `WRatio`: en
    tokens cortos como los de tipos de datos, WRatio infla el score si
    una opción está contenida dentro del input (`"datatime"` contiene
    `"time"` → match espurio de 90). `ratio` penaliza inserciones y
    borrados de forma simétrica, que es lo que queremos para corregir
    typos sin saltar a un tipo más corto por sub-cadena.
    """
    universe = list(CANONICAL_TYPES) + list(ALIASES.keys())
    result = process.extractOne(base, universe, scorer=fuzz.ratio)
    if result is None:
        return None, 0.0

    match, score, _ = result
    if score < threshold:
        return None, 0.0

    canonical = ALIASES.get(match, match)
    return canonical, float(score)


_WS_RE = re.compile(r"\s+")


def _collapse_whitespace(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()
