"""Tests de los agregados de memoria (``pcia stats``)."""

from __future__ import annotations

from pcia.domain.models import ProjectSpec, RegistroProyecto
from pcia.stats import generar_reporte


def registro(**overrides) -> RegistroProyecto:
    valores = {
        "fecha": "2026-01-01T00:00:00",
        "spec": ProjectSpec(nombre="demo"),
        "stack": "fastapi",
        "proveedor": "openai_compat:qwen2.5",
        "duracion_segundos": 100.0,
        "estado_final": "aprobado",
        "correcciones_build": [],
    }
    valores.update(overrides)
    return RegistroProyecto(**valores)


def test_sin_registros_lo_dice_explicitamente():
    assert "Todavía no hay proyectos" in generar_reporte([])


def test_agrupa_por_stack_y_estado():
    registros = [
        registro(stack="fastapi", estado_final="aprobado"),
        registro(stack="fastapi", estado_final="fallido"),
        registro(stack="nestjs", estado_final="aprobado"),
    ]
    reporte = generar_reporte(registros)
    assert "Proyectos registrados: 3" in reporte
    assert "fastapi: 2 proyecto(s)" in reporte
    assert "nestjs: 1 proyecto(s)" in reporte
    assert "aprobado: 1" in reporte
    assert "fallido: 1" in reporte


def test_agrupa_por_proveedor_con_duracion_promedio():
    registros = [
        registro(proveedor="anthropic_api:claude-sonnet-5", duracion_segundos=60.0),
        registro(proveedor="anthropic_api:claude-sonnet-5", duracion_segundos=140.0),
    ]
    reporte = generar_reporte(registros)
    assert "anthropic_api:claude-sonnet-5: 2 proyecto(s), duración promedio 100s" in reporte


def test_diagnosticos_repetidos_se_senalan_como_defecto_de_plantilla():
    registros = [
        registro(correcciones_build=["faltaba el lockfile de npm"]),
        registro(correcciones_build=["faltaba el lockfile de npm"]),
        registro(correcciones_build=["otro problema puntual"]),
    ]
    reporte = generar_reporte(registros)
    assert "Diagnósticos de build repetidos" in reporte
    assert "(2x) faltaba el lockfile de npm" in reporte
    assert "otro problema puntual" not in reporte  # solo apareció una vez


def test_sin_diagnosticos_repetidos_no_agrega_la_seccion():
    reporte = generar_reporte([registro(correcciones_build=["único"])])
    assert "Diagnósticos de build repetidos" not in reporte


def test_stack_o_proveedor_desconocidos_no_rompen_el_reporte():
    reporte = generar_reporte([registro(stack=None, proveedor=None, duracion_segundos=None)])
    assert "(sin stack)" in reporte
    assert "(desconocido)" in reporte
    assert "sin datos" in reporte
