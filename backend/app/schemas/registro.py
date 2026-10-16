"""Esquemas de registro: tests/jornadas, sesiones y bienestar (RF-02, RF-08)."""

from datetime import date, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Momento = Literal["pre", "intermedia", "post", "seguimiento"]


def _no_futura(v: date) -> date:
    if v > date.today():
        raise ValueError("La fecha no puede ser futura")
    return v


# ------------------------------------------------------------------ tests / jornadas
class _JornadaBase(BaseModel):
    fecha: date
    momento: Momento
    hora_inicio: time | None = None
    observaciones: str | None = Field(default=None, max_length=500)

    _fecha_ok = field_validator("fecha")(_no_futura)


class TestsJugadorIn(_JornadaBase):
    """POST /jugadores/{id}/tests. CMJ admite hasta 3 intentos: {"CMJ": [38, 40, 39]}."""

    pruebas: dict[str, list[Decimal]] = Field(min_length=1)


class ResultadoJugadorIn(BaseModel):
    id_jugador: int
    pruebas: dict[str, list[Decimal]] = Field(min_length=1)


class JornadaLoteIn(_JornadaBase):
    """POST /jornadas: registro por lote (grilla jugadores × pruebas)."""

    resultados: list[ResultadoJugadorIn] = Field(min_length=1)


class ResultadoAtipicoOut(BaseModel):
    id_resultado: int
    id_jugador: int
    prueba: str
    valor: float


class JornadaRegistradaOut(BaseModel):
    id_jornada: int
    fecha: date
    momento: str
    jornada_nueva: bool
    resultados_registrados: int
    atipicos: list[ResultadoAtipicoOut]


class JornadaOut(BaseModel):
    id_jornada: int
    fecha: date
    momento: str
    hora_inicio: time | None
    origen: Literal["sistema", "migracion"]
    observaciones: str | None
    jugadores: int
    resultados: int


class CambioMomentoIn(BaseModel):
    momento: Momento
    confirmar_advertencia: bool = False


class ResultadoPendienteOut(BaseModel):
    id_resultado: int
    id_jornada: int
    fecha: date
    id_jugador: int
    codigo_seudonimo: str
    prueba: str
    valor: float
    intento: int


class ConfirmacionIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class ConfirmacionOut(BaseModel):
    confirmados: int
    ignorados: list[int]


class CorreccionIn(BaseModel):
    valor: Decimal


# ------------------------------------------------------------------ sesiones
class RegistroSesionIn(BaseModel):
    id_jugador: int
    duracion_efectiva_min: int = Field(ge=10, le=240)
    rpe: int = Field(ge=0, le=10, description="Esfuerzo percibido 0-10 (obligatorio)")


class _SesionBase(BaseModel):
    fecha: date
    tipo: Literal["entrenamiento", "partido"] = "entrenamiento"
    duracion_planificada_min: int | None = Field(default=None, ge=10, le=240)
    descripcion: str | None = Field(default=None, max_length=300)

    _fecha_ok = field_validator("fecha")(_no_futura)


class SesionLoteIn(_SesionBase):
    """POST /sesiones: varios jugadores de una misma sesión."""

    registros: list[RegistroSesionIn] = Field(min_length=1)


class SesionJugadorIn(_SesionBase):
    """POST /jugadores/{id}/sesiones."""

    duracion_efectiva_min: int = Field(ge=10, le=240)
    rpe: int = Field(ge=0, le=10)


class RegistroSesionEdicionIn(BaseModel):
    duracion_efectiva_min: int | None = Field(default=None, ge=10, le=240)
    rpe: int | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def _algo_que_cambiar(self):
        if not self.model_fields_set:
            raise ValueError("Indique al menos un campo a modificar")
        return self


class RegistroSesionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_registro: int
    id_sesion: int
    id_jugador: int
    duracion_efectiva_min: int
    rpe: int | None
    srpe: int | None


class SesionRegistradaOut(BaseModel):
    id_sesion: int
    fecha: date
    tipo: str
    sesion_nueva: bool
    registros: list[RegistroSesionOut]


# ------------------------------------------------------------------ bienestar
class _Items(BaseModel):
    """Los 4 ítems se orientan igual: 5 = mejor (duerme bien, sin fatiga, sin dolor, buen ánimo)."""

    sueno: int = Field(ge=1, le=5)
    fatiga: int = Field(ge=1, le=5, description="5 = sin fatiga")
    dolor_muscular: int = Field(ge=1, le=5, description="5 = sin dolor")
    animo: int = Field(ge=1, le=5)


class BienestarIn(_Items):
    semana_inicio: date = Field(description="Lunes de la semana")

    @field_validator("semana_inicio")
    @classmethod
    def _lunes(cls, v: date) -> date:
        if v.weekday() != 0:
            raise ValueError("semana_inicio debe ser un lunes")
        if v > date.today():
            raise ValueError("La semana no puede ser futura")
        return v


class BienestarEdicionIn(_Items):
    pass


class BienestarOut(BaseModel):
    id_cuestionario: int
    id_jugador: int
    semana_inicio: date
    sueno: int
    fatiga: int
    dolor_muscular: int
    animo: int
    indice_bienestar: int


# ------------------------------------------------------------------ carga (RF-09)
class SemanaCargaOut(BaseModel):
    semana_inicio: date
    carga_semanal: int
    num_sesiones: int
    acwr: float | None = Field(description="None = no disponible (menos de 4 semanas de historia)")
    caida_cmj_pct: float | None
    indice_bienestar: int | None
    var_bienestar: int | None


class CargaOut(BaseModel):
    id_jugador: int
    codigo_seudonimo: str
    semanas: list[SemanaCargaOut]
