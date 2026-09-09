"""Feature `bulk_upload` (doc 55 · doc 78): carga masiva desde Excel DENTRO
de un draft (changeset) según un PERFIL de carga del proyecto — crea o
actualiza carpetas, canvases, esquemas, tablas y columnas.

El front lee el `.xlsx` (SheetJS) y manda TODAS las hojas como grillas crudas
+ `profileId`; acá el perfil (`profiles/`) ubica hojas y cabeceras, mapea
cada columna a un campo o a uno o varios UDP (lógico y/o físico), corre las
reglas y políticas del perfil, y recién el planner valida contra el estado
EFECTIVO del changeset (publicado + overlay) y los Data Standards vivos. Se
arma un plan de cambios (docs COMPLETOS) con un reporte de errores/warnings;
al aplicar se re-valida y se escribe por `changesets.service.add_changes_bulk`
(tandas de 1000). Todo corre como job asíncrono en memoria del proceso
(`jobs.py`); el front hace polling.

Capas: `schemas` (DTOs) · `profiles` (perfiles: modelo, CRUD, built-in,
sugerencias, aplicación y reglas) · `normalize`/`datatypes`/`parser`/`planner`
(puros) · `loader` (lecturas vía repositories) · `jobs` (registro) · `service`
(orquestación) · `router`.

La API pública es `router`.
"""

from .router import router

__all__ = ["router"]
