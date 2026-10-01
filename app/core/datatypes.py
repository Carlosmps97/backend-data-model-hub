"""Gramática CANÓNICA de tipos de dato de la plataforma (doc 55 §5.5 + doc 62).

Vivía en `app/features/bulk_upload/datatypes.py`; se movió a core porque ya no
es solo de la carga masiva: los DTOs de parent domains (domains /
data_standards) y el kit de migración Erwin homologan `defaultDataType` con
`canonicalize_default_type`. Importar el módulo viejo sigue funcionando (shim
re-export) — y ESTE módulo es puro (stdlib), importable desde el kit/notebook
sin arrastrar FastAPI.

Espeja el combobox del front (`typePick.ts` + `complexTypes.ts`): catálogo
cerrado en MAYÚSCULAS, argumentos solo en los tipos que los llevan, y
complejos `ARRAY<T>` / `MAP<K,V>` / `STRUCT<nombre:T,…>` recursivos. Además
se aceptan tal cual los `defaultDataType` de los parent domains vivos
(`extra`): un modelador que copie el tipo de su dominio no debe ver un error.

`canonical_type` devuelve la forma canónica (base en MAYÚSCULAS, argumentos
compactos) o None si el texto no es un tipo válido.

Doc 106: el catálogo trae los tipos de Databricks SQL / Hive, Oracle y SQL
Server, sin validar por motor (un tipo que el motor no acepta es del modelador).
Los argumentos aceptan texto (`MAX`, `30 CHAR`, `*`, `-2`); solo se rechaza lo
que rompería el texto del tipo. Los tipos de varias palabras (`TIMESTAMP WITH
TIME ZONE`) van sin argumentos.

`canonicalize_default_type` (doc 62) homologa el default de un PARENT DOMAIN a
la grafía de la plataforma: `Array` → `ARRAY<>` (como elegir ARRAY en el
combobox), `BIG INTEGER` → `BIGINT`, `DECIMAL (22,4)` → `DECIMAL(22,4)`. Lo
que no se reconoce queda VERBATIM (jamás se inventa un tipo).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# Mismo catálogo que `COLUMN_DATA_TYPES` del front (src/types/model.ts).
# Doc 62 suma: VARIANT (JSON libre, semiestructurado) + NUMBER/VARCHAR2 (tipos
# Oracle presentes en los dominios de la data real: NUMBER(22,3), VARCHAR2(10)).
# Doc 106 suma los tipos de los cuatro motores (lista única, sin separar por motor).
CATALOG: frozenset[str] = frozenset({
    "INTEGER", "BIGINT", "SMALLINT", "TINYINT",
    "FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC", "NUMBER", "MONEY",
    "VARCHAR", "VARCHAR2", "CHAR", "TEXT", "NVARCHAR", "STRING",
    "BOOLEAN",
    "DATE", "DATETIME", "TIMESTAMP", "TIMESTAMPTZ", "TIME",
    "BINARY", "VARBINARY", "BLOB", "BYTES",
    "JSON", "JSONB", "XML", "UUID", "VARIANT",
    "ARRAY", "MAP", "STRUCT",
    # Doc 106 · Databricks SQL / Hive
    "INT", "TIMESTAMP_NTZ", "DOUBLE PRECISION", "GEOGRAPHY", "GEOMETRY",
    # Doc 106 · Oracle
    "NVARCHAR2", "NCHAR", "BINARY_FLOAT", "BINARY_DOUBLE", "LONG", "LONG RAW", "RAW", "ROWID",
    "UROWID", "CLOB", "NCLOB", "BFILE", "XMLTYPE", "VECTOR",
    "TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITH LOCAL TIME ZONE",
    "INTERVAL YEAR TO MONTH", "INTERVAL DAY TO SECOND",
    # Doc 106 · SQL Server
    "BIT", "SMALLMONEY", "DATETIME2", "DATETIMEOFFSET", "SMALLDATETIME", "NTEXT", "IMAGE",
    "UNIQUEIDENTIFIER", "SQL_VARIANT", "HIERARCHYID", "ROWVERSION",
})

# Cantidad MÁXIMA de argumentos por tipo (typePick.ts ARG_SPECS); todos opcionales.
ARG_SPECS: dict[str, int] = {
    "DECIMAL": 2, "NUMERIC": 2, "NUMBER": 2,
    "VARCHAR": 1, "CHAR": 1, "NVARCHAR": 1, "VARBINARY": 1, "VARCHAR2": 1,
    # Doc 106
    "NVARCHAR2": 1, "NCHAR": 1, "RAW": 1, "UROWID": 1, "BINARY": 1,
    "FLOAT": 1, "TIMESTAMP": 1, "TIME": 1, "DATETIME2": 1, "DATETIMEOFFSET": 1,
    "VECTOR": 2, "GEOGRAPHY": 1, "GEOMETRY": 1,
}

# Sinónimos entre dialectos → base del catálogo (solo para HOMOLOGAR defaults
# de dominio; la carga masiva NO los acepta — su gramática queda intacta).
# Doc 106: `INT` y `DOUBLE PRECISION` dejan de ser sinónimos (son del catálogo).
_BASE_ALIASES: dict[str, str] = {
    "BIGINTEGER": "BIGINT",         # «BIG INTEGER» (dominio BigInt del DDV)
    "BOOL": "BOOLEAN",
}

# Base de una o más palabras (`TIMESTAMP WITH TIME ZONE`) + argumentos opcionales.
_SIMPLE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*(?:\s+[A-Za-z][A-Za-z0-9_]*)*)\s*(?:\(([^()]*)\))?$")
# Doc 106: un argumento es un número o una palabra (`MAX`, `*`, `-2`, `FLOAT32`),
# con una unidad opcional que es una PALABRA (`30 CHAR`; `18 2` no). Nada que
# rompa el texto del tipo. Solo ASCII (se valida ANTES de pasar a MAYÚSCULA).
_ARG_RE = re.compile(r"^-?[A-Za-z0-9_*]+(?: [A-Za-z][A-Za-z0-9_]*)?$")
_COMPLEX_RE = re.compile(r"^(STRUCT|ARRAY|MAP)\s*<(.*)>$", re.IGNORECASE | re.DOTALL)
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Complejo "recién iniciado": la base sola o con <> vacío (`Array`, `MAP <>`).
_BARE_COMPLEX_RE = re.compile(r"^(STRUCT|ARRAY|MAP)\s*(?:<\s*>)?$", re.IGNORECASE)
# Doc 96 D8: complejo CON estructura (`ARRAY<STRING>`, `STRUCT<a:INT>`…), no el
# genérico vacío (`ARRAY<>` / `Array`, como un VARCHAR sin tamaño).
_COMPLEX_CONTENT_RE = re.compile(r"^\s*(STRUCT|ARRAY|MAP)\s*<\s*[^\s>]", re.IGNORECASE)
# Arranca como complejo (`ARRAY<`…) — espejo de `isComplexTypeStart` del front.
_COMPLEX_START_RE = re.compile(r"^\s*(STRUCT|ARRAY|MAP)\s*<", re.IGNORECASE)


def clean_text(value) -> str:
    """Celda → texto: None → "", no-texto → str(), CRLF/CR → LF, borde recortado."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def norm_type(value) -> str:
    """Clave de comparación de tipos de dato: MAYÚSCULAS sin espacios."""
    return re.sub(r"\s+", "", clean_text(value)).upper()


def fold_type_whitespace(value) -> str:
    """Doc 92 D6: texto de un tipo en UNA línea compacta — saltos de línea y
    tabulaciones a un espacio, y sin espacios pegados a `< > , : ( )`. Para
    tipos complejos que no parsean (p.ej. campos `@param1`) y que igual deben
    salir enteros en el DDL. Casing intacto."""
    text = re.sub(r"\s+", " ", clean_text(value))
    return re.sub(r"\s*([<>,:()])\s*", r"\1", text).strip()


def _split_top(inner: str, sep: str) -> list[str]:
    """Corta por `sep` a profundidad 0 (respeta `<>` y `()` anidados)."""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in inner:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
            if depth < 0:
                return []  # desbalanceado
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if depth != 0:
        return []
    out.append("".join(cur))
    return out


def _simple(text: str) -> str | None:
    m = _SIMPLE_RE.match(text)
    if not m:
        return None
    base = " ".join(m.group(1).split()).upper()
    if base not in CATALOG:
        return None
    raw_args = m.group(2)
    if raw_args is None:
        return base
    args = [" ".join(a.split()) for a in raw_args.split(",")]
    max_args = ARG_SPECS.get(base, 0)
    if not args or len(args) > max_args or any(not _ARG_RE.match(a) for a in args):
        return None
    return f"{base}({','.join(a.upper() for a in args)})"


def _complex(text: str, extra_norm: dict[str, str], aliases: bool = False) -> str | None:
    m = _COMPLEX_RE.match(text)
    if not m:
        return None
    kind, inner = m.group(1).upper(), m.group(2).strip()
    if not inner:
        return None
    if kind == "ARRAY":
        elem = _canonical(inner, extra_norm, aliases)
        return f"ARRAY<{elem}>" if elem else None
    if kind == "MAP":
        parts = _split_top(inner, ",")
        if len(parts) != 2:
            return None
        key, val = (_canonical(p, extra_norm, aliases) for p in parts)
        return f"MAP<{key},{val}>" if key and val else None
    fields: list[str] = []
    for part in _split_top(inner, ","):
        name_type = _split_top(part, ":")
        if len(name_type) != 2:
            return None
        name, ftype = name_type[0].strip(), _canonical(name_type[1], extra_norm, aliases)
        if not _FIELD_NAME_RE.match(name) or not ftype:
            return None
        fields.append(f"{name}:{ftype}")
    return f"STRUCT<{','.join(fields)}>" if fields else None


def _simple_alias(text: str) -> str | None:
    """`_simple` tras mapear la base por sinónimo de dialecto (`BIG INTEGER` →
    `BIGINT`, `bool` → `BOOLEAN`). Sólo para HOMOLOGAR (aliases=True)."""
    base_part, sep, rest = text.partition("(")
    alias = _BASE_ALIASES.get(norm_type(base_part))
    return _simple(alias + (sep + rest if sep else "")) if alias else None


def _canonical(text: str, extra_norm: dict[str, str], aliases: bool = False) -> str | None:
    t = clean_text(text)
    if not t:
        return None
    if "<" in t or ">" in t:
        return _complex(t, extra_norm, aliases)
    hit = _simple(t)
    if hit is None and aliases:
        # Doc 92 D6: los sinónimos también valen DENTRO de un tipo complejo
        # (`struct<activo:bool, fecha:date>` de Erwin) — antes sólo en la base.
        hit = _simple_alias(t)
    return hit or extra_norm.get(norm_type(t))


def canonical_type(text, extra: Iterable[str] = (), aliases: bool = False) -> str | None:
    """Forma canónica del tipo o None si no es válido. `extra` = tipos
    aceptados tal cual (defaults de parent domains), comparados sin caso ni
    espacios; se devuelve la grafía con la que existen en la plataforma.
    `aliases=True` acepta además los sinónimos de dialecto en cualquier nivel
    (homologación); la carga masiva NO los pasa (gramática estricta)."""
    extra_norm = {norm_type(e): clean_text(e) for e in extra if clean_text(e)}
    return _canonical(clean_text(text) if text is not None else "", extra_norm, aliases)


def canonicalize_default_type(text) -> str:
    """Homologa el `defaultDataType` de un parent domain a la grafía canónica
    de la plataforma (doc 62). Reglas, en orden:

    1) `ARRAY` / `MAP` / `STRUCT` "pelados" (o con `<>` vacío) → `X<>` — lo
       MISMO que emite el combobox al elegirlos (abre el builder del elemento).
    2) `canonical_type` (case/espacios: `decimal (22,4)` → `DECIMAL(22,4)`;
       complejos completos válidos pasan intactos).
    3) Sinónimos de dialecto (`BIG INTEGER` → `BIGINT`, `bool` → `BOOLEAN`) y
       nuevo intento canónico.
    4) Sin reconocer → VERBATIM (recortado): jamás se inventa un tipo.
    """
    t = clean_text(text)
    if not t:
        return ""
    m = _BARE_COMPLEX_RE.match(t)
    if m:
        return f"{m.group(1).upper()}<>"
    hit = canonical_type(t, aliases=True)
    if hit is not None:
        return hit
    # Doc 92 D6: un complejo que no parsea (p.ej. campos `@param1`) sale
    # verbatim pero en UNA línea compacta — jamás partido por saltos de línea.
    return fold_type_whitespace(t) if "<" in t else t


def has_complex_content(value) -> bool:
    """¿STRUCT/ARRAY/MAP con estructura adentro? `ARRAY<>` y `Array` no. Puro."""
    return bool(_COMPLEX_CONTENT_RE.match(value or ""))


def mirror_complex(source, target):
    """Doc 96 D8: un tipo complejo es el MISMO en las dos facetas (su estructura
    es propia de cada columna). Si `source` es un complejo con estructura y
    `target` no lo es (vacío, `ARRAY<>`, el `CHAR(18)` por defecto de Erwin u
    otro simple), `target` toma `source`; si no, queda como está. Direccional:
    la faceta que manda es `source`. Puro (espejo de `mirrorComplex` del front)."""
    if has_complex_content(source) and not has_complex_content(target):
        return source
    return target


def is_complete_simple_type(value) -> bool:
    """¿Un tipo SIMPLE completo (`STRING`, `VARCHAR(20)`, alias como `BOOL`)? Los
    complejos, el genérico `Array` y los textos a medio tipear (`S`) no. Puro."""
    t = clean_text(value)
    if not t or "<" in t or _BARE_COMPLEX_RE.match(t):
        return False
    return canonical_type(t, aliases=True) is not None


def synced_other_facet(next_type, prev_this, prev_other):
    """Doc 96 D8 (espejo de `syncedOtherFacet` del front): al poner `next_type` en
    una faceta, qué toma la otra — `next_type` si es complejo, o si las dos eran
    el MISMO tipo y ese tipo no era un simple completo (un complejo o un estado
    intermedio: siguen enlazadas hasta salir juntas); si no, queda como estaba.
    Los simples siguen independientes (doc 69). Puro."""
    if _COMPLEX_START_RE.match(next_type or ""):
        return next_type
    this = clean_text(prev_this)
    if this and clean_text(prev_other) == this and not is_complete_simple_type(this):
        return next_type
    return prev_other
