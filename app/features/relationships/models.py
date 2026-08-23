"""Modelo de `relationships` (ER: PK/FK entre tablas canónicas).

v2 (doc 19): orientación EXPLÍCITA parent/child + pares múltiples de columnas
(PK compuesta) + roleName por par. El shape v1 (source/target, 1 par) tenía la
convención invertida entre módulos (canvas: target=hijo; seed/migración/DDL:
source=hijo), así que el validator normaliza cualquier payload legacy que siga
llegando (before-images de rollback, drafts viejos): la cardinalidad decide la
orientación y, en empate, gana la convención del seed (source=hijo — validada
175/175 contra los flags FK de la BD el 2026-07-16).
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, model_validator

from app.core.models import DOC_CONFIG

# Cardinalidades crow's foot por extremo (mismo vocabulario que el front).
CARDINALITIES = ("one", "many", "one-only", "zero-one", "one-many", "zero-many")
_MANYISH = {"many", "one-many", "zero-many"}
_ONEISH = {"one", "zero-one", "one-only"}
_LEGACY_FIELDS = ("sourceTableId", "sourceColumnId", "targetTableId",
                  "targetColumnId", "sourceCardinality", "targetCardinality")


def _child_cardinality(child_raw: str, parent_raw: str) -> str:
    """La many-ish más específica disponible: el seed viejo dejó el valor Erwin
    real (zero-many/one-many) en el extremo padre, así que se prefiere una
    variante calificada sobre el 'many' pelado."""
    cands = [c for c in (child_raw, parent_raw) if c in _MANYISH]
    for c in cands:
        if c != "many":
            return c
    return cands[0] if cands else "zero-many"


def normalize_legacy(data: dict) -> dict:
    """Payload v1 (source/target) → v2 (parent/child + pairs). Puro."""
    s_card = str(data.get("sourceCardinality") or "")
    t_card = str(data.get("targetCardinality") or "")
    s_many, t_many = s_card in _MANYISH, t_card in _MANYISH
    # Exactamente un extremo many-ish → ese es el hijo; ambiguo → hijo=source.
    child_is_source = s_many if s_many != t_many else True
    if child_is_source:
        parent_t, parent_c = data.get("targetTableId"), data.get("targetColumnId")
        child_t, child_c = data.get("sourceTableId"), data.get("sourceColumnId")
        parent_raw, child_raw = t_card, s_card
    else:
        parent_t, parent_c = data.get("sourceTableId"), data.get("sourceColumnId")
        child_t, child_c = data.get("targetTableId"), data.get("targetColumnId")
        parent_raw, child_raw = s_card, t_card
    out = {k: v for k, v in data.items() if k not in _LEGACY_FIELDS}
    out.update({
        "parentTableId": parent_t,
        "childTableId": child_t,
        "pairs": [{"parentColumnId": parent_c, "childColumnId": child_c}],
        "parentCardinality": parent_raw if parent_raw in _ONEISH else "one",
        "childCardinality": _child_cardinality(child_raw, parent_raw),
    })
    return out


class RelationshipPairDoc(BaseModel):
    model_config = DOC_CONFIG

    parentColumnId: str
    childColumnId: str
    # Rolename estilo Erwin: nombre que la FK migrada toma en el hijo. El
    # nombre REAL vive en la columna hija; esto es el metadato del par.
    roleName: str | None = None


class RelationshipDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parentTableId: str               # lado PK ("one")
    childTableId: str                # lado FK ("many")
    pairs: list[RelationshipPairDoc] = Field(min_length=1)
    parentCardinality: str = "one"        # one | zero-one | one-only | many | one-many | zero-many
    childCardinality: str = "zero-many"
    identifying: bool = False        # sólida; la FK es parte de la PK del hijo
    # Subcategoría (doc 53): relación de subtipo supertipo→subtipo (ES-UN,
    # Erwin Type 9). El símbolo NO es una entidad persistida: las aristas del
    # mismo grupo comparten `subtypeSymbolId` y el canvas deriva el círculo.
    subcategory: bool = False
    subtypeSymbolId: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("pairs") and data.get("sourceTableId"):
            return normalize_legacy(dict(data))
        return data

    @model_validator(mode="after")
    def _subcategory_shape(self) -> "RelationshipDoc":
        """Normaliza la variante subcategoría (doc 53): ES-UN es 1:1 estricto y
        la PK del padre migra como PK del hijo, así que se fuerzan `identifying`
        y las cardinalidades — todo consumidor que ramifica por esos campos
        queda coherente sin conocer el tipo nuevo. Sin subcategoría, el símbolo
        se limpia (coherencia del par de campos)."""
        if self.subcategory:
            if not (self.subtypeSymbolId or "").strip():
                raise ValueError("subcategory relationship requires subtypeSymbolId")
            self.identifying = True
            self.parentCardinality = "one"
            self.childCardinality = "one"
        elif self.subtypeSymbolId is not None:
            self.subtypeSymbolId = None
        return self
