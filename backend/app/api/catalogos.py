from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import requiere_roles, usuario_actual
from app.models import Posicion, Universidad, Usuario
from app.schemas.jugador import PosicionOut, UniversidadIn, UniversidadOut
from app.services import bitacora

router = APIRouter(prefix="/catalogos", tags=["catalogos"])


@router.get("/posiciones", response_model=list[PosicionOut])
def posiciones(_: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    return db.scalars(select(Posicion).order_by(Posicion.id_posicion)).all()


@router.get("/universidades", response_model=list[UniversidadOut])
def universidades(_: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    return db.scalars(select(Universidad).order_by(Universidad.nombre)).all()


@router.post("/universidades", response_model=UniversidadOut, status_code=201)
def crear_universidad(
    datos: UniversidadIn,
    request: Request,
    usuario: Usuario = Depends(requiere_roles("administrador")),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Universidad).where(Universidad.nombre == datos.nombre)):
        raise HTTPException(409, "La universidad ya existe")
    u = Universidad(nombre=datos.nombre.strip(), siglas=datos.siglas)
    db.add(u)
    db.flush()
    bitacora.registrar(
        db,
        "crear",
        "universidad",
        id_usuario=usuario.id_usuario,
        id_registro=u.id_universidad,
        request=request,
    )
    db.commit()
    return u
