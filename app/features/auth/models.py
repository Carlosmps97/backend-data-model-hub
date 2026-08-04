"""Modelos de la auth propia: `users` y `roles` (RBAC data-driven).

Decisión 2026-07-04: el app gestiona identidad, roles y permisos en la BD
Lakebase (login usuario/contraseña en todos los entornos). Los permisos son data-driven
(colección `roles` con una matriz editable desde el módulo Admin), no
hardcodeados — el mock de Admin trae "Nuevo rol" y toggles por celda.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG

# ── Catálogo de permisos (keys de la matriz Admin) ────────────────────────
# 1:1 con las filas de la pantalla 14 + la fila nueva de Data Standards (R10).
PERMISSIONS: tuple[str, ...] = (
    "model.view",       # Ver modelo / Canvas
    "model.edit",       # Crear / editar tablas (working copy)
    "review.decide",    # Aprobar / rechazar solicitudes
    "publish",          # Publicar a producción
    "rollback",         # Revertir a una versión publicada (Model + Data Standards)
    "export",           # Exportar DDL / metadata
    "standards.edit",   # Editar Data Standards (UDP / Parent Domains)
    "admin.manage",     # Administrar usuarios y permisos
)

# Niveles de acceso legibles (columna "Nivel de acceso" del mock) — derivados.
ACCESS_LEVELS = ("full", "edit", "read")


class RoleDoc(BaseModel):
    """Un rol y su matriz de permisos. `_id` == `key` (slug estable)."""

    model_config = DOC_CONFIG

    id: str                       # key: administrador | modelador | revisor | lector | …
    name: str                     # etiqueta visible
    description: str | None = None
    # Matriz: permiso → permitido. Se valida contra PERMISSIONS al escribir
    # (el modelo acepta cualquier bool; el service filtra keys desconocidas).
    permissions: dict[str, bool] = Field(default_factory=dict)


class UserDoc(BaseModel):
    """Un usuario del app. `_id` == `username` (clave del actor, == `sub` del
    token, == lo que se compara contra `reviewers[]`).

    `email`/`name`/`role` tienen default: la colección `users` puede contener
    documentos legacy (de backends previos) sin esos campos — el modelo los
    tolera (aparecen con valores vacíos, el admin puede limpiarlos) en vez de
    romper el listado. Sin `role` (o vacío) el usuario no tiene permisos."""

    model_config = DOC_CONFIG

    id: str                       # username
    email: str = ""
    name: str = ""
    role: str = ""                # key de RoleDoc
    # Alcance por proyecto: [] o ausencia = todos (admin); si tiene ids, se
    # acota (enforcement fino incremental — ver doc 03 §10).
    projectIds: list[str] = Field(default_factory=list)
    status: str = "active"        # active | invited | disabled
    initials: str | None = None
    # NUNCA se expone al cliente (el service hace exclude en las respuestas).
    passwordHash: str | None = None
