"""Tests de las utilidades de texto."""

from pcia.texto import normalizar, slug_kebab, slug_snake


def test_normalizar_quita_acentos_y_mayusculas():
    assert normalizar("Facturación ELECTRÓNICA") == "facturacion electronica"


def test_slug_kebab():
    assert slug_kebab("Mi Ápi 2.0") == "mi-api-2-0"
    assert slug_kebab("  ") == "proyecto"
    assert slug_kebab("###", defecto="modulo") == "modulo"


def test_slug_snake():
    assert slug_snake("Mi Ápi 2.0") == "mi_api_2_0"
    assert slug_snake("") == "proyecto"
