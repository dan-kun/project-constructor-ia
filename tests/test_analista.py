"""Tests del Agente Analista de Documentos con FakeProvider (sin red)."""

import json

import pytest

from conftest import FakeProvider
from pcia.agents.analista import (
    MAX_CARACTERES_POR_DOC,
    Analista,
    AnalisisDocumentos,
    DocumentoInvalidoError,
    PropuestaCampo,
    leer_documentos,
)
from pcia.agents.llm_json import ContratoInvalidoError

ANALISIS_OK = json.dumps(
    {
        "propuestas": {
            "lenguaje": {
                "valor": "python",
                "evidencia": "el equipo trabaja en Python 3.11",
            }
        },
        "notas": ["el cliente pide integración con AFIP"],
        "preguntas_abiertas": ["no define autenticación"],
    },
    ensure_ascii=False,
)


def crear_doc(tmp_path, nombre="requerimientos.md", texto="El equipo trabaja en Python 3.11."):
    ruta = tmp_path / nombre
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def test_analiza_y_devuelve_propuestas_con_evidencia(tmp_path):
    provider = FakeProvider([ANALISIS_OK])
    analisis = Analista(provider).analizar([crear_doc(tmp_path)])

    assert analisis.propuestas["lenguaje"].valor == "python"
    assert "Python 3.11" in analisis.propuestas["lenguaje"].evidencia
    assert analisis.notas == ["el cliente pide integración con AFIP"]
    assert analisis.preguntas_abiertas == ["no define autenticación"]


def test_prompt_incluye_documentos_y_campos_permitidos(tmp_path):
    provider = FakeProvider([ANALISIS_OK])
    Analista(provider).analizar([crear_doc(tmp_path)])

    system_prompt, _ = provider.llamadas[0]
    assert "requerimientos.md" in system_prompt
    assert "El equipo trabaja en Python 3.11." in system_prompt
    assert "gestion_secretos" in system_prompt  # lista de campos permitidos
    assert "[[" not in system_prompt  # placeholders reemplazados


def test_propuesta_con_campo_invalido_reintenta_con_feedback(tmp_path):
    invalido = json.dumps(
        {
            "propuestas": {
                "sistema_operativo": {"valor": "linux", "evidencia": "usa linux"}
            },
            "notas": [],
            "preguntas_abiertas": [],
        }
    )
    provider = FakeProvider([invalido, ANALISIS_OK])
    analisis = Analista(provider).analizar([crear_doc(tmp_path)])

    assert "lenguaje" in analisis.propuestas
    assert len(provider.llamadas) == 2
    _, mensajes_reintento = provider.llamadas[1]
    assert "sistema_operativo" in mensajes_reintento[-1].content


def test_llm_persistentemente_malformado_escala(tmp_path):
    provider = FakeProvider(["basura"] * 3)
    with pytest.raises(ContratoInvalidoError, match="3 intentos"):
        Analista(provider).analizar([crear_doc(tmp_path)])


def test_extension_no_soportada_falla_antes_de_llamar_al_llm(tmp_path):
    provider = FakeProvider([])
    pdf = crear_doc(tmp_path, nombre="requerimientos.pdf")
    with pytest.raises(DocumentoInvalidoError, match="no soportado"):
        Analista(provider).analizar([pdf])
    assert provider.llamadas == []


def test_documento_inexistente_falla_con_mensaje_claro(tmp_path):
    with pytest.raises(DocumentoInvalidoError, match="No se pudo leer"):
        leer_documentos([tmp_path / "no-existe.md"])


def test_documento_vacio_se_rechaza(tmp_path):
    with pytest.raises(DocumentoInvalidoError, match="está vacío"):
        leer_documentos([crear_doc(tmp_path, texto="   \n")])


def test_sin_documentos_se_rechaza():
    with pytest.raises(DocumentoInvalidoError, match="ningún documento"):
        leer_documentos([])


def test_documento_largo_se_trunca_con_marca_explicita(tmp_path):
    doc = crear_doc(tmp_path, texto="x" * (MAX_CARACTERES_POR_DOC + 500))
    texto = leer_documentos([doc])
    assert f"[documento truncado a {MAX_CARACTERES_POR_DOC} caracteres]" in texto
    assert len(texto) < MAX_CARACTERES_POR_DOC + 200


def test_varios_documentos_se_concatenan_con_sus_nombres(tmp_path):
    doc1 = crear_doc(tmp_path, "acta.md", "Reunión de kickoff.")
    doc2 = crear_doc(tmp_path, "mail.txt", "El cliente prefiere PostgreSQL.")
    texto = leer_documentos([doc1, doc2])
    assert "### acta.md" in texto
    assert "### mail.txt" in texto
    assert "PostgreSQL" in texto


def test_resumen_para_entrevista_lista_propuestas_notas_y_preguntas():
    analisis = AnalisisDocumentos(
        propuestas={
            "base_datos": PropuestaCampo(
                valor="postgresql", evidencia="usaremos PostgreSQL 15"
            )
        },
        notas=["integración con AFIP"],
        preguntas_abiertas=["no define CI/CD"],
    )
    resumen = analisis.resumen_para_entrevista()
    assert '- base_datos: postgresql — evidencia: "usaremos PostgreSQL 15"' in resumen
    assert "- integración con AFIP" in resumen
    assert "- no define CI/CD" in resumen


def test_resumen_sin_datos_lo_dice_explicitamente():
    resumen = AnalisisDocumentos().resumen_para_entrevista()
    assert "no aportó datos" in resumen
