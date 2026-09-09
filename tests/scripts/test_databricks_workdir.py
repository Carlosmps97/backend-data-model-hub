"""Limpieza y verificación del área de trabajo del driver de Databricks (doc 77
§5): el `rm -rf` que corre el notebook antes de bajar los XML de ADLS."""
from __future__ import annotations

import pytest

from scripts.databricks.workdir import limpiar, verificar


def _arbol(root, *rels, contenido="xyz"):
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contenido)
    return root


def test_limpiar_borra_el_arbol_y_reporta_lo_borrado(tmp_path):
    work = _arbol(tmp_path / "erwin" / "carpeta", "MODELO DDV/a.xml", "suelto.xml")
    assert limpiar(work, permitidos=(str(tmp_path),)) == (2, 6)
    assert not work.exists()


def test_limpiar_ignora_lo_que_no_existe(tmp_path):
    assert limpiar(tmp_path / "erwin" / "nada", permitidos=(str(tmp_path),)) == (0, 0)


def test_limpiar_se_niega_fuera_de_las_rutas_permitidas(tmp_path):
    fuera = _arbol(tmp_path / "datos", "importante.xml")
    with pytest.raises(RuntimeError, match="fuera del área de trabajo"):
        limpiar(fuera, permitidos=(str(tmp_path / "erwin"),))
    assert (fuera / "importante.xml").exists()      # nada se tocó


def test_limpiar_se_niega_si_la_ruta_escapa_por_dot_dot(tmp_path):
    fuera = _arbol(tmp_path / "datos", "importante.xml")
    escape = tmp_path / "erwin" / ".." / "datos"
    with pytest.raises(RuntimeError, match="fuera del área de trabajo"):
        limpiar(escape, permitidos=(str(tmp_path / "erwin"),))
    assert (fuera / "importante.xml").exists()


def test_limpiar_se_niega_a_borrar_la_raiz_permitida_entera(tmp_path):
    """Sólo subcarpetas: borrar la raíz se llevaría el caché de elkjs con todo."""
    raiz = _arbol(tmp_path / "erwin", "elk/package/lib/elk.bundled.js")
    with pytest.raises(RuntimeError, match="fuera del área de trabajo"):
        limpiar(raiz, permitidos=(str(raiz),))
    assert (raiz / "elk/package/lib/elk.bundled.js").exists()


def test_verificar_detecta_lo_que_falta_y_lo_que_no_cuadra_de_tamano(tmp_path):
    _arbol(tmp_path, "ok.xml", "corto.xml")
    esperados = [(str(tmp_path / "ok.xml"), 3),
                 (str(tmp_path / "corto.xml"), 999),
                 (str(tmp_path / "nunca.xml"), 10)]
    assert verificar(esperados) == [str(tmp_path / "corto.xml"),
                                    str(tmp_path / "nunca.xml")]


def test_verificar_vacio_cuando_todo_cuadra(tmp_path):
    _arbol(tmp_path, "ok.xml")
    assert verificar([(str(tmp_path / "ok.xml"), 3)]) == []
