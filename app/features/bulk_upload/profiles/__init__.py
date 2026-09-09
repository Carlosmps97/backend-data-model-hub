"""Perfiles de carga (doc 78): configuración por proyecto de cómo se interpreta
un workbook — hojas, fila de cabecera, mapeo cabecera → campo / UDP (lógico y/o
físico), reglas por columna y políticas. `models` (forma + validación pura) ·
`repository` (CRUD scoped) · `service` · `router` · `builtin` (perfil de
`Plantilla.xlsx`) · `suggest` (mapeo propuesto por nombre) · `apply` (workbook
crudo + perfil → filas tipadas) · `rules` (motor de reglas).

La API pública es `router` (se exporta desde `router.py`; este paquete no lo
re-importa para que `models`/`builtin` sigan siendo importables sin FastAPI)."""
