"""Feature `bulk_upload` (doc 55): carga masiva desde la plantilla Excel
`dmh-upload-template.xlsx` DENTRO de un draft (changeset) — crea o actualiza
proyectos, carpetas, canvases, esquemas, tablas y columnas.

El front parsea el `.xlsx` (SheetJS) y manda JSON por hoja; acá se valida
contra el estado EFECTIVO del changeset (publicado + overlay) y los Data
Standards vivos, se arma un plan de cambios (docs COMPLETOS) con un reporte
de errores/warnings, y al aplicar se re-valida y se escribe por
`changesets.service.add_changes_bulk` (tandas de 1000). Todo corre como job
asíncrono en memoria del proceso (`jobs.py`); el front hace polling.

Capas: `schemas` (DTOs) · `normalize`/`datatypes`/`parser`/`planner` (puros)
· `loader` (lecturas vía repositories) · `jobs` (registro) · `service`
(orquestación) · `router`.

La API pública es `router`.
"""

from .router import router

__all__ = ["router"]
