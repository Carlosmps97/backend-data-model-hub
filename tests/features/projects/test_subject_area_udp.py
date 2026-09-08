"""F5 — UDP a nivel canvas: `SubjectAreaDoc.udpValues` (round-trip §2.6) y el
diagrama devuelve `udpValues` dentro de `subjectArea`. Luego (Task 3) este
archivo suma los tests del endpoint PUT /udp."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.projects.models import SubjectAreaDoc


def test_subject_area_udp_values_default():
    sa = SubjectAreaDoc.model_validate({"projectId": "p1", "name": "ER"})
    assert sa.udpValues == {}


def test_subject_area_udp_values_roundtrip():
    raw = {"id": "sa1", "projectId": "p1", "name": "ER",
           "udpValues": {"u9": "Riesgos"}, "flgactive": True}
    dumped = SubjectAreaDoc.model_validate(raw).model_dump()
    assert dumped["udpValues"] == {"u9": "Riesgos"}
    assert "flgactive" not in dumped


def test_diagram_incluye_udp_values(monkeypatch):
    from app.features.catalog import repository as catalog_repo
    from app.features.projects import service
    from app.features.relationships import repository as rel_repo

    # El mock reproduce el repo real: model_validate + model_dump (si el campo
    # no está declarado en SubjectAreaDoc, se pierde acá — como en producción).
    raw = {"id": "sa1", "projectId": "p1", "name": "ER", "udpValues": {"u9": "Riesgos"}}
    monkeypatch.setattr(service.repository, "get_subject_area",
                        AsyncMock(return_value=SubjectAreaDoc.model_validate(raw).model_dump()))
    monkeypatch.setattr(catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog_repo, "list_columns_for_tables", AsyncMock(return_value=[]))
    monkeypatch.setattr(rel_repo, "list_for_tables", AsyncMock(return_value=[]))

    dia = asyncio.run(service.diagram("sa1"))
    assert dia["subjectArea"]["udpValues"] == {"u9": "Riesgos"}


# ── Endpoint PUT /api/subject-areas/{sa_id}/udp (Task 3) ────────────────────
