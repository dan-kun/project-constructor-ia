"""Tests del contrato JSON compartido y del logging opcional de diagnóstico."""

from __future__ import annotations

import json

from conftest import FakeProvider
from pcia.agents import llm_json
from pcia.agents.llm_json import consultar_con_contrato
from pcia.domain.ports import ChatMessage
from pydantic import BaseModel


class ContratoDemo(BaseModel):
    ok: bool


def test_debug_desactivado_por_defecto_no_escribe_nada(tmp_path, monkeypatch):
    monkeypatch.delenv(llm_json.VAR_DEBUG, raising=False)
    ruta = tmp_path / "debug.jsonl"
    monkeypatch.setenv(llm_json.VAR_DEBUG_ARCHIVO, str(ruta))

    provider = FakeProvider([json.dumps({"ok": True})])
    consultar_con_contrato(provider, "system", [ChatMessage(role="user", content="hola")], ContratoDemo)

    assert not ruta.exists()


def test_debug_activado_registra_cada_intento(tmp_path, monkeypatch):
    monkeypatch.setenv(llm_json.VAR_DEBUG, "1")
    ruta = tmp_path / "debug.jsonl"
    monkeypatch.setenv(llm_json.VAR_DEBUG_ARCHIVO, str(ruta))

    provider = FakeProvider(["basura", json.dumps({"ok": True})])
    consultar_con_contrato(
        provider, "system-x", [ChatMessage(role="user", content="hola")], ContratoDemo
    )

    lineas = ruta.read_text(encoding="utf-8").strip().splitlines()
    assert len(lineas) == 2
    primero = json.loads(lineas[0])
    assert primero["intento"] == 1
    assert primero["error"] is not None
    assert primero["system_prompt"] == "system-x"
    segundo = json.loads(lineas[1])
    assert segundo["intento"] == 2
    assert segundo["error"] is None
    assert segundo["respuesta_cruda"] == json.dumps({"ok": True})


def test_debug_no_impide_que_el_contrato_persistentemente_malo_escale(tmp_path, monkeypatch):
    from pcia.agents.llm_json import ContratoInvalidoError

    monkeypatch.setenv(llm_json.VAR_DEBUG, "1")
    monkeypatch.setenv(llm_json.VAR_DEBUG_ARCHIVO, str(tmp_path / "debug.jsonl"))

    provider = FakeProvider(["basura"] * 3)
    try:
        consultar_con_contrato(provider, "s", [ChatMessage(role="user", content="hola")], ContratoDemo)
        raise AssertionError("debía escalar")
    except ContratoInvalidoError:
        pass
