"""Warm-up de sesión para Databricks Apps (doc 36): validación del `next`
(anti open-redirect, mismo allowlist que CORS) y el redirect 302. `next` viaja
en el PATH como base64url SIN padding (igual que lo genera el front) porque el
replay post-SSO del proxy de Databricks se atora con query strings."""
from __future__ import annotations

import base64

from app.core.config import settings
from app.features.auth.router import warmup_next_allowed


def _b64(url: str) -> str:
    # Mismo formato que el front: base64url sin '=' de padding.
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


# ── warmup_next_allowed (puro) ────────────────────────────────────────────

def test_next_allowed_exact_origin(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:3000"])
    monkeypatch.setattr(settings, "CORS_ORIGIN_REGEX", "")
    assert warmup_next_allowed("http://localhost:3000/login") is True
    assert warmup_next_allowed("http://localhost:3000") is True


def test_next_allowed_via_regex(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", [])
    monkeypatch.setattr(
        settings, "CORS_ORIGIN_REGEX",
        r"https://frnt-data-model-hub-.*\.databricksapps\.com")
    assert warmup_next_allowed(
        "https://frnt-data-model-hub-123.17.azure.databricksapps.com/login?x=1") is True
    # el regex es fullmatch sobre el ORIGEN: un dominio colgado no pasa
    assert warmup_next_allowed(
        "https://frnt-data-model-hub-123.17.azure.databricksapps.com.evil.com/") is False


def test_next_rejected_foreign_or_malformed(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:3000"])
    monkeypatch.setattr(settings, "CORS_ORIGIN_REGEX", "")
    assert warmup_next_allowed("https://evil.com/phish") is False
    assert warmup_next_allowed("javascript:alert(1)") is False
    assert warmup_next_allowed("//evil.com") is False
    assert warmup_next_allowed("/relative/path") is False
    assert warmup_next_allowed("") is False


def test_next_regex_invalido_no_revienta(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", [])
    monkeypatch.setattr(settings, "CORS_ORIGIN_REGEX", "https://(")
    assert warmup_next_allowed("https://x.com/") is False


# ── endpoint (via TestClient; next en el PATH como base64url) ─────────────

def test_warmup_redirects_to_allowed_next(client, monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:3000"])
    r = client.get(
        f"/api/auth/warmup/{_b64('http://localhost:3000/login')}",
        follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "http://localhost:3000/login"
    assert r.headers["cache-control"] == "no-store"


def test_warmup_rejects_open_redirect(client, monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:3000"])
    monkeypatch.setattr(settings, "CORS_ORIGIN_REGEX", "")
    r = client.get(
        f"/api/auth/warmup/{_b64('https://evil.com/')}", follow_redirects=False)
    assert r.status_code == 400


def test_warmup_rejects_garbage_b64(client, monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:3000"])
    r = client.get("/api/auth/warmup/%%%no-es-b64", follow_redirects=False)
    assert r.status_code == 400
