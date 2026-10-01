"""Rechazo de texto que Postgres no puede guardar, en la entrada de la API (doc 105).

Postgres —Lakebase— no acepta el carácter NUL (U+0000) en `text` ni en
`jsonb`, y asyncpg no puede codificar un surrogate UTF-16 SIN pareja
(`\\ud800`): cualquier dato así que llegara a una consulta o a una escritura
daba 500 (lo encontraron las revisiones del Reporting con un cursor; vale para
toda la API). En la BD en memoria de los tests pasa sin error, por eso nadie
lo veía. No hay datos legítimos así en la plataforma (el front quita los NUL
de un Excel: SheetJS decodifica `_x0000_` a U+0000); se rechaza en la puerta
con 400, antes de tocar la base.

Middleware ASGI puro: revisa la ruta, la query y el cuerpo. El cuerpo se lee
entero (los endpoints lo leen entero igual) y se entrega a la app en UN solo
mensaje, sin retener otra copia. La detección corre en C (`bytes.replace` /
`in` / una regex); sólo un escape de surrogate —raro: los navegadores mandan
los emoji en UTF-8, no escapados— lleva a parsear el JSON para ver si está
suelto.
"""
from __future__ import annotations

import json
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

NUL_TEXT = "Text can't contain the NUL character (U+0000)."
SURROGATE_TEXT = "Text contains an invalid character (an unpaired UTF-16 surrogate)."
UTF8_TEXT = "The request body isn't valid UTF-8 text."
_SURROGATE_ESCAPE = re.compile(rb"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")


def _real_escapes(body: bytes) -> bytes:
    """El cuerpo sin los pares `\\\\` (barra escapada): toda barra que queda abre
    un escape REAL (`\\\\u0000` es el texto literal «\\u0000», no un NUL)."""
    return body.replace(b"\\\\", b"")


def json_has_nul(body: bytes) -> bool:
    """¿El cuerpo trae un NUL? En JSON sólo puede venir como el escape
    `\\u0000` (o como un byte 0x00 crudo, que ni siquiera es JSON válido)."""
    return b"\x00" in body or b"\\u0000" in _real_escapes(body)


def _has_lone_surrogate(body: bytes) -> bool:
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001 — JSON inválido o demasiado anidado: responde FastAPI
        return False
    stack = [data]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            try:
                v.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(v, dict):
            stack.extend(v.keys())
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return False


def body_problem(body: bytes) -> str | None:
    """None si el cuerpo sirve; si no, el motivo (texto del 400)."""
    if b"\x00" in body:
        return NUL_TEXT
    # Ronda 4: `json.loads(bytes)` (el de FastAPI) decodifica con `surrogatepass`:
    # un surrogate en bytes CRUDOS (sin escape) llegaba igual al handler. UTF-8
    # estricto (en C) lo corta, y cualquier cuerpo que no sea UTF-8 válido.
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return UTF8_TEXT
    real = _real_escapes(body)
    if b"\\u0000" in real:
        return NUL_TEXT
    if _SURROGATE_ESCAPE.search(real) and _has_lone_surrogate(body):
        return SURROGATE_TEXT
    return None


class RejectNulMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # `path` ya viene decodificado; la query no (un NUL llega como %00). Un
        # surrogate no puede llegar por la URL: su UTF-8 es inválido y el
        # decodificado lo reemplaza.
        if "\x00" in scope.get("path", "") or b"%00" in scope.get("query_string", b""):
            await JSONResponse({"detail": NUL_TEXT}, status_code=400)(scope, receive, send)
            return
        buffer = bytearray()
        tail: Message | None = None           # p. ej. un http.disconnect: se entrega tal cual
        while True:
            message = await receive()
            if message["type"] != "http.request":
                tail = message
                break
            buffer += message.get("body", b"")
            if not message.get("more_body", False):
                break
        body = bytes(buffer)
        del buffer                            # una sola copia viva durante el request
        problem = body_problem(body)
        if problem is not None:
            await JSONResponse({"detail": problem}, status_code=400)(scope, receive, send)
            return
        pending: list[Message] = [{"type": "http.request", "body": body, "more_body": False}]
        del body
        if tail is not None:
            pending.append(tail)

        async def replay() -> Message:
            if pending:
                return pending.pop(0)
            return await receive()

        await self.app(scope, replay, send)
