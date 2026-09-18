"""Migración Erwin XML → colecciones publicadas de Data Model Hub.

SIN `--apply` es un dry-run: parsea, corre el gate de calidad y muestra el
plan (no abre conexión a la BD). Con `--apply` escribe en la BD (Lakebase)
(patrón "Erwin one-shot": directo a colecciones publicadas, sin changeset).

Doc 75 (proyectos independientes): el PROYECTO se resuelve PRIMERO (por nombre;
se crea si no existe) y todo lo demás vive dentro de él — cada doc de alcance
lleva `projectId`, el prefetch/adopción/unicidad de nombres se acotan al
proyecto y los estándares (dominios, glosario, defs UDP, naming) se siembran
POR PROYECTO. Los ids de plataforma son deterministas y NAMESPACEADOS por
proyecto (uuid5 de `<projectId>|<Long_Id de Erwin>`), así que re-correr el
mismo archivo (o una versión nueva del mismo modelo) actualiza en su sitio,
mientras que el mismo GUID en otro proyecto es otro documento. Documentos
preexistentes del proyecto con la misma clave natural pero OTRO id se
resuelven con las políticas multi-archivo (doc 32b, owner 2026-07-24) — un
modelo Erwin viene partido en ~15 archivos y el solapamiento es el flujo
NORMAL:

  - ADOPCIÓN (R1): tabla/vista cuya clave natural (schema+nombre) ya existe
    se mapea al doc VIVO — las relaciones/vistas/canvases del archivo se
    enganchan a él ("sumar" el uso nuevo, sin duplicar ni pisar).
  - SCORE DE USO (R2): si la estructura difiere, gana la versión MÁS USADA
    (`policies.score_usage`: 2×relaciones + canvases + vistas). Si gana la
    del archivo, se actualiza el doc existente EN SU SITIO (mismo `_id`).
  - ALIAS de duplicados internos (R3): copias de la misma clave dentro del
    archivo → gana la de más uso; las demás re-apuntan a la ganadora.
  - DEDUP de relaciones (R4): clave natural (padre, hijo, pares por NOMBRE);
    la misma FK llegada en dos archivos no se duplica.
  - Columnas duplicadas (R5): gana la de MÁS metadata (`column_score`).
  - Partición (R6): PART_nn siempre se marca; el orden efectivo es el físico
    (correlativos incongruentes = reasignados, al reporte).
  - Estándares (por proyecto): glosario/dominios/defs UDP se REUSAN por clave
    natural dentro del proyecto — unión DISTINTA entre los archivos que lo
    componen: el primero en llegar gana y una discrepancia (abreviatura o
    tipo de dominio distinto) va al reporte, nunca se pisa en silencio. (Doc
    61 r2: las defs UDP son un CATÁLOGO FIJO — `--keep-unused-udp-defs` quedó
    DEPRECADO, se acepta y se ignora.)

Rendimiento: escrituras BUFFEREADAS por colección y despachadas con
`bulk_write` (el adaptador Lakebase las ejecuta en 2 round-trips por lote) +
prefetch de claves naturales en memoria — la carga de un archivo grande pasa
de horas a minutos. Cada `--apply` deja un reporte JSON con toda decisión
tomada (adopciones, conflictos con score, alias, dedups, reasignaciones).

Jerarquía: Modelo → proyecto · Subject Area → folder · ER_Diagram → canvas.
Los N archivos de una familia cargan al MISMO proyecto (R8) — pásalo SIEMPRE
con `--project` (el one-shot lo deduce de la carpeta, doc 77); si el
proyecto ya existe y no lo pasaste, el script ABORTA (nada de fusiones
silenciosas). `--description` describe el proyecto si se crea. Folders y
canvases homónimos del proyecto se reusan.

Uso:
  .venv/bin/python -m scripts.erwin_migration.migrate modelo.xml [más.xml ...]
      [--apply]                  escribir (sin esto: dry-run)
      [--project "Nombre"]       proyecto destino (obligatorio si ya existe)
      [--description "Texto"]    descripción del proyecto si se crea
      [--only-sa CPYBCAPYM]      migrar solo canvases/folders de esa SA
      [--force]                  continuar aunque el gate tenga ERRORs
      [--report ruta.json]       reporte de decisiones (default: migration-reports/)
      [--keep-unused-udp-defs]   DEPRECADO (doc 61 r2: catálogo fijo) — no-op
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pymongo import UpdateOne

# Motor de naming REAL del backend (app.core.naming es PURO, sin config/BD):
# el estampado del override (doc 68) debe derivar EXACTAMENTE igual que el
# rephysicalize retroactivo — un espejo local driftearía.
from app.core.datatypes import canonicalize_default_type
from app.core.facets import normalize_udp_view
from app.core.naming import physicalize
from app.core.scope import PROJECT_SCOPED, naming_id
from app.features.settings.models import DEFAULTS as NAMING_DEFAULTS

from . import erwin_parser as ep
from . import policies as pol
from . import standard_udps as std_udps
from .quality import analyze, summarize

_STAMP = {"migratedFrom": "erwin"}
_ACTIVE = {"flgactive": {"$ne": False}}
_BATCH = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_type(t: str) -> str:
    return re.sub(r"\s+", "", (t or "").upper())


def _col_type(t: str) -> str:
    """Doc 92 D6: tipo físico persistido. Los complejos (`<`) que Erwin trae
    multilínea con tabulaciones se guardan canónicos (`ARRAY<STRUCT<a:VARCHAR(30),
    …>>`, sinónimos anidados incluidos) o, si no parsean (`@param1`), plegados
    a UNA línea compacta. Los simples quedan tal cual (vacío ⇒ STRING)."""
    raw = (t or "").strip()
    if "<" in raw:
        return canonicalize_default_type(raw) or raw
    return raw or "STRING"


class Migrator:
    def __init__(self, db, model: ep.ErwinModel, project_name: str | None,
                 only_sa: str | None, keep_unused_udp_defs: bool = False,
                 source_folder: str | None = None, description: str | None = None):
        self.db = db
        self.m = model
        self.project_name = project_name or model.name or "Modelo Erwin"
        self.only_sa = only_sa
        self.keep_unused_udp_defs = keep_unused_udp_defs
        # Doc 54 §9: capa de ORIGEN — carpeta raíz del proyecto (dominio del
        # Mart o nombre del archivo) que agrupa las SAs de ESTE archivo.
        # None = plano (comportamiento previo; lo usan los tests).
        self.source_folder = (source_folder or "").strip() or None
        self.description = (description or "").strip() or None
        self.stats: Counter = Counter()
        self.warnings: list[str] = []
        self.report: dict[str, list] = {
            "adopted": [], "conflicts": [], "renamed_dups": [], "rels_reused": [],
            "cols_dropped": [], "partitions_reassigned": [],
            "views_discarded": [], "udp_defs_skipped": [], "glossary_conflicts": [],
            "domain_conflicts": [], "udp_values_unmatched": [],
        }
        self._buf: dict[str, list[UpdateOne]] = defaultdict(list)
        self.ops_flushed = 0
        # Doc 75: el proyecto PRIMERO — es el namespace de ids y el alcance de
        # todo lo que sigue (prefetch, unicidad, estándares).
        self.project_id = self._ensure_project()
        self.schema_of = model.owner_schema()
        self.attr_idx = model.attr_index()
        # mapas Erwin id → id de plataforma FINAL (puede ser un doc preexistente)
        self.domain_pid: dict[str, str] = {}
        self.udp_pid: dict[str, str] = {}      # clave colapsada → def id plataforma
        self.table_pid: dict[str, str] = {}
        self.col_pid: dict[str, str] = {}
        self.view_pid: dict[str, str] = {}
        self.schemas_used: set[str] = set()    # nombres de schema de objetos migrados
        # doc 44: de qué MIEMBROS viene cada schema (el XML no trae este dato:
        # los Hive_Database no llevan atributo/UDP que marque vistas) — deriva
        # el `kind` del doc de schema. Mixto → tables gana.
        self.schemas_tables: set[str] = set()
        self.schemas_views: set[str] = set()
        # Doc 69: una def por (nivel, faceta, nombre) — clave `level|view|name`.
        self.defs = pol.udp_defs_by_view(model.udp_defs)
        self.udp_vals = pol.resolve_udp_values(model.udp_values, self.defs)
        # veredicto por tabla EXISTENTE en conflicto ("xml"|"db") — las vistas
        # espejo siguen la suerte de su tabla fuente (R2).
        self.table_verdicts: dict[str, str] = {}
        # columnas escritas/adoptadas por dueño Erwin: PHYS.upper → col pid
        self._cols_by_owner: dict[str, dict[str, str]] = {}
        self._stale_scope: dict[str, set[str]] = {}  # table pid escrito → cpids vigentes
        self._xml_usage()
        self._prefetch()

    # ---------- proyecto (doc 75) ----------
    def pid(self, long_id: str) -> str:
        """Id de plataforma del proyecto para un Long_Id (o clave) de Erwin."""
        return pol.project_scoped_id(self.project_id, long_id)

    def _ensure_project(self) -> str:
        """Proyecto destino por NOMBRE: se reusa el vivo o se crea (id
        determinista por nombre). Es la única escritura fuera del alcance."""
        existing = self.db.projects.find_one({"name": self.project_name, **_ACTIVE}, {"_id": 1})
        if existing:
            return existing["_id"]
        proj_id = pol.platform_id(f"project|{self.project_name}")
        self._upsert("projects", proj_id, {
            "name": self.project_name,
            "description": self.description or f"Migrado de Erwin — {self.m.name}"})
        self._flush("projects")
        self.stats["proyectos creados"] += 1
        return proj_id

    # ---------- infraestructura de escritura (bufferizada) ----------
    def _upsert(self, coll: str, pid: str, doc: dict) -> None:
        """$set idempotente por _id; createdAt/flgactive solo al insertar.
        Bufferizado: el adaptador ejecuta cada lote en 2 round-trips. Doc 75:
        todo doc de una colección de alcance lleva `projectId`."""
        if coll in PROJECT_SCOPED:
            doc = {**doc, "projectId": self.project_id}
        self._buf[coll].append(UpdateOne(
            {"_id": pid},
            {"$set": {**doc, **_STAMP, "updatedAt": _now()},
             "$setOnInsert": {"flgactive": True, "createdAt": _now()}},
            upsert=True))
        if len(self._buf[coll]) >= _BATCH:
            self._flush(coll)

    def _derived_physical(self, logical: str, scope: str) -> str:
        """Físico DERIVADO del lógico por la regla vigente del scope (glosario
        + naming config del proyecto). '' sin lógico. Doc 85: lo usan el
        override de tablas/columnas (doc 68) y el físico del dominio."""
        logical = (logical or "").strip()
        if not logical:
            return ""
        sep, case = self.naming_cfg[scope]
        return physicalize(logical, self.gloss_scope.get(scope) or {}, separator=sep, case=case)

    def _physical_override(self, logical: str, physical: str, scope: str) -> bool:
        """Doc 68: ¿el físico real difiere del derivado por la regla vigente
        del scope? True ⇒ se estampa `physicalNameOverridden` y el
        rephysicalize retroactivo del Glosario NO lo pisa. Sin lógico o sin
        físico no hay derivación posible (el barrido los salta) ⇒ False."""
        physical = (physical or "").strip()
        derived = self._derived_physical(logical, scope)
        if not derived or not physical:
            return False
        return derived != physical

    def _soft_delete(self, coll: str, pid: str) -> None:
        self._buf[coll].append(UpdateOne(
            {"_id": pid},
            {"$set": {"flgactive": False, "deletedAt": _now(), "updatedAt": _now()}}))
        if len(self._buf[coll]) >= _BATCH:
            self._flush(coll)

    def _flush(self, coll: str) -> None:
        ops = self._buf.pop(coll, [])
        if ops:
            self.db[coll].bulk_write(ops)
            self.ops_flushed += len(ops)

    def flush_all(self) -> None:
        for coll in list(self._buf):
            self._flush(coll)

    # ---------- uso (score) ----------
    def _xml_usage(self) -> None:
        """Uso de cada objeto DENTRO del archivo (para el score R2/R3)."""
        self.x_rels: Counter = Counter()
        for r in self.m.relationships.values():
            if r.rel_type in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING,
                              ep.REL_SUBTYPE):
                self.x_rels[r.parent_ref] += 1
                self.x_rels[r.child_ref] += 1
        self.x_canv: Counter = Counter()
        for d in self.m.diagrams:
            for ref in {ref for ref, _a in d.shapes}:
                self.x_canv[ref] += 1
        self.x_views: Counter = Counter()
        for rels in self.m.view_source_rels().values():
            for parent in {r.parent_ref for r in rels}:
                self.x_views[parent] += 1

    def _xml_score(self, oid: str) -> int:
        return pol.score_usage(self.x_rels[oid], self.x_canv[oid], self.x_views[oid])

    def _db_score(self, tid: str) -> int:
        return pol.score_usage(self.db_rel_count[tid], self.db_canv_count[tid],
                               self.db_view_count[tid])

    # ---------- prefetch (mata las consultas por-doc) ----------
    def _prefetch(self) -> None:
        """Foto en memoria de lo que YA existe en el PROYECTO (doc 75: nada de
        otro proyecto entra al censo, la adopción ni el dedup)."""
        db = self.db
        active = {**_ACTIVE, "projectId": self.project_id}
        self.ex_tables: dict[tuple[str, str], dict] = {}
        # Política _DUPn (2026-08-22): el físico es único POR PROYECTO (doc 50 +
        # doc 75) → censo de nombres tomados + continuidad por erwinLongId para
        # que los re-runs conserven el sufijo asignado (sin ratchet _DUPn+1).
        self.taken: set[str] = set()
        self.ex_by_erwin: dict[str, dict] = {}
        for t in db.canonical_tables.find(active, {"schema": 1, "physicalName": 1,
                                                   "erwinLongId": 1}):
            phys = t.get("physicalName") or ""
            key = ((t.get("schema") or "").upper(), phys.upper())
            self.ex_tables[key] = {"_id": t["_id"]}
            if phys:
                self.taken.add(phys.upper())
            if t.get("erwinLongId"):
                self.ex_by_erwin[t["erwinLongId"]] = {
                    "_id": t["_id"], "physicalName": phys,
                    "schema": t.get("schema") or ""}
        self.ex_views: dict[tuple[str, str], str] = {}
        for v in db.views.find(active, {"schema": 1, "name": 1}):
            self.ex_views[((v.get("schema") or "").upper(),
                           (v.get("name") or "").upper())] = v["_id"]
        self.ex_schemas: dict[str, str] = {
            (s.get("name") or "").upper(): s["_id"]
            for s in db.schemas.find(active, {"name": 1})}
        # NOMBRE → {_id, defaultDataType}: el tipo sirve para detectar el mismo
        # dominio con tipo DISTINTO en otro archivo del proyecto (al reporte).
        self.ex_domains: dict[str, dict] = {
            (d.get("name") or "").upper(): {"_id": d["_id"],
                                            "defaultDataType": d.get("defaultDataType") or ""}
            for d in db.parent_domains.find(active, {"name": 1, "defaultDataType": 1})}
        # term.upper() → abbrev vigente (para detectar conflictos de glosario
        # entre archivos/corridas — política 2026-08-22: la BD nunca se pisa,
        # pero la discrepancia SÍ se reporta).
        self.ex_gloss: dict[str, str] = {}
        # Doc 68: mappings por SCOPE para derivar físicos como el backend
        # (`list_entries`: docs sin `scope` persistido caen en 'column').
        self.gloss_scope: dict[str, dict[str, str]] = {"table": {}, "column": {}}
        for g in db.glossary_terms.find(active, {"term": 1, "abbrev": 1, "scope": 1}):
            term = (g.get("term") or "").strip()
            if not term:
                continue
            abbrev = (g.get("abbrev") or "").strip()
            self.ex_gloss[term.upper()] = abbrev
            self.gloss_scope.setdefault(g.get("scope") or "column", {})[term] = abbrev
        # naming_config del PROYECTO por scope (separator, case) — default
        # corporativo join+UPPER (app/features/settings/models.DEFAULTS; la
        # config del proyecto, si existe, manda).
        self.naming_cfg: dict[str, tuple[str, str]] = {
            sc: (cfg["separator"], cfg["case"]) for sc, cfg in NAMING_DEFAULTS.items()}
        for n in db.naming_config.find({"projectId": self.project_id}):
            sc = n.get("scope")
            if sc in self.naming_cfg:
                cur = self.naming_cfg[sc]
                self.naming_cfg[sc] = (
                    n["separator"] if n.get("separator") is not None else cur[0],
                    n.get("case") or cur[1])
        # Doc 69: reuso por (nombre, nivel, FACETA) — las defs pre-doc 69 sin
        # `view` son físicas (normalize_udp_view).
        self.ex_udp: dict[tuple[str, str, str], dict] = {}
        for u in db.udp_definitions.find(active, {"name": 1, "level": 1, "view": 1, "allowedValues": 1}):
            level = u.get("level") or ""
            self.ex_udp[((u.get("name") or "").upper(), level, normalize_udp_view(level, u.get("view")))] = {
                "_id": u["_id"], "allowedValues": u.get("allowedValues") or []}
        # uso en BD (score del lado existente) + material del dedup de relaciones
        self.db_rel_count: Counter = Counter()
        self.db_rels_raw: list[dict] = []
        for r in db.relationships.find(active, {"parentTableId": 1, "childTableId": 1,
                                                "pairs": 1, "identifying": 1}):
            self.db_rel_count[r.get("parentTableId")] += 1
            self.db_rel_count[r.get("childTableId")] += 1
            self.db_rels_raw.append(r)
        self.db_canv_count: Counter = Counter()
        self.db_canvases: list[dict] = []
        for sa in db.subject_areas.find(active, {"name": 1, "folderId": 1, "projectId": 1,
                                                 "tableIds": 1, "layout": 1, "udpValues": 1}):
            for tid in set(sa.get("tableIds") or []):
                self.db_canv_count[tid] += 1
            self.db_canvases.append(sa)
        self.db_view_count: Counter = Counter()
        for v in db.views.find(active, {"sourceTableIds": 1}):
            for tid in set(v.get("sourceTableIds") or []):
                self.db_view_count[tid] += 1
        # clave con el padre (doc 54 §9): los homónimos solo se reúsan DENTRO
        # de la misma capa de origen, ya no entre archivos distintos.
        self.ex_folders: dict[tuple[str, str, str], str] = {}
        for f in db.folders.find(active, {"projectId": 1, "parentFolderId": 1, "name": 1}):
            self.ex_folders[(f.get("projectId") or "", f.get("parentFolderId") or "",
                             (f.get("name") or "").upper())] = f["_id"]

    # ---------- pasos ----------
    def standards(self) -> None:
        # dominios padre. Los BUILT-IN de Erwin con nombre (Number, String,
        # Datetime, Blob) SÍ se siembran (owner 2026-09-11): son dominios reales
        # que los modeladores asignan — «Number» (INTEGER/INT) lo usan 14 023
        # atributos en MODELO DDV y sin él quedaban sin parent domain. Solo se
        # saltan los placeholders `<root>` / `<default>` (usarlos = sin dominio).
        # Reuso por nombre case-insensitive vía cache (cero round-trips).
        # Doc 62: el default se HOMOLOGA a la grafía canónica de la plataforma
        # (`Array` → `ARRAY<>`, `BIG INTEGER` → `BIGINT`).
        # Doc 75: unión DISTINTA entre los archivos del proyecto — el primero
        # gana; el mismo nombre con tipo distinto va al reporte, no se pisa.
        seeded_domains: list[tuple[str, str]] = []   # (erwin_id, pid) sembrados por ESTE archivo (doc 85)
        for d in self.m.domains.values():
            if d.name.startswith("<"):
                continue
            name_key = d.name.strip().upper()
            # Doc 69: default = tipo FÍSICO (lo que emite el DDL); el lógico
            # va aparte. Sin Physical_Data_Type cae al lógico (modelos viejos).
            phys_type = canonicalize_default_type(d.physical_type or d.data_type) or "STRING"
            existing = self.ex_domains.get(name_key)
            pid = existing["_id"] if existing else self.pid(d.id)
            self.domain_pid[d.id] = pid
            if existing:
                self.stats["dominios reusados"] += 1
                kept = existing["defaultDataType"]
                if kept and _norm_type(kept) != _norm_type(phys_type):
                    self.report["domain_conflicts"].append(
                        {"name": d.name, "kept": kept, "ignored": phys_type})
                    self.stats["dominios en conflicto (tipo distinto)"] += 1
                continue
            self.ex_domains[name_key] = {"_id": pid, "defaultDataType": phys_type}
            # Doc 85 §3.4: faceta física del dominio. El físico de Erwin se
            # guarda SOLO si difiere del derivado por la regla del scope column
            # (None = derivado: se calcula al mostrar/heredar); el Comment solo
            # si difiere de la Definition. Los UDP por defecto se estampan en
            # una segunda pasada (más abajo): `_udp_values_for` resuelve contra
            # el catálogo fijo, que se siembra DESPUÉS de este bucle.
            dom_phys_name = (d.physical_name or "").strip()
            phys_name = dom_phys_name if dom_phys_name and dom_phys_name != self._derived_physical(d.name, "column") else None
            phys_desc = d.comment if d.comment and d.comment != (d.definition or "") else None
            self._upsert("parent_domains", pid, {
                "name": d.name,
                "defaultDataType": phys_type,
                "logicalDataType": canonicalize_default_type(d.data_type) or None,
                # Doc 79: `Attribute_Definition` marca al dominio "atributo
                # estándar" (FecRutina, CodMes…): al asignarlo, el atributo
                # hereda nombre + definición. Su texto es la definición que se
                # empuja (los genéricos de tipo NO la llevan y conservan su
                # propia definición de dominio, solo para mostrar en la UI).
                "namingTerm": None,
                "inheritsName": bool(d.attribute_definition),
                "description": d.attribute_definition or d.definition or None,
                "physicalName": phys_name,
                "physicalDescription": phys_desc,
                "erwinLongId": d.id})
            seeded_domains.append((d.id, pid))
            if phys_name:
                self.stats["dominios con físico custom (override)"] += 1
            self.stats["dominios creados"] += 1

        # glosario (term+abbrev; nunca tocar existentes/locked). Full outer
        # join entre archivos (política 2026-08-22): los términos nuevos se
        # SUMAN; el mismo término con abreviatura DISTINTA es una incongruencia
        # que se detecta y reporta (gana la vigente — 1º BD, luego 1ª del
        # archivo), jamás se pisa en silencio.
        file_abbrev: dict[str, str] = {}
        for g in self.m.glossary:
            term = g[0].strip()
            abbrev = g[1].strip() if len(g) > 1 else ""
            if not term:
                continue
            key = term.upper()
            if key in file_abbrev:
                prev = file_abbrev[key]
                if abbrev and prev and abbrev.upper() != prev.upper():
                    self.report["glossary_conflicts"].append(
                        {"term": term, "kept": prev, "ignored": abbrev,
                         "source": "duplicado dentro del archivo"})
                    self.stats["glosario en conflicto (abbrev distinta)"] += 1
                else:
                    self.stats["glosario duplicado omitido"] += 1
                continue
            file_abbrev[key] = abbrev
            db_abbrev = self.ex_gloss.get(key)
            if db_abbrev is not None:
                if abbrev and db_abbrev and abbrev.upper() != db_abbrev.upper():
                    self.report["glossary_conflicts"].append(
                        {"term": term, "kept": db_abbrev, "ignored": abbrev,
                         "source": "ya existente en BD (otro archivo/corrida)"})
                    self.stats["glosario en conflicto (abbrev distinta)"] += 1
                else:
                    self.stats["glosario reusado"] += 1
                continue
            self.ex_gloss[key] = abbrev
            self.gloss_scope["column"][term] = abbrev
            self._upsert("glossary_terms", self.pid(f"gloss|{key}"), {
                "term": term, "abbrev": abbrev, "scope": "column",
                "wordType": None, "locked": False})
            self.stats["glosario creado"] += 1

        # defs UDP — CATÁLOGO FIJO (doc 61 ronda 2, owner 2026-08-30): las
        # definiciones ya NO se derivan del XML. Se siembra/actualiza SIEMPRE
        # el catálogo completo (lookup por nombre + NIVEL — lección C8; el
        # catálogo MANDA: pisa dataType/default/allowedValues de la homónima
        # existente — reemplaza la convergencia A3 y la poda A4, que aplicaban
        # cuando las defs venían del XML). Las defs del XML solo se MAPEAN a
        # las fijas (nombre case-insensitive) para asociar valores; fuera del
        # catálogo → no se crean (al reporte).
        self.udp_fixed: dict[str, dict] = {}   # clave `level|view|name` → def FIJA
        fixed_pid: dict[tuple[str, str, str], str] = {}
        # Doc 91 D8: ids de plataforma de las defs FIJAS por (level, view,
        # nombre normalizado) — `views()` estampa «Tipo de Vista» con esto.
        self.fixed_pid = fixed_pid
        for fx in std_udps.FIXED_UDPS:
            lookup_key = (fx["name"].strip().upper(), fx["level"], fx["view"])
            existing = self.ex_udp.get(lookup_key)
            # Ids deterministas dentro del proyecto (doc 75: cada proyecto tiene
            # su catálogo); las lógicas (doc 69) llevan el segmento `logical`.
            fresh = (self.pid(f"udpfix|{fx['level']}|{fx['name']}") if fx["view"] == "physical"
                     else self.pid(f"udpfix|{fx['level']}|logical|{fx['name']}"))
            pid = existing["_id"] if existing else fresh
            fixed_pid[(fx["level"], fx["view"], pol.norm_enum(fx["name"]))] = pid
            allowed = list(fx["allowedValues"])
            self._upsert("udp_definitions", pid, {
                "name": fx["name"], "level": fx["level"], "view": fx["view"],
                "dataType": fx["dataType"], "defaultValue": fx["defaultValue"],
                "allowedValues": allowed,
                "description": fx.get("description") or "Estándar fijo (doc 61/69)"})
            self.ex_udp[lookup_key] = {"_id": pid, "allowedValues": allowed}
            self.stats["defs UDP fijas sembradas"] += 1
        lk = std_udps.fixed_lookup()
        for key, entry in self.defs.items():
            fx = lk.get((entry["level"], entry["view"], pol.norm_enum(entry["name"])))
            if not fx:
                self.report["udp_defs_skipped"].append(
                    {"name": entry["name"], "level": entry["level"], "view": entry["view"],
                     "dataType": entry["dataType"],
                     "motivo": "fuera del catálogo fijo"})
                self.stats["defs UDP del XML fuera del catálogo fijo"] += 1
                continue
            self.udp_pid[key] = fixed_pid[(fx["level"], fx["view"], pol.norm_enum(fx["name"]))]
            self.udp_fixed[key] = fx

        # Doc 85 D6: UDP por DEFECTO del dominio (ambas facetas: explícitos y
        # derivados, materializados por Erwin en cada <Domain>). Va DESPUÉS del
        # catálogo fijo porque `_udp_values_for` resuelve contra `udp_fixed` /
        # `udp_pid`, que recién existen acá. Solo los dominios sembrados por
        # ESTE archivo (los reusados no se tocan — política doc 75); siempre se
        # estampa (aunque sea {}) para que el doc no quede en None.
        # Doc 85 §11.2: el buffer se VACÍA antes — el fast path del adaptador
        # Lakebase (`_bulk_update_by_id`) resuelve un lote con UPDATE de los
        # pre-existentes + INSERT … ON CONFLICT DO NOTHING de los nuevos: un
        # segundo op del mismo `_id` en el MISMO lote que su insert se descarta
        # en silencio (prueba viva del owner: dominios con físico y sin UDP).
        self._flush("parent_domains")
        for erwin_id, dom_pid in seeded_domains:
            dom_udps = self._udp_values_for(erwin_id, "column")
            self._upsert("parent_domains", dom_pid, {"udpValues": dom_udps})
            if dom_udps:
                self.stats["dominios con UDP por defecto"] += 1

        # naming_config del proyecto (doc 75): se SIEMBRA con los defaults
        # corporativos sólo si no existe — la config viva del proyecto manda.
        for scope, cfg in NAMING_DEFAULTS.items():
            self._buf["naming_config"].append(UpdateOne(
                {"_id": naming_id(self.project_id, scope)},
                {"$setOnInsert": {**cfg, "scope": scope, "projectId": self.project_id,
                                  "flgactive": True, "createdAt": _now(), "updatedAt": _now()}},
                upsert=True))

    def _udp_values_for(self, erwin_id: str, level: str) -> dict[str, str]:
        """Valores UDP de una entidad contra el CATÁLOGO FIJO (doc 61 r2):
        match case/espacios-insensitive (+ alias de typos conocidos) → se
        asigna la grafía CANÓNICA; sin match → la key NO se escribe (rige el
        default de la definición). Una muestra va al reporte."""
        out = {}
        for key, fx in self.udp_fixed.items():
            if fx["level"] != level:
                continue
            raw = self.udp_vals.get((erwin_id, key))
            if not raw:
                continue
            val = std_udps.match_value(fx, raw)
            if val is None:
                self.stats["valores UDP sin match (quedan en default)"] += 1
                if len(self.report["udp_values_unmatched"]) < 200:
                    self.report["udp_values_unmatched"].append(
                        {"udp": fx["name"], "level": level, "view": fx["view"], "valor": raw})
                continue
            out[self.udp_pid[key]] = val
            self.stats[f"valores UDP asociados ({fx['view']})"] += 1
        return out

    # ---------- tablas (R1/R2/_DUPn/R5/R6) ----------
    def _resolve_dup_names(self) -> dict[str, str]:
        """Nombres EFECTIVOS por entidad (política 2026-08-22, reemplaza el
        alias R3): TODAS las copias se migran como tablas reales; los
        homónimos reciben sufijo correlativo `_DUPn` (contenido intacto — solo
        cambia el físico) para no violar la unicidad del físico POR PROYECTO
        (doc 50 + doc 75) y conservar la metadata de cada duplicado para
        revisión del equipo. Reglas, deterministas para el mismo archivo:

        1. CONTINUIDAD: una tabla ya migrada (mismo erwinLongId + schema en
           BD) conserva el físico con el que quedó — re-runs sin ratchet.
        2. En cada grupo homónimo (schema+físico) conserva el nombre limpio la
           copia de MÁS USO (score R2; empate → 1ª del archivo), que además es
           la que adopta/actualiza el doc vivo homónimo si existe (R1/R2).
        3. El resto — y cualquier colisión con la BD del proyecto (otro
           esquema) — toma el primer `_DUPn` libre, en orden de archivo.
        """
        eff: dict[str, str] = {}
        claimed: set[str] = set()
        pending: list = []
        for e in self.m.entities.values():
            prev = self.ex_by_erwin.get(e.id)
            schema = pol.schema_or_default(self.schema_of.get(e.id))
            if prev and prev["physicalName"] and prev["schema"].upper() == schema.upper():
                eff[e.id] = prev["physicalName"]
                claimed.add(prev["physicalName"].upper())
            else:
                pending.append(e)

        groups: dict[tuple[str, str], list] = defaultdict(list)
        order: list[tuple[str, str]] = []
        for e in pending:
            schema = pol.schema_or_default(self.schema_of.get(e.id))
            key = (schema.upper(), e.physical.upper())
            if key not in groups:
                order.append(key)
            groups[key].append(e)

        for key in order:
            copies = groups[key]
            raw = copies[0].physical
            if not raw:              # sin físico resoluble no hay clave que chocar
                for e in copies:
                    eff[e.id] = e.physical
                continue
            nat_live = key in self.ex_tables
            keeper = max(copies, key=lambda e: self._xml_score(e.id))  # max estable
            renamed: list[dict] = []
            for e in [keeper] + [c for c in copies if c is not keeper]:
                up = e.physical.upper()
                # Conserva el crudo el PRIMERO en reclamarlo (el keeper) cuando
                # es su doc vivo homónimo (nat_live ⇒ adopción/score R1/R2) o
                # el nombre está libre en el proyecto; el resto va a `_DUPn`.
                if up not in claimed and (nat_live or up not in self.taken):
                    eff[e.id] = e.physical
                    claimed.add(up)
                    continue
                k = 1
                while f"{e.physical}_DUP{k}".upper() in claimed \
                        or f"{e.physical}_DUP{k}".upper() in self.taken:
                    k += 1
                suffixed = f"{e.physical}_DUP{k}"
                eff[e.id] = suffixed
                claimed.add(suffixed.upper())
                renamed.append({"erwinLongId": e.id, "logicalName": e.name,
                                "from": e.physical, "to": suffixed})
                self.stats["tablas duplicadas → renombradas con sufijo _DUPn"] += 1
            if renamed:
                schema = pol.schema_or_default(self.schema_of.get(copies[0].id))
                self.report["renamed_dups"].append({
                    "key": f"{schema}.{raw}".upper(), "copies": len(copies),
                    "kept": eff[keeper.id], "renamed": renamed})
        return eff

    def tables(self) -> None:
        eff = self._resolve_dup_names()
        entities = list(self.m.entities.values())

        # columnas existentes de las tablas cuya clave natural YA está en BD
        # (adopción/score/estabilidad de ids en update-in-place)
        matched: dict[str, str] = {}     # entity id → _id existente
        for e in entities:
            schema = pol.schema_or_default(self.schema_of.get(e.id))
            hit = self.ex_tables.get((schema.upper(), eff[e.id].upper()))
            if hit:
                matched[e.id] = hit["_id"]
        self.ex_cols: dict[tuple[str, str], str] = {}
        self.ex_cols_inv: dict[str, tuple[str, str]] = {}
        ids = sorted(set(matched.values()))
        for i in range(0, len(ids), 500):
            for c in self.db.canonical_columns.find(
                    {**_ACTIVE, "tableId": {"$in": ids[i:i + 500]}},
                    {"tableId": 1, "physicalName": 1}):
                k = (c["tableId"], (c.get("physicalName") or "").upper())
                self.ex_cols[k] = c["_id"]
                self.ex_cols_inv[c["_id"]] = k

        fk_ids = {a for pairs in self.m.fk_pairs().values() for p, c in pairs for a in (p, c)}
        udp_counts = Counter(oid for (oid, _k) in self.udp_vals)

        for e in entities:
            schema = pol.schema_or_default(self.schema_of.get(e.id))
            nat_key = f"{schema}.{eff[e.id]}".upper()
            own_pid = self.pid(e.id)
            existing_id = matched.get(e.id)

            if existing_id and existing_id != own_pid:
                # misma clave natural con OTRO id → política multi-archivo
                sx, sd = self._xml_score(e.id), self._db_score(existing_id)
                verdict = "xml" if sx > sd else "db"
                self.table_verdicts[existing_id] = verdict
                self.report["conflicts"].append(
                    {"key": nat_key, "scoreXml": sx, "scoreDb": sd, "won": verdict})
                if verdict == "db":
                    # ADOPCIÓN (R1): la tabla viva queda tal cual; este archivo
                    # se engancha a ella (relaciones/vistas/canvases suman).
                    self.table_pid[e.id] = existing_id
                    self.schemas_used.add(schema)
                    self.schemas_tables.add(schema)
                    owner_cols: dict[str, str] = {}
                    unmapped = 0
                    for a in e.attributes:
                        cpid = self.ex_cols.get((existing_id, a.physical.upper()))
                        if cpid:
                            self.col_pid[a.id] = cpid
                            owner_cols[a.physical.upper()] = cpid
                        else:
                            unmapped += 1
                    self._cols_by_owner[e.id] = owner_cols
                    self.report["adopted"].append(
                        {"key": nat_key, "columnsUnmapped": unmapped})
                    self.stats["tablas adoptadas (ya existían)"] += 1
                    continue
                pid = existing_id  # gana el archivo → se actualiza EN SU SITIO
                self.stats["tablas actualizadas (ganó el archivo por uso)"] += 1
            else:
                pid = existing_id or own_pid  # re-run del mismo archivo o nueva

            self.table_pid[e.id] = pid
            self.schemas_used.add(schema)
            self.schemas_tables.add(schema)
            t_override = self._physical_override(e.name, eff[e.id], "table")
            if t_override:
                self.stats["tablas con físico custom (override)"] += 1
            self._upsert("canonical_tables", pid, {
                "physicalName": eff[e.id], "logicalName": e.name,
                "physicalNameOverridden": t_override,
                # Doc 69: existencia en una sola faceta de Erwin.
                "logicalOnly": e.logical_only, "physicalOnly": e.physical_only,
                "schema": schema,
                "description": e.definition or e.comment or None,
                "udpValues": self._udp_values_for(e.id, "table"),
                "erwinLongId": e.id})
            self.stats["tablas"] += 1

            cols, dropped = pol.dedupe_columns(
                e.attributes,
                scorer=lambda a: pol.column_score(
                    a, fk_attr_ids=fk_ids, pk_attr_ids=e.pk_attr_ids,
                    udp_count=udp_counts[a.id]))
            for a in dropped:
                self.report["cols_dropped"].append({"table": nat_key, "column": a.physical})
            self.stats["columnas duplicadas descartadas"] += len(dropped)

            pk_pos = {aid: i for i, aid in enumerate(e.pk_attr_order)}
            # Doc 74: orden ÚNICO de la plataforma — llaves primero (orden de la
            # llave) + resto en el Column order de Erwin; `ordinal` = índice.
            cols = pol.column_order(cols, e.pk_attr_ids, e.pk_attr_order)
            # Partición v2 (R6): PART_nn SIEMPRE marca; incongruencias =
            # reasignadas por el orden de columnas, al reporte.
            part_vals = [(a.id, corr) for a in cols
                         if (corr := pol.partition_correlative(
                             self.udp_vals.get((a.id, pol.PARTITION_UDP_KEY)))) is not None]
            part_ids, reassigned = pol.partition_marks(part_vals)
            if reassigned:
                self.report["partitions_reassigned"].append(
                    {"table": nat_key, "detail": reassigned})
                self.stats["tablas con partición reasignada por orden de columnas"] += 1
            elif part_ids:
                self.stats["tablas con partición nativa"] += 1

            owner_cols = {}
            current_cpids: set[str] = set()
            for i, a in enumerate(cols):
                # estabilidad: si la tabla ya existía, la columna homónima
                # CONSERVA su _id (las relaciones vivas no se rompen)
                cpid = self.ex_cols.get((pid, a.physical.upper())) or self.pid(a.id)
                self.col_pid[a.id] = cpid
                owner_cols[a.physical.upper()] = cpid
                current_cpids.add(cpid)
                dom_pid = self.domain_pid.get(a.domain_ref or "")
                dom = self.m.domains.get(a.domain_ref or "")
                # Doc 69: override POR FACETA — físico de la columna vs físico del
                # dominio, lógico vs lógico (canonizados: INT ≡ INTEGER). Antes se
                # comparaba el FÍSICO de la columna con el LÓGICO del dominio
                # (VARCHAR(30) vs VARCHAR(20)) ⇒ miles de falsos overrides.
                dom_phys = canonicalize_default_type(dom.physical_type or dom.data_type) if dom else ""
                dom_log = canonicalize_default_type(dom.data_type) if dom else ""
                col_phys = canonicalize_default_type(a.data_type)
                col_log = canonicalize_default_type(a.logical_type) or None
                overridden = bool(dom_pid and dom and dom_phys and col_phys != dom_phys)
                log_overridden = bool(dom_pid and dom and dom_log and col_log and col_log != dom_log)
                if col_log and col_log != col_phys:
                    self.stats["columnas con tipo lógico ≠ físico"] += 1
                c_override = self._physical_override(a.name, a.physical, "column")
                if c_override:
                    self.stats["columnas con físico custom (override)"] += 1
                self._upsert("canonical_columns", cpid, {
                    "tableId": pid, "physicalName": a.physical,
                    "physicalNameOverridden": c_override,
                    "logicalName": a.name, "parentDomainId": dom_pid,
                    "dataType": _col_type(a.data_type),
                    "typeOverridden": overridden,
                    "logicalDataType": col_log,
                    "logicalTypeOverridden": log_overridden,
                    "logicalOnly": a.logical_only, "physicalOnly": a.physical_only,
                    "isPrimaryKey": a.id in e.pk_attr_ids,
                    "pkPosition": pk_pos.get(a.id),
                    "isForeignKey": bool(a.parent_attr_ref),
                    "isNullable": a.nullable, "isPartition": a.id in part_ids,
                    "description": a.definition or a.comment or None,
                    # Doc 85 D10: Comment de Erwin = descripción FÍSICA, solo
                    # cuando hay Definition y difieren (si no hay Definition el
                    # Comment ya es la descripción de arriba).
                    "physicalDescription": a.comment if (a.comment and a.definition and a.comment != a.definition) else None,
                    "ordinal": i,
                    "udpValues": self._udp_values_for(a.id, "column"),
                    "erwinLongId": a.id})
                self.stats["columnas"] += 1
            self._cols_by_owner[e.id] = owner_cols
            # Doc 73 §11.3 (D10): el saneo «columnas que ya no están en el XML»
            # aplica SOLO al re-run del MISMO archivo (misma entidad Erwin ⇒ mismo
            # pid). Si la tabla viene de OTRO archivo de la familia y este gana
            # por uso, sus columnas se actualizan/suman pero las que este archivo
            # no trae se CONSERVAN: el export lógico de UDV no trae las columnas
            # físicas (retiraba 424 y dejaba 12 relaciones huérfanas).
            if existing_id and existing_id != own_pid:
                kept = sum(1 for (tid, _n), cid in self.ex_cols.items()
                           if tid == pid and cid not in current_cpids)
                if kept:
                    self.stats["columnas de otro archivo conservadas (tabla actualizada por uso)"] += kept
            else:
                self._stale_scope[pid] = current_cpids
        # (El alias R3 murió con la política _DUPn 2026-08-22: cada copia es
        # una tabla real con sus columnas — sus relaciones/canvases la siguen
        # por su propio id de entidad, sin re-apuntar nada.)

    def _stale_columns(self) -> None:
        """Re-runs auto-saneados: columnas MIGRADAS de las tablas ESCRITAS en
        esta corrida que ya no vienen en el archivo → soft-delete. SOLO docs
        con erwinLongId — lo creado a mano en la plataforma jamás se toca.
        Por lotes (una consulta cada 500 tablas)."""
        tids = sorted(self._stale_scope)
        for i in range(0, len(tids), 500):
            chunk = tids[i:i + 500]
            for c in self.db.canonical_columns.find(
                    {**_ACTIVE, "tableId": {"$in": chunk},
                     "erwinLongId": {"$exists": True}},
                    {"_id": 1, "tableId": 1}):
                if c["_id"] not in self._stale_scope.get(c["tableId"], set()):
                    self._soft_delete("canonical_columns", c["_id"])
                    self.stats["columnas migradas retiradas (ya no están en el XML)"] += 1

    # ---------- relaciones (R4) ----------
    def relationships(self) -> None:
        """v2 (doc 19): UN doc por relación Erwin, con TODOS sus pares. Dedup
        multi-archivo (R4): la misma FK (extremos + pares por NOMBRE) llegada
        en otro archivo se REUSA en vez de duplicarse."""
        pairs = self.m.fk_pairs()
        # claves naturales de las relaciones VIVAS cuyos pares se resuelven
        # con las columnas prefetcheadas (= tablas preexistentes tocadas por
        # este archivo; las demás no pueden chocar y se saltan solas)
        seen: dict[tuple, dict] = {}
        for r in self.db_rels_raw:
            name_pairs = []
            for p in (r.get("pairs") or []):
                pk = self.ex_cols_inv.get(p.get("parentColumnId"))
                ck = self.ex_cols_inv.get(p.get("childColumnId"))
                if not pk or not ck:
                    name_pairs = []
                    break
                name_pairs.append((pk[1], ck[1]))
            if name_pairs:
                seen[pol.rel_nat_key(r.get("parentTableId"), r.get("childTableId"),
                                     name_pairs,
                                     subcategory=bool(r.get("subcategory")))] = r

        # doc 53: relación Type 9 → símbolo Erwin que la agrupa (el símbolo NO
        # se persiste como entidad: las aristas comparten `subtypeSymbolId`).
        sym_of = self.m.subtype_symbol_of_rel()
        for r in self.m.relationships.values():
            is_sub = r.rel_type == ep.REL_SUBTYPE
            if r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING) \
                    and not is_sub:
                continue
            child_t = self.table_pid.get(r.child_ref)    # hijo = lado FK
            parent_t = self.table_pid.get(r.parent_ref)  # padre = lado PK
            if not child_t or not parent_t:
                self.stats["relaciones omitidas (tabla no migrada)"] += 1
                continue
            rel_pairs, name_pairs = [], []
            for p_attr, c_attr in pairs.get(r.id, []):
                p_col, c_col = self.col_pid.get(p_attr), self.col_pid.get(c_attr)
                if p_col and c_col:
                    rel_pairs.append({"parentColumnId": p_col,
                                      "childColumnId": c_col, "roleName": None})
                    pa, ca = self.attr_idx.get(p_attr), self.attr_idx.get(c_attr)
                    name_pairs.append((pa.physical if pa else "", ca.physical if ca else ""))
            if not rel_pairs:
                self.stats["relaciones omitidas (sin pares FK)"] += 1
                continue
            key = pol.rel_nat_key(parent_t, child_t, name_pairs, subcategory=is_sub)
            prev = seen.get(key)
            if prev is not None:
                self.stats["relaciones reusadas (ya existían — R4)"] += 1
                entry = {"key": r.name, "parent": parent_t, "child": child_t}
                if isinstance(prev, dict) and "identifying" in prev and \
                        bool(prev.get("identifying")) != (is_sub or r.rel_type == ep.REL_IDENTIFYING):
                    entry["discrepancy"] = "identifying difiere — se conserva la existente"
                self.report["rels_reused"].append(entry)
                continue
            seen[key] = {}
            if is_sub:
                sym_eid = sym_of.get(r.id)
                if sym_eid is None:
                    # 0 casos en los XML UDV; defensivo (W-SUBTYPE-ORPHAN-REL).
                    self.warnings.append(
                        f"relación de subtipo {r.name} sin Subtype_Symbol — "
                        f"se agrupa en símbolo sintético propio")
                    sym_eid = f"subsym|{r.id}"
                extra = {"subcategory": True,
                         "subtypeSymbolId": self.pid(sym_eid),
                         # ES-UN (doc 53): 1:1 estricto; la PK del padre migra
                         # como PK del hijo → identifying, como en Erwin.
                         "parentCardinality": "one",
                         "childCardinality": "one",
                         "identifying": True}
                self.stats["relaciones de subcategoría (supertipo→subtipo)"] += 1
            else:
                extra = {"subcategory": False, "subtypeSymbolId": None,
                         # Lado padre desde Null_Option_Type ("100" = FK
                         # nullable → padre opcional 0..1), como el rombo Erwin.
                         "parentCardinality": pol.map_parent_cardinality(r.null_option),
                         "childCardinality": pol.map_cardinality(r.cardinality),
                         "identifying": r.rel_type == ep.REL_IDENTIFYING}
            self._upsert("relationships", self.pid(r.id), {
                "parentTableId": parent_t, "childTableId": child_t,
                "pairs": rel_pairs, **extra, "erwinLongId": r.id})
            self.stats["relaciones"] += 1
            if len(rel_pairs) > 1:
                self.stats["relaciones compuestas (2+ pares)"] += 1

    # ---------- vistas (R1/R2/R3/R7) ----------
    def views(self) -> None:
        view_rels = self.m.view_source_rels()

        # duplicados internos de vista (R3): gana la más usada/completa
        groups: dict[tuple[str, str], list] = defaultdict(list)
        order: list[tuple[str, str]] = []
        for v in self.m.views.values():
            key = (pol.schema_or_default(self.schema_of.get(v.id)).upper(), v.name.upper())
            if key not in groups:
                order.append(key)
            groups[key].append(v)
        view_alias: dict[str, str] = {}
        winners = []
        for key in order:
            copies = groups[key]
            win = max(copies, key=lambda v: (
                self.x_canv[v.id], sum(1 for a in v.attributes if a.parent_attr_ref)))
            winners.append(win)
            for v in copies:
                if v is not win:
                    view_alias[v.id] = win.id
                    self.stats["vistas duplicadas → alias de la más usada"] += 1

        for v in winners:
            rels = [r for r in view_rels.get(v.id, []) if r.parent_ref in self.table_pid]
            source_pids = list(dict.fromkeys(self.table_pid[r.parent_ref] for r in rels))
            if not source_pids:
                # R7 (owner 2026-07-24): sin fuente física → se descarta.
                self.report["views_discarded"].append({"view": v.name})
                self.stats["vistas omitidas (sin fuente — política R7)"] += 1
                continue
            schema = pol.schema_or_default(self.schema_of.get(v.id))
            own_pid = self.pid(v.id)
            existing_id = self.ex_views.get((schema.upper(), v.name.upper()))
            if existing_id and existing_id != own_pid:
                # la vista espejo sigue la suerte de su TABLA fuente (R2);
                # sin veredicto de tabla este run → se conserva la existente.
                verdict = self.table_verdicts.get(source_pids[0], "db")
                if verdict == "db":
                    self.view_pid[v.id] = existing_id
                    self.stats["vistas adoptadas (ya existían)"] += 1
                    continue
                pid = existing_id
                self.stats["vistas actualizadas (ganó el archivo por uso)"] += 1
            else:
                pid = existing_id or own_pid
            self.view_pid[v.id] = pid
            self.schemas_used.add(schema)
            self.schemas_views.add(schema)

            cols, dropped = pol.dedupe_columns(
                v.attributes, scorer=lambda a: (int(bool(a.parent_attr_ref)),
                                                int(bool(a.definition or a.comment)),
                                                -a.order))
            self.stats["columnas duplicadas descartadas"] += len(dropped)
            sources = []
            for a in cols:
                origin = self.attr_idx.get(a.parent_attr_ref or "")
                origin_tid = (self.table_pid.get(origin.owner_id)
                              if origin and origin.owner_kind == "Entity" else None)
                cast = (_col_type(a.data_type) if origin
                        and _norm_type(origin.data_type) != _norm_type(a.data_type)
                        else None)
                src = {
                    "tableId": origin_tid or source_pids[0],
                    "column": origin.physical if origin else a.physical,
                    "outputAlias": a.physical,
                }
                if cast:
                    src["castType"] = cast
                # F5: definición PROPIA de la columna-de-vista (Erwin la trae a
                # nivel de columna-de-vista). Se guarda SOLO si APORTA: difiere de
                # la del origen físico, o el origen no tiene. Las passthrough
                # idénticas NO se duplican (heredan de la física en el front).
                vdef = (a.definition or a.comment or "").strip()
                odef = ((origin.definition or origin.comment or "").strip()
                        if origin else "")
                if vdef and vdef != odef:
                    src["description"] = vdef
                    self.stats["columnas de vista con definición propia"] += 1
                sources.append(src)
            # Doc 91 D8: `User_Defined_SQL` de Erwin → `customSql` VERBATIM (la
            # vista es Personalizada: el export la emite tal cual) y el UDP fijo
            # «Tipo de Vista» = Personalizada (mismo contrato que el front). Las
            # columnas siguen siendo las que Erwin declara en la vista (S5).
            udp_values = self._udp_values_for(v.id, "view")
            custom_sql = (v.user_defined_sql or "").strip() or None
            if custom_sql:
                tipo_pid = self.fixed_pid.get(("view", "physical", pol.norm_enum("Tipo de Vista")))
                if tipo_pid:
                    udp_values[tipo_pid] = "Personalizada"
                self.stats["vistas con User-Defined SQL (Personalizada)"] += 1
            self._upsert("views", pid, {
                # `sql` = el CREATE VIEW ORIGINAL de Erwin (ViewProps.SQL, doc 19
                # §12). El export DDL sigue generando desde `sources` (estructura);
                # esto preserva la definición fuente como metadata de referencia.
                # Doc 91: sin `tags`/`filter`/`joinOverride` (retirados del modelo).
                "name": v.name, "schema": schema, "sql": v.sql or "",
                "description": v.definition or v.comment or None,
                "tableId": source_pids[0], "sourceTableIds": source_pids,
                "sources": sources,
                "outputAlias": None, "expression": None,
                "customSql": custom_sql,
                # doc 61 r2: nivel UDP 'view' (Tipo de Vista si el XML lo trae;
                # sin valor ⇒ rige el default Regular de la def fija).
                "udpValues": udp_values,
                # decisión owner: TODAS las vistas visibles en canvas
                "showOnCanvas": True,
                "erwinLongId": v.id})
            self.stats["vistas"] += 1

        for loser_id, winner_id in view_alias.items():
            if winner_id in self.view_pid:
                self.view_pid[loser_id] = self.view_pid[winner_id]

    def schema_docs(self) -> None:
        """Entidad `schemas` (doc 18): un doc por nombre de schema usado EN EL
        PROYECTO (doc 75: esquemas aislados por proyecto, id namespaceado).
        Reusa por nombre case-insensitive (cache). El `kind` (doc 44) se deriva
        de los MIEMBROS (tablas/vistas) porque el XML no lo trae; mixto →
        tables. Un schema YA existente no se re-clasifica acá."""
        for name in sorted(self.schemas_used):
            if name.upper() in self.ex_schemas:
                self.stats["schemas reusados"] += 1
                continue
            sid = self.pid(f"schema|{name}")
            self.ex_schemas[name.upper()] = sid
            kind = ("tables" if name in self.schemas_tables
                    else "views" if name in self.schemas_views else None)
            doc: dict = {"name": name, "description": None}
            if kind:
                doc["kind"] = kind
                self.stats[f"schemas creados como {kind}"] += 1
            self._upsert("schemas", sid, doc)
            self.stats["schemas creados"] += 1

    def canvases(self) -> None:
        # El proyecto ya está resuelto (doc 75: `_ensure_project`, antes de
        # cualquier escritura de alcance).
        proj_id = self.project_id

        # UDP de nivel canvas definidos a nivel Model → a todos los canvases
        model_udp: dict[str, str] = {}
        for key, fx in self.udp_fixed.items():
            if fx["level"] != "canvas":
                continue
            for (oid, k), val in self.udp_vals.items():
                if k == key and val:
                    mv = std_udps.match_value(fx, val)
                    if mv is not None:
                        model_udp[self.udp_pid[key]] = mv

        # Doc 54 §9: carpeta de ORIGEN (dominio del Mart / archivo) como capa
        # raíz del proyecto; las SAs del archivo cuelgan de ella. Reuso por
        # nombre en la raíz para que los re-runs no dupliquen.
        src_fid: str | None = None
        if self.source_folder:
            src_key = (proj_id, "", self.source_folder.upper())
            src_fid = self.ex_folders.get(src_key) or self.pid(
                f"srcfolder|{self.source_folder.upper()}")
            if src_key not in self.ex_folders:
                self.ex_folders[src_key] = src_fid
                self._upsert("folders", src_fid, {
                    "projectId": proj_id, "parentFolderId": None,
                    "name": self.source_folder, "order": 0})
                self.stats["carpeta de origen (dominio del Mart / archivo)"] += 1

        folder_pid: dict[str, str] = {}
        for i, sa in enumerate(sorted(self.m.subject_areas, key=lambda s: s["order"])):
            if self.only_sa and sa["name"] != self.only_sa:
                continue
            # R8: folder homónimo del mismo proyecto Y la misma capa de origen
            # → se REUSA, sin re-escribirlo (conserva su orden).
            hit = self.ex_folders.get((proj_id, src_fid or "", sa["name"].upper()))
            own_fid = self.pid(f"folder|{sa['id']}")
            fid = hit or own_fid
            folder_pid[sa["name"]] = fid
            if hit and hit != own_fid:
                self.stats["folders reusados (homónimos de otro archivo)"] += 1
                continue
            self.ex_folders[(proj_id, src_fid or "", sa["name"].upper())] = fid
            self._upsert("folders", fid, {
                "projectId": proj_id, "parentFolderId": src_fid,
                "name": sa["name"], "order": i, "erwinLongId": sa["id"]})
            self.stats["folders"] += 1

        canvases_by_id = {c["_id"]: c for c in self.db_canvases}
        canvases_by_key = {((c.get("folderId") or ""), (c.get("name") or "").upper()): c
                           for c in self.db_canvases}
        for d in self.m.diagrams:
            if self.only_sa and d.subject_area != self.only_sa:
                continue
            table_ids, view_ids, layout = [], [], {}
            ordered = list(dict.fromkeys(ref for ref, _a in d.shapes))
            ents = [r for r in ordered if r in self.table_pid]
            vws = [r for r in ordered if r in self.view_pid]
            seen_nodes: set[str] = set()
            for ref in ents + vws:
                pid = self.table_pid.get(ref) or self.view_pid[ref]
                if pid in seen_nodes:      # copias alias → un solo nodo
                    continue
                seen_nodes.add(pid)
                n = len(layout)
                layout[pid] = {"x": 40 + (n % 4) * 480, "y": 40 + (n // 4) * 360}
                if ref in self.table_pid:
                    table_ids.append(pid)
                else:
                    # doc 70: la vista es MIEMBRO del canvas donde Erwin la
                    # dibuja (antes sólo aportaba layout y la visibilidad la
                    # daba el flag global showOnCanvas).
                    view_ids.append(pid)
            fid = folder_pid.get(d.subject_area)
            own_cid = self.pid(d.id)
            prev = canvases_by_id.get(own_cid)
            merged = canvases_by_key.get((fid or "", d.name.upper()))
            doc_udp = dict(model_udp)
            if prev is None and merged is not None:
                # R8: canvas homónimo del mismo folder (otro archivo) → se
                # FUSIONA: unión de tablas, layout existente intacto.
                cid = merged["_id"]
                table_ids = list(dict.fromkeys((merged.get("tableIds") or []) + table_ids))
                view_ids = list(dict.fromkeys((merged.get("viewIds") or []) + view_ids))
                layout = {**layout, **(merged.get("layout") or {})}
                doc_udp = {**(merged.get("udpValues") or {}), **model_udp}
                self.stats["canvases fusionados (homónimos de otro archivo)"] += 1
            else:
                cid = own_cid
                # Re-runs NO pisan el layout ya trabajado (ELK/arreglos a mano):
                # los nodos existentes conservan su posición; la grilla default
                # solo aplica a nodos NUEVOS de esta corrida.
                if prev and prev.get("layout"):
                    layout = {k: prev["layout"].get(k, v) for k, v in layout.items()}
            self._upsert("subject_areas", cid, {
                "projectId": proj_id,
                "folderId": fid,
                "name": d.name, "tableIds": table_ids, "viewIds": view_ids, "layout": layout,
                "drawings": [], "udpValues": doc_udp,
                "erwinLongId": d.id})
            self.stats["canvases"] += 1

    def run(self) -> None:
        self.standards()
        self.tables()
        self.relationships()
        self.views()
        self.schema_docs()
        self.canvases()
        self._stale_columns()
        self.flush_all()


def _dry_run_plan(m: ep.ErwinModel, project: str | None) -> None:
    """Plan sin BD: volumetría + lo que las políticas multi-archivo harían
    DENTRO del archivo (los cruces contra BD los da `crosscheck`)."""
    defs = pol.udp_defs_by_view(m.udp_defs)
    schema_of = m.owner_schema()
    print("\nDRY-RUN (sin --apply no se escribe nada). Plan:")
    print(f"  proyecto: {project or m.name}")
    print(f"  folders={len(m.subject_areas)} canvases={len(m.diagrams)} "
          f"tablas={len(m.entities)} vistas={len(m.views)} "
          f"defsUDP={len(defs)} (por faceta) glosario={len(m.glossary)} "
          f"dominios={sum(1 for d in m.domains.values() if not d.name.startswith('<'))}")
    dup_t = Counter(f"{pol.schema_or_default(schema_of.get(e.id))}.{e.physical}".upper()
                    for e in m.entities.values())
    dups = sum(n - 1 for n in dup_t.values() if n > 1)
    if dups:
        print(f"  duplicados internos de tabla: {dups} copias → se migran TODAS "
              "con sufijo _DUPn (política 2026-08-22; detalle en el reporte)")
    udp_vals = pol.resolve_udp_values(m.udp_values, defs)
    reassign = 0
    for e in m.entities.values():
        vals = [(a.id, c) for a in e.attributes
                if (c := pol.partition_correlative(
                    udp_vals.get((a.id, pol.PARTITION_UDP_KEY)))) is not None]
        if vals and pol.partition_marks(vals)[1]:
            reassign += 1
    if reassign:
        print(f"  particiones a reasignar por orden físico: {reassign} tablas (R6)")
    print("  cruce contra la BD (adopciones/score): correr "
          "`python -m scripts.erwin_migration.crosscheck`")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migración Erwin XML → Data Model Hub")
    ap.add_argument("xml", nargs="+")
    ap.add_argument("--apply", action="store_true",
                    help="escribir en la BD (sin esto: dry-run)")
    ap.add_argument("--project", help="nombre de proyecto destino (obligatorio "
                                      "si el proyecto ya existe — R8)")
    ap.add_argument("--description", help="descripción del proyecto si se crea "
                                          "(default: «Migrado de Erwin — <modelo>»)")
    ap.add_argument("--only-sa", help="migrar solo canvases de esta subject area")
    ap.add_argument("--force", action="store_true",
                    help="continuar aunque el gate de calidad tenga ERRORs")
    ap.add_argument("--report", help="ruta del reporte JSON de decisiones "
                                     "(default: migration-reports/<xml>-<ts>.json)")
    ap.add_argument("--keep-unused-udp-defs", action="store_true",
                    help="crear también defs UDP sin uso observado (A4 off)")
    args = ap.parse_args(argv)

    rc = 0
    for path in args.xml:
        print(f"\n{'=' * 72}\nARCHIVO: {path}")
        t0 = time.perf_counter()
        m = ep.parse(path)
        print(f"(parseado en {time.perf_counter() - t0:.1f}s)")
        s = summarize(m)
        print(f"Modelo: {s['modelo']} — " +
              " · ".join(f"{k}={v}" for k, v in s.items() if k != "modelo"))

        errors = [x for x in analyze(m) if x["severity"] == "ERROR"]
        if errors:
            for x in errors:
                print(f"[ERROR] {x['code']} ({x['count']}): {x['samples'][:3]}")
            if not args.force:
                print("Gate de calidad con ERRORs — corre primero "
                      "`python -m scripts.erwin_migration.quality` y decide; "
                      "o repite con --force para omitir esos objetos.")
                rc = 1
                continue

        if not args.apply:
            _dry_run_plan(m, args.project)
            continue

        from dotenv import load_dotenv

        from app.core.db.sync import get_sync_db
        load_dotenv()
        db = get_sync_db()

        project_name = args.project or m.name or "Modelo Erwin"
        if not args.project and db.projects.find_one(
                {"name": project_name, **_ACTIVE}, {"_id": 1}):
            print(f"⛔ El proyecto '{project_name}' YA existe y no pasaste "
                  "--project. Los archivos de una familia comparten proyecto a "
                  "PROPÓSITO (doc 32b R8) — confirma con:\n"
                  f"    --project \"{project_name}\"   (fusionar en el existente)\n"
                  "    --project \"Otro nombre\"      (proyecto aparte)")
            rc = 1
            continue

        # Doc 54 §9: capa de ORIGEN = dominio del Mart del propio XML; sin
        # Locator (modelo nunca guardado en Mart), el nombre del archivo.
        parsed_loc = pol.parse_mart_locator(m.locator) if m.locator else None
        source = ((parsed_loc or {}).get("domain")
                  or os.path.splitext(os.path.basename(path))[0])
        print(f"Carpeta de origen (capa por archivo): {source}")

        t1 = time.perf_counter()
        mig = Migrator(db, m, args.project, args.only_sa,
                       keep_unused_udp_defs=args.keep_unused_udp_defs,
                       source_folder=source, description=args.description)
        mig.run()
        elapsed = time.perf_counter() - t1
        print(f"\nRESULTADO ({elapsed:.1f}s · {mig.ops_flushed:,} escrituras en "
              f"{(mig.ops_flushed + _BATCH - 1) // _BATCH} lotes):")
        for k, v in sorted(mig.stats.items()):
            print(f"  {k}: {v}")
        for w in mig.warnings[:20]:
            print(f"  ⚠ {w}")
        if len(mig.warnings) > 20:
            print(f"  ⚠ … y {len(mig.warnings) - 20} avisos más")

        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(path))
        out = args.report or os.path.join(
            "migration-reports",
            f"{stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"file": path, "project": project_name, "projectId": mig.project_id,
                       "at": _now(),
                       "durationSeconds": round(elapsed, 1),
                       "stats": dict(mig.stats), "decisions": mig.report,
                       "warnings": mig.warnings}, fh, ensure_ascii=False, indent=1)
        print(f"\nReporte de decisiones: {out}")
        print("Siguientes pasos: `python -m scripts.audit_data_consistency` "
              "(validación) y `scripts/arrange_all` (layout ELK, acepta --project).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
