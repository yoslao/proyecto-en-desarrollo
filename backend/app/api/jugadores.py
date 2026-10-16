from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import requiere_roles
from app.models import Usuario
from app.schemas.jugador import JugadorIn, JugadorLista, JugadorOut
from app.services import bitacora
from app.services import jugadores as servicio

router = APIRouter(prefix="/jugadores", tags=["jugadores"])

# RF-01: ambos roles gestionan jugadores. El administrador ve nombres solo en este módulo.
_ambos = requiere_roles("administrador", "entrenador")


@router.get("", response_model=JugadorLista)
def listar(
    request: Request,
    q: str | None = Query(None, max_length=100),
    activo: bool | None = None,
    limite: int = Query(50, ge=1, le=200),
    desplazamiento: int = Query(0, ge=0),
    usuario: Usuario = Depends(_ambos),
    db: Session = Depends(get_db),
):
    total, items = servicio.listar(db, q, activo, limite, desplazamiento)
    bitacora.registrar(db, "consultar", "jugador", id_usuario=usuario.id_usuario, request=request)
    db.commit()
    return JugadorLista(total=total, items=[servicio.a_salida(j) for j in items])


@router.post("", response_model=JugadorOut, status_code=201)
def crear(
    datos: JugadorIn,
    request: Request,
    usuario: Usuario = Depends(_ambos),
    db: Session = Depends(get_db),
):
    jugador = servicio.crear(db, datos)
    bitacora.registrar(
        db,
        "crear",
        "jugador",
        id_usuario=usuario.id_usuario,
        id_registro=jugador.id_jugador,
        request=request,
    )
    db.commit()
    db.refresh(jugador)
    return servicio.a_salida(jugador)


@router.get("/{id_jugador}", response_model=JugadorOut)
def detalle(
    id_jugador: int,
    request: Request,
    usuario: Usuario = Depends(_ambos),
    db: Session = Depends(get_db),
):
    jugador = servicio.obtener(db, id_jugador)
    bitacora.registrar(
        db,
        "consultar",
        "jugador",
        id_usuario=usuario.id_usuario,
        id_registro=id_jugador,
        request=request,
    )
    db.commit()
    return servicio.a_salida(jugador)


@router.put("/{id_jugador}", response_model=JugadorOut)
def actualizar(
    id_jugador: int,
    datos: JugadorIn,
    request: Request,
    usuario: Usuario = Depends(_ambos),
    db: Session = Depends(get_db),
):
    jugador = servicio.actualizar(db, id_jugador, datos)
    bitacora.registrar(
        db,
        "modificar",
        "jugador",
        id_usuario=usuario.id_usuario,
        id_registro=id_jugador,
        request=request,
    )
    db.commit()
    db.refresh(jugador)
    return servicio.a_salida(jugador)
