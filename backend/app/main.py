from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analitica, auth, catalogos, ia, jugadores, registro
from app.core.config import get_settings
from app.services import riesgo, riesgo_hooks


def crear_app() -> FastAPI:
    s = get_settings()
    riesgo_hooks.registrar_callback(riesgo.evaluar_en_segundo_plano)  # RF-04
    app = FastAPI(
        title="VoleyIA Coach API",
        version="0.1.0",
        docs_url=None if s.es_produccion else "/docs",
        redoc_url=None if s.es_produccion else "/redoc",
        openapi_url=None if s.es_produccion else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(auth.router)
    app.include_router(catalogos.router)
    app.include_router(jugadores.router)
    app.include_router(registro.router)
    app.include_router(analitica.router)
    app.include_router(ia.router)

    @app.get("/salud", tags=["sistema"])
    def salud():
        return {"estado": "ok"}

    return app


app = crear_app()
