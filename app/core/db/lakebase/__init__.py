"""Adaptador Lakebase Postgres (doc 28).

Emula la superficie Motor/PyMongo que usa este backend sobre tablas
`(id text PRIMARY KEY, doc jsonb)` en Databricks Lakebase Postgres, con
credenciales OAuth rotativas. El seam sigue siendo `app/core/db/client.py`;
los repositorios no cambian.
"""

from app.core.db.lakebase.collection import LakebaseDatabase, PgCollection
from app.core.db.lakebase.pool import create_pool

__all__ = ["LakebaseDatabase", "PgCollection", "create_pool"]
