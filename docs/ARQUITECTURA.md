# Arquitectura — diagramas

Diagramas derivados del código real (`src/pcia/`). Complementan el diseño conceptual de
[`DISENO.md`](DISENO.md).

---

## 1. Arquitectura de componentes (hexagonal)

El dominio no conoce ni a los adaptadores ni a los frameworks. Toda interacción con modelos
de IA cruza el puerto `LLMProvider`; toda interacción con el usuario cruza los callables de
IO del orquestador. Por eso la consola y el navegador son intercambiables.

```mermaid
flowchart TB
    subgraph io["Adaptadores de entrada/salida"]
        CLI["CLI<br>pcia"]
        WEB["Web<br>pcia-web<br>FastAPI + WebSocket"]
    end

    subgraph nucleo["Núcleo de la aplicación"]
        ORQ["Orquestador<br>máquina de estados determinística<br>no es un agente LLM"]

        subgraph agentes["Agentes"]
            AN["Analista"]
            EN["Entrevistador"]
            AU["Auditor"]
            CO["Constructor"]
            VE["Verificador"]
            AP["Aprendizaje"]
        end
    end

    subgraph dominio["Dominio (sin dependencias de frameworks)"]
        SPEC["ProjectSpec<br>estado compartido<br>(pizarra única)"]
        MOD["Modelos<br>Hallazgo · Chequeo<br>RegistroProyecto"]
        PORT{{"LLMProvider<br>puerto"}}
    end

    subgraph recursos["Recursos declarativos"]
        REGLAS[("reglas/<br>incompatibilidades.yaml")]
        PLANT[("templates/<br>fastapi · nestjs · odoo")]
        PROMPTS[("prompts/*.md")]
    end

    subgraph externos["Adaptadores de salida"]
        ANT["anthropic_api"]
        OAI["openai_compat<br>Ollama · llama.cpp · Groq"]
        SUB["claude_subscription<br>CLI headless"]
        MEM[("memory/*.json")]
        DOCKER["Docker<br>builds y smoke tests"]
    end

    CLI -->|"entrada / salida"| ORQ
    WEB -->|"entrada / salida"| ORQ

    ORQ --> AN & EN & AU & CO & VE & AP
    ORQ -.->|"lee y escribe"| SPEC

    AN & EN & AU & CO & VE --> PORT
    AU -.-> REGLAS
    CO -.-> PLANT
    AN & EN & AU & CO & VE -.-> PROMPTS
    VE --> DOCKER
    AP --> MEM
    ORQ --> MEM

    PORT --> ANT & OAI & SUB

    classDef dom fill:#1d3557,stroke:#457b9d,color:#fff
    classDef port fill:#2a9d8f,stroke:#1d7268,color:#fff
    class SPEC,MOD dom
    class PORT port
```

**Reglas no negociables que el diagrama hace visibles:**

- Los agentes **no se comunican entre sí**: comparten estado a través de la `ProjectSpec`.
- Ningún agente llama a un proveedor de IA directamente; todos pasan por `LLMProvider`.
- Las reglas de auditoría, las plantillas y los prompts son **datos versionados**, no código.

---

## 2. Máquina de estados del ciclo

El orquestador es código determinístico. Cada fase devuelve la siguiente; los ciclos de
corrección viven **dentro** de su fase y todos tienen límite explícito.

```mermaid
stateDiagram-v2
    direction TB
    [*] --> ANALISIS

    ANALISIS: Análisis de documentos<br>(opcional, --docs)
    ENTREVISTA: Entrevista
    AUDITORIA: Auditoría de coherencia
    CONSTRUCCION: Construcción
    VERIFICACION: Verificación
    ENTREGA: Entrega
    APRENDIZAJE: Aprendizaje

    ANALISIS --> ENTREVISTA: propuestas con evidencia<br>(o se saltea sin documentos)

    ENTREVISTA --> ENTREVISTA: falta info / ajuste pedido<br>(máx. 30 turnos)
    ENTREVISTA --> AUDITORIA: spec completa y confirmada

    AUDITORIA --> AUDITORIA: 🟡 corregido o asumido<br>🔴 corregido<br>(máx. 3 ciclos)
    AUDITORIA --> CONSTRUCCION: 🟢 semáforo verde
    AUDITORIA --> [*]: 🔴 usuario aborta<br>o límite agotado

    CONSTRUCCION --> CONSTRUCCION: destino inválido<br>(máx. 3 intentos)
    CONSTRUCCION --> VERIFICACION: scaffold escrito

    VERIFICACION --> VERIFICACION: corrección de sintaxis (máx. 3/archivo)<br>corrección de build (máx. 2 ciclos)
    VERIFICACION --> ENTREGA: aprobado / el usuario acepta igual
    VERIFICACION --> [*]: falla persistente y el usuario aborta

    ENTREGA --> APRENDIZAJE: registro persistido
    APRENDIZAJE --> [*]
```

**Los dos bucles de retroalimentación tienen propósitos distintos**: el de coherencia valida
la *decisión* antes de construir nada; el de corrección valida la *ejecución* de lo
construido.

---

## 3. Diagrama de clases (UML)

Modelo de dominio, puerto de IA y agentes. Los agentes son *stateless* respecto del ciclo:
reciben la `ProjectSpec` y devuelven un resultado tipado.

```mermaid
classDiagram
    direction LR

    class ProjectSpec {
        +str nombre
        +str descripcion
        +str tipo_proyecto
        +str lenguaje
        +str framework
        +str arquitectura
        +str base_datos
        +str autenticacion
        +str gestion_secretos
        +str infraestructura
        +str ci_cd
        +str alcance
        +list~str~ notas
        +list~str~ riesgos_asumidos
        +campos_validos() set
        +campos_faltantes() list
        +esta_completa() bool
        +aplicar_updates(updates)
    }

    class Severidad {
        <<enumeration>>
        VERDE
        AMARILLO
        ROJO
        +peso int
    }

    class Hallazgo {
        +str id
        +Severidad severidad
        +str mensaje
        +str correccion_propuesta
        +str origen
    }

    class ResultadoAuditoria {
        +list~Hallazgo~ hallazgos
        +semaforo() Severidad
        +pendientes() list
    }

    class Chequeo {
        +str archivo
        +str estado
        +str detalle
        +bool obligatorio
    }

    class ResultadoVerificacion {
        +list~Chequeo~ chequeos
        +list~Chequeo~ profundos
        +errores() list
        +aprobado() bool
        +estado() EstadoVerificacion
    }

    class ResultadoConstruccion {
        +str stack
        +str raiz
        +list~str~ archivos
        +list~VerificacionProfunda~ verificaciones
    }

    class VerificacionProfunda {
        +str id
        +str tipo
        +list~str~ comando
        +str requiere
        +bool obligatoria
    }

    class RegistroProyecto {
        +str fecha
        +ProjectSpec spec
        +str stack
        +list~ResolucionHallazgo~ resoluciones
        +ResultadoVerificacion verificacion
        +EstadoVerificacion estado_final
        +list~str~ correcciones_build
        +str proveedor
        +float duracion_segundos
    }

    class ResolucionHallazgo {
        +Hallazgo hallazgo
        +str resolucion
    }

    class LLMProvider {
        <<interface>>
        +generate(system_prompt, messages) str
    }

    class Orquestador {
        +ProjectSpec spec
        +Fase fase_actual
        +ResultadoAuditoria auditoria
        +ejecutar() Path
        +construccion ResultadoConstruccion
        +verificacion ResultadoVerificacion
    }

    class Memoria {
        +guardar(registro) Path
        +cargar_registros() list
    }

    class Analista {
        +analizar(rutas) AnalisisDocumentos
    }
    class Entrevistador {
        +iniciar() RespuestaEntrevistador
        +responder(entrada) RespuestaEntrevistador
        +precargar_documentos(contexto)
    }
    class Auditor {
        +auditar(spec) ResultadoAuditoria
    }
    class Constructor {
        +construir(spec, destino) ResultadoConstruccion
    }
    class Verificador {
        +verificar(raiz) ResultadoVerificacion
        +verificar_profundo(...) list
        +corregir_archivo(...)
        +corregir_build(...) CorreccionBuild
    }
    class Aprendizaje {
        +resumen_historial() str
    }

    Orquestador "1" *-- "1" ProjectSpec
    Orquestador --> Analista
    Orquestador --> Entrevistador
    Orquestador --> Auditor
    Orquestador --> Constructor
    Orquestador --> Verificador
    Orquestador --> Aprendizaje
    Orquestador --> Memoria

    Analista ..> LLMProvider
    Entrevistador ..> LLMProvider
    Auditor ..> LLMProvider
    Constructor ..> LLMProvider
    Verificador ..> LLMProvider

    Auditor --> ResultadoAuditoria
    ResultadoAuditoria "1" o-- "*" Hallazgo
    Hallazgo --> Severidad
    Constructor --> ResultadoConstruccion
    ResultadoConstruccion "1" o-- "*" VerificacionProfunda
    Verificador --> ResultadoVerificacion
    ResultadoVerificacion "1" o-- "*" Chequeo
    Memoria --> RegistroProyecto
    RegistroProyecto "1" o-- "*" ResolucionHallazgo
    RegistroProyecto --> ProjectSpec
    ResolucionHallazgo --> Hallazgo

    LLMProvider <|.. AnthropicAPIProvider
    LLMProvider <|.. OpenAICompatProvider
    LLMProvider <|.. ClaudeSubscriptionProvider
```

---

## 4. Secuencia de una corrida real

Corrida verificada end-to-end: especificación incoherente (serverless + WebSockets) que el
Auditor bloquea, se corrige, y termina con build y smoke test reales en Docker.

```mermaid
sequenceDiagram
    autonumber
    actor U as Usuario
    participant O as Orquestador
    participant E as Entrevistador
    participant A as Auditor
    participant C as Constructor
    participant V as Verificador
    participant M as Memoria
    participant L as LLMProvider

    U->>O: pcia-web
    O->>E: iniciar()
    E->>L: generate(prompt entrevistador)
    L-->>E: {message, updates, done:false}
    E-->>U: "¿Qué proyecto querés crear?"
    U->>E: "chat de soporte en tiempo real"
    E->>L: generate(...)
    L-->>E: {updates: spec completa, done:true}
    E-->>O: spec completa
    O->>U: "¿Confirmás la especificación?"
    U-->>O: (enter)

    rect rgb(60, 30, 30)
    Note over O,A: Ciclo de coherencia
    O->>A: auditar(spec)
    A->>A: matriz de reglas determinísticas
    A->>L: pase LLM (lo no catalogado)
    A-->>O: 🔴 serverless-websockets (bloqueante)
    O->>U: "es bloqueante, no puede asumirse"
    U-->>O: aplicar corrección propuesta
    O->>E: responder(hallazgo + decisión)
    E->>L: generate(...)
    E-->>O: infraestructura → docker
    O->>A: auditar(spec)
    A-->>O: 🟢 verde
    end

    O->>C: construir(spec, destino)
    C->>C: render plantilla + condicionales<br>(PostgreSQL → compose + db.py)
    C->>L: generate(README y ADR)
    C-->>O: 15 archivos

    rect rgb(30, 50, 40)
    Note over O,V: Verificación
    O->>V: verificar(raiz)
    V-->>O: sintaxis 11 ok, 0 errores
    O->>V: verificar_profundo(...)
    V->>V: docker build ✅<br>smoke test ✅<br>ruff ⏭️ (opcional, ausente)
    V-->>O: aprobado con advertencias
    end

    O->>M: guardar(RegistroProyecto)
    M-->>O: memory/chat-soporte-*.json
    O-->>U: ⚠️ aprobado con advertencias
```
