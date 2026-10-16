from datetime import datetime

from sqlalchemy import CHAR, Boolean, DateTime, Enum, String, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.security import utcnow

ROLES = ("administrador", "entrenador")


class Usuario(Base):
    __tablename__ = "usuario"

    id_usuario: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    nombre_completo: Mapped[str] = mapped_column(String(120))
    correo: Mapped[str] = mapped_column(String(120), unique=True)
    hash_contrasena: Mapped[str] = mapped_column(CHAR(60))
    rol: Mapped[str] = mapped_column(Enum(*ROLES, name="rol_usuario"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    intentos_fallidos: Mapped[int] = mapped_column(TINYINT, default=0, server_default=text("0"))
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
