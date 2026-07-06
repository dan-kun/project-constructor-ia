# Project Constructor IA

Agente inteligente para la creación asistida y auditada de estructuras de proyectos de software.
Proyecto para el diplomado "Inteligencia Artificial Aplicada a Organizaciones" (UTN-FRBA).
El diseño conceptual completo está en `docs/DISENO.md` — **leerlo antes de implementar cualquier módulo**.

## Qué es

Sistema multiagente que:
1. **Analiza** (opcional, `--docs`) documentación del cliente (`.md`/`.txt`) y extrae propuestas con evidencia textual para precargar la entrevista.
2. **Entrevista** al usuario de forma adaptativa para definir la especificación de un proyecto nuevo (tipo, lenguaje, framework, arquitectura, seguridad, infra, CI/CD).
3. **Audita** la coherencia técnica de la especificación antes de construir (híbrido: reglas determinísticas + LLM).
4. **Construye** el scaffold del proyecto con criterios profesionales.
5. **Verifica** lo construido (parseo de configs, builds, linters, smoke tests) y corrige fallas.
6. **Aprende**: persiste decisiones y resultados para mejorar futuras entrevistas y auditorías.

## Arquitectura (reglas NO negociables)

- **Hexagonal**: el dominio (`src/pcia/domain/`) no importa nada de adaptadores ni frameworks.
- **Agnóstico del modelo de IA**: TODA interacción con LLMs pasa por el puerto `LLMProvider`
  (`src/pcia/domain/ports.py`). Nunca llamar a un proveedor directamente desde agentes u orquestador.
  Adaptadores disponibles: `anthropic_api`, `openai_compat` (Ollama/LM Studio/Groq/etc.),
  `claude_subscription` (CLI `claude -p` headless con suscripción Pro/Max).
- **Estado compartido**: la `ProjectSpec` (Pydantic, `src/pcia/domain/models.py`) es la única
  fuente de verdad entre agentes. Los agentes no se comunican entre sí directamente.
- **Toda salida de LLM se valida** (Pydantic / parseo JSON estricto) y se reintenta con feedback
  del error. Máximo 3 reintentos, luego se escala al usuario.
- **Loops acotados**: todo ciclo de corrección tiene límite explícito de reintentos.

## Estructura

```
src/pcia/
├── domain/        # modelos (ProjectSpec) y puertos (LLMProvider) — sin dependencias externas salvo pydantic
├── adapters/      # implementaciones de LLMProvider por proveedor
├── agents/        # Analista de Documentos, Entrevistador, Auditor, Constructor, Verificador, Aprendizaje (+ prompts/ en .md)
├── orchestrator/  # loop principal y máquina de estados del ciclo
├── config.py      # carga de config.yaml y factory de proveedores
└── cli.py         # punto de entrada (script `pcia`)
tests/             # pytest; usar FakeProvider (adaptador falso) para testear agentes sin red
memory/            # persistencia local (JSON por proyecto en Fase 1; evaluar SQLite en Fase 5)
docs/              # DISENO.md (diseño completo y roadmap)
```

## Convenciones

- Python >= 3.10, type hints en todo el código público.
- Commits en español, formato convencional: `feat(scope): ...`, `fix: ...`, `test: ...`, `docs: ...`.
- Prompts de agentes en archivos `.md` separados (`src/pcia/agents/prompts/`), nunca hardcodeados en strings.
- Textos de cara al usuario en español.
- Tests con pytest; los agentes se testean con `FakeProvider`, jamás con llamadas reales a LLMs.
- No introducir dependencias nuevas sin justificación; el core usa solo pydantic, pyyaml, httpx.

## Estado actual (actualizar al avanzar)

- [x] Diseño conceptual completo (`docs/DISENO.md`)
- [x] Scaffold, config.yaml, pyproject.toml
- [x] Fase 1: puerto `LLMProvider` + 3 adaptadores + `ProjectSpec` + Entrevistador + loop del orquestador
- [x] Fase 2: Agente Auditor (matriz de reglas + validación LLM, semáforo de coherencia)
- [x] Fase 3: Agente Constructor (plantillas: FastAPI, módulo Odoo, NestJS)
- [x] Fase 4a: Agente Verificador de sintaxis + ciclo de corrección acotado
- [x] Fase 4b: builds en Docker, smoke tests en la imagen y linters opcionales (declarados por plantilla)
- [x] Fase 5: Memoria persistente (RegistroProyecto en `memory/`) y Agente de Aprendizaje (precarga de entrevista)
- [x] Fase 6: Agente Analista de Documentos (`--docs`: extrae propuestas con evidencia de la documentación del cliente y precarga la entrevista)

**Roadmap completo (5 fases + Fase 6 de análisis documental).** Posibles próximos pasos: corrida real end-to-end con un LLM de verdad, autocorrección de fallas de build, más plantillas/reglas, soporte PDF en el Analista.

## Comandos

```bash
pip install -e ".[dev]"      # instalar en modo desarrollo
pytest                        # correr tests
pcia                          # ejecutar el CLI (cuando exista cli.py)
```
