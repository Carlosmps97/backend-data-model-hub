"""Tests de las políticas de merge multi-archivo (doc 32b, owner 2026-07-24):
score de uso, dedupe de columnas por metadata, convergencia case-insensitive
de enums UDP y clave natural de relaciones."""
from __future__ import annotations

from dataclasses import dataclass, field

from scripts.erwin_migration import policies as pol


@dataclass
class _Attr:
    id: str
    physical: str
    order: int
    definition: str = ""
    comment: str = ""
    domain_ref: str | None = None
    parent_attr_ref: str | None = None
    extra: dict = field(default_factory=dict)


# ── score_usage (R2) ──────────────────────────────────────────────────────
def test_score_usage_pesa_relaciones_doble():
    assert pol.score_usage(0, 0, 0) == 0
    assert pol.score_usage(1, 0, 0) == 2
    assert pol.score_usage(0, 1, 1) == 2
    # 9 rels + 1 canvas (caso FCA) le gana a 0 rels + 3 canvases
    assert pol.score_usage(9, 1, 1) > pol.score_usage(0, 3, 1)


# ── dedupe_columns con scorer (R5) ────────────────────────────────────────
def test_dedupe_sin_scorer_conserva_primera():
    a1 = _Attr("a1", "COD", 1)
    a2 = _Attr("a2", "cod", 2, definition="la buena")
    keep, dropped = pol.dedupe_columns([a1, a2])
    assert keep == [a1] and dropped == [a2]


def test_dedupe_con_scorer_gana_la_de_mas_metadata():
    a1 = _Attr("a1", "COD", 1)                               # sin nada
    a2 = _Attr("a2", "COD", 2, definition="def", domain_ref="D1")
    b1 = _Attr("b1", "NOM", 3, definition="x")               # sin duplicado
    scorer = lambda a: pol.column_score(a)  # noqa: E731
    keep, dropped = pol.dedupe_columns([a1, a2, b1], scorer=scorer)
    assert keep == [a2, b1]        # gana a2 y el orden físico se mantiene
    assert dropped == [a1]


def test_dedupe_con_scorer_empate_gana_la_primera():
    a1 = _Attr("a1", "COD", 1)
    a2 = _Attr("a2", "COD", 2)
    keep, dropped = pol.dedupe_columns([a1, a2], scorer=pol.column_score)
    assert keep == [a1] and dropped == [a2]   # -order desempata a la 1ª


def test_column_score_prioriza_relaciones_sobre_definicion():
    con_rel = _Attr("a1", "X", 5, parent_attr_ref="P1")
    con_def = _Attr("a2", "X", 1, definition="documentada", domain_ref="D1")
    assert pol.column_score(con_rel) > pol.column_score(con_def)
    # referenciada como PADRE de una FK también cuenta como "en relación"
    padre = _Attr("a3", "X", 9)
    assert pol.column_score(padre, fk_attr_ids={"a3"}) > pol.column_score(con_def)


# ── enums case-insensitive (A3) ───────────────────────────────────────────
def test_norm_enum_colapsa_case_trim_y_espacios():
    assert pol.norm_enum("  No   DAC ") == pol.norm_enum("NO DAC") == "NO DAC"
    assert pol.norm_enum("Sub - Tipo") != pol.norm_enum("Sub Tipo")  # guion ≠
    assert pol.norm_enum(None) == ""


def test_merge_allowed_values_no_crea_casi_duplicados():
    existing = ["No DAC", "DAC-EMAIL", "Sí"]
    incoming = ["NO DAC", "no dac", "DAC", "DAC-EMAIL", "", "Sí "]
    assert pol.merge_allowed_values(existing, incoming) == ["DAC"]
    # y dentro del propio incoming tampoco se duplica
    assert pol.merge_allowed_values([], ["Si", "SI", "si"]) == ["Si"]


# ── clave natural de relación (R4) ────────────────────────────────────────
def test_rel_nat_key_por_nombres_y_orden_estable():
    k1 = pol.rel_nat_key("T1", "T2", [("CodCli", "CODCLI"), ("B", "B2")])
    k2 = pol.rel_nat_key("T1", "T2", [("B", "b2"), ("CODCLI", "codcli")])
    assert k1 == k2                       # case-insensitive y sin orden
    assert k1 != pol.rel_nat_key("T2", "T1", [("CODCLI", "CODCLI"), ("B", "B2")])
