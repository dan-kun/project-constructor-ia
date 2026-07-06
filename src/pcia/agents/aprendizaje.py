"""Agente de Aprendizaje.

Consolida la experiencia registrada en la memoria para el ciclo de mejora
continua: detecta las preferencias históricas del usuario y las resume para
precargar la próxima entrevista ("en tus últimos proyectos usaste
PostgreSQL, ¿mantenemos?").

No usa LLM: la consolidación es determinística (frecuencias sobre las specs
registradas).
"""

from __future__ import annotations

from collections import Counter

from pcia.memoria import Memoria
from pcia.texto import normalizar

CAMPOS_PREFERENCIA = (
    "lenguaje",
    "framework",
    "arquitectura",
    "base_datos",
    "autenticacion",
    "gestion_secretos",
    "infraestructura",
    "ci_cd",
)


class Aprendizaje:
    """Resume el historial de proyectos para acortar futuras entrevistas."""

    def __init__(self, memoria: Memoria) -> None:
        self._memoria = memoria

    def resumen_historial(self) -> str:
        """Preferencias más frecuentes por campo, o cadena vacía sin historial."""
        registros = self._memoria.cargar_registros()
        if not registros:
            return ""

        lineas = []
        total = len(registros)
        for campo in CAMPOS_PREFERENCIA:
            valores = Counter(
                normalizar(valor)
                for registro in registros
                if (valor := getattr(registro.spec, campo))
            )
            if not valores:
                continue
            valor, cantidad = valores.most_common(1)[0]
            lineas.append(f"- {campo}: {valor} (en {cantidad} de {total} proyectos)")

        return "\n".join(lineas)
