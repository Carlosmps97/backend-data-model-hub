"""Campos F3a de vistas multi-fuente (10-PRECISIONES §3-5).

Contrato compartido F3: `sourceTableIds` es el campo canónico de fuentes;
`tableId` legacy se mantiene = sourceTableIds[0] por compat. Normalización
bidireccional vía `normalize_source_tables` (pura) + validator de ViewDoc
(cubre el read path de docs Mongo no migrados).
"""
from __future__ import annotations

from app.features.views.models import ViewDoc, normalize_source_tables
from app.features.views.schemas import ViewBody


# ── normalize_source_tables (pura) ───────────────────────────────────────────

def test_normalize_solo_tableid_materializa_sources():
    out = normalize_source_tables({"name": "v", "tableId": "t1"})
    assert out["sourceTableIds"] == ["t1"]
    assert out["tableId"] == "t1"


def test_normalize_sources_pisan_tableid_legacy():
    out = normalize_source_tables(
        {"name": "v", "tableId": "viejo", "sourceTableIds": ["a", "b"]})
    assert out["tableId"] == "a"
    assert out["sourceTableIds"] == ["a", "b"]


def test_normalize_sin_fuentes_no_inventa_nada():
    out = normalize_source_tables({"name": "v"})
    assert out.get("sourceTableIds") in (None, [])
    assert out.get("tableId") is None


def test_normalize_no_muta_la_entrada():
    data = {"name": "v", "tableId": "t1"}
    normalize_source_tables(data)
    assert "sourceTableIds" not in data


# ── ViewDoc ──────────────────────────────────────────────────────────────────

def test_view_doc_defaults_f3():
    v = ViewDoc.model_validate({"projectId": "p1", "name": "v"})
    assert v.sourceTableIds == []
    assert v.showOnCanvas is False
    assert not hasattr(v, "joinOverride")   # doc 91 D5: retirado


def test_view_doc_normaliza_legacy_en_lectura():
    # Doc de Mongo NO migrado (solo tableId): el read path materializa las
    # fuentes → el API ya expone sourceTableIds sin esperar la migración.
    v = ViewDoc.model_validate({"projectId": "p1", "name": "v", "tableId": "t1", "flgactive": True})
    assert v.sourceTableIds == ["t1"]
    assert v.tableId == "t1"


def test_view_doc_multi_fuente_roundtrip():
    dumped = ViewDoc.model_validate({"projectId": "p1", 
        "name": "v", "sourceTableIds": ["t1", "t2"], "showOnCanvas": True,
        "sources": [{"column": "id", "tableId": "t1",
                     "outputAlias": "cid", "castType": "STRING"}],
    }).model_dump(by_alias=True)
    assert dumped["sourceTableIds"] == ["t1", "t2"]
    assert dumped["tableId"] == "t1"  # compat legacy = primera fuente
    assert dumped["showOnCanvas"] is True
    # sources es list[dict]: tableId/castType pasan sin cambio de schema.
    assert dumped["sources"][0]["castType"] == "STRING"
    assert dumped["sources"][0]["tableId"] == "t1"


# ── ViewBody ─────────────────────────────────────────────────────────────────

def test_view_body_acepta_campos_f3():
    body = ViewBody.model_validate({
        "name": "v", "sourceTableIds": ["t1", "t2"], "showOnCanvas": True,
    })
    dumped = body.model_dump(by_alias=True)
    assert dumped["sourceTableIds"] == ["t1", "t2"]
    assert dumped["showOnCanvas"] is True


def test_view_body_defaults_f3():
    body = ViewBody.model_validate({"name": "v"})
    assert body.sourceTableIds == []
    assert body.showOnCanvas is False
    assert not hasattr(body, "joinOverride")
