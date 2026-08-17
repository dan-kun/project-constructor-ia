"""Agente Constructor.

Genera el scaffold del proyecto en un directorio destino:

1. **Plantillas por stack** (``templates/*.yaml``): árbol de archivos
   determinístico con tokens ``[[NOMBRE]]``, ``[[NOMBRE_KEBAB]]``,
   ``[[PAQUETE]]`` y ``[[DESCRIPCION]]``.
2. **Pase LLM para lo específico**: README.md y ADR-001 (con las decisiones
   de la spec y los riesgos asumidos como riesgo aceptado), con contrato
   JSON estricto y reintentos.

Nada se escribe en disco hasta tener el render completo y las docs
generadas: si el LLM falla, no queda un scaffold a medias.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pcia.agents.llm_json import consultar_con_contrato
from pcia.domain.models import (
    ProjectSpec,
    ResultadoConstruccion,
    VerificacionProfunda,
)
from pcia.domain.ports import ChatMessage, LLMProvider
from pcia.texto import normalizar, slug_kebab, slug_snake

RUTA_TEMPLATES = Path(__file__).parent / "templates"
RUTA_PROMPT = Path(__file__).parent / "prompts" / "constructor.md"
RUTA_ADR = "docs/adr/ADR-001-decisiones-iniciales.md"

TOKEN_SIN_REEMPLAZAR = re.compile(r"\[\[[A-Z_]+\]\]")


class ConstruccionError(Exception):
    """Falla al generar el scaffold del proyecto."""


class PlantillaNoEncontradaError(ConstruccionError):
    """Ninguna plantilla matchea el stack de la spec."""


class DestinoInvalidoError(ConstruccionError):
    """El directorio destino no puede usarse (por ejemplo, no está vacío)."""


class CondicionActivacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campo: str
    contiene: list[str] = Field(min_length=1)


class Condicional(BaseModel):
    """Bloque de plantilla que solo se materializa si la spec lo pide.

    Es el mecanismo por el que una decisión de la entrevista modifica
    realmente el scaffold (p. ej. ``base_datos=postgresql`` agrega el
    compose, las dependencias y el módulo de conexión). ``archivos`` suma
    archivos nuevos; ``fragmentos`` inyecta contenido en tokens
    ``[[ASI]]`` de los archivos base (vacíos si el bloque no activa).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    cuando: CondicionActivacion
    archivos: dict[str, str] = Field(default_factory=dict)
    fragmentos: dict[str, str] = Field(default_factory=dict)


class Plantilla(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stack: str
    descripcion: str = ""
    detecta: list[str] = Field(min_length=1)
    archivos: dict[str, str] = Field(min_length=1)
    verificaciones: list[VerificacionProfunda] = Field(default_factory=list)
    condicionales: list[Condicional] = Field(default_factory=list)
    # Sección "Cómo ejecutar" del README: comandos determinísticos de la
    # plantilla. El LLM redacta contexto y decisiones, nunca instrucciones
    # operativas (un LLM puede inventar comandos sobre archivos que no existen).
    instrucciones: str = ""


class DocsGeneradas(BaseModel):
    """Contrato de salida del pase LLM del Constructor."""

    model_config = ConfigDict(extra="forbid")

    readme_markdown: str = Field(min_length=1)
    adr_markdown: str = Field(min_length=1)


def cargar_plantillas(directorio: Path = RUTA_TEMPLATES) -> list[Plantilla]:
    """Carga y valida las plantillas (falla temprano si están mal escritas)."""
    plantillas = []
    campos_spec = ProjectSpec.campos_validos()
    for ruta in sorted(directorio.glob("*.yaml")):
        plantilla = Plantilla.model_validate(
            yaml.safe_load(ruta.read_text(encoding="utf-8"))
        )
        rutas_declaradas = [
            *plantilla.archivos,
            *(r for c in plantilla.condicionales for r in c.archivos),
        ]
        for relativa in rutas_declaradas:
            pura = PurePosixPath(relativa)
            if pura.is_absolute() or ".." in pura.parts:
                raise ValueError(
                    f"Plantilla '{plantilla.stack}': ruta insegura {relativa!r}"
                )
        for condicional in plantilla.condicionales:
            if condicional.cuando.campo not in campos_spec:
                raise ValueError(
                    f"Plantilla '{plantilla.stack}': el condicional "
                    f"'{condicional.id}' usa un campo desconocido de la spec: "
                    f"{condicional.cuando.campo!r}"
                )
            for token in condicional.fragmentos:
                if not re.fullmatch(r"\[\[[A-Z_]+\]\]", token):
                    raise ValueError(
                        f"Plantilla '{plantilla.stack}': el condicional "
                        f"'{condicional.id}' declara un fragmento con token "
                        f"inválido: {token!r} (formato esperado: [[ASI]])"
                    )
        for verificacion in plantilla.verificaciones:
            if verificacion.tipo != "docker_build" and not verificacion.comando:
                raise ValueError(
                    f"Plantilla '{plantilla.stack}': la verificación "
                    f"'{verificacion.id}' ({verificacion.tipo}) necesita un comando"
                )
        plantillas.append(plantilla)
    if not plantillas:
        raise ValueError(f"No hay plantillas en {directorio}")
    return plantillas


class Constructor:
    """Construye el scaffold a partir de la spec auditada."""

    def __init__(
        self, provider: LLMProvider, ruta_templates: Path = RUTA_TEMPLATES
    ) -> None:
        self._provider = provider
        self._plantillas = cargar_plantillas(ruta_templates)
        self._plantilla_prompt = RUTA_PROMPT.read_text(encoding="utf-8")

    def construir(self, spec: ProjectSpec, destino: Path) -> ResultadoConstruccion:
        plantilla = self._seleccionar(spec)
        _validar_destino(destino)

        archivos, verificaciones, instrucciones = _renderizar(plantilla, spec)
        docs = self._generar_docs(spec, plantilla, sorted(archivos))
        readme = docs.readme_markdown.rstrip() + "\n"
        if instrucciones:
            readme += "\n" + instrucciones.rstrip() + "\n"
        archivos["README.md"] = readme
        archivos[RUTA_ADR] = docs.adr_markdown

        for relativa, contenido in archivos.items():
            ruta = destino / relativa
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(contenido, encoding="utf-8")

        return ResultadoConstruccion(
            stack=plantilla.stack,
            raiz=str(destino),
            archivos=sorted(archivos),
            verificaciones=verificaciones,
        )

    def _seleccionar(self, spec: ProjectSpec) -> Plantilla:
        texto = normalizar(
            " ".join(
                filter(
                    None,
                    [spec.framework, spec.tipo_proyecto, spec.lenguaje, spec.descripcion],
                )
            )
        )
        for plantilla in self._plantillas:
            if any(normalizar(palabra) in texto for palabra in plantilla.detecta):
                return plantilla
        disponibles = ", ".join(p.stack for p in self._plantillas)
        raise PlantillaNoEncontradaError(
            f"No hay plantilla para el stack de la spec (framework="
            f"{spec.framework!r}). Stacks disponibles: {disponibles}."
        )

    def _generar_docs(
        self, spec: ProjectSpec, plantilla: Plantilla, archivos: list[str]
    ) -> DocsGeneradas:
        system_prompt = (
            self._plantilla_prompt.replace(
                "[[ESTADO_SPEC]]", spec.model_dump_json(indent=2)
            )
            .replace("[[STACK]]", plantilla.stack)
            .replace("[[ARCHIVOS]]", "\n".join(f"- {a}" for a in archivos))
            .replace(
                "[[RIESGOS_ASUMIDOS]]",
                "\n".join(f"- {r}" for r in spec.riesgos_asumidos) or "ninguno",
            )
        )
        mensajes = [
            ChatMessage(
                role="user",
                content="Generá el README y el ADR-001 según tus instrucciones.",
            )
        ]
        docs, _ = consultar_con_contrato(
            self._provider, system_prompt, mensajes, DocsGeneradas
        )
        return docs


def _condicional_activo(condicional: Condicional, spec: ProjectSpec) -> bool:
    valor = getattr(spec, condicional.cuando.campo)
    if isinstance(valor, list):
        valor = " ".join(valor)
    if not valor:
        return False
    texto = normalizar(valor)
    return any(normalizar(palabra) in texto for palabra in condicional.cuando.contiene)


def _renderizar(
    plantilla: Plantilla, spec: ProjectSpec
) -> tuple[dict[str, str], list[VerificacionProfunda], str]:
    nombre = spec.nombre or "proyecto"
    tokens = {
        "[[NOMBRE]]": nombre,
        "[[NOMBRE_KEBAB]]": slug_kebab(nombre),
        "[[PAQUETE]]": slug_snake(nombre),
        "[[DESCRIPCION]]": spec.descripcion or "",
        "[[CORS_ORIGINS]]": json.dumps(_origenes_cors(spec.cors)),
    }
    activos = [c for c in plantilla.condicionales if _condicional_activo(c, spec)]
    # Todo fragmento declarado arranca vacío (el token desaparece si su
    # condicional no activa); los activos suman su contenido en orden.
    fragmentos = {
        token: "" for c in plantilla.condicionales for token in c.fragmentos
    }
    for condicional in activos:
        for token, contenido in condicional.fragmentos.items():
            fragmentos[token] += contenido

    def render(texto: str) -> str:
        for token, valor in fragmentos.items():
            texto = texto.replace(token, valor)
        for token, valor in tokens.items():
            texto = texto.replace(token, valor)
        resto = TOKEN_SIN_REEMPLAZAR.search(texto)
        if resto:
            raise ConstruccionError(
                f"La plantilla '{plantilla.stack}' usa un token desconocido: "
                f"{resto.group()}"
            )
        return texto

    archivos = {
        render(ruta): render(contenido) for ruta, contenido in plantilla.archivos.items()
    }
    for condicional in activos:
        for ruta, contenido in condicional.archivos.items():
            ruta_render = render(ruta)
            if ruta_render in archivos:
                raise ConstruccionError(
                    f"El condicional '{condicional.id}' de la plantilla "
                    f"'{plantilla.stack}' pisa el archivo {ruta_render!r}"
                )
            archivos[ruta_render] = render(contenido)
    verificaciones = [
        verificacion.model_copy(
            update={"comando": [render(parte) for parte in verificacion.comando]}
        )
        for verificacion in plantilla.verificaciones
    ]
    return archivos, verificaciones, render(plantilla.instrucciones)


def _origenes_cors(configuracion: str | None) -> list[str]:
    """Extrae orígenes explícitos sin abrir CORS por defecto."""
    if configuracion:
        origenes = re.findall(r"https?://[^\s,;]+", configuracion)
        if origenes:
            return origenes
    return ["http://localhost:3000"]


def _validar_destino(destino: Path) -> None:
    if destino.exists():
        if not destino.is_dir():
            raise DestinoInvalidoError(f"El destino {destino} existe y no es un directorio.")
        if any(destino.iterdir()):
            raise DestinoInvalidoError(
                f"El directorio {destino} no está vacío; elegí otro destino."
            )
