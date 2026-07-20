"""Backend de plataforma (backend-data-model-hub) — monolito modular.

`app.core`     infraestructura compartida (config, logging, db, security, api).
`app.features` un paquete por feature (vertical slice): router · service ·
               repository · schemas · models. Cada feature se importa SOLO por
               su `__init__` (API pública). Ver `doc/feature-architecture.md`.
"""
