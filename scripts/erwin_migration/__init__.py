"""Migración de modelos Erwin (.xml nativo) → Data Model Hub.

Módulos:
  erwin_parser  — parseo streaming del XML a un modelo neutro (dataclasses)
  policies      — decisiones de negocio aprobadas por el owner (doc 12 §8)
  quality       — gate de calidad PRE-migración (reporte de incongruencias)
  migrate       — carga a Mongo (colecciones publicadas); exige --apply

Ver README.md de esta carpeta para uso y decisiones.
"""
