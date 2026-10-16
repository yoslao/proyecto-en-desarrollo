"""Estructuras de datos del ETL (extracción → plan → carga → reporte)."""

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

PRUEBAS_SALTO = ("SALTO_SC", "SALTO_BC", "SALTO_AI", "SALTO_PF")


@dataclass
class Problema:
    categoria: str  # jugador | posicion | consentimiento | valor | fecha | talla | formato | ...
    detalle: str
    hoja: str | None = None


@dataclass
class Valor:
    """Una celda numérica ya limpia, con su trazabilidad hacia el Excel."""

    codigo: str  # SPRINT_20M, NAVETTE, SALTO_*, o DURACION
    valor: Decimal
    celda: str  # p. ej. 'E5'
    texto: str  # contenido original de la celda


@dataclass
class FilaJugador:
    hoja: str
    fila: int  # número de fila en Excel
    nombre_original: str
    clave: str
    edad: int | None
    talla: Decimal | None
    peso: Decimal | None
    posicion_texto: str | None
    valores: list[Valor] = field(default_factory=list)
    velocidad_excel: Decimal | None = None
    vo2_excel: Decimal | None = None
    celda_velocidad: str | None = None
    celda_vo2: str | None = None
    celda_talla: str | None = None
    celda_peso: str | None = None


@dataclass
class Hoja:
    nombre: str
    tipo: str  # 'salto' | 'velocidad'
    titulo: str | None
    fecha: date | None
    hora_inicio: time | None
    observaciones: str
    filas: list[FilaJugador] = field(default_factory=list)


@dataclass
class JugadorPlan:
    clave: str
    nombre_completo: str
    nombres: str
    apellidos: str
    edad: int | None
    posicion: str | None  # nombre del catálogo, None si está pendiente
    motivo_omision: str | None = None
    consentimiento: bool = False
    fecha_consentimiento: date | None = None
    codigo: str | None = None  # se asigna en la carga
    id_jugador: int | None = None

    @property
    def cargable(self) -> bool:
        return self.motivo_omision is None


@dataclass
class Exclusion:
    hoja: str
    celda: str
    clave: str
    codigo: str
    motivo: str


@dataclass
class MedicionPlan:
    hoja: str
    clave: str
    codigo: str
    valor: Decimal
    celda: str
    texto: str
    atipico: bool = False


@dataclass
class AntropoPlan:
    hoja: str
    clave: str
    fecha: date
    talla: Decimal
    peso: Decimal


@dataclass
class DuracionPlan:
    hoja: str
    clave: str
    minutos: int
    celda: str
    texto: str


@dataclass
class Plan:
    hojas: list[Hoja]
    jugadores: dict[str, JugadorPlan]
    mediciones: list[MedicionPlan]
    antropometrias: list[AntropoPlan]
    duraciones: list[DuracionPlan]
    exclusiones: list[Exclusion]
    problemas: list[Problema]
    hojas_omitidas: list[str]
    filas_derivadas: list[FilaJugador]  # filas de velocidad con datos para el chequeo VO2/velocidad
    derivadas_aceptadas: set[tuple[str, str]] = field(
        default_factory=set
    )  # (hoja, celda) aceptadas
