"""Procesamiento determinista del modelo (post-LLM).

Todo lo que NO requiere razonamiento del LLM vive acá:
- Inyección de audit columns desde guidelines.
- Generación de DDL por motor desde un template.
- Generación de exportes Markdown.

El LLM solo decide los nombres físicos de tabla y columna a partir de
las definiciones funcionales del usuario y los guidelines. El resto es
ensamblado mecánico.
"""
