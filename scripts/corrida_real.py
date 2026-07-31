"""Corrida real end-to-end para la evidencia de la entrega final.

No es parte del sistema (vive fuera de src/pcia): guía al Orquestador con
respuestas que apuntan al campo que realmente falta en la spec en cada
turno (más robusto que simular teclas por posición contra una entrevista
adaptativa). Usa el proveedor real configurado en config.yaml (Ollama
local, qwen2.5:14b) — sin mocks, sin FakeProvider.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from pcia.config import cargar_config, crear_provider  # noqa: E402
from pcia.orchestrator.loop import Orquestador  # noqa: E402

RESPUESTAS_POR_CAMPO = {
    "nombre": "api-tareas-demo",
    "descripcion": "Una API REST de gestión de tareas para un equipo interno de 5 personas.",
    "tipo_proyecto": "API backend",
    "lenguaje": "Python",
    "framework": "FastAPI",
    "arquitectura": "hexagonal simple",
    "base_datos": "PostgreSQL",
    "autenticacion": "OAuth2 delegado a un proveedor externo (Auth0), con refresh tokens y protección CSRF ya resueltas por el proveedor",
    "gestion_secretos": "variables de entorno, .env fuera de git",
    "infraestructura": "Docker y docker-compose",
    "ci_cd": "GitHub Actions",
    "alcance": "MVP interno, sin alta disponibilidad ni multi-tenant",
}

orquestador: Orquestador | None = None


def entrada(prompt: str) -> str:
    assert orquestador is not None
    faltantes = orquestador.spec.campos_faltantes()
    if faltantes and not any(
        marca in prompt
        for marca in ("¿Confirmás", "¿Asumís", "¿Cómo lo querés resolver", "bloqueante")
    ):
        respuesta = RESPUESTAS_POR_CAMPO.get(
            faltantes[0], "Usá tu criterio, elegí algo razonable y proporcional al alcance."
        )
    elif "Entrego el proyecto igual" in prompt:
        respuesta = "s"
    elif "¿Asumís el riesgo" in prompt:
        # segunda corrida: asumir amarillos para llegar a construcción/verificación
        # (la primera corrida ya documentó el ciclo de corrección escalando por
        # un hallazgo rojo que no convergía — ver corrida-1-escalada.log)
        respuesta = "s"
    else:
        respuesta = ""  # confirmaciones y defaults sugeridos
    print(f">>> [entrada] {prompt.strip()}\n>>> [respuesta] {respuesta!r}", flush=True)
    return respuesta


def salida(texto: str) -> None:
    print(f"\n{texto}\n", flush=True)


def main() -> int:
    config = cargar_config(RAIZ / "config.yaml")
    provider = crear_provider(config)
    nombre_proveedor = config.get("provider", "")
    modelo = (config.get(nombre_proveedor) or {}).get("model")

    global orquestador
    orquestador = Orquestador(
        provider,
        memory_dir=RAIZ / "memory",
        entrada=entrada,
        salida=salida,
        proveedor=f"{nombre_proveedor}:{modelo}" if modelo else nombre_proveedor,
    )

    t0 = time.time()
    ruta = orquestador.ejecutar()
    print(f"\n=== FIN — spec guardada en {ruta} — duración total {time.time()-t0:.0f}s ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
