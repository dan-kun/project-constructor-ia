# Project Constructor IA

Sistema multiagente que entrevista, audita la coherencia técnica, construye
y verifica el scaffold de un proyecto de software nuevo — con criterio
experto verificable: cuestiona y advierte, no solo genera.

- Diseño completo: [`docs/DISENO.md`](docs/DISENO.md)
- Diagramas (arquitectura, flujo de agentes, secuencia): [`docs/DIAGRAMAS.md`](docs/DIAGRAMAS.md)
- Entrega final (informe): [`docs/ENTREGA_FINAL.md`](docs/ENTREGA_FINAL.md)
- Contexto para Claude Code: [`CLAUDE.md`](CLAUDE.md)

## Inicio rápido — CLI

```bash
pip install -e ".[dev]"
pytest
pcia   # entrevista interactiva por consola
```

Configurar proveedor de IA en `config.yaml` (agnóstico: API de Anthropic,
suscripción Claude Pro/Max, o cualquier API compatible con OpenAI —
Ollama local, Groq, OpenRouter, etc.).

## Demo web

El mismo Orquestador, servido por HTTP (FastAPI + Server-Sent Events) en
vez de la consola — ver [`src/pcia/web/`](src/pcia/web/).

```bash
pip install -e ".[dev,web]"
uvicorn pcia.web.app:app --reload
```

Abrir `http://localhost:8000`: el navegador elige el proveedor de IA y pega
su propia API key por sesión (nada se guarda en el servidor). Desplegable
gratis en Render con el `render.yaml` del repo (Blueprint, sin variables de
entorno que configurar).
