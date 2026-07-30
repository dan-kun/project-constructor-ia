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


class NuevaSesionRequest(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""
    base_url: str = ""


class InputRequest(BaseModel):
    texto: str


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


# Sirve el frontend estático al final: no debe tapar las rutas /api/*.
app.mount("/", StaticFiles(directory=RAIZ / "static", html=True), name="static")
