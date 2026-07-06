"""Tests del Agente Verificador con FakeProvider (sin llamadas reales)."""

import json

import pytest

from conftest import FakeProvider
from pcia.agents.llm_json import ContratoInvalidoError
from pcia.agents.verificador import Verificador


def crear_verificador(respuestas=None):
    return Verificador(FakeProvider(respuestas or []))


def escribir(raiz, relativa, contenido):
    ruta = raiz / relativa
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")


def test_proyecto_valido_aprueba(tmp_path):
    escribir(tmp_path, "src/main.py", "def hola():\n    return 1\n")
    escribir(tmp_path, "config.json", '{"ok": true}')
    escribir(tmp_path, "ci.yml", "jobs:\n  test:\n    runs-on: ubuntu\n")
    escribir(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    escribir(tmp_path, "vista.xml", "<odoo><record id='x'/></odoo>")
    escribir(tmp_path, "accesos.csv", "id,name\n1,a\n2,b\n")

    resultado = crear_verificador().verificar(tmp_path)

    assert resultado.aprobado()
    assert {c.estado for c in resultado.chequeos} == {"ok"}


def test_archivos_sin_verificador_se_omiten_y_no_bloquean(tmp_path):
    escribir(tmp_path, "README.md", "# hola")
    escribir(tmp_path, "src/main.ts", "const x: número =")  # ni se intenta

    resultado = crear_verificador().verificar(tmp_path)

    assert resultado.aprobado()
    assert {c.estado for c in resultado.chequeos} == {"omitido"}


@pytest.mark.parametrize(
    "relativa, contenido",
    [
        ("roto.py", "def hola(:\n"),
        ("roto.json", "{roto"),
        ("roto.yaml", "clave: [sin cerrar"),
        ("roto.toml", "name = sin comillas ni tipo\n= x"),
        ("roto.xml", "<odoo><sin_cerrar></odoo>"),
        ("roto.csv", "id,name\n1\n2,b,c\n"),
    ],
)
def test_sintaxis_rota_se_detecta(tmp_path, relativa, contenido):
    escribir(tmp_path, relativa, contenido)

    resultado = crear_verificador().verificar(tmp_path)

    assert not resultado.aprobado()
    assert resultado.errores()[0].archivo == relativa
    assert resultado.errores()[0].detalle  # el error trae el detalle para corregir


def test_verificar_archivo_individual(tmp_path):
    escribir(tmp_path, "a.json", "{roto")
    chequeo = crear_verificador().verificar_archivo(tmp_path, "a.json")
    assert chequeo.estado == "error"

    escribir(tmp_path, "a.json", '{"ok": 1}')
    chequeo = crear_verificador().verificar_archivo(tmp_path, "a.json")
    assert chequeo.estado == "ok"


def test_corregir_archivo_reescribe_con_lo_que_devuelve_el_llm(tmp_path):
    escribir(tmp_path, "config.json", "{roto")
    correccion = json.dumps({"contenido_corregido": '{"ok": true}\n'})
    verificador = Verificador(FakeProvider([correccion]))

    verificador.corregir_archivo(tmp_path, "config.json", "json inválido")

    assert (tmp_path / "config.json").read_text(encoding="utf-8") == '{"ok": true}\n'


def test_prompt_de_correccion_incluye_ruta_error_y_contenido(tmp_path):
    escribir(tmp_path, "config.json", "{roto")
    provider = FakeProvider([json.dumps({"contenido_corregido": "{}"})])

    Verificador(provider).corregir_archivo(tmp_path, "config.json", "Expecting value")

    system_prompt, _ = provider.llamadas[0]
    assert "config.json" in system_prompt
    assert "Expecting value" in system_prompt
    assert "{roto" in system_prompt
    assert "[[" not in system_prompt


def test_correccion_malformada_reintenta_y_luego_escala(tmp_path):
    escribir(tmp_path, "config.json", "{roto")
    verificador = Verificador(FakeProvider(["basura"] * 3))

    with pytest.raises(ContratoInvalidoError):
        verificador.corregir_archivo(tmp_path, "config.json", "json inválido")
    # el archivo no se tocó
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == "{roto"
