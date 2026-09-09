"""Planner — estructura (doc 55 §4.1 / §6 · doc 78 §3.5): esquemas, proyectos,
carpetas (SPACE raíz, SUBJECT hija) y canvases. Reuso por nombre normalizado;
alta con id nuevo salvo que el perfil exija que el objeto EXISTA (`mustExist`:
error corta, warning crea y avisa); canvases con `tableIds` + posición
provisional (el arrange del front la reemplaza). Puro.
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

    def changes(self) -> list[dict]:
        return [_upsert("schemas", d["id"], dict(d)) for d in self._created.values()]

    def counts(self) -> dict[str, int]:
        return {"create": len(self._created), "update": 0, "unchanged": len(self._used)}


class StructurePlanner:
    """Coloca cada tabla planificada en su PROJECT / SPACE / SUBJECT / DIAGRAMA."""

    def __init__(self, ctx, rb: ReportBuilder, new_id: Callable[[], str],
                 headers: dict[str, str] | None = None, must_exist: dict[str, str] | None = None) -> None:
        self._rb = rb
        self._new_id = new_id
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
            self._canvases.setdefault(key, {"doc": c, "new": False, "added": []})
        self._new_folders: list[dict] = []
        self._used_folders: set[str] = set()
        self._touched_canvases: dict[str, dict] = {}

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
            state = {"doc": doc, "new": True, "added": []}
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
        folder_id: str | None = None
        if space:
            folder_id = self._folder(project_id, None, space, "space", tp.row)
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
        tid = tp.id
        if tid not in state["doc"].get("tableIds", []) and tid not in state["added"]:
            state["added"].append(tid)

    def changes(self) -> list[dict]:
        out = [_upsert("folders", f["id"], dict(f)) for f in self._new_folders]
        for state in self._touched_canvases.values():
            if not state["new"] and not state["added"]:
                continue
            doc = state["doc"]
            layout = {k: dict(v) for k, v in (doc.get("layout") or {}).items()}
            layout.update(_grid_positions(layout, state["added"]))
            payload = SubjectAreaDoc.model_validate({
                **doc, "tableIds": list(doc.get("tableIds") or []) + list(state["added"]), "layout": layout,
            }).model_dump()
            out.append(_upsert("subject_areas", payload["id"], payload))
        return out

    def counts(self) -> dict[str, dict[str, int]]:
        canvases = zero_counts()
        for state in self._touched_canvases.values():
            canvases["create" if state["new"] else ("update" if state["added"] else "unchanged")] += 1
        return {
            "projects": {"create": 0, "update": 0, "unchanged": 1 if self._touched_canvases or self._new_folders or self._used_folders else 0},
            "folders": {"create": len(self._new_folders), "update": 0, "unchanged": len(self._used_folders)},
            "canvases": canvases,
        }

    def affected_canvas_ids(self) -> list[str]:
        return [cid for cid, s in self._touched_canvases.items() if s["new"] or s["added"]]

    def canvas_warnings(self) -> None:
        """Warnings de canvases EXISTENTES que ganan tablas (fila = None: la
        membresía puede venir de varias filas)."""
        for state in self._touched_canvases.values():
            if not state["new"] and state["added"]:
                n = len(state["added"])
                self._rb.warning(SHEET_TABLES, "existing-canvas",
                                 f"Canvas '{state['doc'].get('name')}' already exists — {n} table(s) will be added to it.",
                                 column=self._h["diagram"])


def _grid_positions(layout: dict, table_ids: list[str]) -> dict[str, dict[str, float]]:
    """Posiciones provisionales en grilla debajo del extremo actual del canvas."""
    max_y = max((float(v.get("y") or 0) for v in layout.values()), default=-_GRID_DY)
    start_y = max_y + _GRID_DY
    return {tid: {"x": float(_GRID_X0 + (i % _GRID_COLS) * _GRID_DX), "y": float(start_y + (i // _GRID_COLS) * _GRID_DY)}
            for i, tid in enumerate(table_ids)}
