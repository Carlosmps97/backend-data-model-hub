"""Instrucciones (system prompts) para cada agente del sistema.

Carga las instrucciones desde archivos .prompty para facilitar su edición
sin modificar código fuente.
"""

from pathlib import Path


def _load_prompty(filename: str) -> str:
    """Carga el contenido de un archivo .prompty desde la carpeta prompts/."""
    prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"
    filepath = prompts_dir / filename
    
    if not filepath.exists():
        raise FileNotFoundError(f"Archivo .prompty no encontrado: {filepath}")
    
    content = filepath.read_text(encoding="utf-8")
    
    # Extraer solo el contenido después del frontmatter YAML
    if "---" in content:
        parts = content.split("---")
        if len(parts) >= 3:
            # parts[0] = vacío, parts[1] = frontmatter, parts[2+] = contenido
            return "---".join(parts[2:]).strip()
    
    return content.strip()


# Cargar instrucciones desde archivos .prompty
EXECUTOR_AGENT_INSTRUCTIONS = _load_prompty("executor.prompty")
QA_VALIDATOR_INSTRUCTIONS = _load_prompty("qa_validator.prompty")
CONVERSATIONAL_AGENT_INSTRUCTIONS = _load_prompty("conversational.prompty")
