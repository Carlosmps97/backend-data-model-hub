"""Snapshot de una versión PUBLICADA como changeset VIRTUAL (doc 70 §3).

El versionado es un ledger (`changeset_changes`: `payload` = imagen posterior,
`before` = imagen previa capturada en el publish, doc 16 §5d). No hay snapshot
materializado por publicación, pero el estado exacto del modelo en la versión
V es reconstruible: producción de hoy + el INVERSO de todas las versiones
publicadas DESPUÉS de V (misma composición que `service.rollback`, doc 27).

Ese inverso se expone como un changeset virtual `asof:<versionId>`: todos los
lectores changeset-aware (diagrama, effective, links, explorer, buscadores)
pasan por `repository.changes_map`, que despacha acá — así el canvas, el
explorer y el DBX muestran la versión completa sin tocar ningún endpoint.
Sólo lectura: el id virtual no existe como doc, así que `add_change` falla y
el front lo abre siempre en modo visor.

Límite honesto: una versión posterior publicada ANTES de la captura de
imágenes previas no tiene «antes» ⇒ `AsOfUnavailable("no-before")` — nunca se
inventa un estado (mismo criterio que rollback y compare).
"""
from __future__ import annotations

PREFIX = "asof:"

_MESSAGES = {
    "not-found": "Version not found.",
    "not-applied": "Only published versions can be opened as a snapshot.",
    "no-before": ("A version published after this one predates change-history capture "
                  "(its before-images were not captured), so this snapshot can't be reconstructed."),
}


class AsOfUnavailable(Exception):
    """El snapshot no se puede armar. `reason` ∈ {not-found, not-applied, no-before}."""

    def __init__(self, reason: str, version_id: str, missing: list[str] | None = None):
        self.reason = reason
        self.version_id = version_id
        self.missing = list(missing or [])
        super().__init__(_MESSAGES.get(reason, reason))


def asof_id(version_id: str) -> str:
    """Id del changeset virtual de la versión."""
    return f"{PREFIX}{version_id}"


def asof_version_id(cs_id: str | None) -> str | None:
    """`asof:<versionId>` → versionId; cualquier otro id → None. Puro."""
    if not isinstance(cs_id, str) or not cs_id.startswith(PREFIX):
        return None
    return cs_id[len(PREFIX):] or None


def compose_inverse(ledgers_latest_first: list[dict]) -> tuple[dict, list[str]]:
    """Compone el INVERSO de los ledgers de las versiones publicadas después de
    la objetivo, recibidos del MÁS RECIENTE al más viejo (como `applied_after`).
    Puro.

    Por entidad gana el inverso de la versión MÁS CERCANA a la objetivo (la
    última iterada): su imagen previa es el estado tal como quedó en la
    objetivo. `before=None` (no existía) → delete; `before=doc` → upsert con el
    doc previo. Devuelve ({collection: {entityId: {op, payload?}}}, faltantes)
    — `faltantes` = `coll/eid` sin `beforeAt` (irreconstruible)."""
    out: dict[str, dict[str, dict]] = {}
    missing: list[str] = []
    for changes in ledgers_latest_first:
        for coll, per in (changes or {}).items():
            for eid, ch in per.items():
                if not ch.get("beforeAt"):
                    missing.append(f"{coll}/{eid}")
                    continue
                before = ch.get("before")
                if before is None:
                    out.setdefault(coll, {})[eid] = {"op": "delete"}
                else:
                    out.setdefault(coll, {})[eid] = {
                        "op": "upsert",
                        "payload": {k: v for k, v in before.items() if k != "id"},
                    }
    return out, missing
