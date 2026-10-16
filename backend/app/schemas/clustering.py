from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class ClusteringIn(BaseModel):
    """Ejecución del K-means. Toma la ÚLTIMA medición de cada variable dentro del rango."""

    modo: Literal["principal", "exploratorio"] = "principal"
    fecha_desde: date | None = None
    fecha_hasta: date | None = None

    @model_validator(mode="after")
    def _rango(self):
        if self.fecha_desde and self.fecha_hasta and self.fecha_desde > self.fecha_hasta:
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        return self


class JornadaUsada(BaseModel):
    id_jornada: int
    fecha: date


class ClusterOut(BaseModel):
    cluster: int
    etiqueta: str
    n: int
    centroide: dict[str, float]  # unidades originales
    z: dict[str, float]  # puntaje estandarizado (positivo = por encima de la media del grupo)


class AsignacionOut(BaseModel):
    id_jugador: int
    codigo_seudonimo: str
    cluster: int
    etiqueta: str
    distancia_centroide: float
    jornadas_usadas: dict[str, JornadaUsada]


class SinAsignarOut(BaseModel):
    id_jugador: int
    codigo_seudonimo: str
    faltantes: list[str]


class SiluetaK(BaseModel):
    k: int
    silueta: float


class ClusteringOut(BaseModel):
    id_ejecucion: int
    fecha_hora: datetime
    modo: str
    k: int
    silueta: float
    silueta_minima: float
    silueta_baja: bool
    variables: list[str]
    fecha_desde: date | None
    fecha_hasta: date | None
    k_evaluados: list[SiluetaK]
    advertencias: list[str]
    clusters: list[ClusterOut]
    asignaciones: list[AsignacionOut]
    sin_asignar: list[SinAsignarOut]


class ClusteringResumenOut(BaseModel):
    id_ejecucion: int
    fecha_hora: datetime
    modo: str
    k: int
    silueta: float
    variables: list[str]
    n_asignados: int


class ClusterJugadorOut(BaseModel):
    id_jugador: int
    codigo_seudonimo: str
    id_ejecucion: int
    fecha_hora: datetime
    cluster: int
    etiqueta: str
    modo: str
