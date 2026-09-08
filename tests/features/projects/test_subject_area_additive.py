"""Campos aditivos de `SubjectAreaDoc` (R1a): `folderId` + `drawings`.

Invariante §2.6: declarados en el modelo ⇒ sobreviven al round-trip de lectura
(que re-valida con extra="ignore"). Cambio NO-breaking: siguen siendo opcionales.
"""
from __future__ import annotations

from app.features.projects.models import SubjectAreaDoc


def test_subject_area_additive_defaults():
    sa = SubjectAreaDoc.model_validate({"projectId": "p1", "name": "ER"})
    assert sa.folderId is None
    assert sa.drawings == []


def test_subject_area_additive_persist_roundtrip():
    raw = {
        "id": "sa1",
        "projectId": "p1",
        "folderId": "f1",
        "name": "ER",
        "drawings": [{"type": "rect", "x": 1, "y": 2}],
        "flgactive": True,  # campo interno → ignorado
    }
    dumped = SubjectAreaDoc.model_validate(raw).model_dump()
    assert dumped["folderId"] == "f1"
    assert dumped["drawings"] == [{"type": "rect", "x": 1, "y": 2}]
    assert "flgactive" not in dumped


def test_subject_area_view_ids_legacy_none_y_explicito():
    """Doc 70: `viewIds` aditivo — ausente ⇒ None (canvas legacy, regla vieja);
    lista (aun vacía) ⇒ membresía explícita y sobrevive el round-trip."""
    assert SubjectAreaDoc.model_validate({"projectId": "p1", "name": "ER"}).viewIds is None
    dumped = SubjectAreaDoc.model_validate(
        {"id": "sa1", "projectId": "p1", "name": "ER", "viewIds": []}).model_dump()
    assert dumped["viewIds"] == []
    dumped = SubjectAreaDoc.model_validate(
        {"id": "sa1", "projectId": "p1", "name": "ER", "viewIds": ["v1", "v2"]}).model_dump()
    assert dumped["viewIds"] == ["v1", "v2"]
