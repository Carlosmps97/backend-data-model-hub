"""Doc 75 D5/I10/I11 — el apply de un draft que borra el proyecto cascada; un
proyecto borrado rechaza snapshot/edición/publicación con 409."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.scope import ProjectDeletedError
from app.features.changesets import service

CS = {"id": "cs1", "owner": "ana", "status": "submitted", "projectId": "p1", "submittedAt": "t1"}


def _apply_env(monkeypatch, changes):
    monkeypatch.setattr(service.repository, "transition", AsyncMock(return_value={**CS, "status": "approved"}))
    monkeypatch.setattr(service.repository, "changes_map", AsyncMock(return_value=changes))
    monkeypatch.setattr(service.validation, "validate_changes", lambda c: [])
    monkeypatch.setattr(service, "_publish_duplicates", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_publish_schema_deletes", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_cross_project_check", AsyncMock())
    monkeypatch.setattr(service.repository, "capture_before_images", AsyncMock(return_value={}))
    monkeypatch.setattr(service.repository, "store_before_images", AsyncMock())
    monkeypatch.setattr(service.repository, "apply_changes", AsyncMock(return_value={"projects": 1}))
    monkeypatch.setattr(service.repository, "set_status", AsyncMock(return_value={**CS, "status": "approved", "appliedAt": "t2"}))
    cascade = AsyncMock(return_value={"canonical_tables": 5})
    monkeypatch.setattr(service.projects_repo, "cascade_delete", cascade)
    return cascade


def test_apply_con_delete_de_proyecto_cascada(monkeypatch):
    cascade = _apply_env(monkeypatch, {"projects": {"p1": {"op": "delete", "at": "t0"}}})
    out = asyncio.run(service._apply_and_finalize(CS, {"status": "approved"}, "t1"))
    cascade.assert_awaited_once_with("p1", "cs1")
    assert out["appliedAt"] == "t2"


def test_apply_sin_delete_de_proyecto_no_cascada(monkeypatch):
    cascade = _apply_env(monkeypatch, {"projects": {"p1": {"op": "upsert", "payload": {"name": "Nuevo"}, "at": "t0"}}})
    asyncio.run(service._apply_and_finalize(CS, {"status": "approved"}, "t1"))
    cascade.assert_not_awaited()


def test_cascada_que_falla_devuelve_la_request_a_submitted(monkeypatch):
    cascade = _apply_env(monkeypatch, {"projects": {"p1": {"op": "delete", "at": "t0"}}})
    cascade.side_effect = RuntimeError("timeout")
    with pytest.raises(RuntimeError):
        asyncio.run(service._apply_and_finalize(CS, {"status": "approved"}, "t1"))
    revert = service.repository.transition.await_args_list[-1]
    assert revert.args[1] == "approved" and revert.args[2]["status"] == "submitted"


def test_proyecto_borrado_rechaza_edicion_y_snapshot(monkeypatch):
    monkeypatch.setattr(service.projects_repo, "get_project", AsyncMock(return_value=None))
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={**CS, "status": "draft"}))
    with pytest.raises(ProjectDeletedError):
        asyncio.run(service.ensure_project_alive(CS))
    with pytest.raises(ProjectDeletedError):
        asyncio.run(service.add_change("cs1", "ana", "canonical_tables", "t1", "upsert", {"physicalName": "T", "logicalName": "t"}))
    with pytest.raises(ProjectDeletedError):
        asyncio.run(service.snapshot("ana", "p1", None, None, None))
