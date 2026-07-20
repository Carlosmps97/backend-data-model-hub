"""Tests de la lógica PURA de construcción del seed `scripts/seed_modeler.py`.

No tocan la DB: verifican que las listas se generen y que los ids referenciales
sean consistentes (column.tableId existe, rel endpoints existen, canvas.tableIds
existen, etc.) y que el seed respete el contrato de los modelos Pydantic.
"""
from __future__ import annotations

import pytest

from app.features.auth.models import RoleDoc, UserDoc
from app.features.catalog.models import CanonicalColumnDoc, CanonicalTableDoc
from app.features.changesets.models import ChangeDoc, ChangesetDoc
from app.features.data_standards.models import StandardsVersionDoc
from app.features.glossary.models import AbbreviationDoc
from app.features.domains.models import ParentDomainDoc
from app.features.folders.models import FolderDoc
from app.features.projects.models import ProjectDoc, SubjectAreaDoc
from app.features.relationships.models import RelationshipDoc
from app.features.settings.models import NamingConfigDoc
from app.features.views.models import ViewDoc
from scripts import seed_modeler as sm


@pytest.fixture(scope="module")
def data() -> dict[str, list[dict]]:
    return sm.build_all()


# ── Las listas se generan ────────────────────────────────────────────────────

def test_build_all_has_every_collection(data):
    assert set(data) == set(sm.MODELER_COLLECTIONS)
    for coll, docs in data.items():
        assert isinstance(docs, list) and docs, f"{coll} vacío"


def test_protected_collection_never_wiped():
    assert sm.PROTECTED_COLLECTION == "column_catalog"
    assert sm.PROTECTED_COLLECTION not in sm.MODELER_COLLECTIONS


def test_expected_cardinalities(data):
    assert len(data["projects"]) == 11
    assert len(data["parent_domains"]) == 4
    assert len(data["naming_config"]) == 2
    assert len(data["canonical_tables"]) >= 30
    assert len(data["relationships"]) >= 25
    # 1 versión publicada + 2 requests en revisión.
    statuses = [c["status"] for c in data["changesets"]]
    assert statuses.count("approved") >= 1
    assert statuses.count("submitted") >= 2


def test_every_doc_has_id_and_flgactive(data):
    for coll, docs in data.items():
        for d in docs:
            assert d.get("_id"), f"{coll} doc sin _id"
            assert d.get("flgactive") is True, f"{coll}:{d.get('_id')} no flgactive"


def test_ids_unique_per_collection(data):
    for coll, docs in data.items():
        ids = [d["_id"] for d in docs]
        assert len(ids) == len(set(ids)), f"{coll} tiene _id duplicados"


# ── Integridad referencial ───────────────────────────────────────────────────

def _table_ids(data) -> set[str]:
    return {t["_id"] for t in data["canonical_tables"]}


def _column_ids(data) -> set[str]:
    return {c["_id"] for c in data["canonical_columns"]}


def test_folders_reference_existing_projects(data):
    projects = {p["_id"] for p in data["projects"]}
    folder_ids = {f["_id"] for f in data["folders"]}
    for f in data["folders"]:
        assert f["projectId"] in projects
        if f["parentFolderId"] is not None:
            assert f["parentFolderId"] in folder_ids


def test_columns_reference_existing_tables_and_domains(data):
    tables = _table_ids(data)
    domains = {d["_id"] for d in data["parent_domains"]}
    for c in data["canonical_columns"]:
        assert c["tableId"] in tables, f"col {c['_id']} → tabla inexistente"
        assert c["parentDomainId"] in domains, f"col {c['_id']} → dominio inexistente"


def test_every_table_has_exactly_one_pk(data):
    by_table: dict[str, int] = {}
    for c in data["canonical_columns"]:
        if c.get("isPrimaryKey"):
            by_table[c["tableId"]] = by_table.get(c["tableId"], 0) + 1
    for t in data["canonical_tables"]:
        assert by_table.get(t["_id"], 0) == 1, f"{t['_id']} no tiene exactamente 1 PK"


def test_relationship_endpoints_exist(data):
    tables = _table_ids(data)
    columns = _column_ids(data)
    assert data["relationships"], "sin relaciones"
    for r in data["relationships"]:
        assert r["parentTableId"] in tables
        assert r["childTableId"] in tables
        assert r["pairs"], f"{r['_id']} sin pares"
        for p in r["pairs"]:
            assert p["parentColumnId"] in columns, f"{r['_id']} parentColumn inexistente"
            assert p["childColumnId"] in columns, f"{r['_id']} childColumn inexistente"
            # cada columna del par pertenece a su tabla
            assert any(
                c["_id"] == p["parentColumnId"] and c["tableId"] == r["parentTableId"]
                for c in data["canonical_columns"]
            )
            assert any(
                c["_id"] == p["childColumnId"] and c["tableId"] == r["childTableId"]
                for c in data["canonical_columns"]
            )


def test_canvas_tableids_and_layout_exist(data):
    tables = _table_ids(data)
    projects = {p["_id"] for p in data["projects"]}
    folders = {f["_id"] for f in data["folders"]}
    for sa in data["subject_areas"]:
        assert sa["projectId"] in projects
        if sa["folderId"] is not None:
            assert sa["folderId"] in folders
        assert sa["tableIds"], f"canvas {sa['_id']} sin tableIds"
        for tid in sa["tableIds"]:
            assert tid in tables, f"canvas {sa['_id']} → tabla inexistente {tid}"
            assert tid in sa["layout"], f"canvas {sa['_id']} sin layout para {tid}"
            assert {"x", "y"} <= set(sa["layout"][tid])


def test_changeset_changes_reference_published_tables(data):
    """Los upserts de columnas en requests apuntan a tablas reales (publicadas o
    creadas en el mismo changeset) → el diff/impacto tiene contenido. Cada
    cambio es UN doc de `changeset_changes` con `_id` determinista."""
    published_tables = _table_ids(data)
    cs_ids = {c["_id"] for c in data["changesets"]}
    by_cs: dict[str, list[dict]] = {}
    for ch in data["changeset_changes"]:
        assert ch["csId"] in cs_ids, f"{ch['_id']} → changeset inexistente"
        assert ch["_id"] == f"{ch['csId']}::{ch['collection']}::{ch['entityId']}"
        assert ch["op"] in ("upsert", "delete")
        by_cs.setdefault(ch["csId"], []).append(ch)

    submitted = {c["_id"] for c in data["changesets"] if c["status"] == "submitted"}
    for cs_id, chs in by_cs.items():
        new_tables = {c["entityId"] for c in chs if c["collection"] == "canonical_tables"}
        valid_tables = published_tables | new_tables
        col_changes = [c for c in chs if c["collection"] == "canonical_columns"]
        for ch in col_changes:
            tid = (ch.get("payload") or {}).get("tableId")
            assert tid in valid_tables, f"{ch['_id']} → tabla inexistente {tid}"
        # al menos un request toca una tabla publicada (impacto vía relationships)
        if cs_id in submitted:
            touched = {(c.get("payload") or {}).get("tableId") for c in col_changes}
            assert touched & published_tables, f"{cs_id} no toca tablas publicadas"
    # los 2 requests en revisión tienen cambios sembrados
    assert submitted <= set(by_cs), "hay requests submitted sin cambios"


def test_reviewers_are_simulated_users(data):
    from app.features.identity.users import list_users
    sim_ids = {u["id"] for u in list_users()}
    for cs in data["changesets"]:
        for r in cs.get("reviewers", []):
            assert r in sim_ids, f"{cs['_id']} reviewer {r} no es usuario sim"
        assert cs["owner"] in sim_ids


# ── Naming / diccionario ─────────────────────────────────────────────────────

def test_naming_config_one_doc_per_scope(data):
    scopes = {d["_id"] for d in data["naming_config"]}
    assert scopes == {"column", "table"}
    for d in data["naming_config"]:
        assert d["scope"] == d["_id"]
        assert "separator" in d and "case" in d


def test_glossary_terms_has_both_scopes_and_wordtypes(data):
    scopes = {d["scope"] for d in data["glossary_terms"]}
    assert scopes == {"column", "table"}
    word_types = {d["wordType"] for d in data["glossary_terms"]}
    assert {"prime", "class", "modifier"} <= word_types


def test_physical_names_follow_naming_engine(data):
    """El físico de columna usa separador '_' upper; el de tabla sin separador."""
    cols = {c["logicalName"]: c["physicalName"] for c in data["canonical_columns"]}
    # 'monto deuda dolares' → MTO_DEU_USD (longest match del diccionario column)
    assert cols.get("monto deuda dolares") == "MTO_DEU_USD"
    # nombre físico de tabla: sin separador, upper
    phys = {t["physicalName"] for t in data["canonical_tables"]}
    assert all("_" not in p for p in phys), f"tabla con separador: {phys}"
    assert all(p == p.upper() for p in phys)


# ── Validación contra los modelos Pydantic (contrato del seed) ───────────────

def _strip(doc: dict) -> dict:
    """_id → id; quita campos internos de Mongo (como hace el read path)."""
    d = dict(doc)
    d["id"] = d.pop("_id")
    for k in ("flgactive", "deletedAt", "createdAt", "updatedAt"):
        d.pop(k, None)
    return d


def test_docs_validate_against_models(data):
    model_by_coll = {
        "projects": ProjectDoc,
        "folders": FolderDoc,
        "subject_areas": SubjectAreaDoc,
        "canonical_tables": CanonicalTableDoc,
        "canonical_columns": CanonicalColumnDoc,
        "relationships": RelationshipDoc,
        "views": ViewDoc,
        "parent_domains": ParentDomainDoc,
        "glossary_terms": AbbreviationDoc,
        "changesets": ChangesetDoc,
        "changeset_changes": ChangeDoc,
        "roles": RoleDoc,
        "users": UserDoc,
        "standards_versions": StandardsVersionDoc,
    }
    for coll, model in model_by_coll.items():
        for doc in data[coll]:
            model.model_validate(_strip(doc))  # levanta si el shape no calza


def test_naming_config_validates(data):
    for doc in data["naming_config"]:
        NamingConfigDoc.model_validate({k: v for k, v in doc.items()
                                        if k not in ("flgactive", "createdAt", "updatedAt")})


def test_canonical_table_schema_alias_roundtrips(data):
    """El seed persiste `schema` (alias). Tras validar + dump by_alias debe
    volver a salir como `schema` (no `sql_schema`)."""
    for doc in data["canonical_tables"]:
        m = CanonicalTableDoc.model_validate(_strip(doc))
        dumped = m.model_dump(by_alias=True)
        assert "schema" in dumped and "sql_schema" not in dumped
        assert dumped["schema"] == doc["schema"]
