"""Índices de colecciones (idempotentes).

`ensure_indexes` declara los índices de cada colección; el adaptador Lakebase
los traduce a DDL de Postgres (GIN sobre jsonb + btrees). Es idempotente: los
códigos 48 (namespace ya existe) y 11000 (duplicate-key en un único ya
existente) se tragan; cualquier otro error se propaga.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Doc 73 §11.2 — índices RETIRADOS. En el adaptador Lakebase un índice por campo
# es un btree sobre `(doc ->> 'campo')`: para un ARRAY jsonb eso indexa el texto
# del array completo — inútil para el array-contains (que ya sirve el GIN
# `gin_<tabla>` con jsonb_path_ops) y, con arrays grandes, viola el límite de
# fila del btree ("index row size 4056 exceeds btree version 4 maximum 2704",
# visto al migrar DDV Otros 2026-09-07: canvases con cientos de vistas). Se
# dropean si existen (BDs creadas con el doc 70); en Apps el SP puede no ser
# owner → el error se traga, como en _try.
RETIRED_INDEXES: tuple[tuple[str, str], ...] = (
    ("subject_areas", "ix_subject_areas_viewIds"),
    ("views", "ix_views_sourceTableIds"),
    # Doc 75: la unicidad y el orden pasan a ser POR PROYECTO y `projectId` se
    # indexa por la columna generada `project_id` (D19); los btree por expresión
    # legacy sobre `projectId` y los índices que cambian de forma se retiran.
    ("standards_versions", "ix_standards_versions_seq"),
    ("canonical_tables", "ix_canonical_tables_schema_physicalName"),
    ("folders", "ix_folders_projectId"),
    ("subject_areas", "ix_subject_areas_projectId"),
)


async def ensure_indexes(db: Any) -> None:
    """Crea los índices de las colecciones del backend de plataforma (y retira
    los declarados en RETIRED_INDEXES)."""

    # Los índices se crean en lotes acotados para no abrir demasiadas sesiones
    # lógicas a la vez: lanzar ~30 create_index en paralelo sobre una BD recién
    # vaciada (cada uno abre su sesión lógica) podría reventar el límite de
    # sesiones del worker. Acotamos la concurrencia con un semáforo: sigue siendo
    # concurrente/rápido.
    sem = asyncio.Semaphore(5)

    async def _try(col_name: str, keys: list[tuple], **kwargs: object) -> None:
        async with sem:
            try:
                await db[col_name].create_index(keys, **kwargs)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None)
                if code not in (48, 11000):
                    raise

    async def _drop(col_name: str, idx_name: str) -> None:
        async with sem:
            drop = getattr(db[col_name], "drop_index", None)
            if drop is None:
                return
            try:
                await drop(idx_name)
            except Exception as exc:  # noqa: BLE001
                log.warning("no se pudo retirar el índice %s.%s: %s", col_name, idx_name, exc)

    await asyncio.gather(*(_drop(c, i) for c, i in RETIRED_INDEXES))
    await asyncio.gather(
        # ── Modelo canónico (M1) ────────────────────────────────
        _try("parent_domains", [("flgactive", 1)]),
        _try("glossary_terms", [("flgactive", 1)]),
        _try("udp_definitions", [("flgactive", 1)]),
        # DDL Export Rules (doc 30): colección chica (docenas); la unicidad del
        # `name` la garantiza el apply de standards, no un índice único.
        _try("ddl_rules", [("flgactive", 1)]),
        # Doc 75 D19: estándares POR PROYECTO — btree sobre la columna generada
        # `project_id` (los proyectos chicos hacen index-scan; DDV, seq-scan).
        _try("parent_domains", [("projectId", 1)]),
        _try("glossary_terms", [("projectId", 1)]),
        _try("udp_definitions", [("projectId", 1)]),
        _try("naming_config", [("projectId", 1)]),
        _try("ddl_rules", [("projectId", 1)]),
        # Doc 78: perfiles de carga (colección chica, por proyecto).
        _try("upload_profiles", [("flgactive", 1)]),
        _try("upload_profiles", [("projectId", 1)]),
        # Doc 105: jobs de la carga Excel en la BD (tope por usuario, desalojo).
        _try("upload_jobs", [("owner", 1)]),
        # Doc 95 D11: plantillas Excel del Reporting (colección chica, por proyecto).
        _try("sheet_templates", [("projectId", 1)]),
        _try("canonical_tables", [("flgactive", 1)]),
        # REQUERIDO por la búsqueda server-side del catálogo (?q=&limit=): el
        # top-N se ordena por physicalName, y a escala un orden sobre campos sin
        # índice sería full-scan (invariante de escala).
        _try("canonical_tables", [("physicalName", 1)]),
        # Doc 75 D6/D19: unicidad de físico POR PROYECTO — compuesto con la
        # columna generada `project_id` líder: sirve al chequeo de duplicados
        # (regex anclado CI) y al listado ordenado del catálogo del proyecto.
        # NO-unique a propósito (soft-delete): la garantía vive en el service.
        _try("canonical_tables", [("projectId", 1), ("physicalName", 1)]),
        _try("canonical_columns", [("projectId", 1), ("physicalName", 1)]),
        _try("canonical_columns", [("tableId", 1)]),
        _try("canonical_columns", [("parentDomainId", 1)]),
        # ── Motor de consulta del reporting (07) ────────────────
        # keyset/orden por physicalName (a escala, un orden sin índice sería
        # full-scan) + filtro/group por dataType.
        _try("canonical_columns", [("physicalName", 1)]),
        _try("canonical_columns", [("dataType", 1)]),
        # WILDCARD sobre el mapa embebido de UDP: cubre TODAS las keys UDP
        # presentes Y futuras (el usuario crea UDP en runtime) → filtrar por
        # cualquier UDP hace seek, sin DDL por-key. En tablas y columnas.
        _try("canonical_columns", [("udpValues.$**", 1)]),
        _try("canonical_tables", [("udpValues.$**", 1)]),
        # ── Changesets (M2a) ────────────────────────────────────
        _try("changesets", [("updatedAt", -1)]),
        _try("changesets", [("status", 1)]),
        # Doc 75 D2: versiones POR PROYECTO (bandeja/Home/producción del proyecto).
        _try("changesets", [("projectId", 1), ("status", 1)]),
        _try("changesets", [("projectId", 1), ("appliedAt", 1)]),
        # Un doc POR CAMBIO (un dict embebido con miles de cambios crecería sin
        # techo; por eso un doc por cambio, versionado por changesets):
        # overlay/diff/apply leen por csId (+collection) — el prefijo del
        # compuesto cubre ambos.
        _try("changeset_changes", [("csId", 1), ("collection", 1)]),
        # Historial de auditoría por entidad (doc 51): GET /history cruza los
        # cambios de UNA entidad a través de TODOS los changesets — sin este
        # compuesto sería full-scan del ledger completo por apertura de panel.
        _try("changeset_changes", [("collection", 1), ("entityId", 1)]),
        # ── M3a: Projects + Subject Areas + Relationships + Views ──
        _try("projects", [("flgactive", 1)]),
        _try("subject_areas", [("projectId", 1)]),
        # F5 — UDP del Modelo de Datos: wildcard sobre el mapa embebido (mismo
        # patrón que canonical_columns/tables, líneas de arriba) → filtrar por
        # cualquier UDP de canvas hace seek. `name` soporta el sort/keyset de
        # la entidad `models` del reporting (a escala, un orden sin índice sería
        # full-scan).
        _try("subject_areas", [("udpValues.$**", 1)]),
        _try("subject_areas", [("name", 1)]),
        # Doc 70: la membresía de vistas por canvas (`viewIds`, array) la sirve
        # el GIN de la tabla; su btree se RETIRÓ (doc 73 §11.2, RETIRED_INDEXES).
        _try("relationships", [("flgactive", 1)]),
        _try("relationships", [("projectId", 1)]),                  # doc 75 D19
        # El canvas resuelve relaciones por extremos ($in por tabla) — v2
        # parent/child (doc 19). Los índices legacy source*/target* de BDs
        # viejas quedan huérfanos (inofensivos) hasta droparse a mano.
        _try("relationships", [("parentTableId", 1)]),
        _try("relationships", [("childTableId", 1)]),
        # §8 warning de eliminación (F1): GET /api/relationships/impact busca
        # relaciones por columna DE ALGÚN PAR (multikey por dotted path).
        _try("relationships", [("pairs.parentColumnId", 1)]),
        _try("relationships", [("pairs.childColumnId", 1)]),
        _try("views", [("flgactive", 1)]),
        _try("views", [("projectId", 1)]),                          # doc 75 D19
        # Las vistas se listan por tabla (PropertiesPanel · tab Views).
        _try("views", [("tableId", 1)]),
        # F3: `sourceTableIds` (array) — array-contains/$in por el GIN de la
        # tabla; su btree se RETIRÓ (doc 73 §11.2, RETIRED_INDEXES).
        # ── R1a: Folders (jerarquía del Model Explorer) ──────────
        _try("folders", [("projectId", 1)]),
        # ── Esquemas como entidad versionada (doc 18) ────────────
        # `name` soporta el chequeo de unicidad por regex anclado (NO-unique:
        # el adaptador no soporta índices únicos sobre colecciones pobladas — la
        # garantía vive en service/changesets, como canonical_tables).
        _try("schemas", [("flgactive", 1)]),
        _try("schemas", [("name", 1)]),
        _try("schemas", [("projectId", 1), ("name", 1)]),          # doc 75 D6/D19
        # ── R1c: naming_config (1 doc por scope; _id = scope) ────
        _try("naming_config", [("scope", 1)]),
        # ── Auth propia + RBAC + auditoría (2026-07-04) ──────────
        _try("users", [("email", 1)]),
        _try("audit_log", [("at", -1)]),
        _try("audit_log", [("actor", 1)]),
        # Reporting (doc 75 D13): reportes guardados por proyecto.
        _try("saved_reports", [("projectId", 1)]),
        # Data Standards: seq ÚNICO POR PROYECTO (doc 75 D3) — dos apply/rollback
        # concurrentes del mismo proyecto no pueden crear dos versiones con el
        # mismo seq/label (el service reintenta ante la colisión). Se crea sobre
        # una colección vacía (el one-shot dropea el schema).
        _try("standards_versions", [("projectId", 1), ("seq", 1)], unique=True),
    )
    log.info("lakebase indexes ensured")
