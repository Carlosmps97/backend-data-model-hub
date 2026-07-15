"""`descendant_ids` recoge la carpeta raíz + todas sus subcarpetas (transitivo).

Es la lógica pura que sostiene el borrado en cascada de `folders` (la carpeta y
sus descendientes se soft-borran; los canvases se desadjuntan en el repository).
"""
from __future__ import annotations

from app.features.folders.service import descendant_ids


def _folders():
    # a → b → d ; a → c ; e (suelta, no descendiente de a)
    return [
        {"id": "a", "parentFolderId": None},
        {"id": "b", "parentFolderId": "a"},
        {"id": "c", "parentFolderId": "a"},
        {"id": "d", "parentFolderId": "b"},
        {"id": "e", "parentFolderId": None},
    ]


def test_descendants_transitivos():
    out = set(descendant_ids(_folders(), "a"))
    assert out == {"a", "b", "c", "d"}
    assert "e" not in out


def test_descendants_hoja():
    assert descendant_ids(_folders(), "d") == ["d"]


def test_descendants_incluye_raiz_aunque_no_este_en_lista():
    assert descendant_ids([], "x") == ["x"]


def test_descendants_tolera_ciclos():
    cyclic = [
        {"id": "a", "parentFolderId": "b"},
        {"id": "b", "parentFolderId": "a"},
    ]
    out = set(descendant_ids(cyclic, "a"))
    assert out == {"a", "b"}  # no loop infinito
