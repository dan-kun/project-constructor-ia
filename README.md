# Project Constructor IA

Sistema multiagente para la **creación asistida y auditada** de la estructura inicial
(scaffold) de proyectos de software: entrevista al usuario, audita la coherencia técnica
de lo que pidió, construye, verifica lo construido con builds reales y aprende de cada
proyecto.

Trabajo final del diplomado *Inteligencia Artificial Aplicada a Organizaciones* (UTN-FRBA).

> El diferencial no es generar archivos: es **cuestionar antes de construir y verificar
> después**. El sistema no entrega nada que no pueda verificar, y declara explícitamente
> lo que no sabe hacer.

## Qué hace

1. **Analiza** (opcional) la documentación del cliente y extrae propuestas *con evidencia
   textual* para precargar la entrevista.
2. **Entrevista** de forma adaptativa hasta completar la especificación del proyecto.
3. **Audita** la coherencia técnica: matriz de reglas determinísticas + pase LLM, con
   semáforo 🟢/🟡/🔴. Un hallazgo rojo es **bloqueante**; uno amarillo puede asumirse y
   queda documentado y propagado a la entrega.
4. **Construye** el scaffold desde plantillas versionadas por stack.
5. **Verifica** de verdad: sintaxis de cada archivo, build en Docker, smoke test dentro de
   la imagen y linters. Ante fallas, corrige en ciclos acotados.
6. **Aprende**: persiste decisiones, resoluciones y diagnósticos para la próxima entrevista.

## Inicio rápido

```bash
pip install -e ".[dev]"     # instalar en modo desarrollo
pytest                      # 169 tests, sin red ni Docker
```

Configurar el proveedor de IA en `config.yaml` (ver más abajo).

### Interfaz de consola

```bash
pcia                                  # entrevista completa por CLI
pcia --docs requerimientos.md         # precargando desde documentación del cliente
```

### Interfaz web

```bash
pip install -e ".[web]"
pcia-web                              # http://127.0.0.1:8000
```

Muestra la entrevista como chat y, en vivo, el estado del ciclo: fase actual,
especificación que se va completando, semáforo de auditoría con sus hallazgos, árbol de
archivos generados y resultado de cada verificación.

> La interfaz web es un **adaptador de IO**: el orquestador recibe su entrada/salida como
> callables, así que la consola y el navegador se enchufan en el mismo puerto sin tocar
> el dominio ni los agentes.

## Arquitectura

Hexagonal, con un orquestador determinístico (máquina de estados, no un agente LLM) que
coordina seis agentes sobre un estado compartido (`ProjectSpec`).

```
Análisis → Entrevista → Auditoría → Construcción → Verificación → Entrega → Aprendizaje
              ↑______________|              |__________↑
           ciclo de coherencia         ciclo de corrección
```

Garantías de diseño:

- **Agnóstico del modelo de IA**: toda interacción pasa por el puerto `LLMProvider`.
  Adaptadores: API de Anthropic, cualquier API compatible con OpenAI (Ollama, llama.cpp,
  LM Studio, Groq…) y la suscripción de Claude vía CLI headless.
- **Toda salida de LLM se valida** contra un contrato Pydantic y se reintenta con el error
  como feedback (máx. 3), luego escala al usuario.
- **Todos los ciclos son acotados** y con límite explícito.
- **Degradación con gracia**: con modelos débiles, las reglas determinísticas del Auditor
  y las verificaciones automáticas sostienen las garantías mínimas.

- **Diagramas** (componentes, máquina de estados, UML de clases y secuencia de una corrida
  real): [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md)
- **Log de ciberseguridad**: riesgos identificados, medidas implementadas con su evidencia y
  riesgo residual declarado: [`docs/SEGURIDAD.md`](docs/SEGURIDAD.md)
- **Autoevaluación UX/UI** contra las heurísticas de Nielsen, con backlog priorizado:
  [`docs/UX.md`](docs/UX.md)
- **Diseño completo**, roadmap, trabajo futuro y **matriz de capacidades** (qué materializa
  realmente cada plantilla y qué queda solo documentado): [`docs/DISENO.md`](docs/DISENO.md)

## Configuración del proveedor

`config.yaml` elige el adaptador sin tocar código:

```yaml
provider: openai_compat        # anthropic_api | openai_compat | claude_subscription

openai_compat:
  base_url: http://localhost:11434/v1   # Ollama, llama.cpp, LM Studio, Groq…
  model: qwen2.5:14b
  api_key: ollama
```

Los archivos `config.*.yaml` están fuera del control de versiones: nunca commitear claves.

## Stacks soportados

| Stack | Materializa | Verificaciones |
|---|---|---|
| FastAPI | API con `/health`, config fail-fast, Docker no-root, tests, CI. Con PostgreSQL: `compose.yaml`, dependencias y módulo de conexión | build Docker + smoke (obligatorias), ruff (opcional) |
| NestJS | API con módulo base, build multi-stage, imagen no-root, CI | build Docker + smoke del módulo compilado |
| Módulo Odoo | manifiesto, modelo, vistas y permisos | ruff (obligatoria) |

Si el stack pedido no tiene plantilla, el sistema lo dice y escala: no improvisa.

## Desarrollo

```bash
pytest                        # suite completa (usa un proveedor falso, nunca LLMs reales)
```

Estructura: `domain/` (modelos y puertos, sin dependencias de frameworks) · `adapters/`
(proveedores de LLM) · `agents/` (los seis agentes + prompts en `.md`) · `orchestrator/`
(máquina de estados) · `web/` (adaptador de navegador) · `memory/` (registro por proyecto).
