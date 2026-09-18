"""Normalización de texto de la carga masiva (doc 55). Puro.

- `clean_text`: la celda tal cual (definiciones funcionales con saltos de
  línea, tildes y ñ), solo recortada y con CRLF → LF.
- `norm_ci`: clave de comparación case-insensitive de nombres (lógicos,
  físicos, proyectos, carpetas, canvases, esquemas, dominios).
- `norm_key`: clave de matching por NOMBRE de UDP (doc 78: solo para las
  sugerencias del editor de perfiles y la detección de «Particion») — sin
  tildes, `_`/`-` como espacio, sin stopwords (`UDP_Tipo_Vista` ≡ «Tipo de
  Vista», `UDP_Clasificacion_del_Dato` ≡ «Clasificación del Dato»).
- `norm_enum`: la MISMA regla del kit Erwin (doc 32b A3) para valores de lista.
- `is_pk_mark` / `partition_correlative`: columna PK de la hoja y `PART_nn`.
- `norm_type`: comparación de tipos (sin caso ni espacios).
"""
from __future__ import annotations

import re
import unicodedata

_STOPWORDS = frozenset({"de", "del", "la", "el", "los", "las", "y"})
_PK_MARKS = frozenset({"x", "si", "sí", "yes", "y", "1", "true", "pk"})
_PARTITION_RE = re.compile(r"^PART[_-]?(\d+)$", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def clean_text(value) -> str:
    """Celda → texto: None → "", no-texto → str(), CRLF/CR → LF, borde recortado.
    Los saltos de línea INTERNOS se conservan (definiciones funcionales)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def norm_ci(value) -> str:
    """Clave case-insensitive: minúsculas + espacios internos colapsados."""
    return _WS_RE.sub(" ", clean_text(value)).lower()


def strip_accents(text: str) -> str:
    """'Clasificación' → 'Clasificacion' (NFKD sin marcas combinantes)."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


def norm_name(value) -> str:
    """Clave de nombres «humanos» (proyectos, carpetas, canvases, esquemas,
    dominios, lógicos): `norm_ci` + sin tildes — «Código Clave» ≡ «codigo clave»."""
    return norm_ci(strip_accents(clean_text(value)))


def _udp_words(value) -> list[str]:
    text = strip_accents(clean_text(value)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [w for w in text.split() if w not in _STOPWORDS]
    if words and words[0] == "udp":
        words = words[1:]
    return words


def norm_key(value) -> str:
    """Clave de matching de cabeceras/nombres de UDP (ver docstring del módulo).
    Prefijo `udp` opcional; devuelve palabras significativas en minúsculas."""
    return " ".join(_udp_words(value))


def norm_enum(value) -> str:
    """Clave de comparación de un valor de enum UDP (doc 32b A3): trim,
    espacios internos colapsados, MAYÚSCULAS."""
    return _WS_RE.sub(" ", clean_text(value)).upper()


def is_pk_mark(value) -> bool:
    """`X` (la marca de la plantilla) y sinónimos obvios → True."""
    return clean_text(value).lower() in _PK_MARKS


def partition_correlative(value) -> int | None:
    """`PART_01` → 1 (misma convención del kit Erwin); otro valor → None."""
    m = _PARTITION_RE.match(clean_text(value))
    return int(m.group(1)) if m else None


def norm_type(value) -> str:
    """Clave de comparación de tipos de dato: MAYÚSCULAS sin espacios."""
    return re.sub(r"\s+", "", clean_text(value)).upper()


def clean_logical(text: str, sheet: str, row, column: str, rb) -> str:
    """Doc 92 D8: nombre lógico sin caracteres especiales (letras, dígitos,
    espacio y `_`). Si la celda traía otros caracteres, deja el valor limpio y
    lo AVISA en el reporte de carga (`special-chars-removed`) — el choke point
    del changeset lo limpiaría igual, pero así el modelador lo ve."""
    from app.core.naming.logical import sanitize_logical_name
    clean = sanitize_logical_name(text)
    if clean != (text or "").strip():
        rb.warning(sheet, "special-chars-removed",
                   f"'{text}' has special characters — saved as '{clean}' (letters, digits, spaces and _ only).",
                   row=row, column=column)
    return clean
