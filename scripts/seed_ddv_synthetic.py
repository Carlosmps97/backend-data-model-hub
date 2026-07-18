"""Seed SINTÉTICO tipo-DDV: reemplaza toda la data del modeler por un dataset
muy parecido al export Erwin `DDV - CPYBCA.xml` — PERO no idéntico.

Objetivo (pedido owner 2026-07-14): data realista para validar TODAS las
funcionalidades nuevas (UDP por tabla/columna, vistas en canvas, Data
Standards, reporting) sin las inconsistencias de la data vieja (stress) que se
cargó con funcionalidades menos exigentes (tablas/columnas SIN `udpValues`,
vistas SIN `showOnCanvas` ni `sources` completos).

Qué copia IDÉNTICO del XML  → glosario (120), defs UDP (18), parent domains
                              (46), y las columnas técnicas/de auditoría
                              (FLGREGELIMINADO, FECDIA, CODMES, …) que se
                              repiten en casi toda tabla (validan glosario +
                              cascada de dominio).
Qué hace SIMILAR-no-idéntico → nombres de tablas/vistas/esquemas y columnas de
                              negocio: se sustituyen SOLO los sustantivos de
                              negocio (CLIENTE→CONTRAPARTE, DEUDOR→OBLIGADO, …);
                              los prefijos de abreviatura (COD/FEC/MTO/TIP/DES/
                              FLG/NUM/NBR/CTD/RCC/PROD) se preservan para que el
                              glosario siga validando.
Qué respeta del XML          → mismas DIMENSIONES: 163 tablas · 4.566 columnas
                              (misma distribución por tabla) · 119 vistas _vu
                              1:1 (showOnCanvas) · 177 pares FK · 28 canvases ·
                              1 tabla por canvas sin reúso (igual que el XML).

Jerarquía: 4 subject areas del XML → 4 PROYECTOS (renombrados) → folders →
28 canvases. Usuarios: 1 admin (admin/admin) + 2 por rol con login T#### (pass
== username).

⚠️  Preserva `column_catalog` (colección del agente): jamás se toca.

Uso:
  .venv/bin/python -m scripts.seed_ddv_synthetic            # dry-run (arma + cuenta, NO escribe)
  .venv/bin/python -m scripts.seed_ddv_synthetic --apply    # BORRA la BD y re-siembra
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.erwin_migration import erwin_parser as ep  # noqa: E402
from scripts.erwin_migration import policies as pol      # noqa: E402
from app.core.security import hash_password              # noqa: E402
from app.features.auth.models import PERMISSIONS          # noqa: E402

XML_DEFAULT = ROOT.parent / "folder_data" / "DDV - CPYBCA.xml"
_NOW = datetime.now(timezone.utc).isoformat()


def _now() -> str:
    return _NOW


# ── Colecciones que se BORRAN (todo el modeler; NUNCA column_catalog) ─────────
PROTECTED = "column_catalog"
WIPE_COLLECTIONS = [
    "projects", "folders", "subject_areas", "schemas", "canonical_tables",
    "canonical_columns", "relationships", "views", "parent_domains",
    "glossary_terms", "udp_definitions", "naming_config", "changesets",
    "changeset_changes", "users", "roles", "standards_versions", "audit_log",
    "saved_reports",
    # vestigiales (se dejan vacías, limpias):
    "project_relationships", "project_tables", "semantic_types", "udps",
]

# ── Obfuscación: sustantivos de negocio → sinónimo (uni-direccional) ─────────
# NO se tocan los prefijos-abreviatura ni las palabras "clase" (Codigo, Tipo,
# Descripcion, Fecha, Monto, Numero, Nombre, Cantidad, Porcentaje, Flag, Clave)
# → el glosario y la cascada de dominio siguen validando igual que el XML.
_SYN: dict[str, str] = {
    "CLIENTE": "CONTRAPARTE", "CLIENTES": "CONTRAPARTES",
    "DEUDOR": "OBLIGADO", "DEUDORES": "OBLIGADOS",
    "GARANTIA": "COLATERAL", "GARANTIAS": "COLATERALES",
    "CAMPANIA": "PROMOCION", "CAMPANIAS": "PROMOCIONES",
    "SOLICITUD": "REQUERIMIENTO", "SOLICITUDES": "REQUERIMIENTOS",
    "DESEMBOLSO": "LIQUIDACION", "COLABORADOR": "TRABAJADOR",
    "COLABORADORES": "TRABAJADORES", "ORGANIZACION": "ENTIDADORG",
    "PARAMETRO": "CRITERIO", "PARAMETROS": "CRITERIOS",
    "SEGMENTO": "SUBSEGMENTO", "JEFATURA": "SUPERVISION",
    "GERENTE": "DIRECTIVO", "FUNCIONARIO": "EJECUTIVO",
    "MATRICULA": "CODREGISTRO", "CARTERA": "PORTAFOLIO",
    "HABERES": "REMUNERACION", "PROBABILIDAD": "VEROSIMILITUD",
    "EFECTIVIDAD": "RENDIMIENTO", "CLASIFICACION": "CATEGORIZACION",
    "PRODUCTO": "SERVICIO", "PRODUCTOS": "SERVICIOS",
    "RIESGOS": "EXPOSICIONES", "RIESGO": "EXPOSICION",
    "CREDITO": "FINANCIAMIENTO", "PROYECTO": "INICIATIVA",
    "PROYECTOS": "INICIATIVAS", "PRICING": "TARIFICACION",
    "EFECTIVO": "CONTADO", "VENTA": "COLOCACION", "VENTAS": "COLOCACIONES",
    "FINANCIERO": "CONTABLE", "FINANCIERA": "CONTABLE",
    "ECONOMICO": "SECTORIAL", "SECTOR": "RUBRO", "PARTY": "SUJETO",
    "VIVIENDA": "INMUEBLE", "LEAD": "PROSPECTO", "LEADS": "PROSPECTOS",
    "PORTAFOLIO": "CARTERAPORT",
    # técnicos / catálogo (bajan los nombres idénticos residuales)
    "SOLUCION": "APLICATIVO", "SOLUCIONES": "APLICATIVOS", "UNIDAD": "MODULO",
    "CAPA": "ESTRATO", "HDFS": "DATALAKE", "GESTION": "ADMINISTRACION",
    "SEGUIMIENTO": "MONITOREO", "MODELO": "ESQUEMA", "MODELOS": "ESQUEMAS",
    "ALERTA": "AVISO", "ALERTAS": "AVISOS", "MORA": "ATRASO", "SALDO": "BALANCE",
    "SALDOS": "BALANCES", "NEGOCIOS": "CORPORATIVOS", "JERARQUIA": "ESTRUCTURAORG",
    "TRANSACCIONAL": "OPERACIONAL", "TRANSACCIONALES": "OPERACIONALES",
    "PROVISION": "RESERVA", "PLANEAMIENTO": "PLANIFICACION", "CROSS": "CRUZADO",
    "DATAMART": "MERCADODATOS", "CATEGORIA": "CLASE",
}
_PHYS_RE = re.compile("|".join(sorted((re.escape(k) for k in _SYN), key=len, reverse=True)))

# Columnas técnicas/auditoría: se dejan IDÉNTICAS (se repiten en casi toda
# tabla → validan glosario + dominio, y sirven de "columnas iguales" pedidas).
_KEEP_COLS = {
    "FLGREGELIMINADO", "FECREGELIMINADO", "FECRUTINA", "FECDIA", "CODMES",
    "FECACTUALIZACIONREGISTRO", "TIPFRECUENCIAREGISTRO", "FLGREGELIMINADOFUENTE",
    "FECREGELIMINADOFUENTE", "CODUSUARIOCARGA", "CODUSUARIOLKDE", "TIPINGRESOLKDE",
    "FECACTUALIZACIONREGLKDE", "TIPFILTROSEGURIDAD", "NBRFUENTE", "FECHASTOCK",
    "FECHASTOCKPARTICION", "FECHAPARTICION",
}

# Áreas de esquema del XML → área sintética (mantiene bcp_ddv_ + tema + _vu).
_AREA = {
    "cpybcaneg": "bncneg", "cpybcapym": "bncpym", "riesgos": "riesgocred",
    "pricing": "tarifas", "finpricin": "finmodel", "pycf": "planfin",
    "hip": "hipotec", "rbmctower": "rbmtorre", "cmaplamay": "planmay",
}

# Subject areas del XML → proyecto sintético (nombre, descripción).
_SA_PROJECT = {
    "CPYBCANEG": ("Banca Negocios (DDV Sintético)",
                  "Modelo físico DDV de Banca Negocios: jerarquía, productos, saldos y analítica."),
    "CPYBCAPYM": ("Banca Pyme (DDV Sintético)",
                  "Modelo físico DDV de Banca Pyme: datamart, seguimiento de producto y alertas."),
    "Despriorizado": ("Modelos Despriorizados (DDV)",
                      "Modelos en pausa: RBM, pricing y finanzas fuera del alcance vigente."),
    "Eliminado": ("Modelos Eliminados (DDV)",
                  "Modelos dados de baja: planeamiento mayorista e históricos."),
}


def transform_physical(name: str) -> str:
    """Sustituye sustantivos de negocio en un nombre físico ALLCAPS concatenado."""
    if not name:
        return name
    return _PHYS_RE.sub(lambda mo: _SYN[mo.group()], name.upper())


def transform_logical(name: str) -> str:
    """Sustituye sustantivos de negocio en un nombre lógico (espaciado Title)."""
    if not name:
        return name
    if " " not in name:
        return transform_physical(name)
    out = []
    for tok in re.split(r"(\s+)", name):
        rep = _SYN.get(tok.upper())
        out.append(rep.title() if rep else tok)
    return "".join(out)


def rename_schema(schema: str) -> str:
    if schema == pol.NO_SCHEMA:
        return schema
    parts = schema.split("_")
    if len(parts) >= 4 and parts[0] == "bcp" and parts[1] == "ddv":
        area = parts[2]
        theme = "_".join(parts[3:])
        return f"bcp_ddv_{_AREA.get(area, area + 'x')}_{theme}"
    return schema + "_syn"


# Alias que son EL MISMO tipo con otra grafía (INT≡INTEGER, BIG INTEGER≡BIGINT):
# sin plegarlos, columnas del dominio quedaban typeOverridden=True por pura
# grafía y el impacto del dominio mostraba "0 se actualizan / N override".
_TYPE_ALIAS = {"INT": "INTEGER", "BIGINTEGER": "BIGINT"}


def _norm_type(t: str) -> str:
    n = re.sub(r"\s+", "", (t or "").upper())
    return _TYPE_ALIAS.get(n, n)


# ─────────────────────────────────────────────────────────────────────────────
class Builder:
    """Arma el dataset completo (dicts listos para insert) a partir del XML."""

    def __init__(self, model: ep.ErwinModel):
        self.m = model
        self.schema_of = model.owner_schema()
        self.attr_idx = model.attr_index()
        self.collapsed = pol.collapse_udp_defs(model.udp_defs)
        self.udp_vals = pol.resolve_udp_values(model.udp_values, self.collapsed)
        # mapas erwin_id → id sintético
        self.domain_pid: dict[str, str] = {}
        self.udp_pid: dict[str, str] = {}
        self.table_pid: dict[str, str] = {}
        self.table_schema: dict[str, str] = {}   # erwin ent id → schema sintético
        self.col_pid: dict[str, str] = {}
        self.col_phys: dict[str, str] = {}        # erwin attr id → físico sintético
        self.view_pid: dict[str, str] = {}
        self.data: dict[str, list[dict]] = defaultdict(list)
        self._natkeys: set[str] = set()

    # ---- estándares (IDÉNTICOS al XML) ----
    def standards(self) -> None:
        for d in self.m.domains.values():
            if d.builtin or d.name.startswith("<"):
                continue
            pid = f"pd-{uuid.uuid5(pol._NS, d.id).hex[:12]}"
            self.domain_pid[d.id] = pid
            self.data["parent_domains"].append({
                "_id": pid, "name": d.name,
                "defaultDataType": d.data_type or "STRING",
                "namingTerm": None, "description": d.definition or None,
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

        seen: set[str] = set()
        for g in self.m.glossary:
            term = g[0].strip()
            abbrev = g[1].strip() if len(g) > 1 else ""
            if not term or term.upper() in seen:
                continue
            seen.add(term.upper())
            self.data["glossary_terms"].append({
                "_id": f"gl-{uuid.uuid5(pol._NS, 'gl|' + term.upper()).hex[:12]}",
                "term": term, "abbrev": abbrev, "scope": "column",
                "wordType": None, "locked": False, "lockedBy": None, "lockedAt": None,
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

        values_by_key: dict[str, set] = defaultdict(set)
        for (_oid, key), val in self.udp_vals.items():
            values_by_key[key].add(val)
        for key, entry in self.collapsed.items():
            pid = f"udp-{uuid.uuid5(pol._NS, 'udp|' + key).hex[:12]}"
            self.udp_pid[key] = pid
            allowed = (sorted(values_by_key.get(key, set()) | ({entry["default"]} if entry["default"] else set()))
                       if entry["dataType"] == "list" else [])
            self.data["udp_definitions"].append({
                "_id": pid, "name": entry["name"], "level": entry["level"],
                "dataType": entry["dataType"], "defaultValue": entry["default"],
                "allowedValues": allowed, "description": "Estándar DDV (sintético)",
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

        # Regla corporativa: join + UPPER en ambos scopes — es la regla con la
        # que están derivados los físicos del XML (CODCLAVESUJETOCLI); con "_"
        # la UI de Data Standards mostraba una regla que la data contradecía.
        self.data["naming_config"] = [
            {"_id": "column", "scope": "column", "separator": "", "case": "upper",
             "flgactive": True, "createdAt": _now(), "updatedAt": _now()},
            {"_id": "table", "scope": "table", "separator": "", "case": "upper",
             "flgactive": True, "createdAt": _now(), "updatedAt": _now()},
        ]

    def _udp_for(self, erwin_id: str, level: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, entry in self.collapsed.items():
            if entry["level"] != level:
                continue
            val = self.udp_vals.get((erwin_id, key))
            if val:
                out[self.udp_pid[key]] = val
        return out

    # ---- tablas + columnas (SIMILARES, con udpValues) ----
    def tables(self) -> None:
        for i, e in enumerate(self.m.entities.values()):
            schema = rename_schema(pol.schema_or_default(self.schema_of.get(e.id)))
            base = transform_physical(e.physical)
            if base.upper() == e.physical.upper():         # garantiza "no idéntico"
                base = base + "V2"
            phys, n = base, 2
            while f"{schema}.{phys}".upper() in self._natkeys:   # unicidad
                phys = f"{base}{n}"
                n += 1
            self._natkeys.add(f"{schema}.{phys}".upper())
            tid = f"tbl-{i:04d}"
            self.table_pid[e.id] = tid
            self.table_schema[e.id] = schema
            self.data["canonical_tables"].append({
                "_id": tid, "physicalName": phys,
                "logicalName": transform_logical(e.name) or phys,
                "schema": schema,
                "description": e.definition or e.comment or None,
                "udpValues": self._udp_for(e.id, "table"),
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

            cols, _dropped = pol.dedupe_columns(e.attributes)
            for j, a in enumerate(cols):
                cid = f"{tid}.c{j}"
                self.col_pid[a.id] = cid
                up = a.physical.upper()
                phys_col = up if up in _KEEP_COLS else transform_physical(a.physical)
                self.col_phys[a.id] = phys_col
                dom_pid = self.domain_pid.get(a.domain_ref or "")
                dom = self.m.domains.get(a.domain_ref or "")
                col_type = a.data_type or "STRING"
                overridden = bool(dom_pid and dom
                                  and _norm_type(dom.data_type) != _norm_type(col_type))
                # Columna que SIGUE el tipo del dominio: adopta la grafía del
                # default (INT→INTEGER) para que la cascada de dominio — que
                # compara dataType == old_type EXACTO — la alcance al editar.
                if dom_pid and dom and not overridden and dom.data_type:
                    col_type = dom.data_type
                self.data["canonical_columns"].append({
                    "_id": cid, "tableId": tid, "physicalName": phys_col,
                    "logicalName": transform_logical(a.name) or phys_col,
                    "parentDomainId": dom_pid, "dataType": col_type,
                    "typeOverridden": overridden,
                    "isPrimaryKey": a.id in e.pk_attr_ids,
                    "pkPosition": ({aid: k for k, aid in enumerate(e.pk_attr_order)}.get(a.id)),
                    "isForeignKey": bool(a.parent_attr_ref),
                    "isNullable": a.nullable, "isPartition": False,
                    "description": a.definition or a.comment or None,
                    "ordinal": j, "udpValues": self._udp_for(a.id, "column"),
                    "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

    # ---- relaciones (un doc POR RELACIÓN con sus pares — v2, doc 19) ----
    def relationships(self) -> None:
        pairs = self.m.fk_pairs()
        for r in self.m.relationships.values():
            if r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING):
                continue
            child_t = self.table_pid.get(r.child_ref)
            parent_t = self.table_pid.get(r.parent_ref)
            if not child_t or not parent_t:
                continue
            rel_pairs = [
                {"parentColumnId": p_col, "childColumnId": c_col, "roleName": None}
                for p_attr, c_attr in pairs.get(r.id, [])
                if (p_col := self.col_pid.get(p_attr)) and (c_col := self.col_pid.get(c_attr))
            ]
            if not rel_pairs:
                continue
            self.data["relationships"].append({
                "_id": f"rel-{uuid.uuid5(pol._NS, r.id).hex[:14]}",
                "parentTableId": parent_t, "childTableId": child_t,
                "pairs": rel_pairs,
                "parentCardinality": "one",
                "childCardinality": pol.map_cardinality(r.cardinality),
                "identifying": r.rel_type == ep.REL_IDENTIFYING,
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

    # ---- vistas (_vu 1:1, showOnCanvas, sources completos) ----
    def views(self) -> None:
        view_rels = self.m.view_source_rels()
        for k, v in enumerate(self.m.views.values()):
            rels = [r for r in view_rels.get(v.id, []) if r.parent_ref in self.table_pid]
            if not rels:
                continue
            src_ent = rels[0].parent_ref
            src_tid = self.table_pid[src_ent]
            schema = self.table_schema.get(src_ent, pol.NO_SCHEMA)
            vschema = (schema + "_vu") if schema != pol.NO_SCHEMA else "vistas_vu"
            vid = f"vw-{k:04d}"
            self.view_pid[v.id] = vid
            cols, _ = pol.dedupe_columns(v.attributes)
            sources = []
            for a in cols:
                origin = self.attr_idx.get(a.parent_attr_ref or "")
                out_alias = self.col_phys.get(origin.id) if origin else transform_physical(a.physical)
                col_name = self.col_phys.get(origin.id) if origin else transform_physical(a.physical)
                src = {"tableId": src_tid, "column": col_name, "outputAlias": out_alias}
                if origin and _norm_type(origin.data_type) != _norm_type(a.data_type):
                    src["castType"] = a.data_type
                sources.append(src)
            self.data["views"].append({
                "_id": vid, "name": transform_physical(v.name), "schema": vschema,
                "sql": "", "description": v.definition or v.comment or None,
                "tableId": src_tid, "sourceTableIds": [src_tid], "sources": sources,
                "tags": [], "filter": None, "outputAlias": None, "expression": None,
                "showOnCanvas": True, "joinOverride": None,
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

    # ---- proyectos + folders + canvases ----
    def canvases(self) -> None:
        # UDP nivel Model → nivel canvas (Database, Archivo Base)
        model_udp: dict[str, str] = {}
        for key, entry in self.collapsed.items():
            if entry["level"] != "canvas":
                continue
            for (_oid, k), val in self.udp_vals.items():
                if k == key and val:
                    model_udp[self.udp_pid[key]] = val
                    break

        # proyecto por subject area
        sa_project: dict[str, str] = {}
        for sa in self.m.subject_areas:
            name, desc = _SA_PROJECT.get(sa["name"], (sa["name"], None))
            pid = f"prj-{uuid.uuid5(pol._NS, 'prj|' + sa['name']).hex[:10]}"
            sa_project[sa["name"]] = pid
            self.data["projects"].append({
                "_id": pid, "name": name, "description": desc,
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

        # folders: 1 folder temático por cada 6 canvases de la SA (da la jerarquía)
        per_sa_diags: dict[str, list] = defaultdict(list)
        for d in self.m.diagrams:
            per_sa_diags[d.subject_area].append(d)
        folder_for: dict[str, str | None] = {}   # diagram id → folder id
        for sa_name, diags in per_sa_diags.items():
            proj = sa_project.get(sa_name)
            if proj is None:
                continue
            if len(diags) >= 4:                    # SAs grandes → agrupar en folders
                for gi in range((len(diags) + 5) // 6):
                    fid = f"fld-{uuid.uuid5(pol._NS, f'fld|{sa_name}|{gi}').hex[:10]}"
                    self.data["folders"].append({
                        "_id": fid, "projectId": proj, "parentFolderId": None,
                        "name": f"Dominio {gi + 1}", "order": gi,
                        "flgactive": True, "createdAt": _now(), "updatedAt": _now()})
                    for d in diags[gi * 6:(gi + 1) * 6]:
                        folder_for[d.id] = fid
            else:
                for d in diags:
                    folder_for[d.id] = None

        for d in self.m.diagrams:
            proj = sa_project.get(d.subject_area)
            if proj is None:
                continue
            ordered = list(dict.fromkeys(ref for ref, _a in d.shapes))
            ents = [r for r in ordered if r in self.table_pid]
            vws = [r for r in ordered if r in self.view_pid]
            table_ids, layout = [], {}
            for n, ref in enumerate(ents + vws):
                pid = self.table_pid.get(ref) or self.view_pid[ref]
                layout[pid] = {"x": float(40 + (n % 5) * 460), "y": float(40 + (n // 5) * 340)}
                if ref in self.table_pid:
                    table_ids.append(pid)
            cid = f"cnv-{uuid.uuid5(pol._NS, d.id).hex[:12]}"
            self.data["subject_areas"].append({
                "_id": cid, "projectId": proj, "folderId": folder_for.get(d.id),
                "name": d.name, "tableIds": table_ids, "layout": layout,
                "drawings": [], "udpValues": dict(model_udp),
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})
        self._projects = sa_project

    # ---- auth: roles + usuarios ----
    def auth(self) -> None:
        grants = {
            "administrador": ("Administrador", "Control total · gestiona usuarios", set(PERMISSIONS)),
            "modelador": ("Modelador", "Crea y edita tablas · envía a revisión",
                          {"model.view", "model.edit", "export"}),
            "revisor": ("Revisor", "Aprueba o rechaza solicitudes",
                        {"model.view", "review.decide", "publish", "export"}),
            "lector": ("Lector", "Solo lectura · exporta metadata", {"model.view", "export"}),
        }
        for key, (name, desc, g) in grants.items():
            self.data["roles"].append({
                "_id": key, "name": name, "description": desc,
                "permissions": {p: (p in g) for p in PERMISSIONS},
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

        def _u(username, name, email, role, pwd, projects=None, status="active"):
            parts = [p for p in name.split() if p]
            initials = (parts[0][:2] if len(parts) == 1 else parts[0][0] + parts[-1][0]).upper()
            self.data["users"].append({
                "_id": username, "email": email, "name": name, "role": role,
                "projectIds": projects or [], "status": status, "initials": initials,
                "passwordHash": hash_password(pwd),
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

        # 1 admin de dev
        _u("admin", "Administrador", "admin@empresa.com", "administrador", "admin")
        # 2 usuarios por rol, login T#### (pass == username), email {nombre}{apellido}
        roster = [
            ("T1234", "Carlos Mendoza", "administrador"),
            ("T1235", "Lucia Vargas", "administrador"),
            ("T1236", "Jorge Rios", "modelador"),
            ("T1237", "Ana Salazar", "modelador"),
            ("T1238", "Pedro Campos", "revisor"),
            ("T1239", "Rosa Quispe", "revisor"),
            ("T1240", "Miguel Flores", "lector"),
            ("T1241", "Elena Chavez", "lector"),
        ]
        for username, name, role in roster:
            first, last = name.split()[0], name.split()[-1]
            email = f"{first}{last}@empresa.com".lower().replace(" ", "")
            _u(username, name, email, role, username)

    # ---- changesets (línea base + 1 request) + standards baseline ----
    def governance(self) -> None:
        proj_ids = list(self._projects.values())
        self.data["changesets"].append({
            "_id": "cs-baseline-ddv", "title": "Línea base DDV Sintético",
            "owner": "admin", "status": "approved",
            "description": "Publicación inicial del modelo físico DDV sintético.",
            "versionLabel": "v1", "projectIds": proj_ids,
            "reviewers": ["T1238", "T1239"],
            "approvals": {"T1238": {"status": "approved", "note": "OK base", "at": _now()},
                          "T1239": {"status": "approved", "at": _now()}},
            "comments": [{"author": "admin", "text": "Baseline de producción.", "at": _now()}],
            "createdAt": _now(), "updatedAt": _now(), "submittedAt": _now(),
            "reviewedBy": "T1239", "reviewedAt": _now(), "appliedAt": _now(),
            "flgactive": True})

        # 1 request en revisión: nueva columna en la primera tabla sintética
        tbl = self.data["canonical_tables"][0] if self.data["canonical_tables"] else None
        if tbl:
            self.data["changesets"].append({
                "_id": "cs-req-01", "title": "Nueva columna de trazabilidad",
                "owner": "T1236", "status": "submitted",
                "description": f"Agrega FECCARGADATALAKE a {tbl['physicalName']}.",
                "versionLabel": "v2", "projectIds": [proj_ids[0]] if proj_ids else [],
                "reviewers": ["T1238"], "approvals": {},
                "comments": [{"author": "T1236", "text": "Para linaje de carga.", "at": _now()}],
                "createdAt": _now(), "updatedAt": _now(), "submittedAt": _now(),
                "flgactive": True})
            self.data["changeset_changes"].append({
                "_id": f"cs-req-01::canonical_columns::{tbl['_id']}.cNEW",
                "csId": "cs-req-01", "collection": "canonical_columns",
                "entityId": f"{tbl['_id']}.cNEW", "op": "upsert", "at": _now(),
                "payload": {"id": f"{tbl['_id']}.cNEW", "tableId": tbl["_id"],
                            "physicalName": "FECCARGADATALAKE",
                            "logicalName": "Fecha Carga Datalake",
                            "parentDomainId": None, "dataType": "TIMESTAMP",
                            "isNullable": True, "ordinal": 999},
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

        domains = [{"id": d["_id"], "name": d["name"], "defaultDataType": d["defaultDataType"],
                    "namingTerm": d.get("namingTerm"), "description": d.get("description")}
                   for d in self.data["parent_domains"]]
        terms = [{"id": g["_id"], "term": g["term"], "abbrev": g["abbrev"],
                  "scope": g["scope"], "wordType": g.get("wordType")}
                 for g in self.data["glossary_terms"]]
        naming = {d["_id"]: {"separator": d["separator"], "case": d["case"]}
                  for d in self.data["naming_config"]}
        self.data["standards_versions"].append({
            "_id": "sv-v1", "seq": 1, "label": "v1", "kind": "baseline",
            "title": "Baseline · estándares DDV sintéticos", "description": None,
            "author": "system", "createdAt": _now(), "appliedAt": _now(),
            "status": "baseline", "diff": {"added": [], "edited": [], "removed": []},
            "impact": {"tables": 0, "columns": 0},
            "snapshot": {"domains": domains, "dict": terms,
                         "namingConfig": {"column": naming.get("column"), "table": naming.get("table")}},
            "revertsSeq": None, "flgactive": True})

    def schemas(self) -> None:
        """Entidad `schemas` (doc 18): un doc por cada esquema usado por las
        tablas y las vistas (id determinista) — sin esto, un reseed dejaría los
        dropdowns de esquema vacíos."""
        names = sorted({d.get("schema") for d in self.data["canonical_tables"] if d.get("schema")}
                       | {d.get("schema") for d in self.data["views"] if d.get("schema")})
        for name in names:
            self.data["schemas"].append({
                "_id": f"sch-{name}", "name": name, "description": None,
                "flgactive": True, "createdAt": _now(), "updatedAt": _now()})

    def build(self) -> dict[str, list[dict]]:
        self.standards()
        self.tables()
        self.relationships()
        self.views()
        self.schemas()
        self.canvases()
        self.auth()
        self.governance()
        return self.data


def summarize(data: dict[str, list[dict]]) -> None:
    order = ["projects", "folders", "subject_areas", "schemas", "canonical_tables",
             "canonical_columns", "relationships", "views", "parent_domains",
             "glossary_terms", "udp_definitions", "naming_config", "roles",
             "users", "changesets", "changeset_changes", "standards_versions"]
    print("\nDATASET sintético:")
    for c in order:
        print(f"  {c:<20} {len(data.get(c, []))}")


async def _write_chunked(coll, docs: list[dict], size: int = 200) -> None:
    """Upsert idempotente por chunks (resiliente a throttling 429 de Cosmos y a
    límites de tamaño de batch). ReplaceOne+upsert ⇒ reintentar un chunk a medio
    aplicar no rompe (re-reemplaza, no duplica)."""
    from pymongo import ReplaceOne
    for i in range(0, len(docs), size):
        ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs[i:i + size]]
        for attempt in range(6):
            try:
                await coll.bulk_write(ops, ordered=False)
                break
            except Exception:
                if attempt == 5:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))


async def _apply(data: dict[str, list[dict]]) -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.core.db.indexes import ensure_indexes
    await connect()
    db = await get_db()
    before = await db[PROTECTED].count_documents({})
    assert PROTECTED not in WIPE_COLLECTIONS, "no borrar column_catalog"
    for coll in WIPE_COLLECTIONS:
        await db[coll].drop()
    print(f"dropped {len(WIPE_COLLECTIONS)} colecciones (preservada '{PROTECTED}')")
    print("\nsembrando:")
    for coll, docs in data.items():
        if docs:
            await _write_chunked(db[coll], docs)
        print(f"  {coll:<20} {len(docs)}")
    await ensure_indexes(db)
    after = await db[PROTECTED].count_documents({})
    print(f"\nindexes ensured · column_catalog before={before} after={after} "
          f"[{'OK' if before == after else '⚠️ MISMATCH'}]")
    await disconnect()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed sintético tipo-DDV")
    ap.add_argument("--xml", default=str(XML_DEFAULT))
    ap.add_argument("--apply", action="store_true", help="BORRA la BD y re-siembra (sin esto: dry-run)")
    args = ap.parse_args(argv)

    print(f"parseando {args.xml} …")
    model = ep.parse(args.xml)
    data = Builder(model).build()
    summarize(data)
    if not args.apply:
        print("\nDRY-RUN (sin --apply no se escribe nada).")
        return 0
    asyncio.run(_apply(data))
    print("\nSiguiente: python -m scripts.audit_data_consistency  y  scripts/arrange_all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
