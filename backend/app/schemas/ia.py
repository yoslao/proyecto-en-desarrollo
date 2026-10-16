from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ rutinas
class GenerarRutinaIn(BaseModel):
    semanas: int = Field(ge=1, le=4, description="Duración de la rutina en semanas (1-4)")
    objetivo_prioritario: Literal["velocidad", "resistencia", "salto"]


class TareaCreadaOut(BaseModel):
    id_tarea: str
    estado: str = "pendiente"
    consulta: str = Field(description="URL para consultar el progreso")


class TareaOut(BaseModel):
    id_tarea: str
    tipo: str
    estado: Literal["pendiente", "en_curso", "completada", "error"]
    paso: str
    progreso: int = Field(ge=0, le=100)
    resultado: dict | None = None


class AprobacionIn(BaseModel):
    """El entrenador decide: aprobar o rechazar, calificando de 1 a 5."""

    decision: Literal["aprobar", "rechazar"]
    calificacion_pertinencia: int = Field(ge=1, le=5)
    calificacion_personalizacion: int = Field(ge=1, le=5)
    comentario: str | None = Field(default=None, max_length=500)


class RutinaOut(BaseModel):
    id_rutina: int
    id_jugador: int
    codigo_seudonimo: str
    fecha_generacion: datetime
    semanas: int
    objetivo_prioritario: str
    contenido: dict
    estado: Literal["propuesta", "aprobada", "rechazada"]
    calificacion_pertinencia: int | None
    calificacion_personalizacion: int | None
    comentario_revision: str | None
    fecha_revision: datetime | None
    modelo_ia: str
    version_prompt: str
    es_respaldo: bool


# ------------------------------------------------------------------ riesgo
class IndicadorAfectadoOut(BaseModel):
    indicador: str
    valor: float
    umbral: float | None = None


class EvaluacionRiesgoOut(BaseModel):
    id_evaluacion_riesgo: int
    id_jugador: int
    codigo_seudonimo: str
    origen: str
    fecha_hora: datetime
    nivel_riesgo: Literal["bajo", "medio", "alto"]
    es_alerta: bool
    indicadores_afectados: list[IndicadorAfectadoOut]
    justificacion: str
    recomendacion_inmediata: str | None
    estado_revision: Literal["pendiente", "confirmada", "descartada"]
    fecha_revision: datetime | None
    modelo_ia: str


class RevisionRiesgoIn(BaseModel):
    """Confirmar = el entrenador coincide con el nivel de riesgo; descartar = no coincide."""

    decision: Literal["confirmar", "descartar"]


class CierreSemanaOut(BaseModel):
    jugadores_programados: int
    omitidos_sin_datos: int
