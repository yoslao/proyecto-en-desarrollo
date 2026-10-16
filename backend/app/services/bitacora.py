from fastapi import Request
from sqlalchemy.orm import Session

from app.models import BitacoraAuditoria


def registrar(
    db: Session,
    accion: str,
    entidad: str,
    *,
    id_usuario: int | None,
    id_registro: int | None = None,
    request: Request | None = None,
) -> None:
    """Agrega un asiento a la bitácora dentro de la transacción actual (el llamador hace commit)."""
    ip = request.client.host if request is not None and request.client else None
    db.add(
        BitacoraAuditoria(
            id_usuario=id_usuario,
            accion=accion,
            entidad=entidad,
            id_registro=id_registro,
            ip_origen=ip,
        )
    )
