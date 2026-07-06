# Project Constructor IA

Agente inteligente para la creación asistida y auditada de estructuras de proyectos de software.
Proyecto para el diplomado "Inteligencia Artificial Aplicada a Organizaciones" (UTN-FRBA).
El diseño conceptual completo está en `docs/DISENO.md` — **leerlo antes de implementar cualquier módulo**.

## Qué es

Sistema multiagente que:
1. **Entrevista** al usuario de forma adaptativa para definir la especificación de un proyecto nuevo (tipo, lenguaje, framework, arquitectura, seguridad, infra, CI/CD).
2. **Audita** la coherencia técnica de la especificación antes de construir (híbrido: reglas determinísticas + LLM).
3. **Construye** el scaffold del proyecto con criterios profesionales.
4. **Verifica** lo construido (parseo de configs, builds, linters, smoke tests) y corrige fallas.
5. **Aprende**: persiste decisiones y resultados para mejorar futuras entrevistas y auditorías.

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
├── agents/        # Entrevistador, Auditor, Constructor, Verificador, Aprendizaje (+ prompts/ en .md)
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
- [ ] **Fase 1 (EN CURSO)**: puerto `LLMProvider` + 3 adaptadores + `ProjectSpec` + Entrevistador + loop del orquestador
- [ ] Fase 2: Agente Auditor (matriz de reglas + validación LLM, semáforo de coherencia)
- [ ] Fase 3: Agente Constructor (plantillas: FastAPI, módulo Odoo, NestJS)
- [ ] Fase 4: Agente Verificador (sintaxis primero; builds en Docker después)
- [ ] Fase 5: Memoria persistente y Agente de Aprendizaje

## Comandos

```bash
pip install -e ".[dev]"      # instalar en modo desarrollo
pytest                        # correr tests
pcia                          # ejecutar el CLI (cuando exista cli.py)
```
