from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.mysql import CHAR, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.security import utcnow


class LlamadaIA(Base):
    """Registro de cada llamada al LLM. NUNCA guarda el prompt: solo su hash SHA-256."""

    __tablename__ = "llamada_ia"
    __table_args__ = (
        Index("ix_llamada_tarea_fecha", "tarea", "fecha_hora"),
        Index("ix_llamada_hash", "hash_entrada"),
    )

    id_llamada: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tarea: Mapped[str] = mapped_column(Enum("rutina", "riesgo", name="tarea_ia"))
    modelo: Mapped[str] = mapped_column(String(100))
    es_respaldo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    version_prompt: Mapped[str] = mapped_column(String(20))
    latencia_ms: Mapped[int] = mapped_column(Integer)
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)
    costo_usd: Mapped[Decimal] = mapped_column(Numeric(8, 5), default=0)
    valido_primer_intento: Mapped[bool] = mapped_column(Boolean, default=False)
    num_reintentos: Mapped[int] = mapped_column(TINYINT, default=0, server_default=text("0"))
    estado: Mapped[str] = mapped_column(
        Enum("exito", "invalida", "error_proveedor", name="estado_llamada_ia")
    )
    es_caso_control: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    hash_entrada: Mapped[str] = mapped_column(CHAR(64))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Rutina(Base):
    __tablename__ = "rutina"
    __table_args__ = (
        CheckConstraint("semanas BETWEEN 1 AND 4", name="ck_rutina_semanas"),
        CheckConstraint("JSON_VALID(contenido)", name="ck_rutina_contenido_json"),
        CheckConstraint(
            "calificacion_pertinencia IS NULL OR calificacion_pertinencia BETWEEN 1 AND 5",
            name="ck_rutina_pertinencia",
        ),
        CheckConstraint(
            "calificacion_personalizacion IS NULL OR calificacion_personalizacion BETWEEN 1 AND 5",
            name="ck_rutina_personalizacion",
        ),
        Index("ix_rutina_jugador_fecha", "id_jugador", "fecha_generacion"),
        Index("ix_rutina_estado", "estado"),
    )

    id_rutina: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    id_llamada: Mapped[int] = mapped_column(ForeignKey("llamada_ia.id_llamada"), unique=True)
    id_usuario_solicita: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"))
    id_usuario_revisa: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id_usuario"), nullable=True
    )
    fecha_generacion: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    semanas: Mapped[int] = mapped_column(TINYINT)
    objetivo_prioritario: Mapped[str] = mapped_column(
        Enum("velocidad", "resistencia", "salto", name="objetivo_rutina")
    )
    contenido: Mapped[dict] = mapped_column(JSON)
    estado: Mapped[str] = mapped_column(
        Enum("propuesta", "aprobada", "rechazada", name="estado_rutina"), default="propuesta"
    )
    calificacion_pertinencia: Mapped[int | None] = mapped_column(TINYINT, nullable=True)
    calificacion_personalizacion: Mapped[int | None] = mapped_column(TINYINT, nullable=True)
    comentario_revision: Mapped[str | None] = mapped_column(String(500))
    fecha_revision: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EvaluacionRiesgo(Base):
    __tablename__ = "evaluacion_riesgo"
    __table_args__ = (
        Index("ix_riesgo_estado_nivel", "estado_revision", "nivel_riesgo"),
        Index("ix_riesgo_jugador_fecha", "id_jugador", "fecha_hora"),
    )

    id_evaluacion_riesgo: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id_jugador: Mapped[int] = mapped_column(ForeignKey("jugador.id_jugador"))
    id_llamada: Mapped[int] = mapped_column(ForeignKey("llamada_ia.id_llamada"), unique=True)
    origen: Mapped[str] = mapped_column(Enum("nuevo_test", "cierre_semana", name="origen_riesgo"))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    nivel_riesgo: Mapped[str] = mapped_column(Enum("bajo", "medio", "alto", name="nivel_riesgo"))
    es_alerta: Mapped[bool] = mapped_column(
        Boolean, Computed("nivel_riesgo IN ('medio','alto')", persisted=False), nullable=True
    )
    indicadores_afectados: Mapped[list | None] = mapped_column(JSON)
    justificacion: Mapped[str] = mapped_column(Text)
    recomendacion_inmediata: Mapped[str | None] = mapped_column(String(500))
    estado_revision: Mapped[str] = mapped_column(
        Enum("pendiente", "confirmada", "descartada", name="estado_revision_riesgo"),
        default="pendiente",
    )
    id_usuario_revisa: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id_usuario"), nullable=True
    )
    fecha_revision: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
