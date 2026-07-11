"""Validación de payloads de cambios contra los modelos de documento.

Un upsert de changeset termina APLICADO tal cual a la colección publicada en el
publish (`apply_changes` hace `$set` del payload): sin este gate, un payload
malformado (columna sin `tableId`, relación sin extremos) entra a producción y
rompe a TODOS los lectores. Se valida en dos puntos:

- `add_change` (entrada): feedback inmediato al cliente que mandó el cambio.
- `_apply_and_finalize` (salida): gate autoritativo justo antes de escribir a
  las colecciones publicadas (cubre datos legacy pre-validación).

Los deletes no llevan payload → no validan nada. La validación usa los MISMOS
modelos `*Doc` del read path (round-trip garantizado: lo que entra al changeset
es lo que las lecturas van a poder validar después).
"""
from __future__ import annotations

from pydantic import ValidationError

from app.features.catalog.models import CanonicalColumnDoc, CanonicalTableDoc
from app.features.glossary.models import AbbreviationDoc
from app.features.domains.models import ParentDomainDoc
from app.features.relationships.models import RelationshipDoc
from app.features.views.models import ViewDoc

# Colección versionada → modelo de documento (espeja VERSIONED del repository).
DOC_MODELS = {
    "parent_domains": ParentDomainDoc,
    "glossary_terms": AbbreviationDoc,
    "canonical_tables": CanonicalTableDoc,
    "canonical_columns": CanonicalColumnDoc,
    "relationships": RelationshipDoc,
    "views": ViewDoc,
}


class InvalidPayloadError(ValueError):
    """Payload de cambio que NO valida contra el modelo de su colección.
    El router la convierte en 422 (el mensaje ya es legible)."""


def payload_error(collection: str, entity_id: str, op: str | None, payload: dict | None) -> str | None:
    """Error legible si el payload no valida contra el modelo de la colección;
    None si es válido (o es un delete, que no lleva payload). Puro."""
    if op == "delete":
        return None
    model = DOC_MODELS.get(collection)
    if model is None:  # whitelist del router ya corta; defensivo
        return None
    try:
        # El id de la entidad es la KEY del cambio (no el del payload): se valida
        # el documento como quedaría publicado.
        model.model_validate({**(payload or {}), "id": entity_id})
        return None
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'payload'}: {e['msg']}"
            for e in exc.errors()[:5]
        )
        return f"{collection}/{entity_id}: {detail}"


def validate_changes(changes: dict) -> list[str]:
    """Valida TODOS los cambios de un changeset ({col: {eid: {op, payload?}}}).
    Devuelve la lista de errores legibles (vacía = todo válido). Puro."""
    errors: list[str] = []
    for col, col_changes in (changes or {}).items():
        for eid, ch in (col_changes or {}).items():
            err = payload_error(col, eid, ch.get("op"), ch.get("payload"))
            if err:
                errors.append(err)
    return errors


# ── Unicidad de nombres (spec 10 §9) ───────────────────────────────────────
# Tablas: (schema, physicalName) case-insensitive contra publicado activo +
# upserts pendientes del mismo changeset. Columnas: physicalName dentro de su
# tableId. La comparación es sobre el estado EFECTIVO del slice (los deletes
# pendientes del changeset LIBERAN el nombre). Cosmos no permite índices
# únicos sobre colecciones pobladas → la garantía vive en el router.


class DuplicateEntityError(ValueError):
    """Upsert que viola la unicidad de nombres (tabla o columna).
    El router la convierte en 409 (el mensaje ya es legible)."""


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _table_key(doc: dict) -> tuple[str, str]:
    """Clave de unicidad de tabla. El campo persistido en Mongo es `schema`
    (alias del atributo Pydantic `sql_schema`)."""
    return (_norm(doc.get("schema") or doc.get("sql_schema")), _norm(doc.get("physicalName")))


def _column_key(doc: dict) -> tuple[str, str]:
    return (str(doc.get("tableId") or ""), _norm(doc.get("physicalName")))


def _effective_docs(published: list[dict], pending: dict, exclude_id: str) -> list[dict]:
    """Estado efectivo del slice: publicado + upserts pendientes del changeset
    (que PISAN al publicado homónimo por id), sin los borrados pendientes y sin
    la propia entidad chequeada. Puro."""
    docs = {d["id"]: d for d in published}
    for eid, ch in (pending or {}).items():
        if ch.get("op") == "delete":
            docs.pop(eid, None)
        else:
            docs[eid] = {**(ch.get("payload") or {}), "id": eid}
    docs.pop(exclude_id, None)
    return list(docs.values())


def duplicate_error(collection: str, entity_id: str, payload: dict | None,
                    published: list[dict], pending: dict) -> str | None:
    """Mensaje de duplicado si el upsert viola la unicidad; None si pasa (o la
    colección no chequea unicidad). Puro.

    - `published`: slice publicado relevante (mismo nombre / misma tabla).
    - `pending`: cambios pendientes de la MISMA colección en el changeset.
    """
    p = payload or {}
    if collection == "canonical_tables":
        key = _table_key(p)
        if not key[1]:
            return None
        if any(_table_key(d) == key for d in _effective_docs(published, pending, entity_id)):
            schema = str(p.get("schema") or p.get("sql_schema") or "").strip()
            name = str(p.get("physicalName") or "").strip()
            return f"Ya existe la tabla {f'{schema}.{name}' if schema else name}"
    elif collection == "canonical_columns":
        key = _column_key(p)
        if not (key[0] and key[1]):
            return None
        if any(_column_key(d) == key for d in _effective_docs(published, pending, entity_id)):
            return f"Ya existe la columna {str(p.get('physicalName') or '').strip()} en esta tabla"
    return None
