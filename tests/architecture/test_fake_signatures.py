"""Todo fake que un test instala con `monkeypatch.setattr` acepta la firma
REAL de la función que reemplaza (doc 82).

Hallazgo de fondo del doc 80 §8: cuatro tests reemplazaban `list_udp` /
`list_domains` con `async def _udp(): ...` — la firma PRE-doc-75. Un fake con
la firma vieja no sólo esconde el `TypeError`: certifica un contrato que ya no
existe, y el bug viaja a producción con la suite en verde.

Barrido ESTÁTICO sobre `tests/`: por cada `monkeypatch.setattr(objetivo,
"nombre", fake)` (o la forma con string `"app.x.y.nombre"`) resuelve la función
real por import y compara con el `def`/`lambda` del fake definido en el mismo
archivo. Regla: el fake debe poder recibir los posicionales OBLIGATORIOS de la
real, y no exigir más posicionales de los que la real tiene. `AsyncMock`/
`MagicMock` y fakes con `*args` no se juzgan (aceptan cualquier cosa).
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"


def _aliases(tree: ast.AST, module: str) -> dict[str, str]:
    package = module.rsplit(".", 1)[0]
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in {"app", "scripts"}:
                    out[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            try:
                base = (node.module if not node.level
                        else importlib.util.resolve_name("." * node.level + (node.module or ""), package))
            except (ImportError, ValueError):
                continue
            if not base or base.split(".")[0] not in {"app", "scripts"}:
                continue
            for a in node.names:
                out[a.asname or a.name] = f"{base}.{a.name}"
    return out


def _import_attr(dotted: str):
    """`app.x.y.name` → objeto, probando el corte módulo/atributo más largo."""
    parts = dotted.split(".")
    for i in range(len(parts) - 1, 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except Exception:  # noqa: BLE001
            continue
        try:
            for attr in parts[i:]:
                obj = getattr(obj, attr)
            return obj
        except AttributeError:
            return None
    return None


def _target_of(call: ast.Call, aliases: dict[str, str]):
    """Función real que el setattr reemplaza, o None si no se puede resolver."""
    args = call.args
    if len(args) == 2 and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        return _import_attr(args[0].value)
    if len(args) == 3 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
        chain: list[str] = []
        node = args[0]
        while isinstance(node, ast.Attribute):
            chain.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in aliases:
            return None
        return _import_attr(".".join([aliases[node.id], *chain, args[1].value]))
    return None


def _fake_params(fake: ast.AST, scope_defs: dict[str, ast.AST]):
    """(posicionales totales, posicionales obligatorios, acepta *args) del fake."""
    node = fake
    if isinstance(node, ast.Name):
        node = scope_defs.get(node.id)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = node.args
        positional = a.posonlyargs + a.args
        return len(positional), len(positional) - len(a.defaults), a.vararg is not None
    return None  # AsyncMock(...) / MagicMock / expresión: no se juzga


def _real_params(func):
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return None
    pos = [p for p in sig.parameters.values()
           if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    required = [p for p in pos if p.default is p.empty]
    var = any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
    return len(pos), len(required), var


def _scope_defs(tree: ast.AST) -> dict[str, ast.AST]:
    """Toda función/lambda definida en el archivo, por nombre (último gana)."""
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value
    return out


def _verdict(fake_p, real_p) -> str | None:
    n_pos, n_req, var = fake_p
    r_pos, r_req, r_var = real_p
    if var:
        return None
    if n_pos < r_req:
        return f"el fake acepta {n_pos} posicional(es) y la real exige {r_req}"
    if n_req > r_pos and not r_var:
        return f"el fake exige {n_req} posicional(es) y la real sólo pasa {r_pos}"
    return None


def _mismatches() -> list[str]:
    bad: list[str] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _aliases(tree, module)
        defs = _scope_defs(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setattr" and not node.keywords):
                continue
            target = _target_of(node, aliases)
            if not (inspect.isfunction(target) or inspect.iscoroutinefunction(target)):
                continue
            fake = node.args[-1]
            fake_p = _fake_params(fake, defs)
            real_p = _real_params(target)
            if fake_p is None or real_p is None:
                continue
            why = _verdict(fake_p, real_p)
            if why:
                bad.append(f"{rel}:{node.lineno} — {target.__module__}.{target.__name__}: {why}")
    return bad


def test_ningun_fake_de_test_congela_una_firma_vieja():
    bad = _mismatches()
    assert not bad, (
        "Fakes con la firma de OTRA versión de la función (doc 82) — corrígelos a la firma real, "
        "no al revés:\n  " + "\n  ".join(bad))


def test_el_barrido_detecta_el_fake_del_doc_80():
    """El fake `async def _udp(): ...` contra `list_udp(project_id)` acusa; con
    la firma real, pasa."""
    from app.features.udp.repository import list_udp
    roto = ast.parse("async def _udp():\n    return []\n")
    sano = ast.parse("async def _udp(project_id):\n    return []\n")
    real = _real_params(list_udp)
    assert _verdict(_fake_params(ast.Name(id="_udp"), _scope_defs(roto)), real) is not None
    assert _verdict(_fake_params(ast.Name(id="_udp"), _scope_defs(sano)), real) is None
