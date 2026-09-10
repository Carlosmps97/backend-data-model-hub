"""Toda llamada interna de `app/` satisface la firma REAL de su destino.

Regresión 2026-09-09 (doc 80 §8): el doc 75 volvió los catálogos de Data
Standards por proyecto — `list_udp(project_id)`, `list_domains(project_id)` —
pero SEIS call sites de `changesets/service.py` quedaron con la llamada vieja
sin argumento. Resultado: `TypeError` → 500 en tres pantallas vivas (el popup
«Change details» de la revisión, el historial del Object Inspector y el compare
de versiones). Nada falla hasta que se ejecuta esa línea, así que el error
viajó a producción.

La suite NO podía verlo: los tests reemplazan esos repos con fakes escritos a
mano (`async def _udp(): ...`) que **congelaron la firma pre-doc-75**. Un fake
con la firma vieja no sólo esconde el bug: certifica un contrato que ya no
existe.

Este barrido es ESTÁTICO — inmune a mocks. Resuelve cada llamada a su función
real vía los imports del archivo (tanto `alias.func(...)` como el `func(...)` de
un `from app.x import func`) y valida los argumentos con
`inspect.signature().bind()`. No sustituye a un type checker: cubre justo el
patrón que usa este código (repos y services importados como módulo) y corre en
milisegundos.

Cubre `app/` **y** `scripts/`: el kit de migración lo re-ejecuta el owner contra
la base real, y ahí un `TypeError` se descubre a mitad de una carga.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: Árboles barridos. `scripts/` entra porque el one-shot corre contra la BD viva.
TREES = (ROOT / "app", ROOT / "scripts")

#: Deuda CONOCIDA, no licencia: `scripts/e2e/` quedó desactualizado con el doc 75
#: (sus escenarios además pegan a rutas pre-`/api/projects/{pid}/…`). Se aísla
#: acá para que el resto de `scripts/` —el kit de migración— sí quede cubierto.
#: Al reescribir el harness, borrar esta entrada.
_KNOWN_DEBT = ("scripts/e2e/",)


def _module_of(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _aliases(tree: ast.AST, module: str) -> dict[str, str]:
    """Alias local → módulo absoluto, sólo para destinos dentro de `app.`."""
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


def _resolve(node: ast.Call, aliases: dict[str, str]):
    """Función real destino de la llamada, o None si no se puede resolver."""
    fn = node.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        target = aliases.get(fn.value.id)          # alias = MÓDULO
        if not target:
            return None
        try:
            return getattr(importlib.import_module(target), fn.attr, None)
        except Exception:
            return None
    if isinstance(fn, ast.Name):
        target = aliases.get(fn.id)                # alias = FUNCIÓN importada
        if not target:
            return None
        module, _, name = target.rpartition(".")
        try:
            return getattr(importlib.import_module(module), name, None)
        except Exception:
            return None
    return None


def _mismatches() -> list[str]:
    bad: list[str] = []
    files = [p for tree in TREES for p in sorted(tree.rglob("*.py"))]
    for path in files:
        if "__pycache__" in path.parts:
            continue
        rel_path = path.relative_to(ROOT).as_posix()
        if rel_path.startswith(_KNOWN_DEBT):
            continue
        module = _module_of(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        aliases = _aliases(tree, module)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            func = _resolve(node, aliases)
            if not (inspect.isfunction(func) or inspect.iscoroutinefunction(func)):
                continue
            # `f(*args)` / `f(**kw)`: el AST no sabe cuántos son ⇒ no se juzga.
            if any(isinstance(a, ast.Starred) for a in node.args) \
                    or any(k.arg is None for k in node.keywords):
                continue
            try:
                sig = inspect.signature(func)
            except (ValueError, TypeError):
                continue
            sentinel = object()
            try:
                sig.bind(*[sentinel] * len(node.args),
                         **{k.arg: sentinel for k in node.keywords})
            except TypeError as exc:
                bad.append(f"{rel_path}:{node.lineno} — {ast.unparse(fn)}(…) → {exc}")
    return bad


def test_ninguna_llamada_interna_contradice_la_firma_de_su_destino():
    bad = _mismatches()
    assert not bad, (
        "Llamadas que no satisfacen la firma de su destino — cada una es un "
        "TypeError en cuanto se ejecute esa línea (500 en producción):\n  "
        + "\n  ".join(bad)
    )


def test_el_barrido_cubre_las_llamadas_que_importan():
    """Guard del guard: si un refactor rompiera la resolución de alias, el test
    de arriba pasaría vacío y en silencio. Acá se fija un piso."""
    from app.features.changesets import service            # noqa: F401  (import real)
    checked = 0
    path = ROOT / "app" / "features" / "changesets" / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _aliases(tree, _module_of(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _resolve(node, aliases) is not None:
            checked += 1
    assert checked >= 40, f"el barrido sólo alcanzó {checked} llamadas en changesets/service.py"
