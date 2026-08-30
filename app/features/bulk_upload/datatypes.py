"""Gramática de tipos de dato de la carga masiva (doc 55 §5.5). Puro.

Espeja el combobox del front (`typePick.ts` + `complexTypes.ts`): catálogo
cerrado en MAYÚSCULAS, argumentos solo en los tipos que los llevan, y
complejos `ARRAY<T>` / `MAP<K,V>` / `STRUCT<nombre:T,…>` recursivos. Además
se aceptan tal cual los `defaultDataType` de los parent domains vivos
(`extra`): la data real trae `NUMBER(22,3)` o `VARCHAR2(10)` y un modelador
que copie el tipo de su dominio no debe ver un error.

`canonical_type` devuelve la forma canónica (base en MAYÚSCULAS, argumentos
compactos) o None si el texto no es un tipo válido.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .normalize import clean_text, norm_type

# Mismo catálogo que `COLUMN_DATA_TYPES` del front (src/types/model.ts).
CATALOG: frozenset[str] = frozenset({
    "INTEGER", "BIGINT", "SMALLINT", "TINYINT",
    "FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC", "MONEY",
    "VARCHAR", "CHAR", "TEXT", "NVARCHAR", "STRING",
    "BOOLEAN",
    "DATE", "DATETIME", "TIMESTAMP", "TIMESTAMPTZ", "TIME",
    "BINARY", "VARBINARY", "BLOB", "BYTES",
    "JSON", "JSONB", "XML", "UUID",
    "ARRAY", "MAP", "STRUCT",
})

# Cantidad MÁXIMA de argumentos por tipo (typePick.ts ARG_SPECS).
ARG_SPECS: dict[str, int] = {
    "DECIMAL": 2, "NUMERIC": 2,
    "VARCHAR": 1, "CHAR": 1, "NVARCHAR": 1, "VARBINARY": 1,
}

_SIMPLE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*(?:\(([^()]*)\))?$")
_COMPLEX_RE = re.compile(r"^(STRUCT|ARRAY|MAP)\s*<(.*)>$", re.IGNORECASE | re.DOTALL)
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
