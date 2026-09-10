"""Ninguna función de `app/` o `scripts/` queda sin `return` ni con código
muerto tras un `return` (doc 82).

Regresión real (doc 75, commit `da296fa`): al insertar
`changesets_deleting_project` EN MEDIO de `repository.earliest_applied`, la
cola de esta última (`if not docs … return {...}`) quedó pegada DESPUÉS del
`return` de la nueva. Resultado: `earliest_applied` devolvía siempre `None`
(sin `return`), el código real quedó inalcanzable, la suite no lo vio (el
test la mockeaba) y el historial perdió la fila «Initial load».

Dos chequeos AST, baratos y sin falsos positivos conocidos:
1. función con anotación de retorno distinta de `None` cuya cuerpo no tiene
   `return <valor>`, `raise`, `yield` ni es un stub (`...`/`pass`/docstring);
2. sentencia que sigue a un `return`/`raise`/`continue`/`break` en el MISMO
   bloque (inalcanzable).
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = (ROOT / "app", ROOT / "scripts")
_TERMINAL = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _returns_none(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    ann = node.returns
    return ann is None or (isinstance(ann, ast.Constant) and ann.value is None)


def _is_stub(body: list[ast.stmt]) -> bool:
    real = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    return not real or all(isinstance(s, ast.Pass) for s in real)


def _has_value_exit(node: ast.AST) -> bool:
    """`return <expr>` / `raise` / `yield` en el cuerpo, sin bajar a funciones anidadas."""
    for child in ast.walk(node):
        if child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Return) and child.value is not None:
            return True
        if isinstance(child, (ast.Raise, ast.Yield, ast.YieldFrom)):
            return True
    return False


def _walk_functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _dead_code(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block[:-1]):
                if isinstance(stmt, _TERMINAL):
                    lines.append(block[i + 1].lineno)
                    break
    return lines


def _offenders() -> tuple[list[str], list[str]]:
    no_return: list[str] = []
    dead: list[str] = []
    for tree_root in TREES:
        for path in sorted(tree_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for fn in _walk_functions(tree):
                if _returns_none(fn) or _is_stub(fn.body):
                    continue
                if not _has_value_exit(fn):
                    no_return.append(f"{rel}:{fn.lineno} — {fn.name}() anota retorno y no devuelve nada")
            for line in _dead_code(tree):
                dead.append(f"{rel}:{line} — código inalcanzable tras return/raise/continue/break")
    return no_return, dead


def test_toda_funcion_con_retorno_anotado_devuelve_algo():
    no_return, _ = _offenders()
    assert not no_return, "Funciones truncadas (doc 82):\n  " + "\n  ".join(no_return)


def test_no_hay_codigo_muerto_tras_un_return():
    _, dead = _offenders()
    assert not dead, "Código inalcanzable (doc 82):\n  " + "\n  ".join(dead)


def test_el_barrido_detecta_el_corte_del_doc_75():
    """Sobre la forma ROTA (una función sin return + la cola pegada a otra) acusa."""
    roto = ast.parse(
        "async def earliest_applied(project_id: str) -> dict | None:\n"
        "    db = await get_db()\n"
        "    docs = await db.find()\n"
        "\n"
        "async def deleting(ids: list[str]) -> set[str]:\n"
        "    docs = await db.find()\n"
        "    return {d for d in docs}\n"
        "    if not docs:\n"
        "        return None\n"
    )
    fns = list(_walk_functions(roto))
    assert not _has_value_exit(fns[0]) and _has_value_exit(fns[1])
    assert _dead_code(roto) == [8]
    sano = ast.parse("def f() -> int:\n    return 1\n\ndef g() -> None:\n    pass\n")
    assert all(_has_value_exit(f) or _returns_none(f) for f in _walk_functions(sano)) and _dead_code(sano) == []
