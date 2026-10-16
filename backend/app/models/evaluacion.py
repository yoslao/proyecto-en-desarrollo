from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.security import utcnow

MOMENTOS = ("pre", "intermedia", "post", "seguimiento")
ESTADOS_RESULTADO = ("valido", "pendiente_confirmacion", "confirmado")


class TipoPrueba(Base):
    __tablename__ = "tipo_prueba"

    id_tipo_prueba: Mapped[int] = mapped_column(TINYINT, primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str] = mapped_column(String(100))
    unidad: Mapped[str] = mapped_column(String(15))
    valor_minimo: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    valor_maximo: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    mayor_es_mejor: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    intentos_max: Mapped[int] = mapped_column(TINYINT, default=1, server_default=text("1"))


class JornadaEvaluacion(Base):
    __tablename__ = "jornada_evaluacion"
    __table_args__ = (Index("ix_jornada_momento_fecha", "momento", "fecha"),)

    id_jornada: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fecha: Mapped[date] = mapped_column(Date)
    momento: Mapped[str] = mapped_column(Enum(*MOMENTOS, name="momento_jornada"))
    hora_inicio: Mapped[time | None] = mapped_column(Time, nullable=True)
    observaciones: Mapped[str | None] = mapped_column(String(500))
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"))


class ResultadoPrueba(Base):
    __tablename__ = "resultado_prueba"
    __table_args__ = (
        UniqueConstraint(
            "id_jornada", "id_jugador", "id_tipo_prueba", "intento", name="uq_resultado_intento"
        ),
        CheckConstraint("intento BETWEEN 1 AND 3", name="ck_resultado_intento"),
        Index("ix_resultado_jugador_prueba", "id_jugador", "id_tipo_prueba"),
    )

    id_resultado: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id_jornada: Mapped[int] = mapped_column(ForeignKey("jornada_evaluacion.id_jornada"))
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    id_tipo_prueba: Mapped[int] = mapped_column(ForeignKey("tipo_prueba.id_tipo_prueba"))
    intento: Mapped[int] = mapped_column(TINYINT, default=1, server_default=text("1"))
    valor: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    es_atipico: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    estado: Mapped[str] = mapped_column(
        Enum(*ESTADOS_RESULTADO, name="estado_resultado"), default="valido"
    )
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Sesion(Base):
    __tablename__ = "sesion"
    __table_args__ = (Index("ix_sesion_fecha", "fecha"),)

    id_sesion: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fecha: Mapped[date] = mapped_column(Date)
    tipo: Mapped[str] = mapped_column(Enum("entrenamiento", "partido", name="tipo_sesion"))
    duracion_planificada_min: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    descripcion: Mapped[str | None] = mapped_column(String(300))
    origen: Mapped[str] = mapped_column(
        Enum("sistema", "migracion", name="origen_sesion"), default="sistema"
    )
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"))


class RegistroSesionJugador(Base):
    __tablename__ = "registro_sesion_jugador"
    __table_args__ = (
        UniqueConstraint("id_sesion", "id_jugador", name="uq_registro_sesion_jugador"),
        CheckConstraint("duracion_efectiva_min BETWEEN 10 AND 240", name="ck_registro_duracion"),
        CheckConstraint("rpe IS NULL OR rpe BETWEEN 0 AND 10", name="ck_registro_rpe"),
    )

    id_registro: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id_sesion: Mapped[int] = mapped_column(ForeignKey("sesion.id_sesion"))
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    duracion_efectiva_min: Mapped[int] = mapped_column(SmallInteger)
    # NULL solo permitido si sesion.origen = 'migracion' (se valida en el servicio).
    rpe: Mapped[int | None] = mapped_column(TINYINT, nullable=True)
    srpe: Mapped[int | None] = mapped_column(
        Integer, Computed("rpe * duracion_efectiva_min", persisted=False)
    )


class CuestionarioBienestar(Base):
    __tablename__ = "cuestionario_bienestar"
    __table_args__ = (
        UniqueConstraint("id_jugador", "semana_inicio", name="uq_bienestar_jugador_semana"),
        CheckConstraint("WEEKDAY(semana_inicio) = 0", name="ck_bienestar_lunes"),
        CheckConstraint("sueno BETWEEN 1 AND 5", name="ck_bienestar_sueno"),
        CheckConstraint("fatiga BETWEEN 1 AND 5", name="ck_bienestar_fatiga"),
        CheckConstraint("dolor_muscular BETWEEN 1 AND 5", name="ck_bienestar_dolor"),
        CheckConstraint("animo BETWEEN 1 AND 5", name="ck_bienestar_animo"),
    )

    id_cuestionario: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    semana_inicio: Mapped[date] = mapped_column(Date)
    sueno: Mapped[int] = mapped_column(TINYINT)
    fatiga: Mapped[int] = mapped_column(TINYINT)
    dolor_muscular: Mapped[int] = mapped_column(TINYINT)
    animo: Mapped[int] = mapped_column(TINYINT)
    indice_bienestar: Mapped[int] = mapped_column(
        TINYINT, Computed("sueno + fatiga + dolor_muscular + animo", persisted=False), nullable=True
    )


class IndicadorCargaSemanal(Base):
    """Desnormalización controlada: solo la escribe la Calculadora de indicadores."""

    __tablename__ = "indicador_carga_semanal"
    __table_args__ = (
        UniqueConstraint("id_jugador", "semana_inicio", name="uq_indicador_jugador_semana"),
    )

    id_indicador: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    semana_inicio: Mapped[date] = mapped_column(Date)
    carga_semanal: Mapped[int] = mapped_column(Integer)
    num_sesiones: Mapped[int] = mapped_column(TINYINT)
    acwr: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    caida_cmj_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    calculado_en: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
