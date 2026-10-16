from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, catalogos, jugadores
from app.core.config import get_settings


def crear_app() -> FastAPI:
    s = get_settings()
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

    @app.get("/salud", tags=["sistema"])
    def salud():
        return {"estado": "ok"}

    return app


app = crear_app()
