"""F2 #1: ruta POST /api/projects/{project_id}/glossary/validate registrada (smoke, patrón del repo)."""
from __future__ import annotations


def test_validate_route_registrada(client):
    paths = {
        (getattr(r, "path", None), m)
        for r in client.app.routes
        for m in getattr(r, "methods", set()) or set()
    }
    assert ("/api/projects/{project_id}/glossary/validate", "POST") in paths


def test_validate_sin_sesion_401_en_prod(project_client, monkeypatch):
    """I1 (final review): /validate exige SESIÓN (como las lecturas gateadas) —
    en prod (REQUIRE_AUTH) un request anónimo no puede sondear el catálogo.
    Solo sesión, NO standards.edit: el botón Validar del front sigue vivo para
    usuarios sin ese permiso (el caso local/dev lo cubre el test de abajo)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    # Término vacío: si el guard NO existiera, el handler respondería 200 sin
    # tocar DB — el 401 solo puede venir de la dependencia de sesión.
    resp = project_client.post("/api/projects/p1/glossary/validate", json={"term": "   ", "scope": "column"})
    assert resp.status_code == 401


def test_validate_term_vacio_no_llama_al_service(project_client, monkeypatch):
    """Término vacío/whitespace ⇒ contrato 'sin conflictos' SIN tocar el service
    (corpus_regex('') es laxo). El handler corta antes de validate_term."""
    from app.features.glossary import service

    def _boom(*a, **k):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("validate_term NO debe llamarse con término vacío")

    monkeypatch.setattr(service, "validate_term", _boom)
    resp = project_client.post("/api/projects/p1/glossary/validate", json={"term": "   ", "scope": "column"})
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "data": {"ok": True,
                 "conflicts": {"glossaryDuplicate": None, "corpus": [], "total": 0}},
    }


def test_impact_route_registrada_y_delegada(project_client, monkeypatch):
    """Doc 94 D7: dry-run de solo lectura (sesión, sin standards.edit)."""
    from unittest.mock import AsyncMock
    from app.features.glossary import service

    spy = AsyncMock(return_value={"renamed": [], "outOfSync": []})
    monkeypatch.setattr(service, "impact_preview", spy)
    body = {"scope": "column", "termsUpsert": [{"id": "t1", "term": "cliente", "abbrev": "CLTE"}],
            "termsDelete": ["t2"], "namingConfig": {"separator": "", "case": "upper"}}
    resp = project_client.post("/api/projects/p1/glossary/impact", json=body)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"renamed": [], "outOfSync": []}
    spy.assert_awaited_once_with("p1", "column", [{"id": "t1", "term": "cliente", "abbrev": "CLTE"}],
                                 ["t2"], {"separator": "", "case": "upper"})


def test_validate_route_pide_la_lista_completa(project_client, monkeypatch):
    """Doc 95 D1: el popup de conflictos muestra TODAS las columnas."""
    from unittest.mock import AsyncMock
    from app.features.glossary import service

    spy = AsyncMock(return_value={"ok": True,
                                  "conflicts": {"glossaryDuplicate": None, "corpus": [], "total": 0}})
    monkeypatch.setattr(service, "validate_term", spy)
    resp = project_client.post("/api/projects/p1/glossary/validate", json={"term": "codigo", "scope": "column"})
    assert resp.status_code == 200
    spy.assert_awaited_once_with("p1", "codigo", "column", limit=None)
