"""Alcance por proyecto (doc 75 D1): helpers puros + assert defensivo de published()
+ dependencia `alive_project` (D5/I11)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.scope import PROJECT_SCOPED, MissingProjectError, naming_id, require_project, scoped


def test_scoped_agrega_projectid_y_conserva_filtro():
    assert scoped("p1", {"physicalName": "X"}) == {"projectId": "p1", "physicalName": "X"}
    assert scoped("p1") == {"projectId": "p1"}


def test_require_project_rechaza_vacio():
    with pytest.raises(MissingProjectError):
        require_project("")
    with pytest.raises(MissingProjectError):
        require_project(None)
    assert require_project(" p1 ") == "p1"


def test_naming_id():
    assert naming_id("p1", "column") == "p1:column"


def test_colecciones_de_alcance():
    assert {"canonical_tables", "canonical_columns", "relationships", "views", "schemas",
            "parent_domains", "glossary_terms", "udp_definitions", "naming_config",
            "ddl_rules", "ddl_ruleset_config", "changesets", "standards_versions",
            "folders", "subject_areas", "saved_reports"} <= PROJECT_SCOPED
    assert "projects" not in PROJECT_SCOPED and "users" not in PROJECT_SCOPED


def test_published_exige_alcance_en_colecciones_de_proyecto(monkeypatch):
    from app.features.changesets import repository

    async def _db():
        raise AssertionError("no debe llegar a la BD")
    monkeypatch.setattr(repository, "get_db", _db)
    with pytest.raises(MissingProjectError):
        asyncio.run(repository.published("canonical_tables", {"physicalName": "X"}))
    with pytest.raises(MissingProjectError):
        asyncio.run(repository.published("schemas"))


def test_alive_project_404_si_no_existe(monkeypatch):
    from app.features.projects import deps

    monkeypatch.setattr(deps.repository, "get_project", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.alive_project("nope"))
    assert exc.value.status_code == 404
    monkeypatch.setattr(deps.repository, "get_project", AsyncMock(return_value={"id": "p1", "name": "X"}))
    assert asyncio.run(deps.alive_project("p1")) == "p1"
