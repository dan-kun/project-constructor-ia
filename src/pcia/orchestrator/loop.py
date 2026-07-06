"""Orquestador: máquina de estados explícita del ciclo.

No es un agente LLM, es código determinístico. Secuencia completa:
Análisis de documentos (opcional) → Entrevista → Auditoría → Construcción
→ Verificación → Entrega → Aprendizaje.
"""

from __future__ import annotations

import datetime as dt
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
from pcia.texto import slug_kebab

MAX_TURNOS_ENTREVISTA = 30
MAX_CICLOS_COHERENCIA = 3
MAX_INTENTOS_DESTINO = 3
MAX_CORRECCIONES_POR_ARCHIVO = 3
# Cada ciclo de corrección profunda implica un rebuild (minutos): límite corto.
MAX_CORRECCIONES_BUILD = 2

EMOJI_SEMAFORO = {
    Severidad.VERDE: "🟢",
    Severidad.AMARILLO: "🟡",
    Severidad.ROJO: "🔴",
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
    ) -> None:
        self._provider = provider
        self._memory_dir = Path(memory_dir)
        self._entrada = entrada
        self._salida = salida
        self._docs = [Path(doc) for doc in (docs or [])]
        self.spec = ProjectSpec()
        self.ruta_spec: Path | None = None
        self.ruta_proyecto: Path | None = None
        self.resoluciones: list[ResolucionHallazgo] = []
        self.correcciones_build: list[str] = []
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
        fase = Fase.ANALISIS
        while fase is not Fase.FIN:
            fase = manejadores[fase]()
        assert self.ruta_spec is not None
        return self.ruta_spec

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

        for _ in range(MAX_TURNOS_ENTREVISTA):
            self._salida(respuesta.message_to_user)
            if respuesta.done and self.spec.esta_completa():
                return Fase.AUDITORIA
            if respuesta.done:
                # El modelo cerró antes de tiempo: se lo corrige con feedback.
                entrada = (
                    "Todavía no podés terminar: faltan campos requeridos "
                    f"({', '.join(self.spec.campos_faltantes())}). Seguí preguntando."
                )
            else:
                entrada = self._entrada("> ")
            respuesta = entrevistador.responder(entrada)

        raise LimiteDeTurnosError(
            f"La entrevista superó los {MAX_TURNOS_ENTREVISTA} turnos sin "
            "completar la especificación."
        )

    def _fase_auditoria(self) -> Fase:
        """Ciclo de coherencia (Auditoría → Entrevista), acotado.

        Ante cada hallazgo se repregunta al usuario con la corrección
        propuesta; puede corregir (vuelve por el Entrevistador) o asumir el
        riesgo explícitamente (queda documentado en la spec). No se construye
        sobre una spec con conflictos no resueltos.
        """
        auditor = Auditor(self._provider)
        pendientes: list[Hallazgo] = []
        detectados: dict[str, Hallazgo] = {}
        for ciclo in range(MAX_CICLOS_COHERENCIA):
            resultado = auditor.auditar(self.spec)
            self._salida(_formatear_reporte(resultado))
            pendientes = resultado.pendientes()
            detectados.update({h.id: h for h in pendientes})
            if not pendientes:
                self.resoluciones = self._clasificar_resoluciones(detectados)
                return Fase.CONSTRUCCION
            if ciclo == MAX_CICLOS_COHERENCIA - 1:
                break
            self._resolver_hallazgos(pendientes)

        raise CoherenciaNoResueltaError(
            "Quedaron hallazgos sin resolver tras "
            f"{MAX_CICLOS_COHERENCIA} ciclos de coherencia: "
            + ", ".join(h.id for h in pendientes)
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
        for hallazgo in pendientes:
            eleccion = self._entrada(
                f"¿Asumís el riesgo '{hallazgo.id}'? (s = asumir / N = corregir) "
            )
            if eleccion.strip().lower().startswith("s"):
                self.spec.riesgos_asumidos.append(f"{hallazgo.id}: {hallazgo.mensaje}")
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

    def _fase_construccion(self) -> Fase:
        """Genera el scaffold en un directorio destino elegido por el usuario."""
        constructor = Constructor(self._provider)
        sugerido = Path.cwd() / slug_kebab(self.spec.nombre or "proyecto")

        for _ in range(MAX_INTENTOS_DESTINO):
            crudo = self._entrada(f"¿Dónde genero el proyecto? (enter = {sugerido}) ")
            destino = Path(crudo.strip() or sugerido).expanduser()
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
            f"No se encontró un destino válido tras {MAX_INTENTOS_DESTINO} intentos."
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
        if resultado.errores():
            for chequeo in resultado.errores():
                for intento in range(1, MAX_CORRECCIONES_POR_ARCHIVO + 1):
                    self._salida(
                        f"Corrigiendo {chequeo.archivo} "
                        f"(intento {intento}/{MAX_CORRECCIONES_POR_ARCHIVO})…"
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
        for intento in range(1, MAX_CORRECCIONES_BUILD + 1):
            errores = [c for c in profundos if c.estado == "error"]
            if not errores:
                return profundos
            self._salida(
                "Corrigiendo fallas de la verificación profunda "
                f"(intento {intento}/{MAX_CORRECCIONES_BUILD})…"
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
            profundos = verificador.verificar_profundo(
                raiz, self._construccion.verificaciones, etiqueta
            )
            self._salida(_formatear_profundos(profundos))
        return profundos

    def _fase_entrega(self) -> Fase:
        registro = RegistroProyecto(
            fecha=dt.datetime.now().isoformat(timespec="seconds"),
            spec=self.spec,
            stack=self._construccion.stack if self._construccion else None,
            ruta_proyecto=str(self.ruta_proyecto) if self.ruta_proyecto else None,
            resoluciones=self.resoluciones,
            verificacion=self._verificacion,
            correcciones_build=self.correcciones_build,
        )
        self.ruta_spec = self._memoria.guardar(registro)
        self._salida(f"Especificación y registro del proyecto guardados en {self.ruta_spec}")
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
        linea = f"{iconos[chequeo.estado]} [{chequeo.archivo}] {chequeo.estado}"
        if chequeo.detalle:
            linea += f" — {chequeo.detalle}"
        lineas.append(linea)
    return "\n".join(lineas)
