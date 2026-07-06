"""Memoria persistente del sistema: un JSON por proyecto en ``memory/``.

Fase 5 con JSON plano por proyecto; evaluar SQLite si el volumen lo pide.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import ValidationError

from pcia.domain.models import RegistroProyecto
from pcia.texto import slug_kebab


class Memoria:
    """Repositorio de registros de proyectos construidos."""

    def __init__(self, directorio: Path) -> None:
        self._directorio = Path(directorio)

    def guardar(self, registro: RegistroProyecto) -> Path:
        self._directorio.mkdir(parents=True, exist_ok=True)
        marca = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        nombre = slug_kebab(registro.spec.nombre or "proyecto")
        ruta = self._directorio / f"{nombre}-{marca}.json"
        contador = 1
        while ruta.exists():
            contador += 1
            ruta = self._directorio / f"{nombre}-{marca}-{contador}.json"
        ruta.write_text(registro.model_dump_json(indent=2), encoding="utf-8")
        return ruta

    def cargar_registros(self) -> list[RegistroProyecto]:
        """Lee todos los registros válidos, del más viejo al más nuevo.

        Los archivos que no validan (corruptos o de un formato anterior)
        se ignoran en silencio: la memoria degrada, no rompe.
        """
        if not self._directorio.exists():
            return []
        registros = []
        for ruta in sorted(self._directorio.glob("*.json")):
            try:
                registros.append(
                    RegistroProyecto.model_validate_json(
                        ruta.read_text(encoding="utf-8")
                    )
                )
            except (ValidationError, UnicodeDecodeError):
                continue
        return registros
