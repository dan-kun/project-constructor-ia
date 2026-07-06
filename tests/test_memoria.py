"""Tests de la memoria persistente y el Agente de Aprendizaje."""

from pcia.agents.aprendizaje import Aprendizaje
from pcia.domain.models import ProjectSpec, RegistroProyecto
from pcia.memoria import Memoria


def registro(nombre="demo", **spec_overrides) -> RegistroProyecto:
    valores = {
        "nombre": nombre,
        "descripcion": "d",
        "tipo_proyecto": "api",
        "lenguaje": "python",
        "framework": "fastapi",
        "arquitectura": "capas",
        "base_datos": "postgresql",
        "autenticacion": "jwt",
        "gestion_secretos": "variables de entorno",
        "infraestructura": "docker",
        "ci_cd": "github actions",
        "alcance": "interno",
    }
    valores.update(spec_overrides)
    return RegistroProyecto(fecha="2026-07-05T10:00:00", spec=ProjectSpec(**valores))


def test_guardar_y_cargar_registro(tmp_path):
    memoria = Memoria(tmp_path / "memory")

    ruta = memoria.guardar(registro(nombre="Mi Ápi"))

    assert ruta.name.startswith("mi-api-")
    registros = memoria.cargar_registros()
    assert len(registros) == 1
    assert registros[0].spec.nombre == "Mi Ápi"


def test_directorio_inexistente_devuelve_vacio(tmp_path):
    assert Memoria(tmp_path / "no-existe").cargar_registros() == []


def test_archivos_corruptos_o_de_formato_viejo_se_ignoran(tmp_path):
    directorio = tmp_path / "memory"
    memoria = Memoria(directorio)
    memoria.guardar(registro())
    # formato viejo (spec plana, sin envoltorio de registro) y basura
    (directorio / "viejo.json").write_text('{"nombre": "spec vieja"}', encoding="utf-8")
    (directorio / "roto.json").write_text("{roto", encoding="utf-8")

    registros = memoria.cargar_registros()

    assert len(registros) == 1
    assert registros[0].spec.nombre == "demo"


def test_aprendizaje_sin_historial_devuelve_vacio(tmp_path):
    aprendizaje = Aprendizaje(Memoria(tmp_path / "memory"))
    assert aprendizaje.resumen_historial() == ""


def test_aprendizaje_resume_preferencias_mas_frecuentes(tmp_path):
    memoria = Memoria(tmp_path / "memory")
    memoria.guardar(registro(base_datos="PostgreSQL", lenguaje="python"))
    memoria.guardar(registro(base_datos="postgresql", lenguaje="python"))
    memoria.guardar(registro(base_datos="mysql", lenguaje="typescript", framework="nestjs"))

    resumen = Aprendizaje(memoria).resumen_historial()

    # normaliza mayúsculas/acentos al contar
    assert "- base_datos: postgresql (en 2 de 3 proyectos)" in resumen
    assert "- lenguaje: python (en 2 de 3 proyectos)" in resumen
    assert "- framework: fastapi (en 2 de 3 proyectos)" in resumen
