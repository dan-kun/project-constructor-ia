"""Tests del adapter web: bridging de colas y validación del formulario.

No levanta un servidor HTTP real (eso lo cubre la corrida manual de la demo);
prueba la lógica de puenteo hilo/colas con FakeProvider, sin red.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from pcia.config import ConfigError
from pcia.web.sessions import GestorSesiones, Sesion, validar_config_proveedor


def test_sesion_bridging_entrada_salida():
    sesion = Sesion(id="test")
    sesion.enviar_input("hola")
    # "> " es el marcador genérico de turno normal de entrevista: la
    # pregunta real ya viajó por su propio salida() un instante antes, así
    # que acá no debe reenviarse texto (a diferencia de un prompt con
    # texto) — pero sí un marcador de "ahora se espera respuesta".
    assert sesion.entrada("> ") == "hola"
    marcador = sesion.proximo_evento(timeout=1)
    assert marcador is not None
    assert marcador.texto == ""
    assert marcador.espera_respuesta is True

    sesion.salida("mensaje al usuario")
    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.tipo == "mensaje"
    assert evento.texto == "mensaje al usuario"
    assert evento.espera_respuesta is False


def test_entrada_reenvia_prompts_con_texto_antes_de_bloquear():
    """Bug real detectado en la demo: preguntas como '¿Confirmás la
    especificación?' solo viajaban como argumento de entrada() — en la CLI
    ``input(prompt)`` las imprime sola, pero en la web se perdían en
    silencio y la sesión quedaba esperando una respuesta que el usuario
    nunca vio. Ahora entrada() las publica como mensaje antes de bloquear."""
    sesion = Sesion(id="test")
    sesion.enviar_input("si")

    respuesta = sesion.entrada(
        "¿Confirmás la especificación? (enter = continuar, o escribí qué ajustar) "
    )

    assert respuesta == "si"
    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.tipo == "mensaje"
    assert "¿Confirmás la especificación?" in evento.texto


def test_entrada_destino_muestra_mensaje_amigable_no_la_ruta_del_servidor():
    """La pregunta real del destino incluye una ruta del filesystem del
    servidor (irrelevante para el visitante); se reemplaza por un mensaje
    propio en vez de reenviarla tal cual."""
    sesion = Sesion(id="test")
    sesion.enviar_input("mi-api")

    sesion.entrada("¿Dónde genero el proyecto? (enter = /opt/render/project/src/mi-api) ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert "/opt/render" not in evento.texto
    assert "carpeta del proyecto" in evento.texto
    assert evento.espera_respuesta is True


# --- distinción "esperando respuesta" vs "trabajando" (indicador de actividad) -----


def test_salida_informativa_no_marca_espera_respuesta():
    """Un reporte de progreso (auditoría, verificación, etc.) no es un
    punto donde el orquestador se detenga a esperar: el navegador debe
    poder distinguirlo para no habilitar el input ni decir 'tu turno'."""
    sesion = Sesion(id="test")
    sesion.salida("Auditoría de coherencia — semáforo: 🟢 verde")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.espera_respuesta is False


def test_turno_normal_de_entrevista_emite_marcador_de_espera_sin_burbuja_vacia():
    """El turno normal ('> ') no reenvía texto (la pregunta ya viajó por su
    propio salida()), pero sí debe señalar que ahora se espera respuesta —
    si no, el navegador nunca sabe que puede dejar de mostrar 'trabajando'."""
    sesion = Sesion(id="test")
    sesion.enviar_input("cualquier cosa")

    sesion.entrada("> ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.texto == ""
    assert evento.espera_respuesta is True
    # el marcador (sin contenido) no ensucia el transcript: una sola línea
    assert sesion.transcript.texto_completo() == "> cualquier cosa\n"


def test_entrada_confina_el_destino_de_construccion_pese_a_path_traversal():
    """Un visitante de la demo web no puede decidir una ruta arbitraria en el
    filesystem del servidor: el destino siempre queda bajo el directorio
    propio de la sesión, sin importar lo que haya tipeado."""
    sesion = Sesion(id="sesion-abc")
    prompt_destino = "¿Dónde genero el proyecto? (enter = /home/render/x) "

    for intento_malicioso in ("../../../etc/pasando", "/etc/cron.d/evil", "C:\\Windows\\System32"):
        sesion.enviar_input(intento_malicioso)
        destino = Path(sesion.entrada(prompt_destino))
        base_segura = Path(tempfile.gettempdir()) / "pcia-web-sesiones" / "sesion-abc"
        assert base_segura in destino.parents or destino.parent == base_segura
        assert ".." not in destino.parts


def test_entrada_no_toca_el_destino_para_otros_prompts():
    sesion = Sesion(id="sesion-xyz")
    sesion.enviar_input("../../lo-que-sea")
    assert sesion.entrada("¿Confirmás la especificación?") == "../../lo-que-sea"


def test_guardar_transcript_registra_salidas_y_entradas_en_orden(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    sesion = Sesion(id="sesion-transcript")
    sesion.salida("¿Cómo se llama el proyecto?")
    sesion.enviar_input("mi-api")
    sesion.entrada("> ")

    ruta = sesion.guardar_transcript()

    assert ruta == sesion.ruta_transcript
    contenido = ruta.read_text(encoding="utf-8")
    assert contenido.index("¿Cómo se llama el proyecto?") < contenido.index("> mi-api")


def test_proximo_evento_sin_eventos_devuelve_none():
    sesion = Sesion(id="test")
    assert sesion.proximo_evento(timeout=0.05) is None


# --- documentos del cliente subidos desde el navegador (U9) -----------------------


def test_validar_documentos_acepta_md_y_txt():
    from pcia.web.sessions import validar_documentos

    validar_documentos([("requerimientos.md", "el cliente quiere una API")])
    validar_documentos([("mail.txt", "contenido del mail")])


def test_validar_documentos_rechaza_extension_no_soportada():
    from pcia.web.sessions import validar_documentos

    with pytest.raises(ConfigError, match="Formato no soportado"):
        validar_documentos([("requerimientos.pdf", "x")])


def test_validar_documentos_rechaza_contenido_vacio():
    from pcia.web.sessions import validar_documentos

    with pytest.raises(ConfigError, match="está vacío"):
        validar_documentos([("acta.md", "   \n")])


def test_validar_documentos_rechaza_demasiados_documentos():
    from pcia.web.sessions import MAX_DOCUMENTOS_WEB, validar_documentos

    documentos = [(f"doc{i}.md", "contenido") for i in range(MAX_DOCUMENTOS_WEB + 1)]
    with pytest.raises(ConfigError, match="Máximo"):
        validar_documentos(documentos)


def test_validar_documentos_rechaza_documento_demasiado_grande():
    from pcia.web.sessions import MAX_CARACTERES_POR_DOCUMENTO_WEB, validar_documentos

    with pytest.raises(ConfigError, match="supera el máximo"):
        validar_documentos([("grande.md", "x" * (MAX_CARACTERES_POR_DOCUMENTO_WEB + 1))])


def test_guardar_documentos_sanitiza_nombres_con_path_traversal(tmp_path):
    from pcia.web.sessions import _guardar_documentos

    rutas = _guardar_documentos(tmp_path, [("../../etc/malicioso.md", "contenido")])

    assert len(rutas) == 1
    assert rutas[0] == tmp_path / "docs" / "malicioso.md"
    assert rutas[0].read_text(encoding="utf-8") == "contenido"
    assert not (tmp_path.parent / "etc" / "malicioso.md").exists()


def test_gestor_crear_con_documentos_analiza_antes_de_entrevistar(monkeypatch, tmp_path):
    """Equivalente web de --docs (ver docs/UX.md U9): el Analista corre
    antes que el Entrevistador y su resumen llega como mensaje."""
    from conftest import FakeProvider

    from pcia.web import sessions as web_sessions

    analisis = json.dumps(
        {
            "propuestas": {
                "lenguaje": {"valor": "python", "evidencia": "una API en Python"}
            },
            "notas": [],
            "preguntas_abiertas": [],
        }
    )
    provider = FakeProvider(
        [
            analisis,
            json.dumps({"message_to_user": "¿Qué más?", "updates": {}, "done": False}),
        ]
    )
    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: provider)

    gestor = GestorSesiones(memory_dir=tmp_path / "memory")
    sesion = gestor.crear(
        {"provider": "openai_compat", "openai_compat": {}},
        documentos=[("requerimientos.md", "El cliente quiere una API en Python.")],
    )

    mensajes = []
    for _ in range(5):
        evento = sesion.proximo_evento(timeout=5)
        assert evento is not None
        mensajes.append(evento.texto)
        if "Análisis de la documentación" in evento.texto:
            break
    assert any("Análisis de la documentación" in m for m in mensajes)
    assert (sesion.directorio_base / "docs" / "requerimientos.md").exists()


def test_crear_sesion_con_documentos_invalidos_falla_sin_abrir_hilo(monkeypatch, tmp_path):
    from conftest import FakeProvider

    from pcia.web import sessions as web_sessions

    monkeypatch.setattr(
        web_sessions, "crear_provider", lambda config: FakeProvider([])
    )
    gestor = GestorSesiones(memory_dir=tmp_path / "memory")

    with pytest.raises(ConfigError, match="Formato no soportado"):
        gestor.crear(
            {"provider": "openai_compat", "openai_compat": {}},
            documentos=[("malicioso.pdf", "x")],
        )
    assert gestor._sesiones == {}


# --- corregir/retomar con datos ya respondidos (U3/U4/U5) -------------------------


def test_gestor_crear_con_spec_inicial_precarga_la_entrevista(monkeypatch, tmp_path):
    """Corregir algo ya respondido, o retomar tras un error: una sesión
    nueva puede arrancar con campos ya completos en vez de la spec vacía."""
    from conftest import FakeProvider

    from pcia.web import sessions as web_sessions

    provider = FakeProvider(
        [json.dumps({"message_to_user": "Solo falta el CI/CD.", "updates": {}, "done": False})]
    )
    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: provider)

    gestor = GestorSesiones(memory_dir=tmp_path / "memory")
    sesion = gestor.crear(
        {"provider": "openai_compat", "openai_compat": {}},
        spec_inicial={"nombre": "mi-api", "lenguaje": "python"},
    )

    assert sesion.orquestador.spec.nombre == "mi-api"
    assert sesion.orquestador.spec.lenguaje == "python"
    assert "nombre" not in sesion.orquestador.spec.campos_faltantes()
    assert "lenguaje" not in sesion.orquestador.spec.campos_faltantes()
    evento = sesion.proximo_evento(timeout=5)
    assert evento is not None
    system_prompt, _ = provider.llamadas[0]
    assert '"nombre": "mi-api"' in system_prompt  # la spec ya viaja precargada


def test_crear_sesion_con_spec_inicial_invalida_devuelve_error(monkeypatch, tmp_path):
    from conftest import FakeProvider

    from pcia.web import sessions as web_sessions

    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: FakeProvider([]))
    gestor = GestorSesiones(memory_dir=tmp_path / "memory")

    with pytest.raises(ConfigError, match="Datos de partida inválidos"):
        gestor.crear(
            {"provider": "openai_compat", "openai_compat": {}},
            spec_inicial={"campo_inexistente": "x"},
        )
    assert gestor._sesiones == {}


def test_validar_config_proveedor_anthropic_default_model():
    config = validar_config_proveedor({"provider": "anthropic_api", "api_key": "clave"})
    assert config == {
        "provider": "anthropic_api",
        "anthropic_api": {"model": "claude-sonnet-4-6", "api_key": "clave"},
    }


def test_validar_config_proveedor_openai_compat_requiere_base_url():
    with pytest.raises(ConfigError, match="base_url"):
        validar_config_proveedor({"provider": "openai_compat", "model": "qwen2.5:14b"})


def test_validar_config_proveedor_provider_invalido():
    # claude_subscription se rechaza por defecto: depende de la CLI instalada
    # en la máquina que sirve la app, que en una instancia publicada no existe.
    with pytest.raises(ConfigError, match="ejecución local"):
        validar_config_proveedor({"provider": "claude_subscription"})


def test_gestor_crear_corre_orquestador_en_hilo_y_publica_primer_mensaje(
    monkeypatch, tmp_path
):
    """El Orquestador corre en background y su primer 'salida' llega por la cola,
    sin esperar a que termine toda la entrevista (que sigue bloqueada en 'entrada')."""
    from pcia.web import sessions as web_sessions

    respuesta_inicial = json.dumps(
        {"message_to_user": "¿Cómo se llama el proyecto?", "updates": {}, "done": False}
    )

    class FakeProviderLocal:
        def generate(self, system_prompt, messages):
            return respuesta_inicial

    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: FakeProviderLocal())

    gestor = GestorSesiones(memory_dir=tmp_path)
    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    evento = sesion.proximo_evento(timeout=5)
    assert evento is not None
    assert evento.tipo == "mensaje"
    assert "proyecto" in evento.texto.lower()

    # Sin alimentar la cola de entrada, el hilo queda bloqueado esperando
    # respuesta del usuario (igual que la CLI esperando input()).
    time.sleep(0.05)
    assert sesion.hilo.is_alive()


def test_credenciales_invalidas_fallan_rapido_como_evento_de_error(monkeypatch, tmp_path):
    """La sesión se crea (200) apenas se valida la FORMA de la config —
    ``crear_provider`` no hace ninguna llamada de red. Una credencial mala
    recién se descubre en la primera consulta real, que el orquestador hace
    de inmediato en el hilo de fondo: no hace falta un ping de validación
    aparte, alcanza con que ese primer fallo llegue rápido como evento
    'error' (no se pierda en silencio ni tumbe el proceso)."""
    from pcia.domain.ports import LLMProviderError
    from pcia.web import sessions as web_sessions

    class ProviderConCredencialInvalida:
        def generate(self, system_prompt, messages):
            raise LLMProviderError("401 Unauthorized: API key inválida")

    monkeypatch.setattr(
        web_sessions, "crear_provider", lambda config: ProviderConCredencialInvalida()
    )

    gestor = GestorSesiones(memory_dir=tmp_path / "memory")
    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    evento = sesion.proximo_evento(timeout=5)
    assert evento is not None
    assert evento.tipo == "error"
    assert "API key inválida" in evento.texto
    assert sesion.ruta_transcript is not None and sesion.ruta_transcript.exists()


def test_flujo_completo_web_muestra_confirmacion_y_guarda_transcript(monkeypatch, tmp_path):
    """Regresión de punta a punta del bug real: antes del fix, la sesión
    llegaba a 'terminar' la entrevista a los ojos del usuario pero quedaba
    esperando en silencio la confirmación de la spec, sin avisar nada."""
    from conftest import FakeProvider

    from pcia.agents import verificador as modulo_verificador
    from pcia.web import sessions as web_sessions

    # Sin esto, la verificación profunda intenta un build de Docker real si
    # está instalado en la máquina que corre los tests (como acá).
    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: False)

    updates_completos = {
        "nombre": "mi-api",
        "descripcion": "API de facturación",
        "tipo_proyecto": "api",
        "lenguaje": "python",
        "framework": "fastapi",
        "arquitectura": "capas",
        "base_datos": "postgresql",
        "autenticacion": "jwt",
        "gestion_secretos": "variables de entorno",
        "infraestructura": "docker",
        "ci_cd": "github actions",
        "alcance": "mvp",
    }

    def respuesta_json(mensaje="ok", updates=None, done=False):
        return json.dumps(
            {"message_to_user": mensaje, "updates": updates or {}, "done": done}
        )

    provider = FakeProvider(
        [
            respuesta_json("¿Qué querés construir?"),
            respuesta_json("Listo, resumen final.", updates_completos, done=True),
            '{"hallazgos": []}',  # pase LLM del Auditor
            json.dumps({"readme_markdown": "# mi api\n", "adr_markdown": "# ADR-001\n"}),
        ]
    )
    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: provider)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    gestor = GestorSesiones(memory_dir=tmp_path / "memory")
    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    # Los 3 únicos turnos que requieren respuesta en este flujo (idéntico al
    # equivalente por CLI en test_orchestrator.py): la primera respuesta
    # libre, confirmar la spec, y nombrar la carpeta del proyecto. Se cargan
    # de antemano porque no hay forma de saber, solo mirando los eventos de
    # salida, cuáles de ellos bloquean esperando input y cuáles son
    # puramente informativos.
    sesion.enviar_input("una API de facturación en python")
    sesion.enviar_input("")
    sesion.enviar_input("mi-api")

    mensajes: list[str] = []
    evento = None
    for _ in range(30):
        evento = sesion.proximo_evento(timeout=10)
        assert evento is not None, "la sesión no terminó dentro del timeout"
        if evento.tipo == "mensaje":
            mensajes.append(evento.texto)
        else:
            break

    assert evento is not None and evento.tipo == "fin", (
        f"la sesión no terminó en 'fin': {evento}, mensajes: {mensajes}"
    )
    # el bug: esta pregunta se perdía en silencio y nunca llegaba al chat
    assert any("¿Confirmás la especificación?" in m for m in mensajes)
    assert any("carpeta del proyecto" in m for m in mensajes)
    assert not any("/opt/render" in m or str(tmp_path) in m for m in mensajes if "genero" in m.lower())

    assert sesion.ruta_transcript is not None and sesion.ruta_transcript.exists()
    contenido = sesion.ruta_transcript.read_text(encoding="utf-8")
    assert "¿Confirmás la especificación?" in contenido
    assert sesion.ruta_proyecto is not None and sesion.ruta_proyecto.exists()


# --- aislamiento de memoria entre visitantes -------------------------------------


def test_memoria_se_aisla_por_sesion_por_defecto(monkeypatch, tmp_path):
    """La spec de un visitante no debe persistirse en la memoria compartida
    ni precargar la entrevista de otro (ver docs/SEGURIDAD.md, R12)."""
    from pcia.web import sessions as web_sessions

    class FakeProviderLocal:
        def generate(self, system_prompt, messages):
            return json.dumps({"message_to_user": "hola", "updates": {}, "done": False})

    monkeypatch.setattr(
        web_sessions, "crear_provider", lambda config: FakeProviderLocal()
    )
    compartida = tmp_path / "memory-compartida"
    gestor = GestorSesiones(memory_dir=compartida)

    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    assert gestor.memoria_por_sesion is True
    memoria_usada = Path(sesion.orquestador._memory_dir)
    assert memoria_usada == sesion.directorio_base / "memory"
    assert memoria_usada != compartida
    # dos sesiones no comparten memoria entre sí
    otra = gestor.crear({"provider": "openai_compat", "openai_compat": {}})
    assert Path(otra.orquestador._memory_dir) != memoria_usada


def test_limpiar_expiradas_borra_el_directorio_de_la_sesion(monkeypatch, tmp_path):
    """Sin esto, cada sesión vencida queda huérfana en /tmp para siempre
    (proyecto generado, transcript y memoria propia incluidos)."""
    from pcia.web import sessions as web_sessions

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    class FakeProviderLocal:
        def generate(self, system_prompt, messages):
            return json.dumps({"message_to_user": "hola", "updates": {}, "done": False})

    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: FakeProviderLocal())

    gestor = GestorSesiones(memory_dir=tmp_path / "memory")
    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})
    # Espera a que el hilo de fondo llegue a bloquearse esperando respuesta
    # (ver ``espera_respuesta``): antes de eso puede seguir escribiendo en
    # el directorio de la sesión (p. ej. el checkpoint de progreso), lo que
    # compite con el rmtree de abajo — no es el borrado lo que se prueba acá.
    for _ in range(20):
        evento = sesion.proximo_evento(timeout=2)
        assert evento is not None
        if evento.espera_respuesta:
            break
    directorio = sesion.directorio_base
    directorio.mkdir(parents=True, exist_ok=True)
    (directorio / "marca.txt").write_text("restos de la sesión", encoding="utf-8")

    sesion.ultimo_acceso -= web_sessions.TTL_SESION_SEGUNDOS + 1
    gestor._limpiar_expiradas()

    assert sesion.id not in gestor._sesiones
    assert not directorio.exists()


def test_memoria_compartida_es_opt_in(monkeypatch, tmp_path):
    from pcia.web import sessions as web_sessions

    class FakeProviderLocal:
        def generate(self, system_prompt, messages):
            return json.dumps({"message_to_user": "hola", "updates": {}, "done": False})

    monkeypatch.setattr(
        web_sessions, "crear_provider", lambda config: FakeProviderLocal()
    )
    compartida = tmp_path / "memory-compartida"
    gestor = GestorSesiones(memory_dir=compartida, memoria_por_sesion=False)

    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    assert Path(sesion.orquestador._memory_dir) == compartida


# --- decisiones estructuradas (botones) -------------------------------------------


def test_confirmar_spec_ofrece_boton_de_confirmacion():
    sesion = Sesion(id="test")
    sesion.enviar_input("")
    sesion.entrada("¿Confirmás la especificación? (enter = continuar, o escribí qué ajustar) ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones == [{"texto": "Confirmar especificación", "valor": ""}]


def test_hallazgo_rojo_ofrece_aplicar_o_abortar():
    sesion = Sesion(id="test")
    sesion.enviar_input("")
    sesion.entrada(
        "El hallazgo 'x' es 🔴 bloqueante y no puede asumirse. ¿Cómo lo querés "
        "resolver? (enter = aplicar la corrección propuesta / 'abortar' = "
        "cancelar el proyecto) "
    )

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones == [
        {"texto": "Aplicar corrección propuesta", "valor": ""},
        {"texto": "Abortar el proyecto", "valor": "abortar"},
    ]


def test_hallazgo_amarillo_ofrece_asumir_o_corregir():
    sesion = Sesion(id="test")
    sesion.enviar_input("")
    sesion.entrada("¿Asumís el riesgo 'x'? (s = asumir / N = corregir) ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones == [
        {"texto": "Asumir el riesgo", "valor": "s"},
        {"texto": "Corregir", "valor": "n"},
    ]


def test_como_resolver_generico_ofrece_aplicar_correccion():
    """Distinto del caso rojo: acá 'cómo lo querés resolver' aparece solo,
    sin la frase 'no puede asumirse' delante (viene del camino amarillo)."""
    sesion = Sesion(id="test")
    sesion.enviar_input("")
    sesion.entrada("¿Cómo lo querés resolver? (enter = aplicar la corrección propuesta) ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones == [{"texto": "Aplicar corrección propuesta", "valor": ""}]


def test_verificacion_fallida_ofrece_entregar_o_cancelar():
    sesion = Sesion(id="test")
    sesion.enviar_input("")
    sesion.entrada(
        "La verificación sigue fallando tras las correcciones. "
        "¿Entrego el proyecto igual? (s/N) "
    )

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones == [
        {"texto": "Entregar igual", "valor": "s"},
        {"texto": "Cancelar", "valor": "n"},
    ]


def test_dockerfile_reescrito_ofrece_ejecutar_o_cancelar():
    sesion = Sesion(id="test")
    sesion.enviar_input("")
    sesion.entrada(
        "El corrector reescribió el Dockerfile. ¿Ejecuto el build con el "
        "Dockerfile corregido? (S/n) "
    )

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones == [
        {"texto": "Ejecutar el build", "valor": "s"},
        {"texto": "Cancelar", "valor": "n"},
    ]


def test_prompts_sin_decision_reconocida_no_llevan_opciones():
    sesion = Sesion(id="test")
    sesion.enviar_input("respuesta libre")
    sesion.entrada("¿Cuál es el lenguaje del proyecto? ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones is None


def test_destino_no_ofrece_opciones_estructuradas():
    """El prompt de destino ya se reemplaza por un mensaje propio (ver test
    de arriba); tampoco debería llevar botones."""
    sesion = Sesion(id="sesion-x")
    sesion.enviar_input("mi-api")
    sesion.entrada("¿Dónde genero el proyecto? (enter = /home/render/x) ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.opciones is None


# --- panel de estado observable --------------------------------------------------


def test_estado_refleja_el_avance_del_orquestador(monkeypatch, tmp_path):
    """El evento lleva la foto del ciclo: el navegador no parsea texto."""
    from conftest import FakeProvider

    from pcia.web import sessions as web_sessions

    updates = {
        "nombre": "mi api",
        "descripcion": "API de facturación",
        "tipo_proyecto": "api",
        "lenguaje": "python",
        "framework": "fastapi",
        "arquitectura": "capas",
        "base_datos": "postgresql",
        "autenticacion": "jwt",
        "gestion_secretos": "variables de entorno",
        "infraestructura": "docker",
        "ci_cd": "github actions",
        "alcance": "mvp",
    }
    provider = FakeProvider(
        [
            json.dumps({"message_to_user": "¿Qué construimos?", "updates": {}, "done": False}),
            json.dumps({"message_to_user": "Listo.", "updates": updates, "done": True}),
            '{"hallazgos": []}',
            json.dumps({"readme_markdown": "# mi api\n", "adr_markdown": "# ADR-001\n"}),
        ]
    )
    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: provider)
    from pcia.agents import verificador as modulo_verificador

    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: False)

    gestor = GestorSesiones(memory_dir=tmp_path / "memory")
    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    eventos = []
    respuestas = ["una API de facturación", "", "mi-api"]
    while True:
        evento = sesion.proximo_evento(timeout=5.0)
        assert evento is not None, "la sesión se quedó sin eventos"
        eventos.append(evento)
        if evento.tipo in ("fin", "error"):
            break
        if respuestas:
            sesion.enviar_input(respuestas.pop(0))

    assert eventos[-1].tipo == "fin", eventos[-1].texto
    final = eventos[-1].estado
    assert final["fase"] == "fin"
    assert final["spec"]["framework"] == "fastapi"
    assert final["campos_faltantes"] == []
    assert final["semaforo"] == "verde"
    assert final["stack"] == "fastapi"
    assert "pyproject.toml" in final["archivos"]
    # sin docker en el entorno de test, los chequeos obligatorios quedan omitidos
    assert final["verificacion"]["estado"] == "inconcluso"
    # la fase avanza durante la corrida, no solo al final
    fases = [e.estado["fase"] for e in eventos if e.estado]
    assert "entrevista" in fases and "construccion" in fases


# --- suscripción de Claude: solo en ejecución local ------------------------------


def test_suscripcion_aceptada_cuando_se_permite():
    config = validar_config_proveedor(
        {"provider": "claude_subscription", "model": ""}, permitir_suscripcion=True
    )

    assert config == {"provider": "claude_subscription", "claude_subscription": {"model": None}}


def test_suscripcion_acepta_modelo_explicito():
    config = validar_config_proveedor(
        {"provider": "claude_subscription", "model": "claude-sonnet-5"},
        permitir_suscripcion=True,
    )

    assert config["claude_subscription"]["model"] == "claude-sonnet-5"


def test_proveedor_invalido_sigue_rechazandose():
    with pytest.raises(ConfigError, match="proveedor válido"):
        validar_config_proveedor({"provider": "inventado"}, permitir_suscripcion=True)
