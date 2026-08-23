"""Decisiones de negocio de la migración Erwin (owner, doc 12 §8 + doc 32b).

Reglas aprobadas:
  - Tablas/vistas sin Hive_Database → schema "No_Definido".
  - Columnas duplicadas dentro de un objeto → queda UNA: la de MAYOR metadata
    (participa en relaciones, PK, definición, dominio, UDPs — doc 32b R5);
    empate total → la 1ª por orden físico (regla original).
  - Vistas: TODAS se migran con showOnCanvas=True; vistas sin fuente física
    se DESCARTAN (owner 2026-07-24).
  - Anotaciones de diagrama: se descartan.
  - Índices (Key_Group IF*): NO se migran (pendiente decidir feature web).
  - UDP: Entity→'table', Attribute→'column', Model→'canvas'; el resto de
    niveles Erwin (View, Key_Group, Relationship, Hive_Database) se omite.
    Las defs Logical/Physical con el mismo nombre se COLAPSAN en una sola
    (si un objeto tiene valor en ambas, gana la Physical).
  - Glosario: scope='column', wordType=None (defaults de plataforma).
  - Cardinalidad del lado PADRE: del Null_Option_Type de la relación
    ("100" nulls allowed → 0..1; "101"/otro → exactamente 1) — 2026-07-17.
  - Partición (v2, owner 2026-07-24, doc 32b R6): TODA columna con UDP
    "Particion" = PART_nn se marca `isPartition`; el orden EFECTIVO es el
    orden físico (lo que el DDL emite). Correlativos duplicados o en desorden
    se entienden REASIGNADOS por orden físico y se reportan; el valor UDP
    queda visible tal cual llegó.
  - Multi-archivo (doc 32b, owner 2026-07-24): un modelo Erwin puede venir
    partido en ~15 archivos. La identidad cross-archivo es la CLAVE NATURAL
    (schema+nombre); en conflicto de versiones gana la de MÁS USO
    (`score_usage`); los enums de UDP convergen case/espacios-insensitive.
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


def dedupe_columns(attrs: list, scorer=None) -> tuple[list, list]:
    """Dedup de columnas homónimas (case-insensitive) dentro de un objeto.

    Sin `scorer`: conserva la 1ª por orden físico (regla original — la usan
    quality/extract para reportar). Con `scorer` (callable attr → tupla
    comparable, doc 32b R5): conserva la copia de MAYOR score — "la de más
    metadata" — manteniendo su posición física; empate → la 1ª.

    Devuelve (columnas_únicas EN ORDEN FÍSICO, descartadas).
    """
    by_key: dict[str, list] = {}
    order: list[str] = []
    for a in attrs:
        key = a.physical.upper()
        if key not in by_key:
            order.append(key)
        by_key.setdefault(key, []).append(a)
    keep, dropped = [], []
    winners: dict[int, object] = {}
    for key in order:
        copies = by_key[key]
        if len(copies) == 1 or scorer is None:
            win = copies[0]
        else:
            win = max(copies, key=scorer)  # max estable: empate → la 1ª
        winners[id(win)] = win
        dropped.extend(a for a in copies if a is not win)
    keep = [a for a in attrs if id(a) in winners]
    return keep, dropped


def column_score(attr, *, fk_attr_ids: set[str] = frozenset(),
                 pk_attr_ids: set[str] = frozenset(),
                 udp_count: int = 0) -> tuple:
    """Score de una copia de columna (doc 32b R5): gana la de MÁS metadata.

    Lexicográfico: participa en relaciones (propio FK o referenciada como
    padre) > es PK > tiene definición > tiene dominio > # valores UDP >
    posición física más temprana (desempate = regla original "la 1ª")."""
    return (
        int(bool(attr.parent_attr_ref) or attr.id in fk_attr_ids),
        int(attr.id in pk_attr_ids),
        int(bool(attr.definition or attr.comment)),
        int(bool(attr.domain_ref)),
        udp_count,
        -attr.order,
    )


# ── Merge multi-archivo (doc 32b) ─────────────────────────────────────────

def score_usage(n_rels: int, n_canvases: int, n_views: int) -> int:
    """Score de USO de una tabla/vista (doc 32b R2): decide qué versión
    prevalece cuando la misma clave natural viene con estructuras distintas.
    Las relaciones pesan doble (señal más fuerte de uso real). Es simétrico:
    se computa igual para una copia del XML y para el doc vivo en BD."""
    return 2 * n_rels + n_canvases + n_views


def norm_enum(value: str | None) -> str:
    """Clave de comparación de un valor de enum UDP (doc 32b A3):
    case-insensitive, trim y espacios internos colapsados."""
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def merge_allowed_values(existing: list[str], incoming: list[str]) -> list[str]:
    """Valores de `incoming` que FALTAN en `existing`, comparando con
    `norm_enum` (así 'NO DAC' no duplica 'No DAC'). La primera grafía vista
    (la de la BD) gana; el orden de llegada se respeta."""
    seen = {norm_enum(v) for v in existing}
    out: list[str] = []
    for v in incoming:
        k = norm_enum(v)
        if v and k not in seen:
            seen.add(k)
            out.append(v)
    return out


def rel_nat_key(parent_tid: str, child_tid: str,
                name_pairs: list[tuple[str, str]],
                subcategory: bool = False) -> tuple:
    """Clave natural de una relación (doc 32b R4): con N archivos del mismo
    modelo, la MISMA FK conceptual llega repetida con ids Erwin distintos —
    se identifica por extremos + pares de columnas POR NOMBRE. Una relación de
    subcategoría (doc 53) lleva marcador propio: no debe fusionarse con una
    identifying de los mismos extremos y pares."""
    base = (parent_tid, child_tid,
            tuple(sorted((p.upper(), c.upper()) for p, c in name_pairs)))
    return base + ("subcategory",) if subcategory else base


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

    `vals` = [(attr_id, correlativo)] EN ORDEN FÍSICO. Política v2 (owner
    2026-07-24, doc 32b R6): se marcan TODAS las columnas PART_nn — el orden
    EFECTIVO de la partición es el físico, que es lo que el DDL de la
    plataforma emite. Si los correlativos vienen duplicados o en desorden se
    entienden REASIGNADOS por orden físico: se marca igual y se devuelve el
    motivo para el reporte (el valor UDP original no se toca).

    Devuelve (ids_a_marcar, motivo_de_reasignación | None)."""
    if not vals:
        return set(), None
    ids = {aid for aid, _ in vals}
    nums = [n for _, n in vals]
    if len(nums) != len(set(nums)):
        dup = sorted({n for n in nums if nums.count(n) > 1})
        return ids, (f"correlativo duplicado (PART_{dup[0]:02d} aparece "
                     f"{nums.count(dup[0])} veces) → reasignado por orden físico")
    if nums != sorted(nums):
        return ids, ("orden por correlativo ≠ orden físico "
                     f"(correlativos leídos: {nums}) → reasignado por orden físico")
    return ids, None


def udp_datatype(code: str) -> str:
    """tag_Udp_Data_Type de Erwin → dataType de UdpDefinitionDoc."""
    return "list" if code == "6" else "string"


def collapse_udp_defs(defs: dict) -> dict[str, dict]:
    """Colapsa defs Logical/Physical homónimas por (nivel plataforma, nombre).

    Devuelve {clave: {"name", "level", "dataType", "default", "allowed_values",
    "erwin_ids", "physical_ids"}} solo para niveles soportados. `erwin_ids` = todas las
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
            "allowed_values": [],          # lista explícita (unión, orden Erwin)
            "erwin_ids": set(), "physical_ids": set(),
        })
        entry["erwin_ids"].add(d.id)
        if d.view_mode == "Physical":
            entry["physical_ids"].add(d.id)
        if udp_datatype(d.data_type_code) == "list":
            entry["dataType"] = "list"
        if not entry["default"] and d.default:
            entry["default"] = d.default
        for v in d.allowed_values:          # unión de las listas Logical+Physical
            if v not in entry["allowed_values"]:
                entry["allowed_values"].append(v)
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
