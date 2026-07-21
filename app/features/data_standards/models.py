"""Modelo del versionado independiente de Data Standards.

Cada documento de `standards_versions` es UNA versión aplicada (append-only). A
diferencia de los changesets del canvas (deltas copy-on-write con aprobación),
los estándares se aplican DIRECTO a las colecciones publicadas y cada apply
genera una versión con el **snapshot completo** del estado de estándares tras
aplicar — suficiente para hacer rollback (restaurar el snapshot objetivo y
re-derivar). El snapshot es acotado (dominios ~decenas + términos ~cientos-miles
+ naming_config 2 docs) → muy por debajo del límite de 2MB/doc de Cosmos RU.

Ver `plan-implementacion/03-DATA-STANDARDS-ADMIN-AUTH.md` §4.2/§4.3.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG

# kind de una versión (para el icono/filtro del historial 15f).
# 'glossary' = ex-'udp' (glosario de términos/abreviaturas que cascadea nombres
# físicos). 'udp' se reserva para el NUEVO User Defined Properties (etiquetas).
# 'ddl' = DDL Export Rules (doc 30).
KINDS = ("glossary", "udp", "domain", "naming", "ddl", "batch", "baseline", "rollback")


class StandardsVersionDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str                       # uuid
    seq: int                      # orden monotónico; label = f"v{seq}"
    label: str
    kind: str = "batch"
    title: str
    description: str | None = None
    author: str
    createdAt: str | None = None
    appliedAt: str | None = None
    status: str = "applied"       # applied | baseline
    # Qué cambió (para el historial): listas de strings legibles.
    diff: dict = Field(default_factory=lambda: {"added": [], "edited": [], "removed": []})
    impact: dict = Field(default_factory=lambda: {"tables": 0, "columns": 0})
    # Estado COMPLETO de estándares TRAS aplicar (para rollback determinista).
    snapshot: dict = Field(default_factory=dict)   # {domains[], dict[], namingConfig{}}
    revertsSeq: int | None = None  # si kind=rollback: la versión a la que revirtió
