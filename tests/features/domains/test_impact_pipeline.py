"""F2 #2: pipeline del impacto agrupado por tabla (puro, sin DB)."""
from __future__ import annotations

from app.features.domains.repository import impact_pipeline


def test_pipeline_matchea_dominio_activo_y_agrupa_por_tabla():
    pipe = impact_pipeline("pd-monto")
    assert pipe[0] == {"$match": {"parentDomainId": "pd-monto",
                                  "flgactive": {"$ne": False}}}
    group = pipe[1]["$group"]
    assert group["_id"] == "$tableId"
    assert group["columns"] == {"$sum": 1}
    # overridden = suma condicional SOLO de columnas con override manual.
    assert group["overridden"] == {"$sum": {"$cond": [{"$eq": ["$typeOverridden", True]}, 1, 0]}}
    assert len(pipe) == 2  # $match + $group, nada más (NO $lookup, joins en Python)
