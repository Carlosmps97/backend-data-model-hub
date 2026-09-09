"""Área de trabajo del driver de Databricks: limpieza y verificación de las
copias locales de los XML (doc 77 §5).

Vive acá —y no suelto en una celda del notebook— porque es un `rm -rf` sobre una
ruta que sale de la configuración: el guard de dónde se puede borrar tiene que
estar cubierto por tests. El notebook lo importa desde el repo.

Por qué existe: el tmp del driver es ACUMULATIVO entre corridas. Antes se
copiaba sólo lo que faltaba (`if not os.path.isfile(dst)`), así que los restos
de una corrida previa —una carpeta de ADLS que se renombró, un XML que se
sacó— seguían ahí y el orquestador los levantaba como si fueran de este run.
Con la convención del doc 77 cada residuo se vuelve un PROYECTO nuevo. La
limpieza total antes de copiar es lo que lo corta de raíz.
"""
from __future__ import annotations

import os
import shutil

# Dónde puede borrar el notebook (el disco efímero del driver, o /tmp cuando no
# hay `local_disk0`). Todo lo demás es un error, no un `rm -rf` con suerte.
PERMITIDOS: tuple[str, ...] = ("/local_disk0/tmp/erwin", "/tmp/erwin")


def _dentro(path: str, permitidos: tuple[str, ...]) -> bool:
    """¿`path` está ESTRICTAMENTE dentro de alguna raíz permitida? Se compara
    contra la ruta real (resuelve `..` y symlinks) y la raíz misma no cuenta:
    borrarla se llevaría el caché de elkjs, que no es data de la carga."""
    real = os.path.realpath(path)
    return any(real.startswith(os.path.realpath(p) + os.sep) for p in permitidos)


def limpiar(path, permitidos: tuple[str, ...] = PERMITIDOS) -> tuple[int, int]:
    """Borra el árbol `path` y devuelve (archivos, bytes) borrados. Si no existe,
    (0, 0). Si cae fuera del área de trabajo, RuntimeError sin tocar nada."""
    path = str(path)
    if not _dentro(path, permitidos):
        raise RuntimeError(
            f"me niego a borrar {os.path.realpath(path)}: está fuera del área de "
            f"trabajo permitida ({', '.join(permitidos)})")
    if not os.path.isdir(path):
        return 0, 0
    archivos = octetos = 0
    for dirpath, _, names in os.walk(path):
        for name in names:
            try:
                octetos += os.path.getsize(os.path.join(dirpath, name))
                archivos += 1
            except OSError:
                pass
    shutil.rmtree(path)
    return archivos, octetos


def verificar(esperados: list[tuple[str, int]]) -> list[str]:
    """De `[(ruta local, bytes en ADLS)]`, las rutas que faltan o cuyo tamaño no
    cuadra — una copia a medias es indistinguible de una completa sin esto."""
    return [ruta for ruta, size in esperados
            if not os.path.isfile(ruta) or os.path.getsize(ruta) != size]
