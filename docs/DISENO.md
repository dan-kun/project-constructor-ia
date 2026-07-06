# Project Constructor IA — Documento de Diseño

> Fuente: diseño conceptual desarrollado para la entrega de medio ciclo del diplomado
> "Inteligencia Artificial Aplicada a Organizaciones" (UTN-FRBA). Este documento traduce
> ese diseño a decisiones de implementación. La entrega final del curso requiere la
> implementación funcional de este sistema.

## 1. Problema

La creación de la estructura inicial (scaffold) de proyectos de software es repetitiva pero
no trivial, recae en perfiles senior (cuello de botella), produce resultados inconsistentes
entre proyectos y las decisiones de diseño rara vez quedan documentadas. Las decisiones
incongruentes tomadas al inicio generan deuda técnica desde el día uno.

## 2. Solución

Asistente agéntico que entrevista, audita coherencia, construye con verificación continua
y aprende de cada proyecto. Diferencial clave: **criterio experto verificable** — el sistema
cuestiona y advierte, no solo genera.

## 3. Agentes y responsabilidades

| Agente | Rol | Notas de implementación |
|---|---|---|
| Orquestador | Coordina el ciclo, gestiona estado y límites | Máquina de estados explícita; no es un agente LLM, es código determinístico |
| Entrevistador | Elicitación adaptativa de requisitos | LLM con salida JSON estricta: `{message_to_user, updates, done}`; deriva preguntas del estado de la spec |
| Auditor | Detección de incongruencias técnicas | **Híbrido**: matriz de reglas determinísticas (YAML) + análisis LLM para lo no catalogado. Salida: hallazgos con severidad (verde/amarillo/rojo) y corrección propuesta |
| Constructor | Genera scaffold, configs, docs, ADRs | Plantillas por stack + LLM para lo específico. Fase 3: FastAPI, módulo Odoo, NestJS |
| Verificador | Valida lo construido | Fase 4a: parseo de sintaxis (yaml/toml/json/py). Fase 4b: builds en Docker, linters, smoke tests |
| Aprendizaje | Persiste y consolida experiencia | Registra spec final, hallazgos, resoluciones, fallas por stack, feedback del usuario |

## 4. Ciclos (orquestación)

Secuencia: Entrevista → Auditoría → Construcción → Verificación → Entrega → Aprendizaje

- **Ciclo de coherencia** (Auditoría → Entrevista): ante hallazgo, se repregunta al usuario
  con la corrección propuesta. Repite hasta spec coherente o riesgo asumido explícitamente
  (queda documentado en el ADR como riesgo aceptado).
- **Ciclo de corrección** (Verificación → Construcción): ante falla, informar + corregir +
  re-verificar. **Máximo 3 reintentos por componente**, luego escalar al usuario.
- **Ciclo de mejora continua** (Aprendizaje → próxima Entrevista): la memoria precarga
  respuestas, acorta la entrevista y agrega reglas al Auditor.
- Mini-ciclo transversal: **toda salida LLM malformada se reintenta con el error como
  feedback** (máx. 3), es el mismo patrón de corrección a escala micro.

## 5. Reglas y restricciones

- Seguridad primero: auth y gestión de secretos definidos ANTES de construir. Nunca
  secretos hardcodeados.
- Proporcionalidad: la arquitectura recomendada debe ser proporcional al alcance declarado.
- No construir sobre spec con conflictos no resueltos.
- Decisiones de alto impacto siempre las confirma el humano.
- Toda decisión queda registrada (ADR autogenerado).

## 6. Independencia del modelo de IA (decisión de arquitectura)

El LLM es un adaptador detrás del puerto `LLMProvider`, nunca parte del dominio.

Matriz de cobertura:

| Familia | Adaptador | Casos |
|---|---|---|
| API directa | `anthropic_api` | API key de Anthropic, pay-per-use |
| API compatible OpenAI | `openai_compat` | **Ollama local**, LM Studio, Groq, OpenRouter, DeepSeek, etc. (solo cambia `base_url` + `model`) |
| Suscripción | `claude_subscription` | CLI `claude -p --output-format json` headless, autenticada con plan Pro/Max. Estado al 2026-07: consume límites de la suscripción (el cambio de billing del 15/06/2026 fue pausado por Anthropic) |

Consecuencias de diseño:
- Prompts portables: escribir instrucciones explícitas + validación estricta, asumiendo el
  modelo más débil. Si hace falta, perfiles de prompt por capacidad ("fuerte"/"débil").
- Degradación con gracia: con modelos débiles, las reglas determinísticas del Auditor y las
  verificaciones automáticas sostienen las garantías mínimas.
- Riesgo de cambios de políticas/billing de proveedores queda contenido en un adaptador.
- Opción futura: usar el Claude Agent SDK no solo como proveedor de texto sino como motor
  de ejecución del Constructor/Verificador (delega creación de archivos y ejecución de
  comandos), ahorrando implementar sandbox propio en el MVP.

## 7. Roadmap por fases (cada fase es demostrable por sí misma)

1. **Fase 1 — Núcleo**: `LLMProvider` + adaptadores (anthropic_api, openai_compat,
   claude_subscription) + `ProjectSpec` (Pydantic) + Entrevistador + loop del orquestador.
   Criterio de aceptación: entrevista completa por CLI que termina con la spec en JSON
   guardada en `memory/`.
2. **Fase 2 — Auditor**: matriz de incompatibilidades en YAML (ej.: serverless+websockets,
   sqlite+alta concurrencia de escritura, hexagonal+alcance mínimo → advertencia) + pase LLM.
   Criterio: semáforo de coherencia con ciclo de repregunta funcionando.
3. **Fase 3 — Constructor**: plantillas FastAPI / módulo Odoo / NestJS + generación de
   README y ADR-001. Criterio: proyecto generado en un directorio destino.
4. **Fase 4 — Verificador**: 4a) validación de sintaxis de todo archivo generado;
   4b) builds Docker + linters + smoke tests. Criterio: reporte de verificación + ciclo de
   corrección con límite de 3.
5. **Fase 5 — Memoria/Aprendizaje**: persistir specs, hallazgos y resoluciones; precargar
   entrevista con historial (ej.: "en tus últimos proyectos usaste PostgreSQL, ¿mantenemos?").

## 8. Formato de salida del Entrevistador (contrato)

```json
{
  "message_to_user": "texto en español para mostrar al usuario",
  "updates": {"campo_de_la_spec": "valor"},
  "done": false
}
```

Reglas: responder SOLO JSON (sin markdown fences), `updates` solo con claves válidas de la
spec, `done=true` únicamente cuando no queden campos requeridos vacíos.
