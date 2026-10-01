"""Doc 105 (A2-o6): toda ruta que nombra la suite E2E (`scripts/e2e/*.py`)
existe en la app CON ese método. Estático (AST), así cubre también lo que sólo
corre contra el backend vivo (`e2e_sso.py`) y las ramas que la corrida en
memoria no recorre. La suite vieja pegaba a rutas anteriores al doc 75
(`/api/catalog/tables`, `/api/domains`, `/api/standards/apply`…) sin que nada
lo avisara."""
from __future__ import annotations

import ast
import re
from pathlib import Path

E2E = Path(__file__).resolve().parents[2] / "scripts" / "e2e"
_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}
#: Rutas que la suite nombra A PROPÓSITO para comprobar que ya no existen (404).
_GONE_ON_PURPOSE = {("GET", "/api/dictionary")}


def _raw(node: ast.AST, names: dict[str, str]) -> str | None:
    """Texto de un str / f-string / nombre asignado a uno, con `{x}` por cada
    valor interpolado."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in names:
                parts.append(names[v.value.id])
            else:
                parts.append("{x}")
        return "".join(parts)
    return None


def _path(raw: str | None) -> str | None:
    i = (raw or "").find("/api/")
    return raw[i:].split("?")[0] if i >= 0 else None


def e2e_calls() -> list[tuple[str, str, str]]:
    """(método, ruta, archivo:línea) de cada llamada HTTP de la suite."""
    out = []
    for f in sorted(E2E.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        scopes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))] + [tree]
        for scope in scopes:
            names = {t.id: raw for n in ast.walk(scope) if isinstance(n, ast.Assign)
                     for t in n.targets if isinstance(t, ast.Name)
                     if (raw := _raw(n.value, {})) and "/api/" in raw}
            for node in ast.walk(scope):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                method = _METHODS.get(name.lstrip("_").lower())
                path = _path(_raw(node.args[0], names)) if method else None
                if path:
                    out.append((method, path, f"{f.name}:{node.lineno}"))
    return sorted(set(out))


def test_toda_ruta_de_la_suite_e2e_existe_en_la_app():
    from app.main import app
    routes = [(m, re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", r.path) + "$"))
              for r in app.routes for m in (getattr(r, "methods", None) or ())]
    calls = e2e_calls()
    assert len(calls) >= 60, f"el barrido sólo encontró {len(calls)} llamadas: ¿cambió el cliente del harness?"
    bad = [f"{m} {p}  ({where})" for m, p, where in calls
           if (m, p) not in _GONE_ON_PURPOSE and not any(rm == m and rx.match(p) for rm, rx in routes)]
    assert not bad, "Rutas de la suite E2E que la app no expone:\n  " + "\n  ".join(bad)
    assert not any(any(rm == m and rx.match(p) for rm, rx in routes) for m, p in _GONE_ON_PURPOSE)


def test_el_barrido_resuelve_fstrings_y_nombres():
    tree = ast.parse('gl = f"/api/projects/{pid}/glossary"\ncli.post(f"{gl}/validate?x=1")\n_get(f"{B}/api/views?tableId={t}")')
    names = {"gl": _raw(tree.body[0].value, {})}
    assert _path(_raw(tree.body[1].value.args[0], names)) == "/api/projects/{x}/glossary/validate"
    assert _path(_raw(tree.body[2].value.args[0], names)) == "/api/views"
