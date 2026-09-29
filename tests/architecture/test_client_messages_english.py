"""Doc 100 (P4): los mensajes de error que llegan a la pantalla van en INGLÉS
(regla del owner: la UI es en inglés; el chat y el código, en español).

Barrido AST de los errores que se convierten en respuesta HTTP: el `detail` de
`HTTPException` en todo el backend y los errores del motor de reporting
(`QueryError` — el router lo pasa tal cual — y `SqlError`, que el editor SQL
subraya en pantalla). El Reporting tenía ~30 en español, dos con voseo.
Doc 101: también los `ValueError` de los módulos cuyo texto el service devuelve
tal cual como `detail` de un 422 (las Output settings del DDL)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
CLIENT_ERRORS = {"HTTPException", "QueryError", "SqlError"}
# Módulos cuyos ValueError llegan a pantalla (el apply los convierte en 422 con
# el mismo texto). Ruta relativa a `app/`.
VALUE_ERROR_MODULES = {"features/ddl_rules/output.py"}
# Letras propias del español o palabras que no existen en inglés.
SPANISH = re.compile(
    r"[áéíóúñÁÉÍÓÚÑ¿¡]|\b(desconocid[oa]s?|inválid[oa]s?|soportad[oa]s?|requiere|Falta|Solo|calculado|"
    r"encontrad[oa]|tuyo|agrupado|campo|Campo|Vista|vista|Reporte|reporte|Valor|Filtro|permitid[oa]|"
    r"No se|no es|para|una|del)\b")


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _texts(node: ast.AST) -> list[str]:
    """Partes literales de un str o f-string (sin las expresiones)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    if isinstance(node, ast.BinOp):
        return _texts(node.left) + _texts(node.right)
    return []


def spanish_client_messages(root: Path = APP, value_error_modules: set[str] = VALUE_ERROR_MODULES) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        wanted = CLIENT_ERRORS | ({"ValueError"} if path.relative_to(root).as_posix() in value_error_modules else set())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_name(node) in wanted):
                continue
            parts = [a for a in node.args[:2]] + [k.value for k in node.keywords if k.arg == "detail"]
            text = " ".join(t for p in parts for t in _texts(p))
            if SPANISH.search(text):
                found.append(f"{path.relative_to(root.parent)}:{node.lineno}: {text!r}")
    return found


def test_el_barrido_incluye_los_value_error_que_llegan_como_422(tmp_path):
    """Doc 101 (revisión independiente #5): un ValueError en español de un módulo
    listado se detecta; en otro módulo (errores internos) no."""
    (tmp_path / "features" / "ddl_rules").mkdir(parents=True)
    (tmp_path / "features" / "ddl_rules" / "output.py").write_text('raise ValueError("Tipo inválido")\n')
    (tmp_path / "otro.py").write_text('raise ValueError("Tipo inválido")\n')
    found = spanish_client_messages(tmp_path)
    assert len(found) == 1 and "output.py" in found[0]


def test_los_errores_que_llegan_a_pantalla_van_en_ingles():
    found = spanish_client_messages()
    assert not found, "Mensajes en español hacia la pantalla:\n" + "\n".join(found)


def test_el_barrido_detecta_un_mensaje_en_espanol(tmp_path):
    (tmp_path / "x.py").write_text(
        'raise QueryError(f"Campo desconocido: {k!r}", code=400)\n'
        'raise HTTPException(404, "Reporte no encontrado (o no es tuyo).")\n'
        'raise HTTPException(status_code=409, detail="The request is no longer in review.")\n'
        'raise SqlError("Only SELECT is allowed.")\n', encoding="utf-8")
    found = spanish_client_messages(tmp_path)
    assert len(found) == 2 and "Campo desconocido" in found[0] and "no es tuyo" in found[1]
