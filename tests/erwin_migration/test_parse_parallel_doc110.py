"""Doc 110 — el gate parsea EN PARALELO los XML de un proyecto.

Un proceso por XML deja cada modelo en la caché del run; el recorrido del gate
(y después el migrate) los leen de ahí. Con 30 XML más en DDV, el gate ya no
crece archivo por archivo. Un XML roto no tumba el paralelo: lo reporta el
recorrido del gate, con su archivo, como siempre."""
from __future__ import annotations

import pytest

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import quality
from tests.erwin_migration.test_diagram_style_doc109 import FIXTURE


def _xmls(tmp_path, *names_models):
    paths = []
    for name, model in names_models:
        p = tmp_path / name
        p.write_text(FIXTURE.replace("Modelo Estilos", model), encoding="utf-8")
        paths.append(str(p))
    return paths


def test_deja_cada_xml_en_la_cache_y_despues_no_se_vuelve_a_parsear(tmp_path, monkeypatch):
    a, b = _xmls(tmp_path, ("a.xml", "Modelo A"), ("b.xml", "Modelo B"))
    cache = tmp_path / "cache"
    quality.prewarm_parse([a, b], str(cache), workers=2)
    assert len(list(cache.glob("*.pkl"))) == 2
    monkeypatch.setattr(ep, "parse", lambda p: pytest.fail(f"volvió a parsear {p}"))
    assert ep.parse_cached(b, str(cache)).name == "Modelo B"


def test_un_xml_roto_no_tumba_el_paralelo(tmp_path):
    (good,) = _xmls(tmp_path, ("ok.xml", "Modelo OK"))
    bad = tmp_path / "roto.xml"
    bad.write_text("<erwin><sin cerrar", encoding="utf-8")
    cache = tmp_path / "cache"
    quality.prewarm_parse([str(bad), good], str(cache), workers=2)
    assert len(list(cache.glob("*.pkl"))) == 1


def test_el_gate_paraleliza_solo_con_cache_y_varios_xml(tmp_path, monkeypatch):
    a, b = _xmls(tmp_path, ("a.xml", "Modelo A"), ("b.xml", "Modelo B"))
    cache = str(tmp_path / "cache")
    calls: list[list[str]] = []
    monkeypatch.setattr(quality, "prewarm_parse", lambda paths, cache_dir, workers=None: calls.append(list(paths)))
    quality.main([a, b, "--parse-cache", cache])
    quality.main([a, "--parse-cache", cache])
    quality.main([a, b])
    assert calls == [[a, b]]


@pytest.mark.parametrize("raw", ["auto", "2.5", "4,"])
def test_un_valor_raro_en_dmh_parse_workers_no_tumba_el_gate(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("DMH_PARSE_WORKERS", raw)
    a, b = _xmls(tmp_path, ("a.xml", "Modelo A"), ("b.xml", "Modelo B"))
    cache = tmp_path / "cache"
    quality.prewarm_parse([a, b], str(cache))
    assert len(list(cache.glob("*.pkl"))) == 2
