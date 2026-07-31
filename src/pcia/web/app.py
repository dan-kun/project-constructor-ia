"""Servidor web: sirve la interfaz y conecta el navegador con el orquestador.

Una conexión WebSocket = una corrida del ciclo. El servidor solo bombea
eventos entre la ``SesionWeb`` (que corre el orquestador en un hilo) y el
navegador; toda la lógica del ciclo sigue viviendo en el orquestador.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from pcia.config import cargar_config, crear_provider
from pcia.web.sesion import SesionWeb

RUTA_ESTATICOS = Path(__file__).parent / "static"


def crear_app(ruta_config: str = "config.yaml") -> FastAPI:
    app = FastAPI(
        title="Project Constructor IA",
        description="Interfaz web del agente de construcción de proyectos",
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(RUTA_ESTATICOS / "index.html")

    @app.get("/api/config")
    def config_actual() -> dict[str, str | None]:
        """Qué proveedor está configurado (se muestra en la interfaz)."""
        config = cargar_config(ruta_config)
        nombre = config.get("provider", "")
        modelo = (config.get(nombre) or {}).get("model")
        return {"proveedor": nombre, "modelo": modelo}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            config = cargar_config(ruta_config)
            provider = crear_provider(config)
        except Exception as exc:
            await websocket.send_json(
                {"tipo": "error", "texto": f"Error de configuración: {exc}"}
            )
            await websocket.close()
            return

        nombre = config.get("provider", "")
        modelo = (config.get(nombre) or {}).get("model")
        sesion = SesionWeb(
            provider,
            memory_dir=Path(config.get("memory_dir", "memory")),
            proveedor=f"{nombre}:{modelo}" if modelo else nombre or None,
        )
        sesion.iniciar()

        # Un task bombea eventos del orquestador al navegador; el loop
        # principal escucha las respuestas del usuario.
        async def bombear() -> None:
            while True:
                evento = await asyncio.to_thread(sesion.siguiente_evento)
                await websocket.send_json(evento)
                if evento["tipo"] in ("fin", "error"):
                    return

        tarea = asyncio.create_task(bombear())
        try:
            while not tarea.done():
                recibir = asyncio.create_task(websocket.receive_json())
                listos, _ = await asyncio.wait(
                    {recibir, tarea}, return_when=asyncio.FIRST_COMPLETED
                )
                if recibir in listos:
                    sesion.responder(str(recibir.result().get("texto", "")))
                else:
                    recibir.cancel()
        except WebSocketDisconnect:
            pass
        finally:
            tarea.cancel()
            await asyncio.to_thread(sesion.cerrar)

    return app


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script ``pcia-web``."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(
        prog="pcia-web", description="Interfaz web de Project Constructor IA"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    print(f"Project Constructor IA — http://{args.host}:{args.port}")
    uvicorn.run(crear_app(args.config), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
