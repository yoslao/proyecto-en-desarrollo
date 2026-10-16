"""Esquemas Pydantic de lo que debe devolver el LLM, y la validación de seguridad de la rutina."""

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")  # claves de más no invalidan la respuesta


# ------------------------------------------------------------------ rutina
class EjercicioLLM(_Base):
    nombre: str = Field(min_length=1, max_length=150)
    series: int = Field(ge=1, le=12)
    repeticiones: str = Field(min_length=1, max_length=30)
    duracion: str | None = Field(default=None, max_length=50)
    intensidad: Literal["baja", "media", "alta"]
    descanso_seg: int = Field(ge=0, le=900)

    @field_validator("repeticiones", mode="before")
    @classmethod
    def _rep_a_texto(cls, v):
        return str(v) if isinstance(v, int) else v

    @field_validator("intensidad", mode="before")
    @classmethod
    def _intensidad_minuscula(cls, v):
        return _sin_tildes(v).strip().lower() if isinstance(v, str) else v


class SesionLLM(_Base):
    dia: Literal["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    enfoque: str = Field(min_length=1, max_length=200)
    duracion_min: int = Field(ge=10, le=300)
    ejercicios: list[EjercicioLLM] = Field(min_length=1, max_length=20)

    @field_validator("dia", mode="before")
    @classmethod
    def _dia_normalizado(cls, v):
        return _sin_tildes(v).strip().lower() if isinstance(v, str) else v


class SemanaLLM(_Base):
    numero: int = Field(ge=1, le=4)
    sesiones: list[SesionLLM] = Field(min_length=1, max_length=7)


class RutinaLLM(_Base):
    semanas: list[SemanaLLM] = Field(min_length=1, max_length=4)
    justificacion: str = Field(min_length=10, max_length=4000)
    indicadores_usados: list[str] = Field(min_length=1)
    precauciones: list[str] = Field(default_factory=list)


def validar_seguridad_rutina(
    rutina: RutinaLLM, semanas_pedidas: int, max_sesiones_semana: int, max_duracion_sesion: int
) -> None:
    """Rechaza (ValueError) rutinas que incumplen los límites de seguridad o la solicitud."""
    if len(rutina.semanas) != semanas_pedidas:
        raise ValueError(f"Se pidieron {semanas_pedidas} semanas y llegaron {len(rutina.semanas)}")
    for i, sem in enumerate(rutina.semanas, start=1):
        if sem.numero != i:
            raise ValueError(f"La semana {i} viene numerada como {sem.numero}")
        if len(sem.sesiones) > max_sesiones_semana:
            raise ValueError(
                f"Semana {i}: {len(sem.sesiones)} sesiones superan el máximo de {max_sesiones_semana}"
            )
        dias = [s.dia for s in sem.sesiones]
        if len(set(dias)) != len(dias):
            raise ValueError(f"Semana {i}: más de una sesión el mismo día")
        for s in sem.sesiones:
            if s.duracion_min > max_duracion_sesion:
                raise ValueError(
                    f"Semana {i}, {s.dia}: {s.duracion_min} min superan el máximo de "
                    f"{max_duracion_sesion} min"
                )


# ------------------------------------------------------------------ riesgo
class IndicadorAfectadoLLM(_Base):
    indicador: str = Field(min_length=1, max_length=100)
    valor: float
    umbral: float | None = None


class RiesgoLLM(_Base):
    nivel_riesgo: Literal["bajo", "medio", "alto"]
    indicadores_afectados: list[IndicadorAfectadoLLM] = Field(default_factory=list)
    justificacion: str = Field(min_length=10, max_length=3000)
    recomendacion_inmediata: str = Field(min_length=1, max_length=600)

    @field_validator("nivel_riesgo", mode="before")
    @classmethod
    def _nivel_minuscula(cls, v):
        return v.strip().lower() if isinstance(v, str) else v
