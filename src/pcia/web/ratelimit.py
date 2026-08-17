"""Límite de tasa en memoria de proceso para endpoints públicos.

Cada sesión abre un hilo propio y un directorio en ``/tmp`` (ver
``sessions.py``): sin límite, cualquiera podría crear sesiones sin parar y
agotar los recursos del servidor. Ventana deslizante simple, sin
dependencias nuevas — alcanza para una demo de un solo proceso.
"""

from __future__ import annotations

import time
from collections import defaultdict


class LimiteExcedidoError(Exception):
    """La clave (típicamente una IP) superó el máximo de eventos permitido."""


class LimitadorTasa:
    """Ventana deslizante: como máximo ``max_eventos`` por ``ventana_segundos``."""

    def __init__(self, max_eventos: int, ventana_segundos: float) -> None:
        self._max_eventos = max_eventos
        self._ventana = ventana_segundos
        self._eventos: dict[str, list[float]] = defaultdict(list)

    def verificar(self, clave: str) -> None:
        """Registra un evento para ``clave``; levanta si excede el límite.

        Descarta primero los eventos fuera de la ventana, así la memoria no
        crece sin límite con claves que dejaron de pedir.
        """
        ahora = time.monotonic()
        recientes = [t for t in self._eventos[clave] if ahora - t < self._ventana]
        if len(recientes) >= self._max_eventos:
            self._eventos[clave] = recientes
            raise LimiteExcedidoError(
                f"Demasiadas solicitudes ({self._max_eventos} por "
                f"{self._ventana:.0f}s). Esperá un momento y reintentá."
            )
        recientes.append(ahora)
        self._eventos[clave] = recientes
