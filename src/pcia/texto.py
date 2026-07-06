"""Utilidades de texto compartidas: normalización para matching y slugs."""

from __future__ import annotations

import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para matching por palabras clave."""
    sin_acentos = (
        unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    )
    return sin_acentos.lower()


def slug_kebab(texto: str, defecto: str = "proyecto") -> str:
    """Slug apto para nombres de archivo y de proyecto: ``mi-api-2-0``."""
    slug = re.sub(r"[^a-z0-9]+", "-", normalizar(texto)).strip("-")
    return slug or defecto


def slug_snake(texto: str, defecto: str = "proyecto") -> str:
    """Slug apto para paquetes Python / módulos Odoo: ``mi_api_2_0``."""
    return slug_kebab(texto, defecto).replace("-", "_")
