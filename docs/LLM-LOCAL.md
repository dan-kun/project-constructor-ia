# Integración de un LLM/SLM local

> Parte 2 del trabajo final. **No es una reflexión hipotética**: el sistema ya se ejecutó de
> punta a punta contra un modelo local, y lo que sigue está fundamentado en esas corridas.

**Configuración usada:** servidor `llama.cpp` en `http://localhost:8088/v1`, sirviendo
**Qwen3-30B-A3B** (GGUF de Unsloth, arquitectura MoE), con ventana de contexto de 8192
tokens. Se conecta mediante el adaptador `openai_compat`, sin ninguna modificación de código.

Corridas realizadas con ese modelo: un proyecto FastAPI completo que terminó en verde
(auditoría, construcción, build de Docker y smoke test dentro de la imagen), y un proyecto
NestJS que expuso un defecto real de plantilla. Ambas están documentadas en
[`IA-COWORK.md`](IA-COWORK.md).

---

## 1. Rol en la arquitectura

### 1.1 El modelo local no es un caso especial

La decisión estructural se tomó al inicio del diseño: **toda interacción con modelos de IA
cruza el puerto `LLMProvider`** (`src/pcia/domain/ports.py`), una interfaz de un solo método.
El modelo local no es un modo degradado ni un *fallback*: es uno de tres adaptadores
intercambiables, con los mismos derechos que la API en la nube.

Cambiar de un proveedor comercial a un modelo local son dos líneas de `config.yaml`:

```yaml
provider: openai_compat
openai_compat:
  base_url: http://localhost:8088/v1     # llama.cpp / Ollama / LM Studio
  model: unsloth/Qwen3-30B-A3B-GGUF
  api_key: local
```

Ningún agente, ni el orquestador, ni el dominio se enteran del cambio. Que esto funcione no es
una promesa de diseño: es lo que efectivamente se hizo para ejecutar las corridas reales.

### 1.2 Rol diferenciado por agente — la evolución natural

La observación más útil de trabajar con un modelo pequeño es que **los seis agentes no exigen
lo mismo del modelo**:

| Agente | Exigencia sobre el modelo | Comportamiento observado con el modelo local |
|---|---|---|
| Aprendizaje | Ninguna (es determinístico, no usa LLM) | — |
| Constructor (README/ADR) | Redacción; baja exigencia de juicio | Adecuado |
| Entrevistador | Conversación + JSON estricto; exigencia media | Adecuado: JSON limpio, sin bloques de razonamiento filtrados |
| Analista de documentos | Comprensión de texto largo; alta demanda de **contexto** | Limitado por la ventana de 8192 tokens |
| Verificador (corrector de builds) | Diagnóstico de código, contrato multi-archivo; **alta** | El más costoso: contratos complejos implican más reintentos |
| **Auditor (pase LLM)** | **Juicio técnico experto; la más alta** | **Falso positivo observado** (§4.1) |

Esto sugiere una evolución concreta y ya soportada por la arquitectura: **enrutamiento
híbrido**. Un SLM local para las tareas de alto volumen y bajo riesgo —turnos de entrevista,
redacción de documentación— y un modelo mayor (local grande o en la nube) para las de juicio
crítico: el pase LLM del Auditor y la corrección de builds.

El puerto ya lo permite; solo requeriría que el orquestador reciba más de un proveedor y que
cada agente declare su perfil de exigencia. Es la extensión de una idea que ya está en el
diseño: *perfiles de prompt por capacidad del modelo* ([`DISENO.md`](DISENO.md) §6).

### 1.3 Rol futuro: embeddings locales para documentación de clientes

Además de generar texto, un modelo local resuelve un problema que hoy el sistema tiene
abierto: el Analista trunca cada documento a 15.000 caracteres, y lo que queda fuera del
corte simplemente no existe para el sistema.

El diseño propuesto —puerto `EmbeddingProvider` y recuperación por campo de la
especificación— está desarrollado en [`DISENO.md`](DISENO.md) §9.1. Ahí el argumento a favor
de lo local no es el costo: es que **cierta información no debería viajar** (§2.1).

---

## 2. Qué le aporta al usuario final

### 2.1 Confidencialidad — el aporte decisivo

El sistema lee **documentación del cliente**: requisitos de negocio, detalles de
infraestructura, a veces datos personales. Con un proveedor en la nube, cada fragmento de esa
documentación sale de la organización hacia un tercero.

Para el usuario objetivo de este sistema —una persona desarrolladora o consultora que arranca
proyectos para clientes— esto no es una preocupación abstracta: **muchos acuerdos de
confidencialidad lo prohíben explícitamente**. Sin la opción local, el Analista de Documentos
sería inutilizable en esos contextos, que son justamente los que más se benefician de él.

Con el modelo local, el ciclo completo —incluido el análisis de la documentación— se ejecuta
sin que ningún fragmento cruce la red. Verificado en las corridas reales.

### 2.2 Costo marginal cero sobre un flujo intrínsecamente conversacional

El ciclo es *hablador* por diseño: hasta 30 turnos de entrevista, hasta 3 ciclos de
auditoría, hasta 3 reintentos por contrato JSON malformado, hasta 3 correcciones de sintaxis
por archivo y hasta 2 ciclos de corrección de build. Cada reintento es una llamada más.

Con facturación por token, esa robustez tiene un precio que desincentiva exactamente lo que
hace confiable al sistema. Con un modelo local, reintentar es gratis: **el diseño defensivo
deja de competir con el presupuesto**.

### 2.3 Independencia operativa

Sin conexión a internet, sin límites de tasa, sin caídas de servicio de terceros y sin quedar
expuesto a cambios de política o de precios del proveedor. Este último punto no es teórico
para el proyecto: el cambio de facturación de la suscripción de Claude (junio de 2026) fue un
riesgo real que la arquitectura contuvo dentro de un adaptador.

---

## 3. Qué le aportó al equipo como profesionales

Trabajar con un modelo local produjo aprendizajes que **un modelo fuerte en la nube habría
ocultado**:

### 3.1 El diseño defensivo se valida con modelos débiles, no con los fuertes

El falso positivo del Auditor (§4.1) apareció porque el modelo era pequeño. Con un modelo
potente probablemente no habría ocurrido, y el sistema habría parecido más robusto de lo que
es. **El modelo local funcionó como instrumento de prueba de la robustez de la arquitectura**:
es la forma práctica de verificar que la "degradación con gracia" declarada en el diseño
realmente ocurre.

Es una conclusión transferible: si un sistema agéntico solo se prueba con el mejor modelo
disponible, no se sabe qué garantías provienen del diseño y cuáles del modelo.

### 3.2 El contrato estricto es lo que hace viable un modelo pequeño

Sin validación por Pydantic y reintento con el error como feedback, un modelo de esta escala
—que ocasionalmente produce JSON malformado o filtra bloques de razonamiento— sería
inutilizable para un flujo automatizado. Con el contrato, es perfectamente usable.

Dicho al revés: **la inversión en validación estricta es lo que amplía el rango de modelos que
el sistema puede usar**. No es burocracia defensiva, es lo que compra la independencia del
proveedor.

### 3.3 La ventana de contexto es una restricción de diseño, no un número de una ficha técnica

Trabajar con 8192 tokens obligó a decisiones concretas que hoy están en el código: el Analista
trunca documentos con marca explícita; el corrector de builds **excluye archivos irrelevantes**
(README, documentación) del contexto y limita cada archivo a 4.000 caracteres. Esas defensas
existen porque el modelo local las hizo necesarias, y mejoran el sistema también con modelos
grandes.

### 3.4 El costo real de cada ciclo se vuelve tangible

Con latencia local medible, los límites dejan de ser arbitrarios: `MAX_CORRECCIONES_BUILD = 2`
es bajo porque cada ciclo implica un *rebuild* de Docker más una llamada al modelo. El
registro de cada proyecto ahora persiste `proveedor` y `duracion_segundos`, de modo que
comparar adaptadores es medición y no impresión.

---

## 4. Limitaciones reales frente a una API en la nube

Sin adornos, y con la evidencia de las corridas:

### 4.1 Calidad de juicio técnico — la limitación más seria

En una corrida real, el pase LLM del Auditor marcó en amarillo el uso de **variables de
entorno** para gestionar secretos, que es exactamente la práctica que el sistema recomienda.
Un falso positivo sobre una práctica estándar.

El daño fue nulo porque la arquitectura lo previó: la regla determinística no disparó y la
decisión final era del humano. Pero la lección es clara: **la capa de juicio del Auditor es la
menos confiable con modelos pequeños**, y es precisamente el componente que el proyecto
declara como su diferencial. De ahí la propuesta de enrutamiento híbrido (§1.2).

### 4.2 Ventana de contexto

8192 tokens frente a las ventanas de 200k o 1M de las APIs actuales. Consecuencia directa: el
Analista no puede leer un documento extenso completo, y el corrector de builds recibe un
scaffold recortado. Es mitigable (ampliar el contexto al lanzar el servidor, recuperación
semántica), pero es una restricción estructural, no un detalle de configuración.

### 4.3 Rendimiento y hardware

La generación es más lenta que una API, y el modelo debe caber en la memoria disponible.
Qwen3-30B-A3B es viable en hardware de escritorio por ser MoE (activa una fracción de sus
parámetros); un modelo denso equivalente no lo sería. Esto acota qué modelos son realmente
opciones, y no toda máquina de desarrollo puede correrlos.

### 4.4 Seguimiento de instrucciones complejas

Los contratos más exigentes —el multi-archivo del corrector de builds— requieren más
reintentos con modelos pequeños. El sistema lo tolera (hasta 3, con feedback del error), pero
cada reintento cuesta tiempo.

### 4.5 Costo de operación desplazado, no eliminado

Desaparece el costo por token y aparece el de mantener el servidor: elegir la cuantización,
actualizar versiones, dimensionar el contexto, diagnosticar cuando no responde. **El costo se
mueve de la factura al tiempo del equipo.** Además, los modelos en la nube mejoran solos; el
modelo local mejora únicamente cuando alguien decide actualizarlo.

### 4.6 Conclusión: no es una disyuntiva

La comparación correcta no es "local *o* nube", sino **qué tarea conviene a cuál**. El aporte
real de la arquitectura no es haber elegido bien, sino **no tener que elegir de una vez y para
siempre**: la elección quedó como configuración, no como decisión de diseño. Un proyecto con
documentación bajo NDA corre entero en local; uno sin restricciones puede usar la nube para el
Auditor y el modelo local para el resto.

---

## 5. Demostración reproducible

### 5.1 El modelo local respondiendo una pregunta del proyecto

Con el servidor levantado, una consulta directa a la misma API que usa el adaptador:

```bash
curl -s http://localhost:8088/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "unsloth/Qwen3-30B-A3B-GGUF",
    "messages": [{
      "role": "user",
      "content": "¿Por qué desplegar WebSockets persistentes en infraestructura serverless es incoherente? Respondé en dos oraciones."
    }],
    "temperature": 0
  }' | python3 -m json.tool
```

### 5.2 El ciclo completo con el modelo local

```bash
# config.local.yaml (ignorado por git)
cat > config.local.yaml <<'YAML'
provider: openai_compat
openai_compat:
  base_url: http://localhost:8088/v1
  model: unsloth/Qwen3-30B-A3B-GGUF
  api_key: local
memory_dir: memory
YAML

pcia --config config.local.yaml            # por consola
pcia-web                                   # por navegador (el proveedor se elige ahí)
```

El registro que queda en `memory/` incluye `proveedor` y `duracion_segundos`, lo que permite
comparar la misma especificación ejecutada con distintos adaptadores.

> **Recomendación operativa.** Levantar el servidor con una ventana de contexto mayor
> (`-c 16384`) reduce el truncado en el Analista y en el corrector de builds, que son los dos
> agentes que más contexto consumen.
