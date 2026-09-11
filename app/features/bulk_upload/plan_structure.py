"""Planner — estructura (doc 55 §4.1 / §6 · doc 78 §3.5 · doc 87 §3.2-3.5):
esquemas, proyectos, carpetas (SPACE raíz, SUBJECT hija) y canvases. Reuso por
nombre normalizado; alta con id nuevo salvo que el perfil exija que el objeto
EXISTA (`mustExist`: error corta, warning crea y avisa); canvases con
`tableIds`/`viewIds` + posición provisional (el arrange del front la reemplaza).

Doc 87: cuando el proyecto tiene una capa de «proyectos internos» (carpetas
raíz con subcarpetas — DDV: CPYBCA / Otros), SPACE / SUBJECT / DIAGRAMA cuelgan
de la carpeta destino elegida (`base_folder_id`); con una sola raíz así se
toma sola; sin capa, de la raíz del proyecto (como siempre). Puro.
"""
from __future__ import annotations

from typing import Callable

from app.features.projects.models import SubjectAreaDoc
from app.features.schemas.service import NAME_RE as SCHEMA_NAME_RE

from .normalize import clean_text, norm_name
from .report import SHEET_TABLES, ReportBuilder

# Grilla provisional (misma del kit Erwin): 4 por fila, paso 480×360, debajo
# del extremo actual del canvas.
_GRID_COLS, _GRID_DX, _GRID_DY, _GRID_X0 = 4, 480, 360, 40
# Cabeceras de la plantilla histórica (doc 55): fallback del `column` de las
# incidencias cuando el perfil no mapea ese campo.
_DEFAULT_HEADERS = {"project": "PROJECT", "space": "SPACE", "subject": "SUBJECT", "diagram": "DIAGRAMA"}
_KIND_LABEL = {"space": ("Space", "spaces"), "subject": ("Subject", "subjects"), "diagram": ("Diagram", "diagrams")}


def _upsert(collection: str, entity_id: str, payload: dict) -> dict:
    return {"collection": collection, "entityId": entity_id, "op": "upsert", "payload": payload}


def zero_counts() -> dict[str, int]:
    return {"create": 0, "update": 0, "unchanged": 0}


# ── Proyecto destino (doc 87 §3.5, decisión D1) ────────────────────────────
TARGET_REQUIRED = "target-folder-required"
TARGET_INVALID = "target-folder-invalid"


def upload_targets(folders: list[dict], canvases: list[dict] | None = None) -> dict:
    """Capa de «proyectos internos» del proyecto: las carpetas RAÍZ que
    contienen subcarpetas. `mode` = `none` (no hay capa: todo cuelga de la raíz),
    `auto` (una sola candidata: se toma sin preguntar), `choose` (2+: el popup
    exige elegir). Cada candidata trae sus conteos para el selector. Puro."""
    kids: dict[str, int] = {}
    for f in folders:
        parent = f.get("parentFolderId")
        if parent:
            kids[str(parent)] = kids.get(str(parent), 0) + 1
    canv: dict[str, int] = {}
    for c in canvases or []:
        fid = c.get("folderId")
        if fid:
            canv[str(fid)] = canv.get(str(fid), 0) + 1
    roots = [f for f in folders if not f.get("parentFolderId") and kids.get(str(f.get("id")), 0) > 0]
    roots.sort(key=lambda f: (int(f.get("order") or 0), norm_name(f.get("name"))))
    candidates = [{"id": str(f["id"]), "name": clean_text(f.get("name")),
                   "folders": kids.get(str(f["id"]), 0), "canvases": canv.get(str(f["id"]), 0)} for f in roots]
    mode = "none" if not candidates else ("auto" if len(candidates) == 1 else "choose")
    return {"mode": mode, "candidates": candidates}


def resolve_base_folder(targets: dict, target_folder_id: str | None) -> tuple[str | None, str | None]:
    """(carpeta base, código de error). `none` ⇒ raíz (un id enviado es
    inválido); `auto` ⇒ la única candidata (un id enviado debe coincidir);
    `choose` ⇒ el id es obligatorio y debe ser candidata. Puro."""
    ids = [c["id"] for c in targets.get("candidates") or []]
    wanted = clean_text(target_folder_id) or None
    if targets.get("mode") == "none":
        return None, (TARGET_INVALID if wanted else None)
    if targets.get("mode") == "auto":
        return (ids[0], None) if wanted in (None, ids[0]) else (None, TARGET_INVALID)
    if wanted is None:
        return None, TARGET_REQUIRED
    return (wanted, None) if wanted in ids else (None, TARGET_INVALID)


def target_error_message(code: str, targets: dict) -> str:
    names = ", ".join(c["name"] for c in targets.get("candidates") or [])
    if code == TARGET_REQUIRED:
        return f"This project has internal project folders ({names}): pick the target project for the upload."
    return "The target project folder doesn't exist anymore (it may have been deleted or moved): pick it again."


class SchemaResolver:
    """`ESQUEMA` → nombre canónico del esquema (grafía de la plataforma),
    creándolo si no existe. Un esquema de VISTAS no puede alojar tablas."""

    def __init__(self, ctx, rb: ReportBuilder, new_id: Callable[[], str]) -> None:
        self._rb = rb
        self._new_id = new_id
        self._project_id = ctx.project_id
        self._by_name: dict[str, dict] = {}
        for s in ctx.schemas:
            self._by_name.setdefault(norm_name(s.get("name")), s)
        self._created: dict[str, dict] = {}
        self._used: set[str] = set()

    def resolve(self, name: str, row: int, must_exist: str | None = None, column: str = "ESQUEMA") -> str | None:
        raw = clean_text(name)
        key = norm_name(raw)
        hit = self._by_name.get(key) or self._created.get(key)
        if hit is not None:
            if hit.get("kind") == "views":
                self._rb.error(SHEET_TABLES, "schema-kind",
                               f"Schema '{hit['name']}' holds views; a table can't be created in it.",
                               row=row, column=column)
                return None
            self._used.add(key)
            return str(hit["name"])
        if must_exist:
            self._rb.issue(must_exist, SHEET_TABLES, "must-exist",
                           f"Schema '{raw}' doesn't exist in the project and the profile requires existing schemas.",
                           row=row, column=column)
            if must_exist == "error":
                return None
        if not SCHEMA_NAME_RE.fullmatch(raw):
            self._rb.error(SHEET_TABLES, "invalid-schema-name",
                           f"Schema name '{raw}' is invalid: letters, digits and underscore, starting with a letter.",
                           row=row, column=column)
            return None
        doc = {"id": self._new_id(), "projectId": self._project_id, "name": raw,
               "description": None, "kind": "tables"}
        self._created[key] = doc
        return raw

    def resolve_view_schema(self, base: str, row: int | None, column: str = "ESQUEMA") -> str | None:
        """Esquema de VISTAS de una tabla (doc 87 §3.2): `<base>_vu` por nombre
        CI (grafía exacta si existe; `_vu`/`_VU` según el case de la base al
        crear, `kind: views`). Uno catalogado como de TABLAS es un error de la
        fila (dato inconsistente), mismo criterio que `schema-kind`."""
        raw = clean_text(base)
        if not raw:
            return None
        wanted = raw + ("_vu" if raw == raw.lower() else "_VU")
        key = norm_name(wanted)
        hit = self._by_name.get(key) or self._created.get(key)
        if hit is not None:
            if hit.get("kind") == "tables":
                self._rb.error(SHEET_TABLES, "view-schema-kind",
                               f"Schema '{hit['name']}' holds tables; the views of this table can't be created there.",
                               row=row, column=column)
                return None
            self._used.add(key)
            return str(hit["name"])
        doc = {"id": self._new_id(), "projectId": self._project_id, "name": wanted,
               "description": None, "kind": "views"}
        self._created[key] = doc
        return wanted

    def changes(self) -> list[dict]:
        return [_upsert("schemas", d["id"], dict(d)) for d in self._created.values()]

    def counts(self) -> dict[str, int]:
        return {"create": len(self._created), "update": 0, "unchanged": len(self._used)}


class StructurePlanner:
    """Coloca cada tabla planificada en su PROJECT / SPACE / SUBJECT / DIAGRAMA."""

    def __init__(self, ctx, rb: ReportBuilder, new_id: Callable[[], str],
                 headers: dict[str, str] | None = None, must_exist: dict[str, str] | None = None,
                 base_folder_id: str | None = None) -> None:
        self._rb = rb
        self._new_id = new_id
        # Doc 87 §3.5: carpeta «proyecto interno» bajo la que cuelga TODO lo
        # que la hoja ubica (None = raíz del proyecto, comportamiento clásico).
        self._base = base_folder_id or None
        # Doc 75: el proyecto es el del changeset; el workbook sólo puede
        # nombrarlo (columna PROJECT) — nunca crear otro ni referir a otro.
        self._project_id = ctx.project_id
        self._project_name = ctx.project_name
        self._h = {**_DEFAULT_HEADERS, **{k: v for k, v in (headers or {}).items() if v}}
        self._must = dict(must_exist or {})
        self._folders: dict[tuple[str, str, str], dict] = {}
        self._siblings: dict[tuple[str, str], int] = {}
        for f in ctx.folders:
            key = (str(f.get("projectId")), str(f.get("parentFolderId") or ""), norm_name(f.get("name")))
            self._folders.setdefault(key, f)
            self._siblings[key[:2]] = self._siblings.get(key[:2], 0) + 1
        self._canvases: dict[tuple[str, str, str], dict] = {}
        for c in ctx.canvases:
            key = (str(c.get("projectId")), str(c.get("folderId") or ""), norm_name(c.get("name")))
            self._canvases.setdefault(key, {"doc": c, "new": False, "added": [], "added_views": []})
        self._new_folders: list[dict] = []
        self._used_folders: set[str] = set()
        self._touched_canvases: dict[str, dict] = {}
        # Canvas resuelto por fila de tabla (tp.id) — `place_view` agrega las
        # vistas `_vu` de la tabla al MISMO canvas (doc 87 D4).
        self._placed: dict[str, dict] = {}

    # ── Resolución ─────────────────────────────────────────────────────────
    def _project(self, name: str, row: int) -> str | None:
        """El PROJECT de la hoja debe ser el del changeset (doc 75 D8/D12)."""
        if norm_name(name) != norm_name(self._project_name):
            self._rb.error(SHEET_TABLES, "project-mismatch",
                           f"PROJECT '{clean_text(name)}' doesn't match the version's project "
                           f"'{self._project_name}'.", row=row, column=self._h["project"])
            return None
        return self._project_id

    def _missing(self, kind: str, name: str, row: int) -> bool:
        """Aplica `mustExist` de `kind` a un objeto que NO existe: True = cortar."""
        sev = self._must.get(kind)
        if not sev:
            return False
        label, plural = _KIND_LABEL[kind]
        self._rb.issue(sev, SHEET_TABLES, "must-exist",
                       f"{label} '{clean_text(name)}' doesn't exist and the profile requires existing {plural}.",
                       row=row, column=self._h[kind])
        return sev == "error"

    def _folder(self, project_id: str, parent_id: str | None, name: str, kind: str, row: int) -> str | None:
        key = (project_id, parent_id or "", norm_name(name))
        hit = self._folders.get(key)
        if hit is None:
            if self._missing(kind, name, row):
                return None
            order = self._siblings.get(key[:2], 0)
            hit = {"id": self._new_id(), "projectId": project_id, "parentFolderId": parent_id,
                   "name": clean_text(name), "order": order}
            self._folders[key] = hit
            self._siblings[key[:2]] = order + 1
            self._new_folders.append(hit)
        else:
            self._used_folders.add(hit["id"])
        return str(hit["id"])

    def _canvas(self, project_id: str, folder_id: str | None, name: str, row: int) -> dict | None:
        key = (project_id, folder_id or "", norm_name(name))
        state = self._canvases.get(key)
        if state is None:
            if self._missing("diagram", name, row):
                return None
            doc = {"id": self._new_id(), "projectId": project_id, "folderId": folder_id,
                   "name": clean_text(name), "tableIds": [], "layout": {}, "drawings": [], "udpValues": {}}
            state = {"doc": doc, "new": True, "added": [], "added_views": []}
            self._canvases[key] = state
        self._touched_canvases.setdefault(str(state["doc"]["id"]), state)
        return state

    # ── API ────────────────────────────────────────────────────────────────
    def place(self, tp) -> None:
        """Resuelve la jerarquía de una fila de `Tablas` y anota la membresía
        de la tabla al canvas (si hay DIAGRAMA). Doc 78: sin PROJECT en la
        hoja se asume el proyecto de la versión (la plantilla nueva no lo trae)."""
        c = tp.canvas or {}
        project, space, subject, diagram = (clean_text(c.get(k)) for k in ("project", "space", "subject", "diagram"))
        if not (project or space or subject or diagram):
            return
        project_id = self._project(project, tp.row) if project else self._project_id
        if project_id is None:
            return
        folder_id: str | None = self._base
        if space:
            folder_id = self._folder(project_id, self._base, space, "space", tp.row)
            if folder_id is None:
                return
        if subject:
            folder_id = self._folder(project_id, folder_id, subject, "subject", tp.row)
            if folder_id is None:
                return
        if not diagram:
            return
        state = self._canvas(project_id, folder_id, diagram, tp.row)
        if state is None:
            return
        self._placed[tp.id] = state
        tid = tp.id
        if tid not in state["doc"].get("tableIds", []) and tid not in state["added"]:
            state["added"].append(tid)

    def place_view(self, tp, view_id: str) -> bool:
        """Agrega una vista NUEVA al canvas donde la fila ubicó su tabla (doc 87
        D4). False si la tabla no quedó en ningún canvas."""
        state = self._placed.get(tp.id)
        if state is None:
            return False
        if view_id not in (state["doc"].get("viewIds") or []) and view_id not in state["added_views"]:
            state["added_views"].append(view_id)
        return True

    @staticmethod
    def _gained(state: dict) -> bool:
        return bool(state["added"] or state["added_views"])

    def changes(self) -> list[dict]:
        out = [_upsert("folders", f["id"], dict(f)) for f in self._new_folders]
        for state in self._touched_canvases.values():
            if not state["new"] and not self._gained(state):
                continue
            doc = state["doc"]
            layout = {k: dict(v) for k, v in (doc.get("layout") or {}).items()}
            layout.update(_grid_positions(layout, list(state["added"]) + list(state["added_views"])))
            fields = {**doc, "tableIds": list(doc.get("tableIds") or []) + list(state["added"]), "layout": layout}
            # Doc 87 S9: la lista de vistas se materializa solo cuando hay vistas
            # que agregar (canvas nuevo o lista ya existente); un canvas legacy
            # (`viewIds` None) sin vistas nuevas sigue con la regla vieja.
            if state["added_views"]:
                if state["new"] or doc.get("viewIds") is not None:
                    fields["viewIds"] = list(doc.get("viewIds") or []) + list(state["added_views"])
            payload = SubjectAreaDoc.model_validate(fields).model_dump()
            out.append(_upsert("subject_areas", payload["id"], payload))
        return out

    def counts(self) -> dict[str, dict[str, int]]:
        canvases = zero_counts()
        for state in self._touched_canvases.values():
            canvases["create" if state["new"] else ("update" if self._gained(state) else "unchanged")] += 1
        return {
            "projects": {"create": 0, "update": 0, "unchanged": 1 if self._touched_canvases or self._new_folders or self._used_folders else 0},
            "folders": {"create": len(self._new_folders), "update": 0, "unchanged": len(self._used_folders)},
            "canvases": canvases,
        }

    def affected_canvas_ids(self) -> list[str]:
        return [cid for cid, s in self._touched_canvases.items() if s["new"] or self._gained(s)]

    def canvas_warnings(self) -> None:
        """Warnings de canvases EXISTENTES que ganan tablas o vistas (fila =
        None: la membresía puede venir de varias filas)."""
        for state in self._touched_canvases.values():
            if state["new"] or not self._gained(state):
                continue
            parts = []
            if state["added"]:
                parts.append(f"{len(state['added'])} table(s)")
            if state["added_views"]:
                parts.append(f"{len(state['added_views'])} view(s)")
            self._rb.warning(SHEET_TABLES, "existing-canvas",
                             f"Canvas '{state['doc'].get('name')}' already exists — {' and '.join(parts)} will be added to it.",
                             column=self._h["diagram"])


def _grid_positions(layout: dict, table_ids: list[str]) -> dict[str, dict[str, float]]:
    """Posiciones provisionales en grilla debajo del extremo actual del canvas."""
    max_y = max((float(v.get("y") or 0) for v in layout.values()), default=-_GRID_DY)
    start_y = max_y + _GRID_DY
    return {tid: {"x": float(_GRID_X0 + (i % _GRID_COLS) * _GRID_DX), "y": float(start_y + (i // _GRID_COLS) * _GRID_DY)}
            for i, tid in enumerate(table_ids)}
