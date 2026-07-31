"""Transcript de una sesión (entrevista + auditoría + construcción + verificación).

Utilidad de interfaz, no de dominio: la usan los adapters (CLI, web) para
dejarle al usuario un archivo de referencia con la conversación completa,
independientemente de si la sesión terminó bien o escaló con un error.
"""

from __future__ import annotations

from pathlib import Path


class Transcript:
    """Acumula mensajes del sistema y respuestas del usuario, en orden."""

    def __init__(self) -> None:
        self._lineas: list[str] = []

    def registrar_salida(self, texto: str) -> None:
        self._lineas.append(texto.rstrip())

    def registrar_entrada(self, texto: str) -> None:
        self._lineas.append(f"> {texto}".rstrip())

    def registrar_error(self, texto: str) -> None:
        self._lineas.append(f"[La sesión terminó con un error: {texto}]")

    def texto_completo(self) -> str:
        return "\n\n".join(self._lineas) + "\n"

    def guardar(self, ruta: Path) -> Path:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(self.texto_completo(), encoding="utf-8")
        return ruta
