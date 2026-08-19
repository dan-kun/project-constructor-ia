"""Orquestador: máquina de estados explícita del ciclo.

No es un agente LLM, es código determinístico. Secuencia completa:
Análisis de documentos (opcional) → Entrevista → Auditoría → Construcción
→ Verificación → Entrega → Aprendizaje.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

from pcia.agents.analista import Analista
from pcia.agents.aprendizaje import Aprendizaje
from pcia.agents.auditor import Auditor
from pcia.agents.constructor import Constructor, DestinoInvalidoError
from pcia.agents.interviewer import Entrevistador
from pcia.agents.llm_json import ContratoInvalidoError
from pcia.agents.verificador import Verificador
from pcia.domain.models import (
    Chequeo,
    EstadoVerificacion,
    Hallazgo,
    ProjectSpec,
    RegistroProyecto,
    ResolucionHallazgo,
    ResultadoAuditoria,
    ResultadoConstruccion,
    ResultadoVerificacion,
    Severidad,
)
from pcia.domain.ports import LLMProvider
from pcia.memoria import Memoria
from pcia.texto import normalizar, slug_kebab

MAX_TURNOS_ENTREVISTA = 30
MAX_CICLOS_COHERENCIA = 3
MAX_INTENTOS_DESTINO = 3
MAX_CORRECCIONES_POR_ARCHIVO = 3
# Cada ciclo de corrección profunda implica un rebuild (minutos): límite corto.
MAX_CORRECCIONES_BUILD = 2
# Cuántas veces se puede seguir ajustando la resolución de UN hallazgo antes
# de pasar al siguiente (ver ``_resolver_hallazgos``): acota el mini-diálogo
# de confirmación, mismo patrón que el resto de los loops del ciclo.
MAX_AJUSTES_POR_HALLAZGO = 3


@dataclass(frozen=True)
class LimitesCiclo:
    """Topes de los loops acotados del ciclo (ver docs/DISENO.md §4).

    Configurables desde ``config.yaml`` (sección ``limites``): con un modelo
    local lento, bajar estos números acorta una corrida que se estanca;
    subirlos da más margen a un modelo débil antes de escalar al usuario.
    Los defaults son los mismos valores que el sistema usó siempre.
    """

    max_turnos_entrevista: int = MAX_TURNOS_ENTREVISTA
    max_ciclos_coherencia: int = MAX_CICLOS_COHERENCIA
    max_intentos_destino: int = MAX_INTENTOS_DESTINO
    max_correcciones_por_archivo: int = MAX_CORRECCIONES_POR_ARCHIVO
    max_correcciones_build: int = MAX_CORRECCIONES_BUILD
    max_ajustes_por_hallazgo: int = MAX_AJUSTES_POR_HALLAZGO

# Respuestas (normalizadas) que confirman: "no tengo nada que ajustar, seguí".
# La lista es deliberadamente amplia porque la interfaz es conversacional: el
# usuario responde en lenguaje natural, no con la tecla que el prompt sugiere
# (mismo modo de falla que generó el proyecto llamado "asi esta bien").
CONFIRMACIONES = (
    "", "s", "si", "sip", "ok", "oka", "okey", "dale", "listo", "no", "nada",
    "ninguno", "ninguna", "seguir", "sigamos", "segui", "continuar",
    "continuemos", "adelante", "confirmo", "correcto", "perfecto",
)

# Frases de cierre que aparecen dentro de una respuesta más larga. Se eligen
# inequívocas a propósito: un "seguir" suelto podría venir de "quiero seguir
# usando postgres", que sí es un ajuste.
FRASES_DE_CIERRE = (
    "no hay nada", "nada que ajustar", "nada mas que", "nada que corregir",
    "sin cambios", "asi esta bien", "esta bien asi", "todo bien",
    "dame los archivos", "avanza", "avancemos",
)


def es_confirmacion(texto: str) -> bool:
    """¿El usuario está diciendo "no tengo nada que ajustar, seguí"?"""
    normalizado = normalizar(texto.strip())
    return normalizado in CONFIRMACIONES or any(
        frase in normalizado for frase in FRASES_DE_CIERRE
    )

EMOJI_SEMAFORO = {
    Severidad.VERDE: "🟢",
    Severidad.AMARILLO: "🟡",
    Severidad.ROJO: "🔴",
}

EMOJI_ESTADO_FINAL = {
    "aprobado": "✅",
    "aprobado_con_advertencias": "⚠️",
    "inconcluso": "❓",
    "fallido": "❌",
}


class Fase(str, Enum):
    ANALISIS = "analisis"
    ENTREVISTA = "entrevista"
    AUDITORIA = "auditoria"
    CONSTRUCCION = "construccion"
    VERIFICACION = "verificacion"
    ENTREGA = "entrega"
    APRENDIZAJE = "aprendizaje"
    FIN = "fin"


class LimiteDeTurnosError(Exception):
    """La entrevista superó el máximo de turnos permitido (loop acotado)."""


class CoherenciaNoResueltaError(Exception):
    """Quedaron hallazgos sin resolver tras agotar los ciclos de coherencia."""


class VerificacionFallidaError(Exception):
    """La verificación siguió fallando tras las correcciones y el usuario abortó."""


class Orquestador:
    """Coordina el ciclo completo sobre una ProjectSpec compartida.

    La interacción con el usuario se inyecta como callables (``entrada`` /
    ``salida``) para poder testear el loop sin consola.
    """

    def __init__(
        self,
        provider: LLMProvider,
        memory_dir: Path,
        entrada: Callable[[str], str],
        salida: Callable[[str], None],
        docs: Sequence[Path] | None = None,
        proveedor: str | None = None,
        limites: LimitesCiclo | None = None,
        spec_inicial: ProjectSpec | None = None,
    ) -> None:
        self._provider = provider
        self._memory_dir = Path(memory_dir)
        self._entrada = entrada
        self._salida = salida
        self._docs = [Path(doc) for doc in (docs or [])]
        self._proveedor = proveedor
        self._limites = limites or LimitesCiclo()
        self._inicio = time.monotonic()
        # ``spec_inicial`` es cómo se retoma una corrida interrumpida (ver
        # ``ruta_checkpoint``) o cómo el usuario corrige algo ya respondido
        # sin reempezar de cero: arranca la entrevista con esos campos ya
        # completos, y el Entrevistador solo pregunta por lo que falte.
        self.spec = spec_inicial or ProjectSpec()
        self.ruta_spec: Path | None = None
        self.ruta_proyecto: Path | None = None
        self.resoluciones: list[ResolucionHallazgo] = []
        self.correcciones_build: list[str] = []
        # Estado observable: los adaptadores de IO (consola, web) leen el
        # avance del ciclo sin parsear los mensajes de texto.
        self.fase_actual = Fase.ANALISIS
        self.auditoria: ResultadoAuditoria | None = None
        self._construccion: ResultadoConstruccion | None = None
        self._verificacion: ResultadoVerificacion | None = None
        self._memoria = Memoria(self._memory_dir)
        self._aprendizaje = Aprendizaje(self._memoria)
        # El entrevistador vive a nivel de orquestador para conservar su
        # historial durante las repreguntas del ciclo de coherencia, y
        # arranca precargado con las preferencias históricas del usuario.
        self._entrevistador = Entrevistador(
            provider, self.spec, historial_previo=self._aprendizaje.resumen_historial()
        )
        # Checkpoint de progreso: se reescribe con la spec actual mientras la
        # corrida no terminó, y se borra al completarse con éxito. Si la
        # corrida se interrumpe (falla o Ctrl+C), queda el último estado
        # conocido para retomar con ``spec_inicial`` en vez de reempezar
        # de cero (ver docs/UX.md U3/U4/U5).
        self._ruta_checkpoint = (
            self._memory_dir / "en-progreso" / f"checkpoint-{uuid.uuid4().hex[:12]}.json"
        )

    @property
    def ruta_checkpoint(self) -> Path | None:
        """Dónde quedó el progreso guardado, si la corrida no llegó a completarse."""
        return self._ruta_checkpoint if self._ruta_checkpoint.exists() else None

    def _guardar_checkpoint(self) -> None:
        try:
            self._ruta_checkpoint.parent.mkdir(parents=True, exist_ok=True)
            self._ruta_checkpoint.write_text(
                self.spec.model_dump_json(indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # best-effort: no debe tumbar el manejo de la falla real

    def _borrar_checkpoint(self) -> None:
        self._ruta_checkpoint.unlink(missing_ok=True)

    def ejecutar(self) -> Path:
        """Corre la máquina de estados y devuelve la ruta de la spec guardada."""
        manejadores: dict[Fase, Callable[[], Fase]] = {
            Fase.ANALISIS: self._fase_analisis,
            Fase.ENTREVISTA: self._fase_entrevista,
            Fase.AUDITORIA: self._fase_auditoria,
            Fase.CONSTRUCCION: self._fase_construccion,
            Fase.VERIFICACION: self._fase_verificacion,
            Fase.ENTREGA: self._fase_entrega,
            Fase.APRENDIZAJE: self._fase_aprendizaje,
        }
        self._inicio = time.monotonic()
        fase = Fase.ANALISIS
        completado = False
        try:
            while fase is not Fase.FIN:
                self.fase_actual = fase
                fase = manejadores[fase]()
                self._guardar_checkpoint()
            completado = True
        finally:
            # ``finally`` cubre tanto una excepción esperada (contrato
            # agotado, verificación fallida, etc.) como una interrupción
            # (Ctrl+C en la CLI). El checkpoint del ``while`` de arriba solo
            # se actualiza al CERRAR una fase completa; si la excepción
            # ocurrió a mitad de una fase (el caso más común: un turno de
            # entrevista que agota sus reintentos), ese último checkpoint
            # quedó desactualizado. ``self.spec`` se muta in-place en cada
            # turno exitoso, así que volver a guardarlo acá — con lo último
            # que sí se aplicó, aunque el turno que falló no — es lo que
            # evita perder ese progreso.
            if completado:
                self._borrar_checkpoint()
            else:
                self._guardar_checkpoint()
        self.fase_actual = Fase.FIN
        assert self.ruta_spec is not None
        return self.ruta_spec

    @property
    def construccion(self) -> ResultadoConstruccion | None:
        """Qué se construyó (solo lectura, para los adaptadores de IO)."""
        return self._construccion

    @property
    def verificacion(self) -> ResultadoVerificacion | None:
        """Cómo salió la verificación (solo lectura)."""
        return self._verificacion

    # --- fases -------------------------------------------------------------

    def _fase_analisis(self) -> Fase:
        """Análisis opcional de la documentación del cliente (Fase 6).

        El Analista extrae propuestas con evidencia; el Entrevistador las
        recibe como contexto y las confirma con el usuario (proponer, no
        asumir). Sin documentos, la fase se saltea.
        """
        if not self._docs:
            return Fase.ENTREVISTA
        nombres = ", ".join(doc.name for doc in self._docs)
        self._salida(f"Analizando la documentación aportada ({nombres})…")
        analisis = Analista(self._provider).analizar(self._docs)
        resumen = analisis.resumen_para_entrevista()
        self._salida(f"Análisis de la documentación:\n{resumen}")
        self._entrevistador.precargar_documentos(resumen)
        return Fase.ENTREVISTA

    def _fase_entrevista(self) -> Fase:
        entrevistador = self._entrevistador
        respuesta = entrevistador.iniciar()

        for _ in range(self._limites.max_turnos_entrevista):
            self._salida(respuesta.message_to_user)
            if respuesta.done and self.spec.esta_completa():
                # Confirmación explícita: el usuario valida la spec antes de
                # auditar, y su última respuesta siempre tiene quién la lea
                # (sin esto, un "así está bien" tipeado acá quedaba en el
                # buffer y lo consumía el próximo input, p. ej. el destino).
                ajuste = self._entrada(
                    "¿Confirmás la especificación? "
                    "(enter = continuar, o escribí qué ajustar) "
                ).strip()
                if es_confirmacion(ajuste):
                    return Fase.AUDITORIA
                entrada = (
                    f"El usuario pide un ajuste antes de cerrar: {ajuste}. "
                    "Aplicalo y volvé a resumir la especificación."
                )
            elif respuesta.done:
                # El modelo cerró antes de tiempo: se lo corrige con feedback.
                entrada = (
                    "Todavía no podés terminar: faltan campos requeridos "
                    f"({', '.join(self.spec.campos_faltantes())}). Seguí preguntando."
                )
            else:
                entrada = self._entrada("> ")
            respuesta = entrevistador.responder(entrada)

        raise LimiteDeTurnosError(
            f"La entrevista superó los {self._limites.max_turnos_entrevista} "
            "turnos sin completar la especificación."
        )

    def _fase_auditoria(self) -> Fase:
        """Ciclo de coherencia (Auditoría → Entrevista), acotado.

        Ante cada hallazgo se repregunta al usuario con la corrección
        propuesta. La severidad define qué puede hacer:

        - 🟡 amarillo: corregir (vuelve por el Entrevistador) o asumir el
          riesgo explícitamente (queda documentado en la spec).
        - 🔴 rojo: bloqueante, no puede asumirse (incluye las reglas no
          negociables, p. ej. secretos hardcodeados): corregir o abortar.

        No se construye sobre una spec con conflictos no resueltos.
        """
        auditor = Auditor(self._provider)
        max_ciclos = self._limites.max_ciclos_coherencia
        pendientes: list[Hallazgo] = []
        detectados: dict[str, Hallazgo] = {}
        # ``max_ciclos`` rondas de resolución + 1 auditoría final de
        # confirmación: el usuario tiene que poder responder a los hallazgos
        # de la última ronda también, no solo a los de las rondas previas
        # (bug real: la última auditoría se reportaba y el ciclo abortaba sin
        # darle turno al usuario para resolverla).
        for ciclo in range(max_ciclos + 1):
            resultado = auditor.auditar(self.spec)
            self.auditoria = resultado
            self._salida(_formatear_reporte(resultado))
            pendientes = resultado.pendientes()
            detectados.update({h.id: h for h in pendientes})
            if not pendientes:
                self.resoluciones = self._clasificar_resoluciones(detectados)
                return Fase.CONSTRUCCION
            if ciclo == max_ciclos:
                break
            self._resolver_hallazgos(pendientes)

        raise CoherenciaNoResueltaError(
            f"Quedaron hallazgos sin resolver tras {max_ciclos} ciclos de "
            "coherencia: " + ", ".join(h.id for h in pendientes)
        )

    def _clasificar_resoluciones(
        self, detectados: dict[str, Hallazgo]
    ) -> list[ResolucionHallazgo]:
        """Cómo terminó cada hallazgo detectado: asumido o corregido.

        Solo se llama cuando la auditoría quedó en verde, así que todo
        hallazgo que no fue asumido explícitamente terminó corregido.
        """
        asumidos = {
            entrada.split(":", 1)[0].strip() for entrada in self.spec.riesgos_asumidos
        }
        return [
            ResolucionHallazgo(
                hallazgo=hallazgo,
                resolucion="asumido" if hallazgo.id in asumidos else "corregido",
            )
            for hallazgo in detectados.values()
        ]

    def _resolver_hallazgos(self, pendientes: list[Hallazgo]) -> None:
        """Ante cada hallazgo, pide la decisión del usuario y la aplica.

        Bug real detectado en uso: el Entrevistador suele cerrar su
        confirmación con una pregunta propia ("¿te parece bien esta
        configuración o preferís otro proveedor?"), y con varios hallazgos
        pendientes el loop pasaba directo al siguiente sin darle al usuario
        turno para responderla — la pregunta quedaba huérfana y la respuesta
        del usuario terminaba interpretada como la decisión del hallazgo
        siguiente. Ahora cada hallazgo cierra con una confirmación explícita
        (mismo patrón que la confirmación de la spec) antes de seguir.
        """
        for hallazgo in pendientes:
            if hallazgo.severidad is Severidad.ROJO:
                detalle = self._entrada(
                    f"El hallazgo '{hallazgo.id}' es 🔴 bloqueante y no puede "
                    "asumirse. ¿Cómo lo querés resolver? (enter = aplicar la "
                    "corrección propuesta / 'abortar' = cancelar el proyecto) "
                )
                if normalizar(detalle) == "abortar":
                    raise CoherenciaNoResueltaError(
                        f"El usuario abortó ante el hallazgo bloqueante "
                        f"'{hallazgo.id}'."
                    )
            else:
                eleccion = self._entrada(
                    f"¿Asumís el riesgo '{hallazgo.id}'? (s = asumir / N = corregir) "
                )
                if eleccion.strip().lower().startswith("s"):
                    self.spec.riesgos_asumidos.append(
                        f"{hallazgo.id}: {hallazgo.mensaje}"
                    )
                    self._salida(f"Riesgo asumido y documentado: {hallazgo.id}")
                    continue
                detalle = self._entrada(
                    "¿Cómo lo querés resolver? (enter = aplicar la corrección propuesta) "
                )
            respuesta = self._entrevistador.responder(
                f"El Auditor encontró una incongruencia [{hallazgo.id}]: "
                f"{hallazgo.mensaje} Corrección propuesta: "
                f"{hallazgo.correccion_propuesta or 'sin propuesta específica'}. "
                "Decisión del usuario: "
                f"{detalle.strip() or 'aplicar la corrección propuesta'}. "
                "Actualizá la especificación en consecuencia."
            )
            self._salida(respuesta.message_to_user)
            self._confirmar_resolucion(hallazgo)

    def _confirmar_resolucion(self, hallazgo: Hallazgo) -> None:
        """Le da al usuario un turno real para reaccionar a la respuesta del
        Entrevistador antes de pasar al siguiente hallazgo (o a auditar de
        nuevo). Acotado: si el usuario sigue pidiendo ajustes sin confirmar,
        se sigue de todos modos tras el límite, como el resto de los loops."""
        for _ in range(self._limites.max_ajustes_por_hallazgo):
            ajuste = self._entrada(
                "(enter para seguir con el siguiente hallazgo, o escribí un "
                "ajuste a esta corrección) "
            ).strip()
            if es_confirmacion(ajuste):
                return
            respuesta = self._entrevistador.responder(
                f"El usuario pide un ajuste sobre cómo se resolvió el hallazgo "
                f"'{hallazgo.id}': {ajuste}. Aplicalo y confirmá brevemente."
            )
            self._salida(respuesta.message_to_user)

    def _fase_construccion(self) -> Fase:
        """Genera el scaffold en un directorio destino elegido por el usuario."""
        constructor = Constructor(self._provider)
        sugerido = Path.cwd() / slug_kebab(self.spec.nombre or "proyecto")

        for _ in range(self._limites.max_intentos_destino):
            crudo = self._entrada(f"¿Dónde genero el proyecto? (enter = {sugerido}) ")
            destino = Path(crudo.strip() or sugerido).expanduser().resolve()
            try:
                resultado = constructor.construir(self.spec, destino)
            except DestinoInvalidoError as exc:
                self._salida(str(exc))
                continue
            self.ruta_proyecto = destino
            self._construccion = resultado
            listado = "\n".join(f"  - {archivo}" for archivo in resultado.archivos)
            self._salida(
                f"Proyecto generado con la plantilla '{resultado.stack}' en "
                f"{destino} ({len(resultado.archivos)} archivos):\n{listado}"
            )
            return Fase.VERIFICACION

        raise DestinoInvalidoError(
            "No se encontró un destino válido tras "
            f"{self._limites.max_intentos_destino} intentos."
        )

    def _fase_verificacion(self) -> Fase:
        """Ciclo de corrección (Verificación → Construcción), acotado.

        Capa de sintaxis: ante una falla, informar + corregir (LLM) +
        re-verificar, con máximo de 3 reintentos por archivo. Con la
        sintaxis en verde corre la capa profunda (builds en Docker, smoke
        tests, linters); sus fallas pasan por el corrector de builds
        (Fase 7, multi-archivo, ciclo acotado). En ambos casos, si algo
        sigue fallando se escala al usuario.
        """
        assert self.ruta_proyecto is not None
        raiz = self.ruta_proyecto
        verificador = Verificador(self._provider)

        resultado = verificador.verificar(raiz)
        self._salida(_formatear_verificacion(resultado))
        max_correcciones = self._limites.max_correcciones_por_archivo
        if resultado.errores():
            for chequeo in resultado.errores():
                for intento in range(1, max_correcciones + 1):
                    self._salida(
                        f"Corrigiendo {chequeo.archivo} "
                        f"(intento {intento}/{max_correcciones})…"
                    )
                    verificador.corregir_archivo(raiz, chequeo.archivo, chequeo.detalle)
                    chequeo = verificador.verificar_archivo(raiz, chequeo.archivo)
                    if chequeo.estado != "error":
                        self._salida(f"{chequeo.archivo} corregido.")
                        break
            resultado = verificador.verificar(raiz)
            self._salida(_formatear_verificacion(resultado))

        if (
            resultado.aprobado()
            and self._construccion is not None
            and self._construccion.verificaciones
        ):
            self._salida("Verificación profunda (builds, smoke tests y linters)…")
            etiqueta = f"pcia-verif-{slug_kebab(self.spec.nombre or 'proyecto')}"
            profundos = verificador.verificar_profundo(
                raiz, self._construccion.verificaciones, etiqueta
            )
            self._salida(_formatear_profundos(profundos))
            profundos = self._corregir_fallas_profundas(
                verificador, raiz, etiqueta, profundos
            )
            resultado = ResultadoVerificacion(
                chequeos=resultado.chequeos, profundos=profundos
            )

        self._verificacion = resultado
        if resultado.aprobado():
            return Fase.ENTREGA

        eleccion = self._entrada(
            "La verificación sigue fallando tras las correcciones. "
            "¿Entrego el proyecto igual? (s/N) "
        )
        if eleccion.strip().lower().startswith("s"):
            self._salida("Entrega con errores de verificación, a pedido del usuario.")
            return Fase.ENTREGA
        raise VerificacionFallidaError(
            "Verificación fallida en: "
            + ", ".join(c.archivo for c in resultado.errores())
        )

    def _corregir_fallas_profundas(
        self,
        verificador: Verificador,
        raiz: Path,
        etiqueta: str,
        profundos: list[Chequeo],
    ) -> list[Chequeo]:
        """Ciclo de corrección de la capa profunda (Fase 7), acotado.

        Best-effort: si el corrector no propone cambios (diagnóstico
        "no se resuelve tocando el scaffold") o no cumple el contrato,
        se corta el ciclo y decide el usuario, como antes de la Fase 7.
        """
        assert self._construccion is not None
        max_correcciones = self._limites.max_correcciones_build
        for intento in range(1, max_correcciones + 1):
            errores = [c for c in profundos if c.estado == "error"]
            if not errores:
                return profundos
            self._salida(
                "Corrigiendo fallas de la verificación profunda "
                f"(intento {intento}/{max_correcciones})…"
            )
            try:
                correccion = verificador.corregir_build(
                    raiz, errores, self._construccion.archivos
                )
            except ContratoInvalidoError as exc:
                self._salida(f"El corrector no produjo una corrección válida: {exc}")
                return profundos
            if not correccion.correcciones:
                self._salida(
                    f"El corrector no propuso cambios: {correccion.diagnostico}"
                )
                return profundos
            self.correcciones_build.append(correccion.diagnostico)
            archivos = ", ".join(c.archivo for c in correccion.correcciones)
            self._salida(
                f"Diagnóstico: {correccion.diagnostico}\n"
                f"Archivos corregidos: {archivos}\n"
                f"(si esta corrección se repite en otros proyectos "
                f"'{self._construccion.stack}', el defecto puede estar en la plantilla)"
            )
            # Ejecutar un Dockerfile reescrito por el LLM corre código nuevo
            # en el host: decisión de alto impacto, la confirma el humano.
            if any(
                Path(c.archivo).name == "Dockerfile" for c in correccion.correcciones
            ):
                eleccion = self._entrada(
                    "El corrector reescribió el Dockerfile. ¿Ejecuto el build "
                    "con el Dockerfile corregido? (S/n) "
                )
                if eleccion.strip().lower().startswith("n"):
                    self._salida(
                        "Re-verificación cancelada: el Dockerfile corregido "
                        "no se ejecutó."
                    )
                    return profundos
            profundos = verificador.verificar_profundo(
                raiz, self._construccion.verificaciones, etiqueta
            )
            self._salida(_formatear_profundos(profundos))
        return profundos

    def _estado_final(self) -> EstadoVerificacion:
        """Estado agregado de la entrega: verificación + riesgos asumidos."""
        estado = self._verificacion.estado() if self._verificacion else "inconcluso"
        if estado == "aprobado" and self.spec.riesgos_asumidos:
            return "aprobado_con_advertencias"
        return estado

    def _fase_entrega(self) -> Fase:
        # Propagación del riesgo: lo asumido en la auditoría no queda solo en
        # el ADR, se advierte en la entrega (y pesa en el estado final).
        if self.spec.riesgos_asumidos:
            lineas = "\n".join(f"  - {riesgo}" for riesgo in self.spec.riesgos_asumidos)
            self._salida(
                f"⚠️ El proyecto se entrega con "
                f"{len(self.spec.riesgos_asumidos)} riesgo(s) asumido(s):\n{lineas}"
            )
        estado_final = self._estado_final()
        self._salida(
            "Estado final de la entrega: "
            f"{EMOJI_ESTADO_FINAL[estado_final]} {estado_final.replace('_', ' ')}"
        )
        registro = RegistroProyecto(
            fecha=dt.datetime.now().isoformat(timespec="seconds"),
            spec=self.spec,
            stack=self._construccion.stack if self._construccion else None,
            ruta_proyecto=str(self.ruta_proyecto) if self.ruta_proyecto else None,
            resoluciones=self.resoluciones,
            verificacion=self._verificacion,
            estado_final=estado_final,
            correcciones_build=self.correcciones_build,
            proveedor=self._proveedor,
            duracion_segundos=round(time.monotonic() - self._inicio, 1),
        )
        self.ruta_spec = self._memoria.guardar(registro)
        self._salida(
            f"Especificación y registro del proyecto guardados en {self.ruta_spec} "
            f"(duración total: {registro.duracion_segundos:.0f}s)"
        )
        return Fase.APRENDIZAJE

    def _fase_aprendizaje(self) -> Fase:
        cantidad = len(self._memoria.cargar_registros())
        mensaje = f"Memoria actualizada: {cantidad} proyecto(s) registrado(s)."
        resumen = self._aprendizaje.resumen_historial()
        if resumen:
            mensaje += (
                "\nPreferencias detectadas para precargar la próxima entrevista:\n"
                + resumen
            )
        self._salida(mensaje)
        return Fase.FIN


def _formatear_reporte(resultado: ResultadoAuditoria) -> str:
    semaforo = resultado.semaforo()
    lineas = [
        f"Auditoría de coherencia — semáforo: {EMOJI_SEMAFORO[semaforo]} {semaforo.value}"
    ]
    if not resultado.hallazgos:
        lineas.append("Sin hallazgos: la especificación es coherente.")
    for hallazgo in resultado.hallazgos:
        lineas.append(
            f"{EMOJI_SEMAFORO[hallazgo.severidad]} [{hallazgo.id}] {hallazgo.mensaje}"
        )
        if hallazgo.correccion_propuesta:
            lineas.append(f"   Corrección propuesta: {hallazgo.correccion_propuesta}")
    return "\n".join(lineas)


def _formatear_verificacion(resultado: ResultadoVerificacion) -> str:
    ok = sum(1 for c in resultado.chequeos if c.estado == "ok")
    omitidos = sum(1 for c in resultado.chequeos if c.estado == "omitido")
    errores = [c for c in resultado.chequeos if c.estado == "error"]
    lineas = [
        f"Verificación de sintaxis: {ok} ok, {len(errores)} con errores, "
        f"{omitidos} sin verificador."
    ]
    for chequeo in errores:
        lineas.append(f"❌ {chequeo.archivo}: {chequeo.detalle}")
    if not errores:
        lineas.append("✅ Todos los archivos verificables son válidos.")
    return "\n".join(lineas)


def _formatear_profundos(profundos: list[Chequeo]) -> str:
    iconos = {"ok": "✅", "error": "❌", "omitido": "⏭️"}
    lineas = ["Resultado de la verificación profunda:"]
    for chequeo in profundos:
        estado = chequeo.estado
        if estado == "omitido" and not chequeo.obligatorio:
            estado += " (opcional)"
        linea = f"{iconos[chequeo.estado]} [{chequeo.archivo}] {estado}"
        if chequeo.detalle:
            linea += f" — {chequeo.detalle}"
        lineas.append(linea)
    return "\n".join(lineas)
