# Uso de IA en co-work durante el desarrollo

Cómo se trabajó con asistentes de IA para construir este sistema, **qué falló y qué
sorprendió**. Se documentan los fallos con el mismo detalle que los aciertos: los tres bugs
más importantes del proyecto los introdujo o los pasó por alto la IA, y ninguno lo detectó la
suite de pruebas.

## 1. Modalidad de trabajo

**Asistente principal: Claude Code**, usado como par de programación durante todo el
desarrollo (las 7 fases del roadmap más las mejoras posteriores al feedback de medio ciclo).

La trazabilidad quedó en el propio historial: **46 de los 48 commits llevan
`Co-Authored-By`**. No es un detalle cosmético — permite auditar después qué se escribió en
co-work y qué no, en lugar de tener que confiar en la memoria.

El ciclo de trabajo fue consistente:

1. **El humano define** el problema, la arquitectura y las reglas no negociables (quedaron
   escritas en `CLAUDE.md` y `DISENO.md` antes de escribir código).
2. **La IA implementa** módulo por módulo, con tests en el mismo commit.
3. **Se verifica ejecutando de verdad**, no solo con tests: corridas end-to-end con un modelo
   real y Docker real.
4. **El humano decide** ante cada disyuntiva de alcance o diseño.

**Revisión cruzada entre modelos.** A partir del feedback de medio ciclo se incorporó una
práctica que resultó más valiosa de lo esperado: someter el trabajo hecho con un asistente a
la revisión de **otro modelo distinto** (Codex 5.6), y luego evaluar críticamente esa
revisión con el primero. Ninguno de los dos tuvo razón por completo (§4.3).

## 2. Qué se delegó y qué no

| Se delegó | No se delegó |
|---|---|
| Implementación de módulos y sus tests | Decisiones de arquitectura (hexagonal, puerto `LLMProvider`, estado compartido) |
| Redacción de prompts de agentes y documentación | Definición del alcance: qué entra y qué queda como trabajo futuro |
| Diagnóstico de errores y propuestas de corrección | Política de producto (que un hallazgo rojo sea bloqueante fue una decisión, no una sugerencia aceptada) |
| Refactors acotados y andamiaje de pruebas | Evaluar si una recomendación externa aplicaba al contexto real del proyecto |

El caso más claro de **no delegación** fue de alcance: una revisión externa propuso un
rediseño ambicioso (paquetes de stack versionados, plan estructurado intermedio, comandos
`plan`/`apply`/`verify`). Técnicamente razonable, pero a tres semanas de la entrega implicaba
migrar las plantillas, reescribir el Constructor y rehacer buena parte de 169 tests. Se
decidió **documentarlo como trabajo futuro y no implementarlo**. Aceptar una buena idea en el
momento equivocado es una forma de fallar.

## 3. Dónde falló la IA

### 3.1 Conocimiento de libro aplicado fuera de contexto — `npm ci` sin lockfile

El Dockerfile de la plantilla NestJS usaba `npm ci`, que **es** la práctica recomendada para
builds reproducibles… y que requiere un `package-lock.json` que el scaffold no generaba. El
build fallaba siempre, en todos los proyectos NestJS.

Los tests pasaban: la capa de Docker estaba simulada, por diseño y con razón (la suite debe
correr sin red ni Docker). El bug apareció en la **primera corrida real** con un modelo local.

> **Lección.** El modelo aplicó una buena práctica correcta en general pero incorrecta en el
> contexto específico. Es el modo de fallo más difícil de detectar en revisión, porque el
> código *se ve* mejor que la alternativa correcta.

Consecuencia de diseño: se agregó el ciclo de corrección de builds (Fase 7) que persiste el
diagnóstico de cada falla, con la regla explícita de que **un diagnóstico repetido entre
proyectos del mismo stack indica un defecto de la plantilla**, no del proyecto. El sistema
aprendió del error de la IA.

### 3.2 Contrato válido, comportamiento incoherente — el proyecto "asi esta bien"

El Entrevistador devolvió `done: true` (especificación completa) pero terminó su mensaje
preguntando algo. El usuario respondió *"asi esta bien"*; como el ciclo ya había avanzado,
ese texto quedó en el buffer de la terminal y lo consumió la pregunta siguiente —la del
directorio destino—. Resultado: un proyecto generado en un directorio llamado
`asi esta bien`.

> **Lección.** El contrato JSON validaba la *estructura* (`done` es booleano) pero no la
> *coherencia* entre `done: true` y un mensaje que sigue preguntando. Un contrato bien
> tipado no garantiza comportamiento coherente.

Corrección: confirmación explícita de la especificación antes de auditar, de modo que la
última respuesta del usuario siempre tenga quién la lea. Es hoy una de las medidas de
prevención de errores mejor valoradas en la evaluación UX ([`UX.md`](UX.md), H5).

### 3.3 Documentación plausible pero falsa

El README generado por el Constructor indicaba `pip install -r requirements.txt` y
`docker-compose up` en un scaffold que **no contenía** ni `requirements.txt` ni
`docker-compose.yml`. Nadie lo detectó durante meses porque nada verificaba la documentación:
los tests comprobaban que el README existiera, no que dijera la verdad. Lo encontró la
revisión cruzada con otro modelo.

> **Lección.** La IA genera documentación *plausible*, y lo plausible es peligroso justamente
> porque no se distingue de lo correcto a simple vista. Un comando inventado sobre un archivo
> inexistente es peor que no dar ningún comando.

Corrección: la sección "Cómo ejecutar" pasó a ser **determinística** (viene de la plantilla,
con tokens renderizados) y el LLM quedó limitado a contexto y justificaciones; además el
Verificador ahora rechaza un README que referencie archivos que no existen en el scaffold.

### 3.4 Falso positivo del Auditor con un modelo pequeño

En una corrida real con Qwen3-30B local, el pase LLM del Auditor marcó en amarillo el uso de
**variables de entorno** para gestionar secretos — que es precisamente la práctica que el
sistema recomienda. La regla determinística no disparó, y el humano descartó el hallazgo.

> **Lección — y el resultado es positivo.** Es la "degradación con gracia" del diseño
> funcionando en vivo: con un modelo débil, la capa determinística y el humano contuvieron el
> error del LLM. Vale más como evidencia que como fallo: muestra que la arquitectura anticipó
> correctamente la falibilidad del componente de IA.

### 3.5 Deriva entre el documento y el código

`DISENO.md` afirmaba que la memoria "agrega reglas al Auditor" cuando el código solo calculaba
frecuencias de preferencias. La intención se había documentado como si fuera el estado.

> **Lección.** Cuando documento y código se escriben rápido y con asistencia, la deriva entre
> ambos también es rápida. La documentación generada tiende a describir lo que el sistema
> *debería* hacer.

Corrección: la afirmación pasó a trabajo futuro y se agregó la **matriz de capacidades**, que
declara explícitamente qué materializa cada plantilla y qué queda solo documentado.

## 4. Dónde sorprendió

### 4.1 Una decisión de testeo pagó como decisión de arquitectura

El orquestador recibe su entrada y salida como *callables* inyectables. Se hizo así por una
razón modesta: poder testear el ciclo sin consola. Meses después, cuando la entrega final
exigió una interfaz web, **la interfaz se enchufó sin modificar una sola línea del
orquestador ni de los agentes**: la consola siempre había sido un adaptador.

Un beneficio que en el diseño era teórico ("hexagonal permite cambiar adaptadores") se
materializó de forma medible y en el peor momento posible para hacer una refactorización.

### 4.2 La IA fue mejor en el andamiaje que en el conocimiento de dominio

Contraintuitivamente, lo que salió rápido y correcto fue la infraestructura de verificación
—suite de tests, proveedor falso, fixtures, simulación de Docker—, mientras que **los bugs se
concentraron en las plantillas**, que es donde vive el conocimiento de dominio (cómo se
construye correctamente un proyecto NestJS o FastAPI). El asistente es más confiable
escribiendo la máquina que juzga que escribiendo lo juzgado.

### 4.3 La revisión cruzada entre modelos encontró lo que ninguno solo

El segundo modelo detectó problemas reales que habían pasado desapercibidos (documentación
alucinada; "omitido" contando como aprobado; un hallazgo rojo aceptable con la misma
facilidad que uno amarillo). Pero su **priorización** estaba orientada a madurez de producto,
no a una entrega académica con fecha, y dos de sus recomendaciones necesitaron ajuste al
contrastarlas con el código real.

> Ninguno de los dos modelos tuvo razón por completo. El valor no estuvo en delegar el
> criterio a un segundo modelo, sino en **usar el desacuerdo entre ambos como disparador de
> una decisión humana informada**.

### 4.4 La corrida real fue la única fuente de verdad

169 tests en verde y 96 % de cobertura de sentencias **no detectaron ninguno de los tres bugs
importantes**. Los tres aparecieron ejecutando el sistema de punta a punta con un modelo real
y Docker real: el `npm ci`, el input colgado y la documentación alucinada.

No es una crítica a las pruebas —son herméticas a propósito, y esa hermeticidad es lo que las
hace rápidas y confiables— sino un límite estructural: **una suite que simula el mundo no
puede descubrir en qué se equivocó al simularlo**.

## 5. Prácticas que funcionaron

1. **Escribir las reglas no negociables antes que el código.** `CLAUDE.md` y `DISENO.md`
   existían desde el primer commit; el asistente trabajó dentro de restricciones explícitas
   en vez de inventarlas en cada sesión.
2. **Tests en el mismo commit que la implementación**, con un proveedor falso: nunca se gastó
   una llamada real de LLM en una prueba, y la suite corre en menos de dos segundos.
3. **Verificar ejecutando, no leyendo.** Toda decisión de seguridad de las plantillas se
   comprobó dentro de un contenedor real (usuario no-root, fallo sin `SECRET_KEY`), no
   revisando el Dockerfile.
4. **Commits chicos y en español**, con co-autoría declarada: el historial es hoy una fuente
   de auditoría del proceso.
5. **Pedir revisión a un modelo distinto** al que escribió el código.

## 6. Limitaciones observadas del co-work

- **El asistente no puede validar lo que no ejecuta.** Los diagramas Mermaid quedaron sin
  verificación de renderizado (requería descargar un navegador completo) y las capturas de
  pantalla de la interfaz no pudieron tomarse automáticamente: ambas tareas volvieron al
  humano. Conviene saber de antemano qué queda fuera del alcance del asistente.
- **La confianza del asistente no correlaciona con su acierto.** El `npm ci` se escribió con
  la misma seguridad que el código correcto que lo rodeaba.
- **El costo de revisión crece con la velocidad de generación.** Producir ~5.500 líneas
  (2.758 de código y 2.702 de tests) es rápido; auditarlas por lectura, no. La respuesta fue
  apoyarse en verificación *ejecutable* —tests, builds, smoke tests— en lugar de revisión
  manual, y aceptar que la cobertura alta mide ejecución, no corrección (§4.4).
