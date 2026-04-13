"""Punto de entrada principal del sistema Data Modeler Agent.

Implementa la interfaz de chat multi-turno con soporte para:
- Texto libre en lenguaje natural
- Carga de archivos .xlsx
- Refinamiento iterativo del modelo
- Formateo con Rich para output en consola
"""

import asyncio
import json
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Agregar directorio raíz al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.factory import get_chat_client
from src.config import settings
from src.tools.excel_tools import parse_excel_file
from src.workflow.graph import run_modeling_pipeline, _extract_json_from_text

console = Console()


def _extract_file_path(user_input: str) -> str | None:
    """Extrae una ruta a archivo .xlsx del input del usuario."""
    # Buscar patrones de ruta de archivo
    patterns = [
        r'["\']([^"\']+\.xlsx)["\']',  # Entre comillas
        r'(\S+\.xlsx)',                  # Sin comillas
    ]
    for pattern in patterns:
        match = re.search(pattern, user_input, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_engine(user_input: str) -> str | None:
    """Extrae el motor de BD del input del usuario."""
    engine_keywords = {
        "databricks": "databricks_sql",
        "databricks_sql": "databricks_sql",
        "delta": "databricks_sql",
        "cosmos": "cosmosdb",
        "cosmosdb": "cosmosdb",
        "sqlserver": "sqlserver",
        "sql server": "sqlserver",
        "mssql": "sqlserver",
        "postgresql": "postgresql",
        "postgres": "postgresql",
        "mysql": "mysql",
    }
    lower_input = user_input.lower()
    for keyword, engine in engine_keywords.items():
        if keyword in lower_input:
            return engine
    return None


def _extract_relationships(user_input: str) -> list[str]:
    """Extrae relaciones mencionadas en el texto del usuario."""
    relationships = []
    # Buscar patrones como "FK", "foreign key", "relación", etc.
    rel_patterns = [
        r'(?:FK|foreign key|clave foránea|relación|referencia|apunta a)[:\s]+(.+?)(?:\.|$)',
        r'(\w+)\s+tiene\s+(?:FK|foreign key|relación)\s+(?:hacia|con|a)\s+(\w+)',
    ]
    for pattern in rel_patterns:
        matches = re.finditer(pattern, user_input, re.IGNORECASE)
        for match in matches:
            relationships.append(match.group(0).strip())

    return relationships


def _display_results(result: dict) -> None:
    """Muestra los resultados del pipeline de forma estructurada con Rich."""
    engine = result.get("engine", "desconocido")

    console.print()
    console.print(Panel.fit(
        f"[bold green]Motor de BD:[/bold green] {engine}",
        title="[bold cyan]📊 Resultado del Modelamiento de Datos[/bold cyan]",
        border_style="cyan",
    ))

    # Intentar parsear el modelo generado
    generated_model = result.get("generated_model", "")
    qa_validation = result.get("qa_validation", "")

    # Mostrar modelo generado
    if generated_model:
        try:
            clean = _extract_json_from_text(generated_model) if isinstance(generated_model, str) else generated_model
            model_data = json.loads(clean) if isinstance(clean, str) else clean
            _display_model(model_data)
        except (json.JSONDecodeError, TypeError):
            console.print(Panel(
                str(generated_model)[:3000],
                title="[bold yellow]Modelo Generado[/bold yellow]",
                border_style="yellow",
            ))

    # Mostrar validación QA
    if qa_validation:
        try:
            clean_qa = _extract_json_from_text(qa_validation) if isinstance(qa_validation, str) else qa_validation
            qa_data = json.loads(clean_qa) if isinstance(clean_qa, str) else clean_qa
            _display_qa_report(qa_data)
        except (json.JSONDecodeError, TypeError):
            console.print(Panel(
                str(qa_validation)[:3000],
                title="[bold yellow]Validación QA[/bold yellow]",
                border_style="yellow",
            ))


def _display_model(model_data: dict) -> None:
    """Muestra las tablas del modelo generado."""
    tables = model_data.get("tables", [])
    summary = model_data.get("summary", "")

    if summary:
        console.print(Panel(summary, title="[bold]Resumen del Modelo[/bold]", border_style="green"))

    for table in tables:
        tname = table.get("table_name", "sin_nombre")
        columns = table.get("columns", [])

        # Tabla de columnas
        col_table = Table(title=f"[bold]{tname}[/bold]", show_lines=True)
        col_table.add_column("Columna", style="cyan", min_width=20)
        col_table.add_column("Tipo", style="green", min_width=15)
        col_table.add_column("Nullable", style="yellow", min_width=8)
        col_table.add_column("PK", style="red", min_width=4)
        col_table.add_column("FK", style="magenta", min_width=4)
        col_table.add_column("Definición", style="white", min_width=30)

        for col in columns:
            col_table.add_row(
                col.get("column_name", ""),
                col.get("data_type", ""),
                "✓" if col.get("is_nullable", True) else "✗",
                "PK" if col.get("is_primary_key", False) else "",
                col.get("fk_reference", "") if col.get("is_foreign_key", False) else "",
                col.get("functional_definition", "")[:50],
            )

        console.print(col_table)
        console.print()

        # DDL
        ddl = table.get("ddl", "")
        if ddl:
            console.print(Panel(
                f"```sql\n{ddl}\n```" if not ddl.startswith("```") else ddl,
                title=f"[bold blue]DDL — {tname}[/bold blue]",
                border_style="blue",
            ))

    # Relaciones
    relationships = model_data.get("relationships", [])
    if relationships:
        rel_text = "\n".join(f"  → {r}" for r in relationships)
        console.print(Panel(rel_text, title="[bold magenta]Relaciones[/bold magenta]", border_style="magenta"))


def _display_qa_report(qa_data: dict) -> None:
    """Muestra el reporte de QA."""
    qa_report = qa_data.get("qa_report", qa_data)

    # Score
    score = qa_report.get("quality_score", 0)
    score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
    console.print(Panel(
        f"[bold {score_color}]{score}/100[/bold {score_color}]",
        title="[bold]Score de Calidad[/bold]",
        border_style=score_color,
    ))

    # Summary
    summary = qa_report.get("summary", "")
    if summary:
        console.print(Panel(summary, title="[bold]Resumen QA[/bold]", border_style="cyan"))

    # Columnas estandarizadas
    std_cols = qa_report.get("standardized_columns", [])
    if std_cols:
        std_table = Table(title="[bold]Columnas Estandarizadas[/bold]", show_lines=True)
        std_table.add_column("Tabla", style="cyan")
        std_table.add_column("Original", style="red")
        std_table.add_column("→", style="white")
        std_table.add_column("Estandarizado", style="green")
        std_table.add_column("Razón", style="yellow")

        for item in std_cols:
            std_table.add_row(
                item.get("table_name", ""),
                item.get("original_name", ""),
                "→",
                item.get("standardized_name", ""),
                item.get("reason", "")[:40],
            )
        console.print(std_table)

    # Violaciones
    violations = qa_report.get("guideline_violations", [])
    if violations:
        viol_table = Table(title="[bold]Violaciones Corregidas[/bold]", show_lines=True)
        viol_table.add_column("Tabla", style="cyan")
        viol_table.add_column("Columna", style="yellow")
        viol_table.add_column("Violación", style="red")
        viol_table.add_column("Corrección", style="green")

        for item in violations:
            viol_table.add_row(
                item.get("table_name", ""),
                item.get("column_name", ""),
                item.get("violation", "")[:40],
                item.get("correction_applied", "")[:40],
            )
        console.print(viol_table)

    # Nuevas entradas al catálogo
    new_entries = qa_report.get("new_catalog_entries", [])
    if new_entries:
        new_table = Table(title="[bold]Nuevas Columnas en Catálogo[/bold]", show_lines=True)
        new_table.add_column("Columna", style="green")
        new_table.add_column("Tipo", style="cyan")
        new_table.add_column("Tabla", style="yellow")
        new_table.add_column("Definición", style="white")

        for item in new_entries:
            new_table.add_row(
                item.get("column_name", ""),
                item.get("data_type", ""),
                item.get("table_name", ""),
                item.get("functional_definition", "")[:40],
            )
        console.print(new_table)

    # DDLs corregidos
    tables = qa_data.get("tables", [])
    for table in tables:
        ddl = table.get("ddl", "")
        if ddl:
            console.print(Panel(
                ddl,
                title=f"[bold blue]DDL Corregido — {table.get('table_name', '')}[/bold blue]",
                border_style="blue",
            ))


def _show_banner() -> None:
    """Muestra el banner de bienvenida."""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║           🏗️  DATA MODELER AGENT  🏗️                        ║
║     Sistema Multi-Agente de Modelamiento de Datos           ║
║     Microsoft Agent Framework · Python 3.12                 ║
╠══════════════════════════════════════════════════════════════╣
║  Comandos:                                                  ║
║    • Escribe tu solicitud en lenguaje natural                ║
║    • Incluye una ruta .xlsx para cargar tablas               ║
║    • 'exit' o 'quit' para salir                              ║
║    • 'engines' para ver motores soportados                   ║
║    • 'help' para ver esta ayuda                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    console.print(Text(banner, style="bold cyan"))
    console.print(f"  Motor por defecto: [bold green]{settings.DEFAULT_DB_ENGINE}[/bold green]")
    console.print(f"  Modelo LLM: [bold green]{settings.FOUNDRY_MODEL}[/bold green]")
    console.print()


async def main() -> None:
    """Loop principal del Data Modeler Agent con soporte multi-turno."""
    _show_banner()

    # Crear cliente compartido
    try:
        client = get_chat_client()
        console.print("[green]✓ Conexión con Azure AI Foundry establecida[/green]\n")
    except Exception as e:
        console.print(f"[red]✗ Error al conectar con Azure AI Foundry: {e}[/red]")
        return

    while True:
        try:
            console.print("[bold cyan]┌─ Data Modeler[/bold cyan]")
            user_input = console.input("[bold cyan]└─▶ [/bold cyan]").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "salir"):
                console.print("[yellow]¡Hasta luego! 👋[/yellow]")
                break

            if user_input.lower() == "help":
                _show_banner()
                continue

            if user_input.lower() == "engines":
                console.print("[bold]Motores soportados:[/bold]")
                for eng in settings.SUPPORTED_ENGINES:
                    console.print(f"  • [green]{eng}[/green]")
                console.print()
                continue

            # Extraer información del input
            file_path = _extract_file_path(user_input)
            engine = _extract_engine(user_input) or settings.DEFAULT_DB_ENGINE
            relationships = _extract_relationships(user_input)

            # Preparar datos de input
            input_data = {
                "user_text": user_input,
                "tables": [],
                "target_engine": engine,
                "relationships": relationships,
            }

            # Parsear Excel si se proporcionó
            if file_path:
                resolved_path = Path(file_path)
                if not resolved_path.is_absolute():
                    resolved_path = settings.PROJECT_ROOT / file_path

                if resolved_path.exists():
                    console.print(f"[yellow]📄 Procesando archivo: {resolved_path}[/yellow]")
                    excel_result = parse_excel_file.func(str(resolved_path))
                    try:
                        excel_data = json.loads(excel_result)
                        if "tables" in excel_data:
                            input_data["tables"] = excel_data["tables"]
                            console.print(
                                f"[green]✓ {excel_data.get('total_tables', 0)} tabla(s) encontrada(s)[/green]"
                            )
                        elif "error" in excel_data:
                            console.print(f"[red]✗ Error en Excel: {excel_data['error']}[/red]")
                            continue
                    except json.JSONDecodeError:
                        console.print(f"[red]✗ Error al parsear resultado del Excel[/red]")
                        continue
                else:
                    console.print(f"[red]✗ Archivo no encontrado: {resolved_path}[/red]")
                    continue

            # Ejecutar pipeline
            console.print()
            with console.status("[bold green]🔄 Ejecutando pipeline de modelamiento...[/bold green]", spinner="dots"):
                result = await run_modeling_pipeline(client, input_data)

            # Mostrar resultados
            if "error" in result:
                console.print(f"[red]✗ Error: {result['error']}[/red]")
            else:
                _display_results(result)

            console.print()
            console.print("[dim]Puedes refinar el modelo con instrucciones adicionales.[/dim]\n")

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrumpido por el usuario.[/yellow]")
            break
        except Exception as e:
            console.print(f"[red]✗ Error inesperado: {e}[/red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
