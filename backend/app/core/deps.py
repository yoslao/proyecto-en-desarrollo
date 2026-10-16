from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decodificar_token
from app.models import Usuario

_bearer = HTTPBearer(auto_error=False)


def usuario_actual(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    no_autenticado = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Credenciales inválidas o sesión expirada",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if cred is None:
        raise no_autenticado
    payload = decodificar_token(cred.credentials)
    if payload is None or not str(payload.get("sub", "")).isdigit():
        raise no_autenticado
    usuario = db.get(Usuario, int(payload["sub"]))
    if usuario is None or not usuario.activo:
        raise no_autenticado
    return usuario


def requiere_roles(*roles: str) -> Callable[..., Usuario]:
    """Dependencia RBAC: el backend verifica el rol en cada endpoint."""

    def _verificar(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No tiene permiso para esta acción")
        return usuario

    return _verificar


def ip_cliente(request: Request) -> str | None:
    return request.client.host if request.client else None
