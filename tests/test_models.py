"""Tests de los modelos de dominio."""

import pytest

from pcia.domain.models import (
    Chequeo,
    Hallazgo,
    ProjectSpec,
    ResultadoAuditoria,
    ResultadoVerificacion,
    Severidad,
)

UPDATES_COMPLETOS = {
    "nombre": "mi-api",
    "descripcion": "API de facturación",
    "tipo_proyecto": "api",
    "lenguaje": "python",
    "framework": "fastapi",
    "arquitectura": "hexagonal",
    "base_datos": "postgresql",
    "autenticacion": "jwt",
    "gestion_secretos": "variables de entorno + vault",
    "infraestructura": "docker",
    "ci_cd": "github actions",
    "alcance": "mvp interno",
}


def test_spec_nueva_esta_incompleta():
    spec = ProjectSpec()
    assert not spec.esta_completa()
    assert spec.campos_faltantes() == list(ProjectSpec.CAMPOS_REQUERIDOS)


def test_aplicar_updates_validos():
    spec = ProjectSpec()
    spec.aplicar_updates({"nombre": "mi-api", "lenguaje": "python"})
    assert spec.nombre == "mi-api"
    assert spec.lenguaje == "python"
    assert "nombre" not in spec.campos_faltantes()


def test_spec_completa_con_todos_los_requeridos():
    spec = ProjectSpec()
    spec.aplicar_updates(UPDATES_COMPLETOS)
    assert spec.esta_completa()
    assert spec.campos_faltantes() == []


def test_clave_invalida_levanta_valueerror_sin_estado_parcial():
    spec = ProjectSpec()
    with pytest.raises(ValueError, match="Claves inválidas"):
        spec.aplicar_updates({"nombre": "mi-api", "color_favorito": "azul"})
    # atómico: la clave válida tampoco se aplicó
    assert spec.nombre is None


def test_valor_invalido_levanta_valueerror_sin_estado_parcial():
    spec = ProjectSpec()
    with pytest.raises(ValueError, match="Valor inválido"):
        spec.aplicar_updates({"nombre": "ok", "notas": 42})
    assert spec.nombre is None


def test_notas_acepta_string_y_lo_agrega_a_la_lista():
    spec = ProjectSpec()
    spec.aplicar_updates({"notas": "usar uv en lugar de pip"})
    spec.aplicar_updates({"notas": "sin websockets"})
    assert spec.notas == ["usar uv en lugar de pip", "sin websockets"]


def test_notas_no_es_campo_requerido():
    spec = ProjectSpec()
    spec.aplicar_updates(UPDATES_COMPLETOS)
    assert spec.notas == []
    assert spec.esta_completa()


def test_riesgos_asumidos_acepta_string_y_no_es_requerido():
    spec = ProjectSpec()
    spec.aplicar_updates({"riesgos_asumidos": "sqlite-alta-concurrencia: asumido"})
    assert spec.riesgos_asumidos == ["sqlite-alta-concurrencia: asumido"]
    assert "riesgos_asumidos" not in ProjectSpec.CAMPOS_REQUERIDOS


def hallazgo(id_, severidad):
    return Hallazgo(id=id_, severidad=severidad, mensaje="m", origen="regla")


def test_semaforo_sin_hallazgos_es_verde():
    resultado = ResultadoAuditoria()
    assert resultado.semaforo() is Severidad.VERDE
    assert resultado.pendientes() == []


def test_semaforo_devuelve_la_peor_severidad():
    resultado = ResultadoAuditoria(
        hallazgos=[
            hallazgo("a", Severidad.VERDE),
            hallazgo("b", Severidad.ROJO),
            hallazgo("c", Severidad.AMARILLO),
        ]
    )
    assert resultado.semaforo() is Severidad.ROJO


def test_pendientes_excluye_los_verdes():
    resultado = ResultadoAuditoria(
        hallazgos=[hallazgo("a", Severidad.VERDE), hallazgo("b", Severidad.AMARILLO)]
    )
    assert [h.id for h in resultado.pendientes()] == ["b"]


def test_verificacion_aprobada_sin_errores():
    resultado = ResultadoVerificacion(
        chequeos=[
            Chequeo(archivo="a.py", estado="ok"),
            Chequeo(archivo="b.md", estado="omitido", detalle="sin verificador"),
        ]
    )
    assert resultado.aprobado()
    assert resultado.errores() == []


def test_verificacion_con_error_no_aprueba():
    con_error = Chequeo(archivo="c.json", estado="error", detalle="json inválido")
    resultado = ResultadoVerificacion(
        chequeos=[Chequeo(archivo="a.py", estado="ok"), con_error]
    )
    assert not resultado.aprobado()
    assert resultado.errores() == [con_error]


def test_verificacion_profunda_con_error_tampoco_aprueba():
    build_roto = Chequeo(archivo="docker-build", estado="error", detalle="falló el build")
    resultado = ResultadoVerificacion(
        chequeos=[Chequeo(archivo="a.py", estado="ok")],
        profundos=[build_roto, Chequeo(archivo="lint", estado="omitido")],
    )
    assert not resultado.aprobado()
    assert resultado.errores() == [build_roto]
