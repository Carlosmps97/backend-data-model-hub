"""Key del rate limiter (doc 36): detrás de los proxies (Databricks Apps +
server.mjs del front) la IP del peer es la del último salto y es LA MISMA para
todos los usuarios — la key debe salir de la PRIMERA IP de X-Forwarded-For."""
from __future__ import annotations

from starlette.requests import Request

from app.core.ratelimit import client_ip


def _req(headers: dict | None = None, client: tuple | None = ("10.9.9.9", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "headers": raw}
    if client:
        scope["client"] = client
    return Request(scope)


def test_usa_la_primera_ip_de_xff():
    r = _req({"X-Forwarded-For": "200.48.10.5, 10.0.0.7, 10.0.0.9"})
    assert client_ip(r) == "200.48.10.5"


def test_xff_de_un_solo_salto():
    assert client_ip(_req({"X-Forwarded-For": "181.65.3.2"})) == "181.65.3.2"


def test_sin_xff_cae_al_peer_del_socket():
    assert client_ip(_req()) == "10.9.9.9"


def test_xff_vacio_no_rompe():
    assert client_ip(_req({"X-Forwarded-For": "  "})) == "10.9.9.9"