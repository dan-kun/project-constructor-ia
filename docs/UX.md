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

> **Problema U1 — severidad 3. RESUELTO**, y con un hallazgo adicional real detectado al
> corregirlo. No había indicador de actividad, y el campo de entrada se deshabilitaba sin más
> señal: con un modelo local (10-40s por turno) o un build de Docker (minutos), la interfaz
> quedaba visualmente congelada, indistinguible de una sesión colgada. Peor: al investigar se
> encontró que el frontend habilitaba el input y decía "Tu turno" después de **cualquier**
> mensaje del agente, incluidos los puramente informativos (reporte de auditoría, "Proyecto
> generado…", progreso de la verificación) — mensajes donde el ciclo seguía corriendo solo,
> sin esperar nada. Eso generaba la incertidumbre exacta que describe esta heurística: el
> usuario no sabía si debía esperar o responder.
>
> Solución aplicada, en dos partes:
> 1. *Indicador de actividad* (`mostrarIndicadorProcesando`/`ocultarIndicadorProcesando`,
>    `app.js`): una burbuja con tres puntos animados mientras hay trabajo en curso, con aviso
>    adicional ("puede tardar más…") si pasan más de 15s sin novedades.
> 2. *Distinción real entre "informativo" y "esperando respuesta"* (`espera_respuesta` en
>    `Evento`, `web/sessions.py`): el orquestador sigue sin saber que su "consola" es web
>    (`entrada()`/`salida()` no cambiaron de firma para loop.py), pero `Sesion.entrada()` ahora
>    marca explícitamente el momento en que va a bloquearse a esperar una respuesta, y
>    `Sesion.salida()` deja ese flag en `False` por defecto para el resto de los mensajes. El
>    frontend solo habilita el input y dice "Tu turno" cuando el flag es `True`.
>
> Evidencia: `test_salida_informativa_no_marca_espera_respuesta`,
> `test_turno_normal_de_entrevista_emite_marcador_de_espera_sin_burbuja_vacia`,
> `test_entrada_destino_muestra_mensaje_amigable_no_la_ruta_del_servidor`.

> **Problema U2 — severidad 2. Parcialmente mitigado por U1.** La verificación profunda
> (build de Docker) puede tardar minutos y el resultado sigue apareciendo de golpe al
> terminar — eso no cambió. Lo que sí cambió: ahora el indicador de actividad de U1 lo cubre
> con el mismo lenguaje que el resto de la espera ("El agente está trabajando…", con aviso
> a los 15s), en vez de dejar la interfaz sin ninguna señal durante esos minutos. Streamear
> las líneas del build en vivo sigue pendiente.

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

> **Bug de correctitud — RESUELTO (no catalogado como U-problema porque no es de la interfaz
> web sino del orquestador, y afectaba también a la CLI).** Reportado por un usuario en uso
> real: cuando la auditoría encontraba más de un hallazgo, `_resolver_hallazgos` (`loop.py`)
> resolvía uno, mostraba la respuesta del Entrevistador —que suele cerrar con una pregunta
> propia, p. ej. "¿te parece bien esta configuración o preferís otro proveedor?"— y pasaba
> directo a preguntar por el **siguiente** hallazgo, sin darle al usuario turno para
> responder la primera pregunta. La respuesta que el usuario le daba a esa pregunta terminaba
> interpretada como la decisión sobre el hallazgo siguiente. En la web esto se veía como dos
> instrucciones seguidas sin espacio para contestar la primera (ver captura del reporte); en
> la CLI el mismo defecto existía, solo que menos visible. Corregido agregando una
> confirmación explícita (`_confirmar_resolucion`) después de cada hallazgo resuelto, acotada
> a `max_ajustes_por_hallazgo` intentos (default 3, configurable como el resto de los
> límites del ciclo — ver `docs/DISENO.md` §4). Evidencia:
> `test_ajuste_tras_resolver_hallazgo_no_se_confunde_con_el_siguiente`,
> `test_confirmacion_de_hallazgo_esta_acotada`.

> **Problemas U3 y U4 — severidad 3. RESUELTOS juntos, con el mismo mecanismo — reportados
> por un usuario real: "en caso de algún fallo hay que volver todo de cero… si se envió una
> respuesta incorrecta no hay como corregirlo".** La causa de fondo era la misma para los
> dos: la `ProjectSpec` en curso solo se persistía al final del ciclo (`_fase_entrega`), así
> que cualquier fallo antes de eso —o darse cuenta más tarde de que una respuesta estaba
> mal— no dejaba nada reutilizable, solo el transcript en texto plano.
>
> Solución aplicada, en el Orquestador (compartida por CLI y web, no es un parche solo de
> interfaz):
> 1. **Checkpoint de progreso** (`Orquestador._guardar_checkpoint`, `loop.py`): la spec en
>    curso se reescribe en disco después de cada fase, y también en el `finally` de
>    `ejecutar()` si la corrida no llegó a completarse (cubre tanto una excepción como
>    Ctrl+C). Se borra automáticamente al terminar con éxito.
> 2. **`Orquestador(spec_inicial=...)`**: una corrida nueva puede arrancar con esa spec ya
>    precargada — el Entrevistador, con su propia lógica de "campos que faltan", solo
>    pregunta por lo que no está, no repite toda la entrevista.
> 3. **CLI**: `pcia --resume <checkpoint>`. Ante un error o Ctrl+C, la CLI imprime la ruta
>    exacta y el comando para retomar.
> 4. **Web**: botón **"Corregir algo"**, visible en cuanto hay algún dato cargado (no solo
>    tras un error). Reutiliza la spec que el propio panel de estado ya muestra
>    (`ultimoEstado.spec` en `app.js`) y la misma configuración de proveedor, para abrir una
>    sesión nueva sin perder lo respondido ni pedir de nuevo la API key.
>
> Evidencia: `test_checkpoint_conserva_el_progreso_ante_una_falla`,
> `test_spec_inicial_precarga_la_entrevista_y_solo_pregunta_lo_que_falta`,
> `test_main_resume_precarga_la_spec_y_solo_pregunta_lo_que_falta`,
> `test_main_falla_deja_checkpoint_y_avisa_como_retomar`,
> `test_gestor_crear_con_spec_inicial_precarga_la_entrevista`.
>
> **Límite honesto:** esto es "empezar de nuevo con lo ya respondido", no "deshacer el
> último mensaje" ni "seguir exactamente donde se cortó" — auditoría, construcción y
> verificación se vuelven a correr. Es una limitación aceptada a propósito: reauditar es
> rápido y correcto (la spec pudo cambiar), y evita el riesgo mucho mayor de reanudar a
> mitad de un build de Docker.

> **Problema U5 — severidad 2. Seguimos sin resolver, y U3/U4 lo dejan más visible.** Recargar
> la página pierde la sesión igual que antes: el estado que ahora permite "Corregir algo"
> (`ultimoEstado`, `payloadProveedorActual`) vive en variables de JavaScript, no sobrevive a
> un F5. El checkpoint del punto anterior sí sobrevive en el servidor, pero la interfaz web
> todavía no ofrece una forma de recuperarlo después de un reload (solo la CLI, vía
> `--resume` con una ruta que el usuario tiene que conocer). Cerrar esto del todo pide
> persistir `session_id` en `localStorage` y un endpoint que reconstruya el estado — más
> trabajo del que entra en esta ronda.

---

### H4 · Consistencia y estándares — **Cumple parcialmente**

La interfaz es internamente consistente (mismos colores por severidad en todo el sistema, el
mismo vocabulario que la CLI y que la documentación).

> **Problema U6 — severidad 3. RESUELTO.** Convenciones de consola filtradas a una interfaz
> gráfica: el usuario web leía `(s = asumir / N = corregir)` y tenía que tipear la letra.
> Solución aplicada: el orquestador sigue hablando en texto plano (no se tocó, sigue siendo
> agnóstico de si su "consola" es CLI o web), pero `Sesion.entrada()` reconoce los prompts de
> opción fija por un fragmento estable del texto (`_opciones_para_prompt`,
> `web/sessions.py`) y los traduce a una lista `opciones` que viaja en el evento SSE. El
> frontend las renderiza como botones (`mostrarOpciones`, `app.js`) sin quitar el campo de
> texto libre, que sigue disponible para cualquier respuesta que no encaje en las opciones
> ofrecidas (por ejemplo, un ajuste puntual a la especificación en vez de "confirmar").
> Cubre: confirmar la especificación, hallazgos rojos (aplicar/abortar), hallazgos amarillos
> (asumir/corregir), la corrección propuesta genérica, entregar con verificación fallida y
> ejecutar un Dockerfile reescrito. Evidencia: `test_hallazgo_rojo_ofrece_aplicar_o_abortar`,
> `test_hallazgo_amarillo_ofrece_asumir_o_corregir`, `test_confirmar_spec_ofrece_boton_de_confirmacion`,
> y el resto de la suite en `tests/test_web_sessions.py` bajo "decisiones estructuradas".

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

> **Problema U9 — severidad 3. RESUELTO.** La interfaz web no exponía `--docs`. Solución
> aplicada: un campo de subida múltiple (`.md`/`.txt`, hasta 5 archivos) en el panel de
> configuración inicial; el navegador los lee como texto (`FileReader`) y los manda en el
> body de `POST /api/sessions`. El servidor valida formato/tamaño/cantidad **antes** de abrir
> la sesión (`validar_documentos`, mismo principio de fail-fast que la validación del
> proveedor) y los escribe en el directorio propio de la sesión, nunca en una ruta que haya
> elegido el visitante (`_guardar_documentos` sanea el nombre de archivo). El Analista corre
> antes que el Entrevistador exactamente igual que en la CLI. Evidencia:
> `test_gestor_crear_con_documentos_analiza_antes_de_entrevistar`,
> `test_validar_documentos_rechaza_extension_no_soportada`,
> `test_guardar_documentos_sanitiza_nombres_con_path_traversal`,
> `test_crear_sesion_con_documento_invalido_devuelve_400`.

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

> **Problema U14 — severidad 3. RESUELTO** (en una revisión de CSS posterior a esta
> evaluación, no en esta ronda de ajustes). El panel de estado ya no se oculta con
> `display: none` bajo 900px: la media query lo reordena arriba del chat y pasa a `position:
> static` dentro del flujo normal (`style.css`, bloque `@media (max-width: 900px)`), en línea
> con lo que este mismo informe recomendaba ("lo correcto sería apilarlo"). Se deja registrado
> para que quede trazable, no porque siga reproduciendo.

---

## 3. Resumen y backlog priorizado

Resueltos desde la evaluación original: U6, U9 y U1 en la ronda anterior de ajustes; U3 y U4
en esta ronda (a partir de feedback de uso real); U14 en una revisión de CSS anterior (ver
el detalle de cada uno en la sección correspondiente). Se corrigió además, entre medio, un
bug de layout no catalogado como U-problema: el recuadro de mensajes no tenía una altura
acotada, así que una conversación larga scrolleaba la página entera en vez de solo el chat
(`#chat.panel` ahora tiene `max-height` + `overflow: hidden`, igual que `.lateral`); y un bug
de correctitud del Orquestador (`_resolver_hallazgos`, ver H3 arriba) que afectaba tanto a la
CLI como a la web.

| # | Problema | Heurística | Sev. | Estado |
|---|---|---|---|---|
| ~~U6~~ | Convenciones de consola (`s/N`, `enter =`) en una interfaz gráfica | H4 | 3 | Resuelto |
| ~~U9~~ | La interfaz web no expone el análisis de documentos (`--docs`) | H7 | 3 | Resuelto |
| ~~U14~~ | El panel de estado se oculta por completo en pantallas angostas | H1 | 3 | Resuelto |
| ~~U1~~ | Sin indicador de actividad mientras el agente procesa | H1 | 3 | Resuelto |
| ~~U3~~ | Sin cancelar ni reiniciar la sesión desde el navegador | H3 | 3 | Resuelto |
| ~~U4~~ | No se puede deshacer una respuesta enviada | H3 | 3 | Resuelto |
| U11 | Errores crudos, sin próximo paso ni opción de reintentar | H9 | 3 | Pendiente |
| U13 | Sin `aria-live` ni etiquetas ARIA; foco no gestionado | Accesibilidad | 3 | Pendiente |
| U2 | La verificación profunda no reporta progreso parcial | H1 | 2 | Parcial (ver U1) |
| U5 | Recargar pierde la sesión; sin reconexión | H3 | 2 | Pendiente (más visible tras U3/U4) |
| U7 | El destino se escribe a mano, sin selector | H4 | 2 | Pendiente |
| U8 | Estados y severidades sin explicación en la interfaz | H6 | 2 | Pendiente |
| U10 | Duplicación entre el texto de consola y el panel estructurado | H8 | 2 | Pendiente |
| U12 | Sin ayuda ni onboarding en la aplicación | H10 | 2 | Pendiente |

**Balance.** Las heurísticas mejor cubiertas son **H5 (prevención de errores)**, **H1
(visibilidad)** y **H6 (reconocimiento)**, que son precisamente las que el propósito del
sistema exige: un asistente que audita antes de construir y verifica después necesita, por
diseño, prevenir errores y mostrar su estado. Las más débiles son **H10 (ayuda)**, **H4
(consistencia)** y **H3 (control)**, y todas comparten la misma causa raíz: **la interfaz web
se construyó sobre un orquestador diseñado para consola**, y heredó sus convenciones de
interacción. Es la contracara del beneficio arquitectónico —reutilizar el núcleo sin
modificarlo— y no un descuido puntual.

**Orden de resolución sugerido para lo que queda pendiente** (mayor impacto sobre esfuerzo):
U1 es un cambio acotado de interfaz con impacto alto; U13 se resuelve agregando atributos
ARIA; U3/U4/U5 comparten una causa de fondo (no hay forma de recuperar o reiniciar una
sesión en curso) y conviene abordarlos juntos.
