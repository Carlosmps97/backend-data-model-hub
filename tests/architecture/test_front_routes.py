"""Contrato de rutas front ↔ back (doc 82): toda llamada `apiGet/apiPost/…` del
frontend apunta a una ruta que el backend REALMENTE expone.

Regresión real (doc 75): las lecturas del catálogo pasaron a
`/api/projects/{pid}/catalog/…`, pero el buscador de tablas del editor de
reglas DDL siguió pidiendo `GET /api/catalog/tables` — 404 silencioso (el
componente traga el error y muestra una lista vacía). Ninguna suite lo vio:
la del front mockea `fetch`; la del back no conoce al front.

El barrido lee las fuentes del front hermano (`../web-data-model-hub/src`),
resuelve las constantes locales de URL (`const X = …` y `const X = (pid)
=> …`), normaliza `${…}` de segmento a parámetro y descarta los sufijos de
query string; después cruza (método, ruta) contra `app.routes`. Si el repo del
front no está al lado (CI del backend), el test se salta."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONT_SRC = ROOT.parent / "web-data-model-hub" / "src"

_CALL = re.compile(r"\bapi(Get|Post|Put|Patch|Delete)\b\s*(?:<[^(]*?>)?\s*\(\s*(`(?:[^`\\]|\\.)*`|'[^']*'|\"[^\"]*\")", re.S)
_CONST_LIT = re.compile(r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*`([^`]*)`")
_CONST_FN = re.compile(r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\([^)]*\)\s*(?::\s*[^=]+?)?=>\s*`([^`]*)`")
_METHOD = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE"}


def normalize(raw: str, lits: dict[str, str], fns: dict[str, str]) -> str:
    """Template TS → ruta comparable: `/api/projects/{x}/catalog/tables`."""
    for _ in range(4):
        raw = raw.replace("${API_BASE}", "")
        raw = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\(([^()]*(?:\([^()]*\))?[^()]*)\)\}",
                     lambda m: fns.get(m.group(1), m.group(0)), raw)
        raw = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: lits.get(m.group(1), m.group(0)), raw)
    raw = raw.split("?")[0]
    # `${…}` que ocupa un segmento entero es un parámetro; cualquier otro
    # `${…}` (sufijo de query string, ternarios) termina la ruta.
    out = []
    i = 0
    while i < len(raw):
        if raw.startswith("${", i):
            if i > 0 and raw[i - 1] == "/":
                depth, j = 0, i
                while j < len(raw):
                    if raw[j] == "{":
                        depth += 1
                    elif raw[j] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                out.append("{x}")
                i = j + 1
                continue
            break
        out.append(raw[i])
        i += 1
    return "".join(out)


def front_calls() -> list[tuple[str, str, str]]:
    calls = []
    for f in sorted(FRONT_SRC.rglob("*.ts*")):
        if ".test." in f.name or f.name.endswith(".d.ts"):
            continue
        src = f.read_text(encoding="utf-8")
        lits, fns = dict(_CONST_LIT.findall(src)), dict(_CONST_FN.findall(src))
        for m in _CALL.finditer(src):
            path = normalize(m.group(2)[1:-1], lits, fns)
            calls.append((_METHOD[m.group(1)], path, f"{f.relative_to(FRONT_SRC)}:{src.count(chr(10), 0, m.start()) + 1}"))
    return calls


def backend_routes() -> list[tuple[str, re.Pattern, str]]:
    from app.main import app
    out = []
    for r in app.routes:
        for m in getattr(r, "methods", None) or ():
            out.append((m, re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", r.path) + "$"), r.path))
    return out


@pytest.mark.skipif(not FRONT_SRC.exists(), reason="frontend hermano no está al lado del backend")
def test_toda_llamada_del_front_tiene_ruta_en_el_backend():
    routes = backend_routes()
    calls = front_calls()
    assert len(calls) >= 100, f"el barrido sólo encontró {len(calls)} llamadas: ¿cambió el cliente HTTP del front?"
    bad = [f"{m} {p}  ({where})" for m, p, where in calls
           if not any(rm == m and rx.match(p) for rm, rx, _ in routes)]
    assert not bad, "Llamadas del front sin ruta en el backend (doc 82):\n  " + "\n  ".join(bad)


def test_la_normalizacion_resuelve_constantes_y_query_strings():
    lits = {"CS": "${API_BASE}/api/changesets"}
    fns = {"STD": "${API_BASE}/api/projects/${encodeURIComponent(pid)}/standards"}
    assert normalize("${CS}/${encodeURIComponent(id)}/diff/details", lits, fns) == "/api/changesets/{x}/diff/details"
    assert normalize("${STD(pid)}/snapshot", lits, fns) == "/api/projects/{x}/standards/snapshot"
    assert normalize("${API_BASE}/api/versions${qs ? `?${qs}` : ''}", lits, fns) == "/api/versions"
    assert normalize("${API_BASE}/api/catalog/tables?q=${encodeURIComponent(q)}", lits, fns) == "/api/catalog/tables"
