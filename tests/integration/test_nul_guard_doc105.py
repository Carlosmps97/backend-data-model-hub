"""Doc 105 (revisión de Reporting): Postgres —Lakebase— no acepta el carácter
NUL (U+0000) en `text` ni en `jsonb`. Cualquier dato de entrada con NUL que
llegara a una consulta o a una escritura daba 500 (en la BD en memoria de los
tests pasa sin error, por eso nadie lo veía). Se rechaza en la puerta con 400;
no hay datos legítimos con NUL en la plataforma."""
from __future__ import annotations

import json

from tests.integration.conftest import table_payload

NUL_TEXT = "Text can't contain the NUL character (U+0000)."


def test_un_nul_en_el_cuerpo_json_da_400_y_no_escribe(api, world, fake_db):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    st, body = ana.call("PUT", f"/api/changesets/{cs}/changes",
                        {"collection": "canonical_tables", "entityId": "t-nul", "op": "upsert",
                         "payload": table_payload("M_NUL", "Con\u0000nul")})
    assert (st, body.get("detail")) == (400, NUL_TEXT), (st, body)
    assert fake_db.raw["changeset_changes"].count_documents({"entityId": "t-nul"}) == 0


def test_un_nul_en_la_url_da_400(api, world):
    st, body = api("ana").call("GET", f"/api/reporting/tables?projectId={world['pid']}%00x")
    assert (st, body.get("detail")) == (400, NUL_TEXT), (st, body)


def test_el_texto_literal_barra_u0000_no_es_un_nul(api, world, fake_db):
    """`\\u0000` escrito como texto (barra + «u0000») es un dato válido."""
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_tables", "t-lit", table_payload("M_LIT", "Lit", description=r"usa \u0000 como fin"))
    doc = fake_db.raw["changeset_changes"].find_one({"entityId": "t-lit"})
    assert doc["payload"]["description"] == r"usa \u0000 como fin"


def test_el_detector_cuenta_las_barras():
    from app.core.nul_guard import json_has_nul

    enc = lambda v: json.dumps(v).encode()  # noqa: E731
    assert json_has_nul(enc({"a": "x\u0000y"}))
    assert not json_has_nul(enc({"a": "x\\u0000y"}))            # barra literal + u0000
    assert json_has_nul(enc({"a": "x\\\u0000y"}))               # barra literal + NUL real
    assert not json_has_nul(enc({"a": "u0000"})) and not json_has_nul(b"")


def test_el_400_lleva_los_headers_de_cors(fake_db):
    """El guard va DENTRO de CORS: sin sus headers el navegador vería un opaco
    «Failed to fetch» en vez del motivo."""
    from starlette.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    origin = settings.CORS_ORIGINS[0]
    r = TestClient(app).get("/api/projects?x=%00", headers={"Origin": origin})
    assert r.status_code == 400 and r.json()["detail"] == NUL_TEXT
    assert r.headers.get("access-control-allow-origin") == origin


# ── Ronda 3 (revisores R4 y R5) ─────────────────────────────────────────────
SURROGATE_TEXT = "Text contains an invalid character (an unpaired UTF-16 surrogate)."


def test_un_surrogate_suelto_da_400_y_un_par_valido_pasa(api, world, fake_db):
    """Misma clase que el NUL: asyncpg no puede codificar un surrogate UTF-16
    sin pareja (`\\ud800`) → 500 en Lakebase en cualquier escritura."""
    from starlette.testclient import TestClient

    from app.main import app

    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    raw = TestClient(app)
    body = ('{"collection": "canonical_tables", "entityId": "t-sur", "op": "upsert", "payload": '
            '{"physicalName": "M_SUR", "logicalName": "X\\ud800Y", "schema": "STG"}}')
    r = raw.put(f"/api/changesets/{cs}/changes", content=body,
                headers={"X-Dev-User": "ana", "Content-Type": "application/json"})
    assert (r.status_code, r.json().get("detail")) == (400, SURROGATE_TEXT), r.text[:200]
    ok = (body.replace("X\\ud800Y", "X\\ud83d\\ude00Y").replace("t-sur", "t-par")
          .replace("M_SUR", "M_PAR"))                                                    # emoji por escape: válido
    assert raw.put(f"/api/changesets/{cs}/changes", content=ok,
                   headers={"X-Dev-User": "ana", "Content-Type": "application/json"}).status_code == 200
    lit = (body.replace("X\\ud800Y", "X\\\\ud800Y").replace("t-sur", "t-lit")
           .replace("M_SUR", "M_LIT"))                                                   # barra literal + «ud800»
    assert raw.put(f"/api/changesets/{cs}/changes", content=lit,
                   headers={"X-Dev-User": "ana", "Content-Type": "application/json"}).status_code == 200


def test_el_detector_no_es_mucho_mas_lento_que_el_parser_json():
    """Un cuerpo armado a propósito (10 MB de barras + «u0000») bloqueaba el
    event loop ~0,4 s ANTES de la autenticación con el bucle en Python."""
    import time

    from app.core.nul_guard import body_problem

    body = b'{"a":"' + b"\\\\" * 5_000_000 + b'u0000"}'
    t0 = time.perf_counter(); json.loads(body); parse = time.perf_counter() - t0
    t0 = time.perf_counter(); problem = body_problem(body); guard = time.perf_counter() - t0
    assert problem is None
    assert guard < 5 * parse, f"guard={guard * 1000:.0f} ms vs json.loads={parse * 1000:.0f} ms"


def test_el_guard_no_retiene_una_copia_extra_del_cuerpo():
    import asyncio
    import tracemalloc

    from app.core.nul_guard import RejectNulMiddleware

    size, chunk = 20_000_000, 65_536
    seen: dict = {}

    async def app(scope, receive, send):
        chunks = []
        while True:
            m = await receive()
            chunks.append(m.get("body", b""))
            if not m.get("more_body"):
                break
        body = b"".join(chunks)
        del chunks
        seen["mem"] = tracemalloc.get_traced_memory()[0]
        assert len(body) == size
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    def retained(wrap: bool) -> int:
        async def run():
            sent = {"n": 0}

            async def receive():
                n = sent["n"]
                sent["n"] += chunk
                return {"type": "http.request", "body": b"a" * min(chunk, size - n), "more_body": n + chunk < size}

            async def send(_m):
                return None

            await (RejectNulMiddleware(app) if wrap else app)({"type": "http", "path": "/x", "query_string": b""},
                                                               receive, send)

        tracemalloc.start()
        base = tracemalloc.get_traced_memory()[0]
        asyncio.run(run())
        tracemalloc.stop()
        return seen["mem"] - base

    plain, guarded = retained(False), retained(True)
    assert guarded - plain < size // 4, f"sin guard={plain / 1e6:.1f} MB · con guard={guarded / 1e6:.1f} MB"


# ── Ronda 4 (revisor R8) ────────────────────────────────────────────────────
def test_un_surrogate_en_bytes_crudos_tambien_da_400(api, world, fake_db):
    """`json.loads(bytes)` decodifica con `surrogatepass`: un surrogate crudo
    (`\\xed\\xa0\\x80`, sin escape) llegaba igual al handler → 500."""
    from starlette.testclient import TestClient

    from app.main import app

    cs = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    body = (b'{"collection": "canonical_tables", "entityId": "t-raw", "op": "upsert", "payload": '
            b'{"physicalName": "M_RAW", "logicalName": "Raw", "schema": "STG", "description": "X\xed\xa0\x80Y"}}')
    r = TestClient(app, raise_server_exceptions=False).put(
        f"/api/changesets/{cs}/changes", content=body, headers={"X-Dev-User": "ana", "Content-Type": "application/json"})
    assert r.status_code == 400, (r.status_code, r.text[:120])


def test_un_json_muy_anidado_con_escape_no_da_500(api, world, fake_db):
    """El parseo del guard levantaba RecursionError → 500; sin el guard, FastAPI
    responde 400 «There was an error parsing the body»."""
    from starlette.testclient import TestClient

    from app.main import app

    cs = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    deep = (b'{"collection": "canonical_tables", "entityId": "t", "op": "upsert", "payload": '
            + b'[' * 100_000 + b'"\\ud83d\\ude00"' + b']' * 100_000 + b'}')
    r = TestClient(app, raise_server_exceptions=False).put(
        f"/api/changesets/{cs}/changes", content=deep, headers={"X-Dev-User": "ana", "Content-Type": "application/json"})
    assert r.status_code == 400, (r.status_code, r.text[:160])
