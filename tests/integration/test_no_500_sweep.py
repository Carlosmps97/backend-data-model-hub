"""Barrido anti-500 (doc 82): TODA ruta GET del backend (y las POST de sólo
lectura) se invoca sobre un proyecto publicado con parámetros reales. Un 4xx
es una respuesta de negocio; un 5xx o una excepción es un bug de cableado —
exactamente la clase de los 500 del doc 80, que sólo se descubrían en vivo.

Con la BD falsa, una ruta puede caer en algo que mongomock no modela
(`NotImplementedError`): esas se listan EXPLÍCITAMENTE en `UNSUPPORTED_BY_FAKE`
para que una ruta nueva sin cobertura no pase en silencio."""
from __future__ import annotations

import base64

import pytest

from app.main import app

# Rutas que el fake no puede ejecutar (motor de agregación fuera de mongomock).
# Cada entrada es una deuda VISIBLE: si mongomock las llega a soportar, quitar.
UNSUPPORTED_BY_FAKE: set[str] = set()

# Rutas fuera del barrido (no son API de negocio o exigen un estado externo).
SKIP_PATHS = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc", "/api/auth/logout",
              "/api/auth/login", "/api/auth/sso/login"}

# Query strings por ruta (parámetros obligatorios o que activan el camino real).
QUERY = {
    "/api/projects/{project_id}/catalog/columns": "q=COD",
    "/api/projects/{project_id}/catalog/tables": "q=M_&limit=10&schema=STG",
    "/api/projects/{project_id}/catalog/search": "q=CLI&changesetId={cs2}",
    "/api/projects/{project_id}/catalog/inventory": "changesetId={cs2}",
    "/api/projects/{project_id}/catalog/views/{view_id}/columns": "changesetId={cs2}",
    "/api/versions/compare": "fromId={v1}&toId={cs2}",
    "/api/versions": "projectId={pid}",
    "/api/requests": "reviewer=beto",
    "/api/users": "can=review.decide",
    "/api/summary": "projectId={pid}",
    "/api/folders": "projectId={pid}",
    "/api/views": "tableId={t1}",
    "/api/views/for-table": "tableId={t1}",
    "/api/relationships": "tableId={t1}",
    "/api/relationships/impact": "columnId={c_a}",
    "/api/relationships/links": "tableIds={t1},{t2}",
    "/api/subject-areas/{sa_id}/diagram": "changesetId={cs2}",
    "/api/catalog/tables/{table_id}/usage": "changesetId={cs2}",
    "/api/catalog/inspect/tables/{table_id}": "changesetId={cs2}",
    "/api/catalog/inspect/views/{view_id}": "changesetId={cs2}",
    "/api/changesets/{cs_id}/effective/{collection}": "tableId={t1}",
    "/api/changesets/history/{collection}/{entity_id}": "limit=10",
    "/api/reporting/tables": "projectId={pid}&limit=50&offset=0",
    "/api/reporting/tables/count": "projectId={pid}",
    "/api/reporting/columns": "projectId={pid}",
    "/api/reporting/views": "projectId={pid}",
    "/api/reporting/filters": "projectId={pid}",
    "/api/reporting/facets": "projectId={pid}",
    "/api/reporting/catalog": "projectId={pid}",
    "/api/reporting/reports": "projectId={pid}",
    "/api/reporting/insights/scorecard": "projectId={pid}",
    "/api/reporting/insights/domain-usage": "projectId={pid}",
    "/api/reporting/insights/glossary-usage": "projectId={pid}",
    "/api/reporting/insights/relationships": "projectId={pid}",
    "/api/reporting/insights/udp-coverage": "projectId={pid}",
    "/api/admin/audit": "limit=20",
}

# POST de sólo lectura (o idempotentes) con body real.
READ_POSTS = [
    ("/api/changesets/{cs_id}/diff/details", {"items": [{"collection": "canonical_tables", "entityId": "{t1}"}]}),
    ("/api/versions/compare/details", {"fromId": "{v1}", "toId": "{cs2}",
                                        "items": [{"collection": "canonical_columns", "entityId": "{c_a}"}]}),
    ("/api/projects/{project_id}/glossary/validate", {"logical": "Codigo Cliente"}),
    ("/api/projects/{project_id}/glossary/physicalize", {"logical": "Codigo Cliente"}),
    ("/api/projects/{project_id}/ddl-rules/validate", {"name": "r", "condition": "", "action": {}}),
    ("/api/projects/{project_id}/ddl-rules/impact", {"rule": {"name": "r", "condition": "", "action": {}}}),
    ("/api/projects/{project_id}/ddl-rules/render", {"tableIds": ["{t1}"]}),
    ("/api/projects/{project_id}/upload-profiles/suggest", {"headers": ["Tabla", "Columna"]}),
    ("/api/reporting/query/validate", {"entity": "columns", "filters": [], "projectId": "{pid}"}),
]


def _fill(template: str, values: dict) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _params(w: dict) -> dict:
    return {
        "project_id": w["pid"], "pid": w["pid"], "cs_id": w["cs2"], "cs2": w["cs2"], "v1": w["v1"],
        "collection": "canonical_tables", "entity_id": w["t1"], "table_id": w["t1"], "t1": w["t1"], "t2": w["t2"],
        "view_id": w["view"], "vid": w["view"], "sa_id": w["canvas"], "folder_id": w["folder"],
        "schema_id": w["schema"], "sid": w["schema"], "rid": w["rel"], "c_a": w["c_a"],
        "domain_id": "no-such-domain", "entry_id": "no-such-term", "profile_id": "no-such-profile",
        "job_id": "no-such-job", "key": "modelador", "username": "ana",
        "next_b64": base64.urlsafe_b64encode(b"/").decode(),
    }


def _routes(method: str):
    for r in app.routes:
        methods = getattr(r, "methods", None)
        if methods and method in methods and r.path not in SKIP_PATHS and r.path.startswith("/api"):
            yield r.path


def test_every_get_route_answers_without_crashing(api, world):
    admin = api("admin")
    values = _params(world)
    failures, unsupported = [], set()
    for path in _routes("GET"):
        url = _fill(path, values)
        if path in QUERY:
            url += "?" + _fill(QUERY[path], values)
        try:
            status, body = admin.call("GET", url)
        except Exception as exc:  # noqa: BLE001 — TestClient re-lanza la excepción del handler
            if isinstance(exc, NotImplementedError) or type(exc).__module__.startswith("mongomock"):
                unsupported.add(path)
                continue
            failures.append(f"GET {url} → {type(exc).__name__}: {exc}")
            continue
        if status >= 500:
            failures.append(f"GET {url} → {status}: {body}")
    assert not failures, "Rutas que revientan:\n  " + "\n  ".join(failures)
    assert unsupported == UNSUPPORTED_BY_FAKE, (
        f"rutas sin cobertura por el fake: {sorted(unsupported)} (esperadas: {sorted(UNSUPPORTED_BY_FAKE)})")


@pytest.mark.parametrize("path,body", READ_POSTS, ids=[p for p, _ in READ_POSTS])
def test_read_only_posts_answer_without_crashing(api, world, path, body):
    values = _params(world)
    url = _fill(path, values)

    def _fill_body(v):
        if isinstance(v, str):
            return _fill(v, values)
        if isinstance(v, list):
            return [_fill_body(x) for x in v]
        if isinstance(v, dict):
            return {k: _fill_body(x) for k, x in v.items()}
        return v

    status, out = api("admin").call("POST", url, json=_fill_body(body))
    assert status < 500, f"POST {url} → {status}: {out}"
