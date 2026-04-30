"""Módulo de workflow — grafo dirigido del pipeline de modelamiento.

El workflow opera por tabla. La concurrencia (varias tablas a la vez)
vive en `api/routes/modeling.py`.
"""

from src.workflow.graph import create_modeling_workflow, run_table_pipeline

__all__ = ["create_modeling_workflow", "run_table_pipeline"]
