"""Instrucciones (system prompt) para el agente executor.

El sistema solo tiene un agente: el ExecutorAgent. Recibe una tabla y
devuelve sus nombres físicos según los lineamientos. Sin QA, sin
agente conversacional intermedio.
"""

from pathlib import Path


def _load_prompty(filename: str) -> str:
    """Carga el contenido de un archivo .prompty desde la carpeta prompts/."""
    prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"
    filepath = prompts_dir / filename

    if not filepath.exists():
        raise FileNotFoundError(f"Archivo .prompty no encontrado: {filepath}")

    content = filepath.read_text(encoding="utf-8")

    # Extraer solo el contenido después del frontmatter YAML.
    if "---" in content:
        parts = content.split("---")
        if len(parts) >= 3:
            return "---".join(parts[2:]).strip()
    return content.strip()


EXECUTOR_AGENT_INSTRUCTIONS = _load_prompty("executor.prompty")
