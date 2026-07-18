"""Decisiones de negocio de la migración Erwin (owner, 2026-07-12, doc 12 §8).

Reglas aprobadas:
  - Tablas/vistas sin Hive_Database → schema "No_Definido".
  - Columnas duplicadas dentro de un objeto → se conserva la 1ª (por orden
    físico) y se descartan las demás.
  - Vistas: TODAS se migran con showOnCanvas=True.
  - Anotaciones de diagrama: se descartan.
  - Índices (Key_Group IF*): NO se migran (pendiente decidir feature web).
  - UDP: Entity→'table', Attribute→'column', Model→'canvas'; el resto de
    niveles Erwin (View, Key_Group, Relationship, Hive_Database) se omite.
    Las defs Logical/Physical con el mismo nombre se COLAPSAN en una sola
    (si un objeto tiene valor en ambas, gana la Physical).
  - Glosario: scope='column', wordType=None (defaults de plataforma).
  - Cardinalidad del lado PADRE: del Null_Option_Type de la relación
    ("100" nulls allowed → 0..1; "101"/otro → exactamente 1) — 2026-07-17.
  - Partición (2026-07-17): el UDP de columna "Particion" con valor PART_nn
    marca `isPartition` NATIVO; el correlativo nn debe coincidir con el orden
    físico (el DDL de la plataforma ordena la partición por orden físico).
    Tablas incongruentes (correlativo duplicado u orden distinto) NO se
    marcan — se reportan para decisión del owner; el valor UDP queda visible.
"""
from __future__ import annotations

import re
import uuid

NO_SCHEMA = "No_Definido"

# Namespace determinista: mismo Long_Id de Erwin → mismo id de plataforma en
# cualquier corrida/archivo (idempotencia y dedup cross-archivo).
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://data-model-hub/erwin-migration")

# Erwin Cardinality → cardinalidad del lado hijo en la plataforma.
_CARDINALITY = {"-3": "zero-many", "-1": "one-many", "-2": "zero-one"}

# Nivel Erwin (owner_class de la def UDP) → nivel de plataforma.
UDP_LEVEL_MAP = {"Entity": "table", "Attribute": "column", "Model": "canvas"}


def platform_id(erwin_long_id: str) -> str:
    """Id determinista de plataforma para un Long_Id de Erwin."""
    return str(uuid.uuid5(_NS, erwin_long_id))


def schema_or_default(schema: str | None) -> str:
    return (schema or "").strip() or NO_SCHEMA


def dedupe_columns(attrs: list) -> tuple[list, list]:
    """Conserva la 1ª ocurrencia (case-insensitive) por orden físico.

    Devuelve (columnas_únicas, descartadas).
    """
    seen: set[str] = set()
    keep, dropped = [], []
    for a in attrs:
        key = a.physical.upper()
        if key in seen:
            dropped.append(a)
        else:
            seen.add(key)
            keep.append(a)
    return keep, dropped


def map_cardinality(erwin_code: str) -> str:
    """Código de cardinalidad Erwin → cardinalidad plataforma (lado hijo)."""
    return _CARDINALITY.get(erwin_code, "many")


def map_parent_cardinality(null_option: str) -> str:
    """Null_Option_Type de la relación → cardinalidad del lado PADRE.

    "100" (Nulls Allowed: la FK del hijo admite NULL) → 'zero-one';
    "101" (No Nulls) u otro/vacío → 'one'. Ver constantes en erwin_parser."""
    return "zero-one" if null_option.strip() == "100" else "one"


# ── Partición nativa desde el UDP de Erwin (convención DDV: PART_01, PART_02…) ─

# Clave COLAPSADA de la def UDP que Erwin usa para particiones (nivel columna).
PARTITION_UDP_KEY = "column|Particion"
_PARTITION_RE = re.compile(r"^PART[_-]?(\d+)$", re.IGNORECASE)


def partition_correlative(value: str | None) -> int | None:
    """Valor UDP → correlativo de partición (PART_01 → 1). Valores que no
    siguen la convención ("No Definido", vacío) NO son partición → None."""
    m = _PARTITION_RE.match((value or "").strip())
    return int(m.group(1)) if m else None


def partition_marks(vals: list[tuple[str, int]]) -> tuple[set[str], str | None]:
    """Qué columnas de una tabla marcar `isPartition` a partir de sus UDP.

    `vals` = [(attr_id, correlativo)] EN ORDEN FÍSICO. La plataforma emite el
    `PARTITIONED BY` en orden físico, así que sólo es seguro marcar cuando el
    orden por correlativo coincide con el físico y no hay correlativos
    duplicados (huecos 1..n se toleran: el orden sigue bien definido).

    Devuelve (ids_a_marcar, None) si es congruente, o (set(), motivo) si NO —
    en ese caso no se marca nada y el motivo va al reporte para el owner."""
    if not vals:
        return set(), None
    nums = [n for _, n in vals]
    if len(nums) != len(set(nums)):
        dup = sorted({n for n in nums if nums.count(n) > 1})
        return set(), f"correlativo duplicado (PART_{dup[0]:02d} aparece {nums.count(dup[0])} veces)"
    if nums != sorted(nums):
        return set(), ("orden por correlativo ≠ orden físico "
                       f"(correlativos en orden físico: {nums})")
    return {aid for aid, _ in vals}, None


def udp_datatype(code: str) -> str:
    """tag_Udp_Data_Type de Erwin → dataType de UdpDefinitionDoc."""
    return "list" if code == "6" else "string"


def collapse_udp_defs(defs: dict) -> dict[str, dict]:
    """Colapsa defs Logical/Physical homónimas por (nivel plataforma, nombre).

    Devuelve {clave: {"name", "level", "dataType", "default", "erwin_ids",
    "physical_ids"}} solo para niveles soportados. `erwin_ids` = todas las
    defs Erwin que caen en esta def de plataforma; `physical_ids` = las de
    vista Physical (sus valores ganan en conflicto).
    """
    out: dict[str, dict] = {}
    for d in defs.values():
        level = UDP_LEVEL_MAP.get(d.owner_class)
        if not level:
            continue
        key = f"{level}|{d.short_name}"
        entry = out.setdefault(key, {
            "name": d.short_name, "level": level,
            "dataType": udp_datatype(d.data_type_code),
            "default": d.default or None,
            "erwin_ids": set(), "physical_ids": set(),
        })
        entry["erwin_ids"].add(d.id)
        if d.view_mode == "Physical":
            entry["physical_ids"].add(d.id)
        if udp_datatype(d.data_type_code) == "list":
            entry["dataType"] = "list"
        if not entry["default"] and d.default:
            entry["default"] = d.default
    return out


def resolve_udp_values(udp_values: list, collapsed: dict) -> dict[tuple[str, str], str]:
    """(owner_id, clave_def_colapsada) → valor final (Physical pisa Logical)."""
    def_to_key: dict[str, tuple[str, bool]] = {}
    for key, entry in collapsed.items():
        for did in entry["erwin_ids"]:
            def_to_key[did] = (key, did in entry["physical_ids"])
    out: dict[tuple[str, str], str] = {}
    phys_won: set[tuple[str, str]] = set()
    for owner_id, _owner_tag, def_id, value in udp_values:
        if def_id not in def_to_key or not value:
            continue
        key, is_phys = def_to_key[def_id]
        slot = (owner_id, key)
        if slot in phys_won:
            continue
        out[slot] = value
        if is_phys:
            phys_won.add(slot)
    return out
