"""API pública de la feature `metadata`.

Importar desde `app.features.metadata`; nunca alcanzar submódulos internos.
"""

from app.features.metadata.models import SemanticTagDoc, SemanticTypeDoc, UdpDoc

__all__ = ["SemanticTagDoc", "SemanticTypeDoc", "UdpDoc"]
