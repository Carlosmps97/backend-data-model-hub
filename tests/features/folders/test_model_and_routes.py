"""Invariante de persistencia de `FolderDoc` + registro de rutas CRUD de folders."""
from __future__ import annotations

from app.features.folders.models import FolderDoc


def test_folder_doc_defaults_and_shape():
    f = FolderDoc.model_validate({"projectId": "p1", "name": "Sales"})
    assert f.id  # uuid autogenerado
    assert f.projectId == "p1"
    assert f.parentFolderId is None
    assert f.order == 0
    assert set(f.model_dump().keys()) == {"id", "projectId", "parentFolderId", "name", "order"}


def test_folder_doc_ignores_internal_fields():
    # extra="ignore": los campos internos de Mongo no entran al modelo.
    f = FolderDoc.model_validate(
        {"id": "f1", "projectId": "p1", "name": "X", "flgactive": True, "deletedAt": None}
    )
    dumped = f.model_dump()
    assert "flgactive" not in dumped and "deletedAt" not in dumped


def test_folders_routes_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/folders" in paths
    assert "/api/folders/{folder_id}" in paths
    assert "/api/projects/{project_id}/folders" in paths
