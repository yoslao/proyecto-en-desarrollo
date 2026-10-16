from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.security import utcnow


class ConfiguracionUmbral(Base):
    __tablename__ = "configuracion_umbral"

    id_umbral: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    clave: Mapped[str] = mapped_column(String(40), unique=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    descripcion: Mapped[str | None] = mapped_column(String(200))
    id_usuario_actualiza: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id_usuario"), nullable=True
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ConfiguracionSistema(Base):
    """Parámetros textuales editables (modelos de IA, variables del K-means...)."""

    __tablename__ = "configuracion_sistema"

    id_config: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    clave: Mapped[str] = mapped_column(String(60), unique=True)
    valor: Mapped[str] = mapped_column(String(500), default="")
    descripcion: Mapped[str | None] = mapped_column(String(200))
    id_usuario_actualiza: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id_usuario"), nullable=True
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BitacoraAuditoria(Base):
    """Solo inserción (triggers en la migración impiden UPDATE y DELETE)."""

    __tablename__ = "bitacora_auditoria"
    __table_args__ = (
        Index("ix_bitacora_fecha", "fecha_hora"),
        Index("ix_bitacora_entidad_registro", "entidad", "id_registro"),
    )

    id_bitacora: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id_usuario: Mapped[int | None] = mapped_column(ForeignKey("usuario.id_usuario"), nullable=True)
    accion: Mapped[str] = mapped_column(
        Enum(
            "consultar",
            "crear",
            "modificar",
            "eliminar",
            "login",
            "login_fallido",
            name="accion_bitacora",
        )
    )
    entidad: Mapped[str] = mapped_column(String(50))
    id_registro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fecha_hora: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    ip_origen: Mapped[str | None] = mapped_column(String(45))


class EjecucionClustering(Base):
    __tablename__ = "ejecucion_clustering"

    id_ejecucion: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Spec original: FK a una jornada. Como el K-means toma la última medición dentro de un
    # rango de fechas (CLAUDE.md), se guarda el rango y las jornadas usadas por jugador.
    fecha_desde: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_hasta: Mapped[date | None] = mapped_column(Date, nullable=True)
    id_jornada: Mapped[int | None] = mapped_column(
        ForeignKey("jornada_evaluacion.id_jornada"), nullable=True
    )
    k: Mapped[int] = mapped_column(TINYINT)
    silueta: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    variables: Mapped[list] = mapped_column(JSON)
    centroides: Mapped[list] = mapped_column(JSON)
    etiquetas: Mapped[dict] = mapped_column(JSON)
    jornadas_usadas: Mapped[dict | None] = mapped_column(JSON)
    # modo, silueta por k, advertencias y jugadores sin asignar
    diagnostico: Mapped[dict | None] = mapped_column(JSON)
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"))


class AsignacionCluster(Base):
    __tablename__ = "asignacion_cluster"
    __table_args__ = (UniqueConstraint("id_ejecucion", "id_jugador", name="uq_asignacion"),)

    id_asignacion: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id_ejecucion: Mapped[int] = mapped_column(ForeignKey("ejecucion_clustering.id_ejecucion"))
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    cluster: Mapped[int] = mapped_column(TINYINT)
    distancia_centroide: Mapped[Decimal] = mapped_column(Numeric(6, 3))
