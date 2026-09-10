"""Ninguna lectura del ledger publicado puede salir SIN alcance de proyecto (doc 75 D1).

`repository.published()` corre `assert_scoped_filter`: si el filtro no trae
`projectId`, `_id` o `tableId` en el PRIMER nivel, levanta `MissingProjectError`
— que por diseño NO se mapea a un status de negocio y sale como **500**. O sea:
un filtro sin alcance no es una fuga entre proyectos, es una pantalla caída.

Fue exactamente eso el 2026-09-09: `service.diff` pedía las relaciones con un
`{"$or": [...]}` pelado y `GET /changesets/{id}/diff` reventaba en cada request
que tocara una tabla — la revisión mostraba "Request not found" y no se podía
aprobar nada. Los tests de servicio no lo vieron porque mockean `published`
entero, así que el guard nunca corre en la suite.

Este barrido cierra la clase de bug ANTES de que llegue a producción: revisa
estáticamente el filtro de CADA llamada a `published(...)` en `app/`. Las
formas aceptadas son deliberadamente pocas:

  · literal `{...}` con `projectId` / `_id` / `tableId` de primer nivel,
  · `scoped(...)` / `_scoped(...)` / `_table_dup_filter(...)` (los constructores
    de alcance — `scoped()` levanta solo si el proyecto viene vacío),
  · sin filtro (colecciones fuera de `PROJECT_SCOPED`),
  · una variable, SOLO en los sitios listados en `_OPAQUE_OK`.

Si agregas una llamada nueva y este test se queja: no la agregues a la lista —
pasa el filtro por `scoped(project_id, {...})`. La lista existe para los tres
casos donde el alcance ya lo puso el llamador y el AST no puede verlo.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

#: Claves que YA acotan a un proyecto (espejo de `app.core.scope._SCOPING_KEYS`).
SCOPING_KEYS = {"projectId", "_id", "tableId"}
#: Helpers que construyen un filtro con alcance.
SCOPE_BUILDERS = {"scoped", "_scoped", "_table_dup_filter"}
#: (módulo relativo a `app/`, nombre de la variable) donde el alcance lo puso el
#: llamador y el AST no lo alcanza. Cada entrada explica POR QUÉ es segura.
_OPAQUE_OK = {
    # `_effective(cs_id, coll, flt)` recibe `scoped(pid)` de `load_context`.
    ("features/bulk_upload/loader.py", "flt"),
    # `_publish_duplicates`: `flt = scoped(project_id, {...})` en la línea previa.
    ("features/changesets/service.py", "flt"),
}


def _callee(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _describe(arg: ast.expr) -> str:
    """Forma del filtro: 'ok' si trae alcance, o el motivo del rechazo."""
    if isinstance(arg, ast.Await):
        return _describe(arg.value)
    if isinstance(arg, ast.IfExp):   # `published(...) if x else []`
        both = [_describe(arg.body), _describe(arg.orelse)]
        return next((d for d in both if d != "ok"), "ok")
    if isinstance(arg, ast.Constant) and arg.value is None:
        return "ok"
    if isinstance(arg, ast.Dict):
        keys = {k.value for k in arg.keys if isinstance(k, ast.Constant)}
        if SCOPING_KEYS & keys:
            return "ok"
        return f"literal sin alcance (claves: {sorted(keys) or 'ninguna'})"
    if isinstance(arg, ast.Call):
        name = _callee(arg)
        if name in SCOPE_BUILDERS:
            return "ok"
        return f"llamada a {name!r}: no construye alcance"
    if isinstance(arg, ast.Name):
        return f"variable {arg.id!r}"
    return f"expresión {type(arg).__name__} no analizable"


def _offenders() -> list[str]:
    bad: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _callee(node) != "published":
                continue
            if len(node.args) < 2:                 # `published(coll)` → sin filtro
                continue
            verdict = _describe(node.args[1])
            if verdict == "ok":
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Name) and (rel, arg.id) in _OPAQUE_OK:
                continue
            bad.append(f"app/{rel}:{node.lineno} — {verdict}")
    return bad


def test_published_siempre_recibe_un_filtro_con_alcance():
    offenders = _offenders()
    assert not offenders, (
        "Lecturas del ledger sin alcance de proyecto (doc 75 D1) — cada una es un "
        "500 en producción, no un error de negocio. Envuelve el filtro en "
        "`scoped(project_id, {...})`:\n  " + "\n  ".join(offenders)
    )


def test_el_barrido_detecta_el_bug_de_2026_09_09():
    """El guard se guarda a sí mismo: sobre el código ROTO, el barrido acusa."""
    roto = ast.parse(
        'await repository.published("relationships", '
        '{"$or": [{"parentTableId": {"$in": t}}, {"childTableId": {"$in": t}}]})'
    )
    call = next(n for n in ast.walk(roto) if isinstance(n, ast.Call) and _callee(n) == "published")
    assert _describe(call.args[1]).startswith("literal sin alcance")

    sano = ast.parse('await repository.published("relationships", scoped(pid, {"$or": []}))')
    call = next(n for n in ast.walk(sano) if isinstance(n, ast.Call) and _callee(n) == "published")
    assert _describe(call.args[1]) == "ok"
