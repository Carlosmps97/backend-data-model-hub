#!/usr/bin/env python3
"""Script standalone para conversar con el Data Modeler Agent.

Uso:
    python chat.py                    # Inicia chat interactivo
    python chat.py --model "Crea una tabla de productos" --engine postgresql

Este script está fuera de src/ para facilitar su ejecución directa.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Agregar src al path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.agents.factory import get_chat_client
from src.config import settings
from src.main import _display_results, _extract_engine, _extract_file_path, _extract_relationships, _show_banner
from src.tools.excel_tools import parse_excel_file
from src.workflow.graph import run_modeling_pipeline

console = Console()


async def run_single_request(user_text: str, engine: str | None = None) -> None:
    """Ejecuta una única solicitud y muestra resultados."""
    try:
        client = get_chat_client()
        console.print("[green]✓ Conexión con Azure AI Foundry establecida[/green]\n")
    except Exception as e:
        console.print(f"[red]✗ Error al conectar: {e}[/red]")
        return

    # Detectar engine si no se proporcionó
    if engine is None:
        engine = _extract_engine(user_text) or settings.DEFAULT_DB_ENGINE

    # Detectar archivo Excel
    file_path = _extract_file_path(user_text)
    relationships = _extract_relationships(user_text)

    # Preparar input
    input_data = {
        "user_text": user_text,
        "tables": [],
        "target_engine": engine,
        "relationships": relationships,
    }

    # Parsear Excel si existe
    if file_path:
        resolved_path = Path(file_path)
        if not resolved_path.is_absolute():
            resolved_path = settings.PROJECT_ROOT / file_path

        if resolved_path.exists():
            console.print(f"[yellow]📄 Procesando: {resolved_path}[/yellow]")
            excel_result = parse_excel_file.func(str(resolved_path))
            try:
                excel_data = json.loads(excel_result)
                if "tables" in excel_data:
                    input_data["tables"] = excel_data["tables"]
                    console.print(f"[green]✓ {excel_data.get('total_tables', 0)} tabla(s) encontrada(s)[/green]")
            except json.JSONDecodeError:
                console.print("[red]✗ Error al parsear Excel[/red]")
                return
        else:
            console.print(f"[red]✗ Archivo no encontrado: {resolved_path}[/red]")
            return

    # Ejecutar pipeline
    console.print()
    with console.status("[bold green]🔄 Ejecutando pipeline...[/bold green]", spinner="dots"):
        result = await run_modeling_pipeline(client, input_data)

    # Mostrar resultados
    if "error" in result:
        console.print(f"[red]✗ Error: {result['error']}[/red]")
    else:
        _display_results(result)


async def interactive_chat() -> None:
    """Inicia el chat interactivo multi-turno."""
    _show_banner()

    try:
        client = get_chat_client()
        console.print("[green]✓ Conexión con Azure AI Foundry establecida[/green]\n")
    except Exception as e:
        console.print(f"[red]✗ Error al conectar: {e}[/red]")
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


def main():
    """Entry point con argument parsing."""
    parser = argparse.ArgumentParser(
        description="Data Modeler Agent - Chat interactivo o solicitud única",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python chat.py                                    # Chat interactivo
  python chat.py -m "Crea tabla de productos"       # Solicitud única
  python chat.py -m "Modela data/sample.xlsx" -e postgresql
        """,
    )
    parser.add_argument(
        "-m", "--model",
        type=str,
        help="Texto de la solicitud de modelamiento (modo no-interactivo)",
    )
    parser.add_argument(
        "-e", "--engine",
        type=str,
        choices=["databricks_sql", "cosmosdb", "sqlserver", "postgresql", "mysql"],
        help="Motor de base de datos destino",
    )

    args = parser.parse_args()

    if args.model:
        # Modo no-interactivo: una sola solicitud
        asyncio.run(run_single_request(args.model, args.engine))
    else:
        # Modo interactivo
        asyncio.run(interactive_chat())


if __name__ == "__main__":
    main()
