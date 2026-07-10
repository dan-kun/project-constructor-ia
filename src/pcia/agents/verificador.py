"""Agente Verificador.

Dos capas:

- **Fase 4a — sintaxis**: verificación por extensión (py, json, yaml, toml,
  xml, csv) de todo archivo generado; lo que no tiene verificador se
  reporta como ``omitido``. El README, generado por LLM, se chequea por
  consistencia: toda ruta de archivo mencionada en sus bloques de código
  debe existir en el scaffold (la documentación no puede inventar archivos).
  Ante una falla, ``corregir_archivo`` pide al LLM la corrección mínima;
  el ciclo acotado lo maneja el orquestador.
- **Fase 4b — profunda**: builds en Docker, smoke tests dentro de la imagen
  y linters host-side, según lo que declare la plantilla del stack. Si la
  herramienta no está disponible (docker, linter), el chequeo se reporta
  como ``omitido``: degradación con gracia, nunca falla por el entorno.

Fase 7: las fallas profundas también se corrigen (``corregir_build``), pero
con un contrato multi-archivo: el LLM recibe el error del build más el
contenido del scaffold y propone correcciones sobre archivos existentes.
El ciclo acotado (y la escalada al usuario) lo maneja el orquestador.
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

try:  # tomllib es stdlib desde Python 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depende de la versión de Python
    import tomli as tomllib

from pcia.agents.llm_json import consultar_con_contrato
from pcia.domain.models import Chequeo, ResultadoVerificacion, VerificacionProfunda
from pcia.domain.ports import ChatMessage, LLMProvider

RUTA_PROMPT = Path(__file__).parent / "prompts" / "verificador.md"
RUTA_PROMPT_BUILD = Path(__file__).parent / "prompts" / "corrector_build.md"
TIMEOUT_BUILD_SEGUNDOS = 600.0
TIMEOUT_COMANDO_SEGUNDOS = 180.0
MAX_DETALLE = 1500
# Contexto acotado para el corrector de builds (modelos locales tienen poco).
MAX_CONTENIDO_ARCHIVO = 4000
# Archivos que no afectan builds: no gastan contexto del corrector.
IRRELEVANTES_PARA_BUILD = ("README.md", "docs/")

# Chequeo de consistencia del README (documentación generada por LLM):
# toda ruta de archivo mencionada en sus bloques de código debe existir en
# el scaffold. Solo cuentan tokens con estas extensiones; lo demás (binarios,
# flags, módulos con ':') no es verificable y se ignora.
EXTENSIONES_REFERENCIABLES = frozenset(
    "py ts js json yaml yml toml md txt csv xml cfg ini example sh lock html css".split()
)
PATRON_BLOQUE_CODIGO = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
PATRON_RUTA = re.compile(r"[\w.-]+(?:/[\w.-]+)*")


class CorreccionArchivo(BaseModel):
    """Contrato de salida del pase LLM del Verificador."""

    model_config = ConfigDict(extra="forbid")

    contenido_corregido: str = Field(min_length=1)


class CorreccionArchivoBuild(BaseModel):
    """Una corrección propuesta por el corrector de builds (Fase 7)."""

    model_config = ConfigDict(extra="forbid")

    archivo: str = Field(min_length=1)
    contenido_corregido: str = Field(min_length=1)


class CorreccionBuild(BaseModel):
    """Contrato de salida del corrector de builds.

    ``correcciones`` vacía es válida: significa que el LLM diagnosticó que
    la falla no se resuelve tocando el scaffold (se escala al usuario).
    """

    model_config = ConfigDict(extra="forbid")

    diagnostico: str = Field(min_length=1)
    correcciones: list[CorreccionArchivoBuild] = Field(default_factory=list)


def _chequear_python(texto: str) -> None:
    compile(texto, "<generado>", "exec")


def _chequear_json(texto: str) -> None:
    json.loads(texto)


def _chequear_yaml(texto: str) -> None:
    yaml.safe_load(texto)


def _chequear_toml(texto: str) -> None:
    tomllib.loads(texto)


def _chequear_xml(texto: str) -> None:
    ET.fromstring(texto)


def _chequear_csv(texto: str) -> None:
    filas = list(csv.reader(io.StringIO(texto)))
    if not filas:
        raise ValueError("CSV vacío")
    columnas = {len(fila) for fila in filas if fila}
    if len(columnas) > 1:
        raise ValueError(f"cantidad de columnas dispareja entre filas: {sorted(columnas)}")


VERIFICADORES: dict[str, Callable[[str], None]] = {
    ".py": _chequear_python,
    ".json": _chequear_json,
    ".yaml": _chequear_yaml,
    ".yml": _chequear_yaml,
    ".toml": _chequear_toml,
    ".xml": _chequear_xml,
    ".csv": _chequear_csv,
}


class Verificador:
    """Verifica el proyecto generado y corrige archivos con el LLM."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._plantilla = RUTA_PROMPT.read_text(encoding="utf-8")
        self._plantilla_build = RUTA_PROMPT_BUILD.read_text(encoding="utf-8")

    def verificar(self, raiz: Path) -> ResultadoVerificacion:
        """Chequea la sintaxis de todos los archivos bajo ``raiz``."""
        chequeos = [
            self.verificar_archivo(raiz, str(archivo.relative_to(raiz)))
            for archivo in sorted(raiz.rglob("*"))
            if archivo.is_file()
        ]
        return ResultadoVerificacion(chequeos=chequeos)

    def verificar_archivo(self, raiz: Path, relativa: str) -> Chequeo:
        if relativa == "README.md":
            return _chequear_referencias_readme(raiz, relativa)
        verificador = VERIFICADORES.get(Path(relativa).suffix.lower())
        if verificador is None:
            return Chequeo(
                archivo=relativa,
                estado="omitido",
                detalle="sin verificador de sintaxis para esta extensión (Fase 4b)",
                obligatorio=False,  # omitido por diseño, no por falta de herramientas
            )
        try:
            verificador((raiz / relativa).read_text(encoding="utf-8"))
        except Exception as exc:
            return Chequeo(archivo=relativa, estado="error", detalle=str(exc))
        return Chequeo(archivo=relativa, estado="ok")

    def verificar_profundo(
        self,
        raiz: Path,
        verificaciones: list[VerificacionProfunda],
        etiqueta_imagen: str,
    ) -> list[Chequeo]:
        """Corre los chequeos profundos declarados por la plantilla (Fase 4b)."""
        chequeos: list[Chequeo] = []
        imagen_construida = False
        for verificacion in verificaciones:
            if verificacion.tipo in ("docker_build", "docker_run") and not _binario_disponible("docker"):
                chequeo = Chequeo(
                    archivo=verificacion.id,
                    estado="omitido",
                    detalle="docker no está disponible en este entorno",
                )
            elif verificacion.tipo == "docker_build":
                chequeo = _correr(
                    verificacion.id,
                    ["docker", "build", "-t", etiqueta_imagen, "."],
                    raiz,
                    TIMEOUT_BUILD_SEGUNDOS,
                )
                imagen_construida = chequeo.estado == "ok"
            elif verificacion.tipo == "docker_run":
                if not imagen_construida:
                    chequeo = Chequeo(
                        archivo=verificacion.id,
                        estado="omitido",
                        detalle="requiere una imagen construida (docker_build ok)",
                    )
                else:
                    chequeo = _correr(
                        verificacion.id,
                        ["docker", "run", "--rm", etiqueta_imagen, *verificacion.comando],
                        raiz,
                        TIMEOUT_COMANDO_SEGUNDOS,
                    )
            else:  # comando host-side (linters)
                if verificacion.requiere and not _binario_disponible(verificacion.requiere):
                    chequeo = Chequeo(
                        archivo=verificacion.id,
                        estado="omitido",
                        detalle=f"'{verificacion.requiere}' no está disponible en este entorno",
                    )
                else:
                    chequeo = _correr(
                        verificacion.id, verificacion.comando, raiz, TIMEOUT_COMANDO_SEGUNDOS
                    )
            chequeos.append(
                chequeo.model_copy(update={"obligatorio": verificacion.obligatoria})
            )

        if imagen_construida:
            # Limpieza best-effort de la imagen de verificación.
            try:
                _ejecutar(["docker", "rmi", "-f", etiqueta_imagen], raiz, 60.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        return chequeos

    def corregir_archivo(self, raiz: Path, relativa: str, error: str) -> None:
        """Pide al LLM la corrección mínima y reescribe el archivo."""
        ruta = raiz / relativa
        system_prompt = (
            self._plantilla.replace("[[RUTA]]", relativa)
            .replace("[[ERROR]]", error)
            .replace("[[CONTENIDO]]", ruta.read_text(encoding="utf-8"))
        )
        mensajes = [
            ChatMessage(role="user", content="Corregí el archivo según tus instrucciones.")
        ]
        correccion, _ = consultar_con_contrato(
            self._provider, system_prompt, mensajes, CorreccionArchivo
        )
        ruta.write_text(correccion.contenido_corregido, encoding="utf-8")

    def corregir_build(
        self, raiz: Path, errores: list[Chequeo], archivos: list[str]
    ) -> CorreccionBuild:
        """Corrección multi-archivo de fallas profundas (Fase 7).

        El LLM recibe los errores y el contenido del scaffold, diagnostica la
        causa raíz y propone correcciones SOLO sobre archivos existentes (el
        contrato lo valida y reintenta con feedback). Las correcciones se
        aplican recién cuando el contrato completo es válido.
        """
        relevantes = [
            relativa
            for relativa in archivos
            if not relativa.startswith(IRRELEVANTES_PARA_BUILD)
        ]
        contenidos = "\n\n".join(
            f"--- {relativa} ---\n"
            + (raiz / relativa).read_text(encoding="utf-8")[:MAX_CONTENIDO_ARCHIVO]
            for relativa in relevantes
        )
        system_prompt = (
            self._plantilla_build.replace(
                "[[ERRORES]]",
                "\n".join(f"[{c.archivo}] {c.detalle}" for c in errores),
            )
            .replace("[[LISTADO]]", "\n".join(f"- {a}" for a in relevantes))
            .replace("[[CONTENIDOS]]", contenidos)
        )
        mensajes = [
            ChatMessage(
                role="user",
                content="Diagnosticá la falla y corregí según tus instrucciones.",
            )
        ]

        def validar(correccion: CorreccionBuild) -> None:
            desconocidos = sorted(
                {c.archivo for c in correccion.correcciones} - set(relevantes)
            )
            if desconocidos:
                raise ValueError(
                    f"Archivos fuera del scaffold: {', '.join(desconocidos)}. "
                    f"Solo podés corregir: {', '.join(relevantes)}"
                )

        correccion, _ = consultar_con_contrato(
            self._provider, system_prompt, mensajes, CorreccionBuild, postproceso=validar
        )
        for cambio in correccion.correcciones:
            (raiz / cambio.archivo).write_text(
                cambio.contenido_corregido, encoding="utf-8"
            )
        return correccion


def _referencias_a_archivos(texto: str) -> set[str]:
    """Rutas de archivo mencionadas en los bloques de código de un markdown."""
    referencias = set()
    for bloque in PATRON_BLOQUE_CODIGO.findall(texto):
        for token in re.split(r"""[\s`'"()\[\]{},;=]+""", bloque):
            if ":" in token:  # URLs, módulos python:objeto, scripts npm
                continue
            limpio = token[2:] if token.startswith("./") else token
            sufijo = PurePosixPath(limpio).suffix.lstrip(".").lower()
            if sufijo in EXTENSIONES_REFERENCIABLES and PATRON_RUTA.fullmatch(limpio):
                referencias.add(limpio)
    return referencias


def _chequear_referencias_readme(raiz: Path, relativa: str) -> Chequeo:
    """El README no puede referenciar archivos que no existen en el scaffold."""
    texto = (raiz / relativa).read_text(encoding="utf-8")
    referencias = _referencias_a_archivos(texto)
    faltantes = sorted(r for r in referencias if not (raiz / r).exists())
    if faltantes:
        return Chequeo(
            archivo=relativa,
            estado="error",
            detalle=(
                "referencia archivos que no existen en el scaffold: "
                + ", ".join(faltantes)
            ),
        )
    return Chequeo(
        archivo=relativa,
        estado="ok",
        detalle=f"{len(referencias)} referencia(s) a archivos verificadas",
    )


def _binario_disponible(nombre: str) -> bool:
    return shutil.which(nombre) is not None


def _ejecutar(comando: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        comando, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


def _correr(id_chequeo: str, comando: list[str], cwd: Path, timeout: float) -> Chequeo:
    """Ejecuta un comando y lo traduce a un Chequeo (ok / error con detalle)."""
    try:
        resultado = _ejecutar(comando, cwd, timeout)
    except subprocess.TimeoutExpired:
        return Chequeo(
            archivo=id_chequeo,
            estado="error",
            detalle=f"timeout tras {timeout:.0f}s: {' '.join(comando)}",
        )
    except OSError as exc:
        return Chequeo(archivo=id_chequeo, estado="error", detalle=str(exc))
    if resultado.returncode != 0:
        salida = (resultado.stderr or resultado.stdout or "").strip()
        return Chequeo(
            archivo=id_chequeo,
            estado="error",
            detalle=f"código {resultado.returncode}: {salida[-MAX_DETALLE:]}",
        )
    return Chequeo(archivo=id_chequeo, estado="ok")
