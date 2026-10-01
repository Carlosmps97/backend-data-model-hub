"""Doc 105 (D1b): los estándares se escriben SÓLO por Data Standards.

El CRUD directo de dominios, glosario y naming, `propagate` (re-tipa columnas
publicadas) y `rephysicalize` (re-deriva los físicos de todo el proyecto)
escribían fuera de `standards_versions`: sin versión, sin historial y sin
rollback. El front ya no los usa. Las rutas siguen declaradas y responden 409
ANTES de cualquier efecto: después del permiso (sin él sigue siendo 403) y
antes de la auditoría (un intento rechazado no es una acción). Los services
siguen: el apply versionado reutiliza la cascada, la validación de términos y
el re-derivado."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.core.identity import Principal, current_principal
from app.features.auth.deps import require_permission

STANDARDS_VERSIONED = ("Standards changes go through Data Standards (Save & apply) so they keep "
                       "a version and can be rolled back.")

_can_edit = require_permission("standards.edit")


def _closed() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STANDARDS_VERSIONED)


async def standards_direct_write(user: dict = Depends(_can_edit)) -> None:
    """Dependencia de RUTA: escritura directa de estándares cerrada."""
    raise _closed()


async def standards_read_only(request: Request, principal: Principal = Depends(current_principal)) -> None:
    """Dependencia de ROUTER para los que ya sólo leen (dominios, naming): leer
    exige sesión; toda escritura exige el permiso y responde 409."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    await _can_edit(principal)
    raise _closed()
