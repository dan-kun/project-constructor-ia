"""Tests del helper de transcript compartido entre CLI y adapter web."""

from __future__ import annotations

from pcia.transcript import Transcript


def test_registra_salidas_y_entradas_en_orden():
    transcript = Transcript()
    transcript.registrar_salida("¿Cómo se llama el proyecto?")
    transcript.registrar_entrada("api-demo")
    transcript.registrar_salida("Perfecto, ¿qué framework preferís?")

    texto = transcript.texto_completo()

    assert texto.index("¿Cómo se llama el proyecto?") < texto.index("> api-demo")
    assert texto.index("> api-demo") < texto.index("¿qué framework preferís?")


def test_registrar_error_queda_en_el_texto():
    transcript = Transcript()
    transcript.registrar_salida("Auditoría de coherencia — semáforo: 🔴 rojo")
    transcript.registrar_error("Quedaron hallazgos sin resolver tras 3 ciclos")

    assert "Quedaron hallazgos sin resolver" in transcript.texto_completo()


def test_guardar_escribe_el_archivo_y_crea_directorios(tmp_path):
    transcript = Transcript()
    transcript.registrar_salida("hola")
    ruta = tmp_path / "sub" / "conversacion.txt"

    resultado = transcript.guardar(ruta)

    assert resultado == ruta
    assert ruta.read_text(encoding="utf-8") == "hola\n"
