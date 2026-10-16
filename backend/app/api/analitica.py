from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import requiere_roles
from app.models import Jugador, Usuario
from app.schemas.clustering import (
    ClusteringIn,
    ClusteringOut,
    ClusteringResumenOut,
    ClusterJugadorOut,
)
from app.services import bitacora, clustering

router = APIRouter(tags=["analitica"])

_admin = requiere_roles("administrador")
_entrenador = requiere_roles("entrenador")
_ambos = requiere_roles("administrador", "entrenador")


@router.post("/analitica/clustering", response_model=ClusteringOut, status_code=201)
def ejecutar_clustering(
    datos: ClusteringIn,
    request: Request,
    usuario: Usuario = Depends(_admin),
    db: Session = Depends(get_db),
):
    salida = clustering.ejecutar(db, usuario, datos)
    bitacora.registrar(
        db,
        "crear",
        "ejecucion_clustering",
        id_usuario=usuario.id_usuario,
        id_registro=salida.id_ejecucion,
        request=request,
    )
    db.commit()
    return salida


@router.get("/analitica/clustering/ultimo", response_model=ClusteringOut)
def ultimo_clustering(_: Usuario = Depends(_ambos), db: Session = Depends(get_db)):
    e = clustering.ultima_ejecucion(db)
    if e is None:
        raise HTTPException(404, "Aún no se ha ejecutado el K-means")
    return clustering.a_salida(db, e)


@router.get("/analitica/clustering", response_model=list[ClusteringResumenOut])
def historial_clustering(
    limite: int = Query(20, ge=1, le=100),
    _: Usuario = Depends(_ambos),
    db: Session = Depends(get_db),
):
    return clustering.historial(db, limite)


@router.get("/jugadores/{id_jugador}/cluster", response_model=ClusterJugadorOut)
def cluster_jugador(
    id_jugador: int, _: Usuario = Depends(_entrenador), db: Session = Depends(get_db)
):
    if db.get(Jugador, id_jugador) is None:
        raise HTTPException(404, "Jugador no encontrado")
    salida = clustering.cluster_de_jugador(db, id_jugador)
    if salida is None:
        raise HTTPException(404, "El jugador no tiene cluster asignado")
    return salida
