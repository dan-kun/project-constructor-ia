"""Reproduce el estado exacto de la spec de conversacion.txt justo antes de
que la auditoría explotara en 6 hallazgos nuevos (3er ciclo), para comparar
el comportamiento del Auditor antes/después del ajuste de alcance en el
prompt. Usa el mismo proveedor real (config.local.yaml, OpenRouter)."""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from pcia.agents.auditor import Auditor  # noqa: E402
from pcia.config import cargar_config, crear_provider  # noqa: E402
from pcia.domain.models import ProjectSpec  # noqa: E402

spec = ProjectSpec(
    nombre="respi",
    descripcion="API REST para gestionar autopartes",
    tipo_proyecto="API REST",
    alcance="producto en producción",
    lenguaje="Python",
    framework="FastAPI",
    arquitectura=(
        "hexagonal (puertos y adaptadores), con estructura de carpetas "
        "domain/application/infrastructure/interfaces y tests de arquitectura "
        "(import-linter/pytest-archon) en CI"
    ),
    base_datos=(
        "PostgreSQL, con Alembic para migraciones (directorio "
        "infrastructure/migrations, step en CI antes del deploy) y driver "
        "asyncpg + SQLAlchemy 2.0 async"
    ),
    autenticacion="JWT",
    gestion_secretos=(
        "Kubernetes Secrets inyectados como variables de entorno en los pods "
        "(.env solo para desarrollo local, fuera de la imagen y el repo)"
    ),
    infraestructura="Docker + Kubernetes",
    ci_cd="GitHub Actions",
    riesgos_asumidos=[
        "observabilidad-ausente: sin stack de observabilidad definido",
        "connection-pooling-postgresql-k8s: sin PgBouncer ni pooler gestionado",
    ],
)


def main() -> int:
    config = cargar_config(RAIZ / "config.local.yaml")
    provider = crear_provider(config)
    auditor = Auditor(provider)

    resultado = auditor.auditar(spec)
    conteo = {"rojo": 0, "amarillo": 0, "verde": 0}
    for h in resultado.hallazgos:
        conteo[h.severidad.value] += 1
        print(f"{h.severidad.value.upper():8} [{h.id}] {h.mensaje}")

    print(f"\nSemáforo: {resultado.semaforo().value}")
    print(f"Conteo: {conteo}")
    print(f"Pendientes (bloquean): {len(resultado.pendientes())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
