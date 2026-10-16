from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import usuario_actual
from app.models import Usuario
from app.schemas.auth import LoginIn, TokenOut, UsuarioOut
from app.services import auth as servicio

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(datos: LoginIn, request: Request, db: Session = Depends(get_db)):
    return servicio.iniciar_sesion(db, datos.correo, datos.contrasena, request)


@router.get("/yo", response_model=UsuarioOut)
def yo(usuario: Usuario = Depends(usuario_actual)):
    return usuario
