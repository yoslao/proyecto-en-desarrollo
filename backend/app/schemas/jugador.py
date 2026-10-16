from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _JugadorBase(BaseModel):
    nombres: str = Field(min_length=1, max_length=100)
    apellidos: str = Field(min_length=1, max_length=100)
    fecha_nacimiento: date | None = None
    id_universidad: int
    id_posicion: int
    consentimiento_informado: bool = False
    fecha_consentimiento: date | None = None
    activo: bool = True

    @field_validator("nombres", "apellidos")
    @classmethod
    def _limpiar(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("No puede estar vacío")
        return v

    @field_validator("fecha_nacimiento")
    @classmethod
    def _edad_plausible(cls, v: date | None) -> date | None:
        if v is None:
            return v
        hoy = date.today()
        edad = hoy.year - v.year - ((hoy.month, hoy.day) < (v.month, v.day))
        if not 14 <= edad <= 60:
            raise ValueError("La edad resultante debe estar entre 14 y 60 años")
        return v

    @model_validator(mode="after")
    def _consentimiento(self):
        if self.consentimiento_informado and self.fecha_consentimiento is None:
            raise ValueError("Indique la fecha del consentimiento informado")
        if self.fecha_consentimiento and self.fecha_consentimiento > date.today():
            raise ValueError("La fecha de consentimiento no puede ser futura")
        return self


class JugadorIn(_JugadorBase):
    pass


class JugadorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_jugador: int
    codigo_seudonimo: str
    nombres: str
    apellidos: str
    fecha_nacimiento: date | None
    edad: int | None
    id_universidad: int
    universidad: str
    id_posicion: int
    posicion: str
    consentimiento_informado: bool
    fecha_consentimiento: date | None
    activo: bool


class JugadorLista(BaseModel):
    total: int
    items: list[JugadorOut]


class UniversidadIn(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    siglas: str | None = Field(default=None, max_length=15)


class UniversidadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_universidad: int
    nombre: str
    siglas: str | None


class PosicionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_posicion: int
    nombre: str
    descripcion: str | None
