"""F2 #1: ruta POST /api/glossary/validate registrada (smoke, patrón del repo)."""
from __future__ import annotations


def test_validate_route_registrada(client):
    paths = {
        (getattr(r, "path", None), m)
        for r in client.app.routes
        for m in getattr(r, "methods", set()) or set()
    }
    assert ("/api/glossary/validate", "POST") in paths


def test_validate_sin_sesion_401_en_prod(client, monkeypatch):
    """I1 (final review): /validate exige SESIÓN (como las lecturas gateadas) —
    en prod (REQUIRE_AUTH) un request anónimo no puede sondear el catálogo.
    Solo sesión, NO standards.edit: el botón Validar del front sigue vivo para
    usuarios sin ese permiso (el caso local/dev lo cubre el test de abajo)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    # Término vacío: si el guard NO existiera, el handler respondería 200 sin
    # tocar DB — el 401 solo puede venir de la dependencia de sesión.
    resp = client.post("/api/glossary/validate", json={"term": "   ", "scope": "column"})
    assert resp.status_code == 401


def test_validate_term_vacio_no_llama_al_service(client, monkeypatch):
    """Término vacío/whitespace ⇒ contrato 'sin conflictos' SIN tocar el service
    (corpus_regex('') es laxo). El handler corta antes de validate_term."""
    from app.features.glossary import service

    def _boom(*a, **k):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("validate_term NO debe llamarse con término vacío")

    monkeypatch.setattr(service, "validate_term", _boom)
    resp = client.post("/api/glossary/validate", json={"term": "   ", "scope": "column"})
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "data": {"ok": True,
                 "conflicts": {"glossaryDuplicate": None, "corpus": [], "total": 0}},
    }
