"""Agregados sobre la memoria de proyectos (``pcia stats``).

Cierra el ciclo de aprendizaje declarado en docs/DISENO.md §4: si un
diagnóstico de build se repite entre proyectos del mismo stack, el defecto
está en la plantilla. Sin este comando, esa señal quedaba enterrada en
archivos JSON individuales — nadie la miraba en agregado.

Determinístico, igual que ``pcia.agents.aprendizaje``: cuentas y promedios
sobre los registros, sin LLM.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from pcia.domain.models import RegistroProyecto


def generar_reporte(registros: list[RegistroProyecto]) -> str:
    """Texto plano con agregados de la memoria, para mostrar en consola."""
    if not registros:
        return "Todavía no hay proyectos registrados en la memoria."

    lineas = [f"Proyectos registrados: {len(registros)}", ""]
    lineas.extend(_por_stack(registros))
    lineas.append("")
    lineas.extend(_por_proveedor(registros))
    diagnosticos = _diagnosticos_repetidos(registros)
    if diagnosticos:
        lineas.append("")
        lineas.extend(diagnosticos)
    return "\n".join(lineas)


def _por_stack(registros: list[RegistroProyecto]) -> list[str]:
    lineas = ["Por stack:"]
    por_stack: dict[str, list[RegistroProyecto]] = defaultdict(list)
    for registro in registros:
        por_stack[registro.stack or "(sin stack)"].append(registro)
    for stack in sorted(por_stack):
        del_stack = por_stack[stack]
        estados = Counter(r.estado_final or "desconocido" for r in del_stack)
        resumen_estados = ", ".join(
            f"{estado.replace('_', ' ')}: {cantidad}"
            for estado, cantidad in estados.most_common()
        )
        lineas.append(f"  - {stack}: {len(del_stack)} proyecto(s) — {resumen_estados}")
    return lineas


def _por_proveedor(registros: list[RegistroProyecto]) -> list[str]:
    lineas = ["Por proveedor:"]
    por_proveedor: dict[str, list[RegistroProyecto]] = defaultdict(list)
    for registro in registros:
        por_proveedor[registro.proveedor or "(desconocido)"].append(registro)
    for proveedor in sorted(por_proveedor):
        del_proveedor = por_proveedor[proveedor]
        duraciones = [r.duracion_segundos for r in del_proveedor if r.duracion_segundos is not None]
        promedio = f"{sum(duraciones) / len(duraciones):.0f}s" if duraciones else "sin datos"
        lineas.append(
            f"  - {proveedor}: {len(del_proveedor)} proyecto(s), "
            f"duración promedio {promedio}"
        )
    return lineas


def _diagnosticos_repetidos(registros: list[RegistroProyecto]) -> list[str]:
    """Diagnósticos del corrector de builds que se repitieron entre proyectos.

    Un mismo diagnóstico apareciendo más de una vez es la señal, según el
    propio principio del sistema, de que el defecto está en la plantilla y
    no en un caso puntual.
    """
    conteo = Counter(
        diagnostico
        for registro in registros
        for diagnostico in registro.correcciones_build
    )
    repetidos = [(d, n) for d, n in conteo.items() if n > 1]
    if not repetidos:
        return []
    lineas = ["Diagnósticos de build repetidos (posible defecto de plantilla):"]
    for diagnostico, cantidad in sorted(repetidos, key=lambda par: -par[1]):
        lineas.append(f"  - ({cantidad}x) {diagnostico}")
    return lineas
