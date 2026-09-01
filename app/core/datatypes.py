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
CATALOG: frozenset[str] = frozenset({
    "INTEGER", "BIGINT", "SMALLINT", "TINYINT",
    "FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC", "NUMBER", "MONEY",
    "VARCHAR", "VARCHAR2", "CHAR", "TEXT", "NVARCHAR", "STRING",
    "BOOLEAN",
    "DATE", "DATETIME", "TIMESTAMP", "TIMESTAMPTZ", "TIME",
    "BINARY", "VARBINARY", "BLOB", "BYTES",
    "JSON", "JSONB", "XML", "UUID", "VARIANT",
    "ARRAY", "MAP", "STRUCT",
})

# Cantidad MÁXIMA de argumentos por tipo (typePick.ts ARG_SPECS).
ARG_SPECS: dict[str, int] = {
    "DECIMAL": 2, "NUMERIC": 2, "NUMBER": 2,
    "VARCHAR": 1, "CHAR": 1, "NVARCHAR": 1, "VARBINARY": 1, "VARCHAR2": 1,
}

# Sinónimos entre dialectos → base del catálogo (solo para HOMOLOGAR defaults
# de dominio; la carga masiva NO los acepta — su gramática queda intacta).
_BASE_ALIASES: dict[str, str] = {
    "BIGINTEGER": "BIGINT",         # «BIG INTEGER» (dominio BigInt del DDV)
    "INT": "INTEGER",
    "BOOL": "BOOLEAN",
    "DOUBLEPRECISION": "DOUBLE",
}

_SIMPLE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*(?:\(([^()]*)\))?$")
_COMPLEX_RE = re.compile(r"^(STRUCT|ARRAY|MAP)\s*<(.*)>$", re.IGNORECASE | re.DOTALL)
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Complejo "recién iniciado": la base sola o con <> vacío (`Array`, `MAP <>`).
_BARE_COMPLEX_RE = re.compile(r"^(STRUCT|ARRAY|MAP)\s*(?:<\s*>)?$", re.IGNORECASE)


def clean_text(value) -> str:
    """Celda → texto: None → "", no-texto → str(), CRLF/CR → LF, borde recortado."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def norm_type(value) -> str:
    """Clave de comparación de tipos de dato: MAYÚSCULAS sin espacios."""
    return re.sub(r"\s+", "", clean_text(value)).upper()


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
    base = m.group(1).upper()
    if base not in CATALOG:
        return None
    raw_args = m.group(2)
    if raw_args is None:
        return base
    args = [a.strip() for a in raw_args.split(",")]
    max_args = ARG_SPECS.get(base, 0)
    if not args or len(args) > max_args or any(not a.isdigit() for a in args):
        return None
    return f"{base}({','.join(args)})"


def _complex(text: str, extra_norm: dict[str, str]) -> str | None:
    m = _COMPLEX_RE.match(text)
    if not m:
        return None
    kind, inner = m.group(1).upper(), m.group(2).strip()
    if not inner:
        return None
    if kind == "ARRAY":
        elem = _canonical(inner, extra_norm)
        return f"ARRAY<{elem}>" if elem else None
    if kind == "MAP":
        parts = _split_top(inner, ",")
        if len(parts) != 2:
            return None
        key, val = (_canonical(p, extra_norm) for p in parts)
        return f"MAP<{key},{val}>" if key and val else None
    fields: list[str] = []
    for part in _split_top(inner, ","):
        name_type = _split_top(part, ":")
        if len(name_type) != 2:
            return None
        name, ftype = name_type[0].strip(), _canonical(name_type[1], extra_norm)
        if not _FIELD_NAME_RE.match(name) or not ftype:
            return None
        fields.append(f"{name}:{ftype}")
    return f"STRUCT<{','.join(fields)}>" if fields else None


def _canonical(text: str, extra_norm: dict[str, str]) -> str | None:
    t = clean_text(text)
    if not t:
        return None
    if "<" in t or ">" in t:
        return _complex(t, extra_norm)
    return _simple(t) or extra_norm.get(norm_type(t))


def canonical_type(text, extra: Iterable[str] = ()) -> str | None:
    """Forma canónica del tipo o None si no es válido. `extra` = tipos
    aceptados tal cual (defaults de parent domains), comparados sin caso ni
    espacios; se devuelve la grafía con la que existen en la plataforma."""
    extra_norm = {norm_type(e): clean_text(e) for e in extra if clean_text(e)}
    return _canonical(clean_text(text) if text is not None else "", extra_norm)


def canonicalize_default_type(text) -> str:
    """Homologa el `defaultDataType` de un parent domain a la grafía canónica
    de la plataforma (doc 62). Reglas, en orden:

    1) `ARRAY` / `MAP` / `STRUCT` "pelados" (o con `<>` vacío) → `X<>` — lo
       MISMO que emite el combobox al elegirlos (abre el builder del elemento).
    2) `canonical_type` (case/espacios: `decimal (22,4)` → `DECIMAL(22,4)`;
       complejos completos válidos pasan intactos).
    3) Sinónimos de dialecto (`BIG INTEGER` → `BIGINT`, `INT` → `INTEGER`) y
       nuevo intento canónico.
    4) Sin reconocer → VERBATIM (recortado): jamás se inventa un tipo.
    """
    t = clean_text(text)
    if not t:
        return ""
    m = _BARE_COMPLEX_RE.match(t)
    if m:
        return f"{m.group(1).upper()}<>"
    hit = canonical_type(t)
    if hit is not None:
        return hit
    base_part, sep, rest = t.partition("(")
    alias = _BASE_ALIASES.get(norm_type(base_part))
    if alias:
        hit = canonical_type(alias + (sep + rest if sep else ""))
        if hit is not None:
            return hit
    return t
