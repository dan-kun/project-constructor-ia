# Autoevaluación UX/UI — heurísticas de Nielsen

Evaluación de la interfaz web (`pcia-web`) contra las 10 heurísticas de usabilidad de Jakob
Nielsen, con la **escala de severidad del propio Nielsen** para cada problema encontrado:

| Severidad | Significado |
|---|---|
| 0 | No es un problema de usabilidad |
| 1 | Cosmético: se arregla si sobra tiempo |
| 2 | Menor: baja prioridad |
| 3 | Mayor: alta prioridad, hay que arreglarlo |
| 4 | Catastrófico: obligatorio arreglarlo antes de publicar |

Es una **autoevaluación honesta**: se listan los incumplimientos aunque no estén resueltos.
El resultado es un backlog priorizado (§3), no un certificado de calidad.

---

## 1. Público objetivo

**Usuario primario:** persona desarrolladora full-stack (semi-senior a senior) o tech lead que
arranca un proyecto nuevo y necesita definir su estructura, arquitectura, seguridad e
infraestructura antes de escribir código.

**Usuario secundario y caso de uso que más importa:** la misma persona trabajando en un
**stack que no domina**. Es el escenario que originó el proyecto: te asignan un proyecto en
un lenguaje o framework con el que no tenés experiencia previa y tenés que producir una base
profesional igual.

Consecuencias de diseño que esto impone:

- **Vocabulario técnico es correcto, no una barrera**: hablar de "arquitectura hexagonal",
  "semáforo de coherencia" o "smoke test" es hablar el idioma del usuario (H2). Simplificarlo
  sería condescendiente.
- **La justificación importa tanto como el resultado**: si el usuario no domina el stack,
  necesita entender *por qué* el sistema propone algo, no solo qué propone. De ahí que cada
  hallazgo del Auditor muestre su corrección propuesta.
- **Errores en lenguaje técnico son aceptables** (`ContratoInvalidoError`) siempre que sean
  accionables.
- **La confianza se gana mostrando la verificación**, no afirmando calidad. El usuario que no
  conoce el stack no puede auditar el scaffold: necesita ver que el build y el smoke test
  corrieron de verdad.

---

## 2. Evaluación por heurística

### H1 · Visibilidad del estado del sistema — **Cumple bien**

El panel derecho es la respuesta directa a esta heurística: fase actual resaltada dentro del
ciclo completo, especificación completándose campo por campo en tiempo real, semáforo de
auditoría, árbol de archivos generados y resultado de cada verificación con su estado. El
encabezado muestra el proveedor de IA activo y un indicador de conexión.

Además, el estado final de la entrega distingue cuatro niveles (*aprobado*, *aprobado con
advertencias*, *inconcluso*, *fallido*) en vez de un binario: el usuario sabe si algo no se
verificó, en lugar de asumir que "sin errores" significa "verificado".

> **Problema U1 — severidad 3.** No hay indicador de actividad mientras el agente procesa. El
> campo de entrada se deshabilita con el texto "Esperando al agente…", pero con un modelo
> local cada turno puede tardar entre 10 y 40 segundos, y durante ese tiempo la interfaz está
> visualmente congelada. El usuario no puede distinguir "está pensando" de "se colgó". Es el
> hallazgo más importante de esta evaluación.

> **Problema U2 — severidad 2.** La verificación profunda (build de Docker) puede tardar
> minutos y no reporta progreso parcial: el resultado aparece de golpe al terminar.

---

### H2 · Coincidencia entre el sistema y el mundo real — **Cumple**

Todo en español rioplatense, con el vocabulario del oficio. La metáfora del **semáforo**
(🟢🟡🔴) para la coherencia técnica es universalmente comprensible y no requiere aprendizaje.
Los iconos de verificación (✅ ❌ ⏭️) siguen convenciones que cualquier desarrollador ya
conoce de sus pipelines de CI. Los nombres de fase corresponden a actividades reales del
proceso, no a jerga interna del sistema.

---

### H3 · Control y libertad del usuario — **Cumple parcialmente**

A favor: el usuario puede pedir un ajuste al confirmar la especificación (vuelve al
Entrevistador), elegir cómo resolver cada hallazgo, decidir el destino, y ante una
verificación fallida decidir si entrega igual o aborta. Ninguna decisión de alto impacto se
toma sin él.

> **Problema U3 — severidad 3.** No hay "salida de emergencia" en la interfaz web. La CLI
> tiene Ctrl+C; el navegador no tiene botón de cancelar ni de reiniciar la sesión. La única
> forma de abandonar es cerrar la pestaña.

> **Problema U4 — severidad 3.** No se puede deshacer una respuesta ya enviada. Si el usuario
> se equivoca al responder una pregunta de la entrevista, debe esperar a la confirmación
> final de la especificación para corregir; y en el ciclo de auditoría, una decisión enviada
> es irreversible.

> **Problema U5 — severidad 2.** Recargar la página pierde toda la sesión: no hay
> persistencia ni reconexión. Si se corta el WebSocket, la interfaz solo informa
> "desconectado" y queda inutilizable.

---

### H4 · Consistencia y estándares — **Cumple parcialmente**

La interfaz es internamente consistente (mismos colores por severidad en todo el sistema, el
mismo vocabulario que la CLI y que la documentación).

> **Problema U6 — severidad 3.** Convenciones de consola filtradas a una interfaz gráfica.
> Las preguntas provienen del orquestador, que fue escrito para terminal, así que el usuario
> web lee cosas como `¿Asumís el riesgo 'api-sin-autenticacion'? (s = asumir / N = corregir)`
> y `(enter = continuar)`. En una interfaz web, una decisión binaria se espera como **dos
> botones**, no como una letra tipeada; y "enter = continuar" es una convención de terminal
> sin sentido en un campo de texto. Es el problema de diseño más visible de la interfaz.

> **Problema U7 — severidad 2.** El destino del proyecto se pide como una ruta escrita a
> mano, sin selector de directorio ni validación previa a enviar.

---

### H5 · Prevención de errores — **Cumple bien**

Es la heurística mejor cubierta, porque coincide con el propósito del sistema:

- La **confirmación explícita de la especificación** antes de auditar (se agregó tras
  detectar en una corrida real que una respuesta del usuario quedaba en el buffer de la
  terminal y era consumida por la pregunta siguiente, generando un proyecto en un directorio
  llamado "asi esta bien").
- Los **hallazgos rojos son bloqueantes**: el sistema no permite construir sobre una
  incoherencia crítica ni sobre secretos hardcodeados, aunque el usuario insista.
- **Confirmación antes de ejecutar un Dockerfile reescrito por el LLM**.
- El destino debe ser un directorio vacío: nunca se pisa un proyecto existente.
- Nada se escribe en disco hasta que el render y la documentación estén completos.

---

### H6 · Reconocimiento antes que recuerdo — **Cumple bien**

El panel lateral elimina la carga de memoria: el usuario no necesita recordar qué respondió
diez turnos atrás porque la especificación está siempre visible. Cada hallazgo del Auditor se
muestra junto a su corrección propuesta, así que no hay que recordar qué proponía. Los
archivos generados se listan completos.

> **Problema U8 — severidad 2.** Los estados y severidades no tienen explicación en la
> interfaz: nada aclara qué significa "inconcluso" frente a "aprobado con advertencias", ni
> por qué un chequeo aparece como "opcional". El usuario debe inferirlo o leer el README.

---

### H7 · Flexibilidad y eficiencia de uso — **Cumple parcialmente**

A favor: la memoria persistente precarga la entrevista con las preferencias históricas del
usuario, y el análisis de documentación permite partir de un pliego real en vez de responder
desde cero. Ambos son aceleradores genuinos para el usuario recurrente. Enter envía la
respuesta sin usar el mouse.

> **Problema U9 — severidad 3.** **La interfaz web no expone `--docs`.** El Analista de
> Documentos, que es una de las capacidades más valiosas del sistema, solo está disponible
> por línea de comandos: no hay forma de subir documentación del cliente desde el navegador.
> Es una funcionalidad completa ausente en la interfaz gráfica.

---

### H8 · Diseño estético y minimalista — **Cumple**

Dos columnas, jerarquía visual clara, paleta sobria de alto contraste, sin elementos
decorativos que compitan con la información. Cada bloque del panel aparece recién cuando
tiene contenido real.

> **Problema U10 — severidad 2.** Los mensajes del agente se muestran como bloques de texto
> preformateado provenientes de la consola (reportes multilínea con iconos embebidos). Es
> legible pero no es diseño web: el mismo contenido ya está estructurado en el panel derecho,
> con lo que hay duplicación entre ambas columnas.

---

### H9 · Ayudar a reconocer, diagnosticar y recuperarse de errores — **Cumple parcialmente**

A favor y bien resuelto: cuando la verificación falla, el sistema no dice "error" — muestra
qué chequeo falló, con qué salida, y el diagnóstico en lenguaje natural del corrector de
builds, junto con la advertencia de que un diagnóstico repetido entre proyectos del mismo
stack indica un defecto de la plantilla. Eso es diagnóstico accionable de verdad.

> **Problema U11 — severidad 3.** Los errores no capturados se muestran crudos, con el nombre
> de la clase de excepción (`ContratoInvalidoError: …`). Para el público objetivo el lenguaje
> técnico es aceptable, pero falta lo esencial: **qué puede hacer el usuario a continuación**.
> Y tras un error la sesión queda muerta, sin ofrecer reintentar.

---

### H10 · Ayuda y documentación — **No cumple en la interfaz**

El proyecto está bien documentado *fuera* de la aplicación: README con inicio rápido,
diagramas de arquitectura, matriz de capacidades y log de seguridad.

> **Problema U12 — severidad 2.** La interfaz no tiene ninguna ayuda: ni una pantalla inicial
> que explique qué va a pasar durante el ciclo, ni tooltips sobre los estados, ni enlace a la
> documentación. El usuario que abre `pcia-web` por primera vez recibe una pregunta sin
> contexto sobre qué esperar ni cuánto va a durar.

---

### Accesibilidad (transversal, fuera de las 10 heurísticas)

> **Problema U13 — severidad 3.** El panel de estado se actualiza dinámicamente sin
> `aria-live`, por lo que un lector de pantalla no anuncia los cambios. No hay etiquetas ARIA
> ni roles semánticos, y el foco no se gestiona al habilitarse el campo de entrada.

> **Problema U14 — severidad 3.** En viewports menores a 860 px **el panel de estado se oculta
> por completo** (`display: none`). En un móvil o una ventana angosta se pierde toda la
> visibilidad del sistema (H1), que es justamente la principal fortaleza de la interfaz.
> Ocultar contenido es la salida fácil; lo correcto sería apilarlo o volverlo colapsable.

---

## 3. Resumen y backlog priorizado

| # | Problema | Heurística | Sev. |
|---|---|---|---|
| U1 | Sin indicador de actividad mientras el agente procesa | H1 | 3 |
| U6 | Convenciones de consola (`s/N`, `enter =`) en una interfaz gráfica | H4 | 3 |
| U9 | La interfaz web no expone el análisis de documentos (`--docs`) | H7 | 3 |
| U14 | El panel de estado se oculta por completo en pantallas angostas | H1 | 3 |
| U3 | Sin cancelar ni reiniciar la sesión desde el navegador | H3 | 3 |
| U4 | No se puede deshacer una respuesta enviada | H3 | 3 |
| U11 | Errores crudos, sin próximo paso ni opción de reintentar | H9 | 3 |
| U13 | Sin `aria-live` ni etiquetas ARIA; foco no gestionado | Accesibilidad | 3 |
| U2 | La verificación profunda no reporta progreso parcial | H1 | 2 |
| U5 | Recargar pierde la sesión; sin reconexión | H3 | 2 |
| U7 | El destino se escribe a mano, sin selector | H4 | 2 |
| U8 | Estados y severidades sin explicación en la interfaz | H6 | 2 |
| U10 | Duplicación entre el texto de consola y el panel estructurado | H8 | 2 |
| U12 | Sin ayuda ni onboarding en la aplicación | H10 | 2 |

**Balance.** Las heurísticas mejor cubiertas son **H5 (prevención de errores)**, **H1
(visibilidad)** y **H6 (reconocimiento)**, que son precisamente las que el propósito del
sistema exige: un asistente que audita antes de construir y verifica después necesita, por
diseño, prevenir errores y mostrar su estado. Las más débiles son **H10 (ayuda)**, **H4
(consistencia)** y **H3 (control)**, y todas comparten la misma causa raíz: **la interfaz web
se construyó sobre un orquestador diseñado para consola**, y heredó sus convenciones de
interacción. Es la contracara del beneficio arquitectónico —reutilizar el núcleo sin
modificarlo— y no un descuido puntual.

**Orden de resolución sugerido** (mayor impacto sobre esfuerzo): U1 y U14 son cambios
acotados de interfaz con impacto alto; U13 se resuelve agregando atributos ARIA; U6 requiere
que el orquestador exponga las opciones de una decisión de forma estructurada en vez de
embeberlas en el texto del prompt —el cambio más profundo, y el que más mejoraría la
experiencia web.
