from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.security import utcnow


class Universidad(Base):
    __tablename__ = "universidad"

    id_universidad: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(150), unique=True)
    siglas: Mapped[str | None] = mapped_column(String(15))


class Posicion(Base):
    __tablename__ = "posicion"

    id_posicion: Mapped[int] = mapped_column(TINYINT, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(30), unique=True)
    descripcion: Mapped[str | None] = mapped_column(String(200))


class Jugador(Base):
    __tablename__ = "jugador"
    __table_args__ = (Index("ix_jugador_apellidos_nombres", "apellidos", "nombres"),)

    id_jugador: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo_seudonimo: Mapped[str] = mapped_column(String(10), unique=True)
    nombres: Mapped[str] = mapped_column(String(100))
    apellidos: Mapped[str] = mapped_column(String(100))
    # Nullable: el Excel original solo trae la edad (ver CLAUDE.md).
    fecha_nacimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    id_universidad: Mapped[int] = mapped_column(ForeignKey("universidad.id_universidad"))
    id_posicion: Mapped[int] = mapped_column(ForeignKey("posicion.id_posicion"))
    consentimiento_informado: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0")
    )
    fecha_consentimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    universidad: Mapped[Universidad] = relationship(lazy="joined")
    posicion: Mapped[Posicion] = relationship(lazy="joined")


class MedicionAntropometrica(Base):
    __tablename__ = "medicion_antropometrica"
    __table_args__ = (
        UniqueConstraint("id_jugador", "fecha", name="uq_antropometria_jugador_fecha"),
        CheckConstraint("talla_m BETWEEN 1.40 AND 2.30", name="ck_antropometria_talla"),
        CheckConstraint("peso_kg BETWEEN 40 AND 150", name="ck_antropometria_peso"),
    )

    id_medicion: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    fecha: Mapped[date] = mapped_column(Date)
    talla_m: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    peso_kg: Mapped[Decimal] = mapped_column(Numeric(5, 2))
