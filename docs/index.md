# Project Constructor IA

Sistema multiagente para la **creación asistida y auditada** de la estructura inicial
(scaffold) de proyectos de software: entrevista al usuario, audita la coherencia técnica de
lo que pidió, construye, verifica lo construido con builds reales y aprende de cada proyecto.

Trabajo final del diplomado *Inteligencia Artificial Aplicada a Organizaciones* (UTN-FRBA).

!!! quote "El diferencial"
    No es generar archivos: es **cuestionar antes de construir y verificar después**. El
    sistema no entrega nada que no pueda verificar, y declara explícitamente lo que no sabe
    hacer.

## Enlaces del proyecto

- **Aplicación en vivo**: [project-constructor-ia.onrender.com](https://project-constructor-ia.onrender.com)
- **Código fuente**: [github.com/dan-kun/project-constructor-ia](https://github.com/dan-kun/project-constructor-ia)
- **Estado de la suite**: [![CI](https://github.com/dan-kun/project-constructor-ia/actions/workflows/ci.yml/badge.svg)](https://github.com/dan-kun/project-constructor-ia/actions/workflows/ci.yml) — 277 tests en Python 3.10 y 3.12

## El ciclo

```
Análisis → Entrevista → Auditoría → Construcción → Verificación → Entrega → Aprendizaje
              ↑______________|              |__________↑
           ciclo de coherencia         ciclo de corrección
```

Un orquestador determinístico —máquina de estados, no un agente LLM— coordina seis agentes
sobre un estado compartido (`ProjectSpec`). Los agentes no se comunican entre sí.

## Documentación

| Documento | Qué contiene |
|---|---|
| [Diseño y roadmap](DISENO.md) | Problema, agentes, ciclos, matriz de capacidades y trabajo futuro |
| [Arquitectura y diagramas](ARQUITECTURA.md) | Componentes, máquina de estados, UML y secuencia de una corrida real |
| [Tecnologías](TECNOLOGIAS.md) | Cada elección justificada, **incluidas las descartadas** |
| [Ciberseguridad](SEGURIDAD.md) | Riesgos por superficie, medidas con evidencia y **riesgo residual declarado** |
| [Autoevaluación UX/UI](UX.md) | Las 10 heurísticas de Nielsen con la escala de severidad del propio Nielsen |
| [IA en co-work](IA-COWORK.md) | Cómo se usó la IA, **qué falló y qué sorprendió** |
| [LLM/SLM local](LLM-LOCAL.md) | Rol en la arquitectura, privacidad y limitaciones frente a una API |

## Garantías de diseño

- **Agnóstico del modelo de IA**: toda interacción pasa por el puerto `LLMProvider`. Funciona
  con la API de Anthropic, cualquier API compatible con OpenAI (Ollama, llama.cpp, Groq…) o
  la suscripción de Claude vía CLI headless.
- **Toda salida de LLM se valida** contra un contrato tipado y se reintenta con el error como
  feedback; agotados los intentos, escala al usuario.
- **Todos los ciclos son acotados**, con límite explícito y configurable.
- **Degradación con gracia**: con modelos débiles, las reglas determinísticas del Auditor y
  las verificaciones automáticas sostienen las garantías mínimas.
