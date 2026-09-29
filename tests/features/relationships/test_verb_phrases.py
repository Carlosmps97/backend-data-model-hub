"""Frases de relación (doc 98): `parentToChildPhrase` / `childToParentPhrase`
son dos propiedades más del OBJETO relación (Erwin: Parent-to-Child Phrase /
Child-To-Parent Phrase). Fijan el contrato del doc: se conservan, se
normalizan y JAMÁS rompen la lectura de una relación."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.relationships.models import RelationshipDoc
from app.features.relationships.schemas import RelationshipBody


def _base(**over) -> dict:
    base = {"projectId": "p1", "id": "r1", "parentTableId": "tP", "childTableId": "tC",
            "pairs": [{"parentColumnId": "cP", "childColumnId": "cC"}]}
    base.update(over)
    return base


def test_las_frases_sobreviven_al_dump():
    # El read/write path re-valida con extra="ignore": campo no declarado = frase perdida.
    d = RelationshipDoc.model_validate(_base(
        parentToChildPhrase="Cliente tiene", childToParentPhrase="pertenece a un Cliente")).model_dump()
    assert d["parentToChildPhrase"] == "Cliente tiene"
    assert d["childToParentPhrase"] == "pertenece a un Cliente"


def test_relacion_sin_frases_lee_null():
    # Relaciones previas al doc 98: sin las llaves → None, sin error.
    d = RelationshipDoc.model_validate(_base())
    assert d.parentToChildPhrase is None and d.childToParentPhrase is None


@pytest.mark.parametrize("raw,want", [
    ("  Cliente tiene  ", "Cliente tiene"),
    ("", None),
    ("   ", None),
    (None, None),
    ("un\testado", "un\testado"),          # solo se recortan los extremos
])
def test_la_frase_se_recorta_y_vacio_es_null(raw, want):
    d = RelationshipDoc.model_validate(_base(parentToChildPhrase=raw, childToParentPhrase=raw))
    assert d.parentToChildPhrase == want
    assert d.childToParentPhrase == want


def test_cada_direccion_es_independiente():
    d = RelationshipDoc.model_validate(_base(childToParentPhrase="un Estado Civil"))
    assert d.parentToChildPhrase is None
    assert d.childToParentPhrase == "un Estado Civil"


def test_frase_larga_no_rompe_la_lectura():
    # Sin validación dura de largo: una frase migrada larga no debe dar 500 al
    # abrir el canvas (el tope vive en la caja de texto del front).
    largo = "x" * 400
    d = RelationshipDoc.model_validate(_base(parentToChildPhrase=largo))
    assert d.parentToChildPhrase == largo


@pytest.mark.parametrize("malo", [2024, False, {}, ["x"]])
def test_frase_que_no_es_texto_se_rechaza(malo):
    # Igual que cualquier otro campo de texto: el changeset valida con este
    # modelo y no debe convertir basura en una etiqueta visible ("False", "{}").
    with pytest.raises(ValidationError):
        RelationshipDoc.model_validate(_base(parentToChildPhrase=malo))
    with pytest.raises(ValidationError):
        RelationshipBody.model_validate({
            "parentTableId": "tP", "childTableId": "tC",
            "pairs": [{"parentColumnId": "cP", "childColumnId": "cC"}], "childToParentPhrase": malo})


def test_payload_legacy_conserva_las_frases():
    # Before-images viejas (source/target) siguen normalizando a v2 CON frases.
    d = RelationshipDoc.model_validate({
        "projectId": "p1", "id": "r1",
        "sourceTableId": "tS", "sourceColumnId": "cS",
        "targetTableId": "tT", "targetColumnId": "cT",
        "childToParentPhrase": "un punto de contacto",
    })
    assert d.childTableId == "tS" and d.parentTableId == "tT"
    assert d.childToParentPhrase == "un punto de contacto"


def test_subcategoria_conserva_las_frases():
    # El XML lógico trae 28 relaciones de subtipo con frase: el dato no se pierde.
    d = RelationshipDoc.model_validate(_base(
        subcategory=True, subtypeSymbolId="sym-1", parentToChildPhrase="Party es un Individuo"))
    assert d.subcategory is True
    assert d.parentToChildPhrase == "Party es un Individuo"


def test_el_dto_del_endpoint_directo_normaliza_igual_que_el_doc():
    # PUT /api/relationships/{id} hace $set del dump del DTO: si el DTO no
    # normaliza, queda guardado "  x  " aunque la respuesta salga limpia.
    body = RelationshipBody.model_validate({
        "parentTableId": "tP", "childTableId": "tC",
        "pairs": [{"parentColumnId": "cP", "childColumnId": "cC"}],
        "parentToChildPhrase": "  Cliente tiene ", "childToParentPhrase": "   ",
    }).model_dump()
    assert body["parentToChildPhrase"] == "Cliente tiene"
    assert body["childToParentPhrase"] is None


def test_el_dto_del_endpoint_directo_acepta_las_frases():
    # POST/PUT /api/relationships filtra por el DTO: sin los campos, se caen.
    body = RelationshipBody.model_validate({
        "parentTableId": "tP", "childTableId": "tC",
        "pairs": [{"parentColumnId": "cP", "childColumnId": "cC"}],
        "parentToChildPhrase": "Cliente tiene", "childToParentPhrase": "pertenece a un Cliente",
    }).model_dump()
    assert body["parentToChildPhrase"] == "Cliente tiene"
    assert body["childToParentPhrase"] == "pertenece a un Cliente"
