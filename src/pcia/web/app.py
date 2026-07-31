"""App FastAPI: adapter web sobre el Orquestador (ver sessions.py).

Arranque local:  uvicorn pcia.web.app:app --reload
Arranque en Render: uvicorn pcia.web.app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pcia.web.sessions import (
    ConfigError,
    GestorSesiones,
    LLMProviderError,
    SesionInvalidaError,
    validar_config_proveedor,
)

RAIZ = Path(__file__).parent
MEMORY_DIR = Path("memory")

app = FastAPI(title="Project Constructor IA — demo web")
gestor = GestorSesiones(memory_dir=MEMORY_DIR)


@app.middleware("http")
async def sin_cache_en_estaticos(request, call_next):
    """El frontend es HTML/JS/CSS servido tal cual desde disco (sin build ni
    hash en el nombre de archivo): sin esto, el navegador puede seguir
    mostrando una versión vieja de style.css/app.js tras cada cambio."""
    respuesta = await call_next(request)
    if not request.url.path.startswith("/api/"):
        respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


class NuevaSesionRequest(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""
    base_url: str = ""


class InputRequest(BaseModel):
    texto: str


class DescubrirModelosRequest(BaseModel):
    base_url: str
    api_key: str = ""


@app.post("/api/sessions")
def crear_sesion(datos: NuevaSesionRequest) -> dict[str, str]:
    try:
        config_proveedor = validar_config_proveedor(datos.model_dump())
        sesion = gestor.crear(config_proveedor)
    except (ConfigError, LLMProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id": sesion.id}


@app.post("/api/sessions/{sesion_id}/input", status_code=204)
def enviar_input(sesion_id: str, datos: InputRequest) -> None:
    try:
        sesion = gestor.obtener(sesion_id)
    except SesionInvalidaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    sesion.enviar_input(datos.texto)


@app.post("/api/discover-models")
def descubrir_modelos(datos: DescubrirModelosRequest) -> dict[str, list[str]]:
    """Respaldo server-side de 'Cargar disponibles' cuando el navegador no
    puede pegarle directo al proveedor (CORS) — Ollama Cloud, por ejemplo,
    no habilita CORS para su API, pero el servidor sí puede alcanzarla sin
    ese problema (CORS es una restricción del navegador, no del backend)."""
    base_url = datos.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {datos.api_key}"} if datos.api_key else {}
    try:
        resp = httpx.get(f"{base_url}/models", headers=headers, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502, detail=f"{base_url} devolvió {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"No se pudo consultar {base_url}: {exc}"
        ) from exc
    datos_respuesta = resp.json()
    nombres = sorted(m["id"] for m in datos_respuesta.get("data", []) if "id" in m)
    if not nombres:
        raise HTTPException(status_code=502, detail="La respuesta no trae modelos.")
    return {"modelos": nombres}


@app.get("/api/sessions/{sesion_id}/events")
async def eventos(sesion_id: str) -> StreamingResponse:
    try:
        sesion = gestor.obtener(sesion_id)
    except SesionInvalidaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def flujo():
        while True:
            evento = await asyncio.to_thread(sesion.proximo_evento)
            if evento is None:
                yield ": keepalive\n\n"  # comentario SSE: mantiene viva la conexión
                continue
            yield f"data: {json.dumps({'tipo': evento.tipo, 'texto': evento.texto})}\n\n"
            if evento.tipo in ("fin", "error"):
                return

    return StreamingResponse(flujo(), media_type="text/event-stream")


@app.get("/api/sessions/{sesion_id}/download")
def descargar_proyecto(sesion_id: str) -> FileResponse:
    try:
        sesion = gestor.obtener(sesion_id)
    except SesionInvalidaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if sesion.ruta_proyecto is None or not sesion.ruta_proyecto.exists():
        raise HTTPException(
            status_code=404,
            detail="Todavía no hay un proyecto construido en esta sesión.",
        )
    destino_zip = Path(tempfile.gettempdir()) / f"pcia-{sesion_id}"
    ruta_zip = shutil.make_archive(str(destino_zip), "zip", sesion.ruta_proyecto)
    return FileResponse(
        ruta_zip, filename=f"{sesion.ruta_proyecto.name}.zip", media_type="application/zip"
    )


@app.get("/api/sessions/{sesion_id}/transcript")
def descargar_transcript(sesion_id: str) -> FileResponse:
    try:
        sesion = gestor.obtener(sesion_id)
    except SesionInvalidaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if sesion.ruta_transcript is None or not sesion.ruta_transcript.exists():
        raise HTTPException(
            status_code=404,
            detail="La conversación todavía no terminó en esta sesión.",
        )
    return FileResponse(
        sesion.ruta_transcript, filename="conversacion.txt", media_type="text/plain"
    )


# Sirve el frontend estático al final: no debe tapar las rutas /api/*.
app.mount("/", StaticFiles(directory=RAIZ / "static", html=True), name="static")
