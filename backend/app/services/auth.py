from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import crear_token, hashear, utcnow, verificar
from app.models import Usuario
from app.schemas.auth import TokenOut
from app.services import bitacora

# Hash de relleno para igualar el tiempo de respuesta cuando el correo no existe.
_HASH_FALSO = hashear("relleno-tiempo-constante")


def iniciar_sesion(db: Session, correo: str, contrasena: str, request: Request) -> TokenOut:
    s = get_settings()
    usuario = db.scalar(select(Usuario).where(Usuario.correo == correo))

    def _fallo(codigo: int, detalle: str) -> HTTPException:
        bitacora.registrar(
            db,
            "login_fallido",
            "usuario",
            id_usuario=usuario.id_usuario if usuario else None,
            id_registro=usuario.id_usuario if usuario else None,
            request=request,
        )
        db.commit()
        return HTTPException(codigo, detalle)

    if usuario is None:
        verificar(contrasena, _HASH_FALSO)
        raise _fallo(status.HTTP_401_UNAUTHORIZED, "Correo o contraseña incorrectos")

    ahora = utcnow()
    if usuario.bloqueado_hasta and usuario.bloqueado_hasta > ahora:
        minutos = int((usuario.bloqueado_hasta - ahora).total_seconds() // 60) + 1
        raise _fallo(
            status.HTTP_423_LOCKED,
            f"Cuenta bloqueada temporalmente. Intente de nuevo en {minutos} min.",
        )

    if not usuario.activo or not verificar(contrasena, usuario.hash_contrasena):
        usuario.intentos_fallidos += 1
        if usuario.intentos_fallidos >= s.login_max_intentos:
            usuario.bloqueado_hasta = ahora + timedelta(minutes=s.login_bloqueo_minutos)
            usuario.intentos_fallidos = 0
        raise _fallo(status.HTTP_401_UNAUTHORIZED, "Correo o contraseña incorrectos")

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    bitacora.registrar(
        db,
        "login",
        "usuario",
        id_usuario=usuario.id_usuario,
        id_registro=usuario.id_usuario,
        request=request,
    )
    db.commit()
    token, expira = crear_token(usuario.id_usuario, usuario.rol)
    return TokenOut(
        access_token=token,
        expira_en_s=expira,
        rol=usuario.rol,
        nombre_completo=usuario.nombre_completo,
    )
