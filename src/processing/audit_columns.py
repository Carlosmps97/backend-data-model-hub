"""Inyección determinista de columnas de auditoría desde los guidelines.

Las guidelines corporativas declaran un bloque `audit_columns` con la
lista exacta de columnas técnicas que deben aparecer en TODA tabla
(excepto tablas estáticas/temporales, según el flag `applies_to`).

En el pipeline anterior, el LLM era responsable de "no olvidar" estas
columnas. Esto fallaba con frecuencia (se omitían tscreated, srcbatchid,
etc., o se renombraban incorrectamente). Ahora las inyectamos en código,
después del LLM, garantizando 100% que aparezcan TAL CUAL las define el
usuario en sus guidelines.
"""

from __future__ import annotations

from typing import Any

from src.schemas import ColumnAPI


# Tipos de tabla a los que NO se les inyectan audit columns por defecto.
# Coincide con la doc del bloque `audit_columns.applies_to` ("ALL tables
# except static reference tables and temporary tables") para los prefijos
# canónicos de los guidelines.
_NO_AUDIT_TABLE_PREFIXES: tuple[str, ...] = ("r", "t")


def _coerce_bool(value: Any, default: bool = True) -> bool:
    """Lee un bool tolerante (true/'true'/1/...)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "y", "si", "sí"):
            return True
        if v in ("false", "0", "no", "n"):
            return False
    return default


def get_audit_column_names(guidelines: dict | None) -> set[str]:
    """Devuelve el set de nombres de las audit columns declaradas.

    Útil para deduplicar (no inyectar dos veces si el LLM ya las emitió).
    """
    if not guidelines:
        return set()
    block = guidelines.get("audit_columns") or {}
    cols = block.get("columns") or []
    return {
        str(c.get("column_name") or "").strip().lower()
        for c in cols
        if c.get("column_name")
    }


def build_audit_columns(guidelines: dict | None) -> list[ColumnAPI]:
    """Materializa los `ColumnAPI` correspondientes a las audit columns.

    Lee el bloque `audit_columns.columns` del JSON de guidelines y
    construye un `ColumnAPI` por cada uno preservando nombre, tipo,
    nullability, default y descripción TAL CUAL los puso el usuario.

    Si los guidelines no tienen el bloque, devuelve lista vacía y NO
    falla — se asume que el usuario quiere desactivar audit columns.
    """
    if not guidelines:
        return []
    block = guidelines.get("audit_columns") or {}
    raw_cols = block.get("columns") or []
    out: list[ColumnAPI] = []
    for raw in raw_cols:
        name = str(raw.get("column_name") or "").strip()
        if not name:
            continue
        observations_parts: list[str] = []
        default_value = raw.get("default")
        if default_value not in (None, ""):
            observations_parts.append(f"default={default_value}")
        observations_parts.append("audit column")
        out.append(
            ColumnAPI(
                column_name=name,
                data_type=str(raw.get("data_type") or "STRING"),
                nullable=_coerce_bool(raw.get("nullable"), default=False),
                is_pk=False,
                is_fk=False,
                fk_references=None,
                functional_definition=str(raw.get("description") or ""),
                observations=" | ".join(observations_parts),
            )
        )
    return out


def should_inject_audit(table_name: str) -> bool:
    """Decide si una tabla recibe audit columns según su prefijo.

    Aplica la regla de los guidelines: las tablas de referencia (`r*`)
    y temporales (`t*`) NO requieren audit columns. El resto sí.

    Si el nombre no tiene prefijo identificable, asumimos que SÍ
    (criterio conservador: preferimos exceso a faltante).
    """
    if not table_name:
        return True
    first_segment = table_name.split("_", 1)[0].lower()
    if not first_segment:
        return True
    # Primer carácter del primer segmento define el tipo.
    type_char = first_segment[0]
    return type_char not in _NO_AUDIT_TABLE_PREFIXES


def inject_audit_columns(
    columns: list[ColumnAPI],
    table_name: str,
    guidelines: dict | None,
) -> list[ColumnAPI]:
    """Devuelve la lista de columnas con las audit columns inyectadas al final.

    Reglas:
    - Si el tipo de tabla no las requiere (r*, t*), retorna `columns` intacto.
    - Si una audit column ya existe (case-insensitive) entre las del LLM,
      NO se duplica — gana la versión del LLM (puede haberla refinado).
    - Las audit columns se agregan SIEMPRE al final, en el orden
      declarado por los guidelines (orden estable y predecible).
    """
    if not should_inject_audit(table_name):
        return list(columns)

    audit = build_audit_columns(guidelines)
    if not audit:
        return list(columns)

    existing = {c.column_name.strip().lower() for c in columns}
    extras = [c for c in audit if c.column_name.strip().lower() not in existing]
    return list(columns) + extras
