# Tecnologías utilizadas y justificación

Toda elección se justifica contra los requisitos del sistema, no por popularidad. Se incluyen
también las **tecnologías descartadas** (§6), porque en varios casos la decisión de *no*
incorporar algo fue más determinante para la arquitectura que las que sí se usaron.

## 1. Criterios de selección

1. **El dominio no depende de frameworks.** Los modelos y puertos (`src/pcia/domain/`) no
   importan nada de adaptadores ni librerías de infraestructura. Cualquier dependencia entra
   por los bordes.
2. **Dependencias mínimas en el núcleo.** Tres librerías en el core. Todo lo demás es un
   extra opcional que se instala solo si se usa.
3. **Preferir datos versionados sobre código.** Reglas de auditoría, plantillas y prompts son
   archivos YAML y Markdown, no clases de Python: se revisan en un diff, los edita alguien
   que no programa y no requieren tocar el orquestador.
4. **Nada que impida cambiar de proveedor de IA.** Es un requisito de arquitectura declarado,
   y descarta por sí solo a la mayoría de los frameworks de agentes (§6).

---

## 2. Núcleo del sistema

| Tecnología | Versión | Rol | Justificación |
|---|---|---|---|
| **Python** | ≥ 3.10 | Lenguaje del sistema | Ecosistema estándar para integración con modelos de IA y experiencia previa del equipo. El piso es 3.10 (no 3.11) para no excluir instalaciones habituales de Ubuntu LTS; el costo es una única dependencia condicional (`tomli`). |
| **Pydantic** | ≥ 2.5 | Validación de todo el I/O y modelo de dominio | Es la pieza que sostiene la garantía central del sistema: **toda salida de LLM se valida contra un contrato tipado y se reintenta con el error como feedback**. `ProjectSpec`, los hallazgos, los contratos de cada agente y el registro persistido son modelos Pydantic. `extra="forbid"` hace que un campo inventado por el modelo falle en vez de colarse. Sin esto habría que escribir validación y parseo a mano en cada agente. |
| **PyYAML** | ≥ 6.0 | Reglas, plantillas y configuración | Formato elegido por legibilidad para el humano que mantiene la matriz de incompatibilidades y las plantillas, que son bloques de texto multilínea con indentación (YAML lo maneja mucho mejor que JSON o TOML). Se usa exclusivamente `safe_load`. |
| **httpx** | ≥ 0.27 | Cliente HTTP del adaptador `openai_compat` | Cliente moderno con timeouts explícitos, suficiente para un único endpoint de chat completions. Evita arrastrar SDKs propietarios para hablar con un servidor local. |
| **tomli** | ≥ 2.0, solo Python < 3.11 | Verificación de sintaxis TOML | `tomllib` es stdlib desde 3.11; el fallback existe solo para 3.10. El Verificador necesita parsear los `pyproject.toml` que genera. |

## 3. Adaptadores y extras opcionales

| Tecnología | Rol | Justificación |
|---|---|---|
| **FastAPI** (extra `web`) | Servidor de la interfaz web | Soporte nativo de WebSocket, que es lo que la interfaz necesita: el ciclo es conversacional y de larga duración, no un request/response. Coherente además con una de las plantillas que el sistema genera. |
| **Uvicorn** (extra `web`) | Servidor ASGI | Estándar de facto para FastAPI. |
| **SDK de Anthropic** (extra `anthropic`) | Adaptador `anthropic_api` | SDK oficial para la API de Anthropic. Opcional a propósito: quien use solo un modelo local nunca lo instala. |
| **pytest** (extra `dev`) | Suite de pruebas | 169 tests, todos herméticos: los agentes se prueban con un proveedor falso (`FakeProvider`) y nunca contra un LLM real, y las herramientas externas se parchean. La suite corre en menos de dos segundos sin red ni Docker. |

## 4. Herramientas externas

| Herramienta | Rol | Justificación |
|---|---|---|
| **Docker** | Builds y smoke tests de lo generado | Es lo que convierte la verificación en real: el scaffold se construye y se ejecuta de verdad, no se inspecciona. También aísla la ejecución de código escrito por un LLM (ver [`SEGURIDAD.md`](SEGURIDAD.md), R2). Si no está disponible, el chequeo se reporta *omitido* y la entrega queda **inconclusa**, nunca aprobada. |
| **ruff** | Linter opcional declarado por plantillas | Rápido y sin configuración previa. Declarado **opcional** en FastAPI (el build y el smoke test son la garantía) y **obligatorio** en Odoo, donde es la única verificación posible del stack. |
| **CLI de Claude Code** | Adaptador `claude_subscription` | Permite usar una suscripción Pro/Max existente en modo headless (`claude -p`), sin pagar API por uso. Es el tercer camino de acceso a un modelo, junto a API y modelo local. |
| **llama.cpp / Ollama / LM Studio** | Modelos locales vía `openai_compat` | Exponen una API compatible con OpenAI, así que el mismo adaptador sirve para los tres cambiando `base_url`. Habilitan el caso de privacidad: documentación de cliente que nunca sale de la máquina. |
| **GitHub Actions** | CI del propio proyecto | Corre la suite en Python 3.10 y 3.12 en cada push. Se agregó al detectar una incoherencia: el sistema genera proyectos con CI pero no tenía CI propio. |
| **Mermaid** | Diagramas versionados | Los diagramas viven como texto en el repositorio y se renderizan solos en GitHub: se versionan y se revisan en un diff, a diferencia de una imagen exportada que queda desactualizada en silencio. |

## 5. Tecnologías de los proyectos generados

No son dependencias de PCIA: son las que aparecen en los scaffolds que produce. La lista
completa de qué materializa cada plantilla está en la matriz de capacidades
([`DISENO.md`](DISENO.md) §8).

| Plantilla | Tecnologías | Justificación de los defaults |
|---|---|---|
| **FastAPI** | Python 3.12, FastAPI, Uvicorn, pydantic-settings, pytest; con PostgreSQL: SQLAlchemy 2, psycopg 3, PostgreSQL 16 | `pydantic-settings` obliga a declarar la configuración como un modelo tipado, lo que permite que `SECRET_KEY` sea obligatoria y la aplicación falle al arrancar sin ella. `psycopg 3` sobre `psycopg2` por soporte moderno; SQLAlchemy 2 por su API tipada. |
| **NestJS** | Node 20, TypeScript, NestJS 10, Jest | Build multi-stage: compila con las dependencias de desarrollo y la imagen final solo lleva `dist/` y `node_modules`. Usa `npm install` y no `npm ci` porque el scaffold no incluye lockfile — un defecto detectado en una corrida real. |
| **Módulo Odoo** | Odoo 17, Python | Estructura estándar de módulo: manifiesto, modelos, vistas y permisos de acceso. |

Ambas plantillas containerizadas ejecutan como **usuario sin privilegios**, nunca root.

## 6. Tecnologías descartadas

| Descartada | En favor de | Razón |
|---|---|---|
| **LangChain / LlamaIndex / frameworks de agentes** | Un puerto propio de ~30 líneas | Es la decisión más importante de esta lista. El agnosticismo del proveedor es un **requisito de arquitectura**, y estos frameworks imponen su propia capa de proveedores, sus abstracciones de cadena y su ritmo de cambios incompatibles. Además, el diferencial declarado es *criterio experto verificable*: una cadena opaca de un framework va en contra de poder auditar exactamente qué prompt se envió y qué se validó. El puerto `LLMProvider` tiene un método; el ciclo de reintento con feedback son unas decenas de líneas y están bajo control total. |
| **Librerías de salida estructurada** (instructor, pydantic-ai) | `consultar_con_contrato` propio | Resuelven el mismo problema que ya resuelve Pydantic más un bucle de reintentos explícito, a cambio de una dependencia más y de acoplar el core a su modelo de proveedores. |
| **Jinja2 u otro motor de plantillas** | Sustitución de tokens `[[TOKEN]]` + bloques condicionales declarativos | Un motor completo permitiría **lógica dentro de las plantillas**, que es una trampa de mantenimiento conocida. Manteniéndolas como datos casi puros, la condicionalidad queda declarada en el YAML (`cuando: {campo, contiene}`) y validada al cargar: un condicional que apunta a un campo inexistente de la spec falla al inicio, no en medio de una construcción. |
| **Base de datos para la memoria** | Un archivo JSON por proyecto | El volumen es de decenas de registros. JSON es inspeccionable a ojo, versionable y no agrega dependencias ni migraciones. Reevaluable si el volumen crece. |
| **Base de datos vectorial / RAG en la memoria** | Conteo determinístico de frecuencias | Análisis completo en [`DISENO.md`](DISENO.md) §9.2: a esta escala todos los registros entran en el prompt, y una similitud coseno no es auditable mientras que "postgresql en 3 de 4 proyectos" sí. RAG queda propuesto donde sí corresponde: documentación extensa de clientes. |
| **Framework de frontend** (React, Vue, Svelte) | Una página con HTML, CSS y JS embebidos | La interfaz es una sola vista con un WebSocket. Un framework agregaría toolchain de build, `node_modules` y un paso de compilación a un proyecto Python, a cambio de nada: el adaptador web completo son ~250 líneas sin build step. El trade-off está aceptado y documentado: si la interfaz creciera, esta decisión habría que revisarla. |
| **Framework de CSS** (Tailwind, Bootstrap) | CSS propio con variables | Misma razón: evita un paso de build para una única página, y el CSS a medida permitió mapear los colores directamente a la semántica del dominio (severidades y estados de verificación). |
