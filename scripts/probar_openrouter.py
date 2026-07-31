"""Compara dos modelos gratis de OpenRouter con una pregunta real del sistema.

Lee base_url/api_key de config.local.yaml (gitignoreado) y prueba ambos
modelos, cronometrando cada uno. Nunca imprime la api_key.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from pcia.adapters.openai_compat import OpenAICompatProvider  # noqa: E402
from pcia.config import cargar_config  # noqa: E402
from pcia.domain.ports import ChatMessage  # noqa: E402

MODELOS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
]

PREGUNTA = (
    "Estoy definiendo la arquitectura de una API REST interna de gestión de "
    "tareas para un equipo de 5 personas, con FastAPI y PostgreSQL. "
    "¿Te parece proporcionada esta elección de base de datos para ese "
    "alcance, o recomendarías algo más liviano? Respondé en 2-3 oraciones."
)


def main() -> int:
    config = cargar_config(RAIZ / "config.local.yaml")
    seccion = config["openai_compat"]
    base_url = seccion["base_url"]
    api_key = seccion["api_key"]

    for modelo in MODELOS:
        print(f"\n=== {modelo} ===")
        provider = OpenAICompatProvider(base_url=base_url, model=modelo, api_key=api_key)
        t0 = time.time()
        try:
            respuesta = provider.generate(
                "Sos un arquitecto de software senior. Respondé siempre en español.",
                [ChatMessage(role="user", content=PREGUNTA)],
            )
            print(f"[{time.time() - t0:.1f}s]\n{respuesta}")
        except Exception as exc:  # noqa: BLE001 — es un script de diagnóstico
            print(f"[{time.time() - t0:.1f}s] ERROR: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
