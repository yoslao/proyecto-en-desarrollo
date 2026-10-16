from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Jugador, Posicion, Universidad
from app.schemas.jugador import JugadorIn, JugadorOut


def calcular_edad(fecha_nacimiento: date | None, hoy: date | None = None) -> int | None:
    if fecha_nacimiento is None:
        return None
    hoy = hoy or date.today()
    return (
        hoy.year
        - fecha_nacimiento.year
        - ((hoy.month, hoy.day) < (fecha_nacimiento.month, fecha_nacimiento.day))
    )


def a_salida(j: Jugador) -> JugadorOut:
    return JugadorOut(
        id_jugador=j.id_jugador,
        codigo_seudonimo=j.codigo_seudonimo,
        nombres=j.nombres,
        apellidos=j.apellidos,
        fecha_nacimiento=j.fecha_nacimiento,
        edad=calcular_edad(j.fecha_nacimiento),
        id_universidad=j.id_universidad,
        universidad=j.universidad.nombre,
        id_posicion=j.id_posicion,
        posicion=j.posicion.nombre,
        consentimiento_informado=j.consentimiento_informado,
        fecha_consentimiento=j.fecha_consentimiento,
        activo=j.activo,
    )


def _siguiente_codigo(db: Session) -> str:
    maximo = db.scalar(select(func.max(cast(func.substr(Jugador.codigo_seudonimo, 3), Integer))))
    return f"J-{(maximo or 0) + 1:02d}"


def _validar_catalogos(db: Session, datos: JugadorIn) -> None:
    if db.get(Universidad, datos.id_universidad) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Universidad inexistente")
    if db.get(Posicion, datos.id_posicion) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Posición inexistente")


def _existe_duplicado(db: Session, datos: JugadorIn, excluir_id: int | None = None) -> bool:
    # La colación utf8mb4_unicode_ci ignora mayúsculas y tildes.
    q = select(Jugador.id_jugador).where(
        Jugador.nombres == datos.nombres, Jugador.apellidos == datos.apellidos
    )
    if excluir_id is not None:
        q = q.where(Jugador.id_jugador != excluir_id)
    return db.scalar(q) is not None


def listar(
    db: Session, q: str | None, activo: bool | None, limite: int, desplazamiento: int
) -> tuple[int, list[Jugador]]:
    filtro = []
    if q:
        patron = f"%{q.strip()}%"
        filtro.append(
            or_(
                Jugador.nombres.like(patron),
                Jugador.apellidos.like(patron),
                Jugador.codigo_seudonimo.like(patron),
            )
        )
    if activo is not None:
        filtro.append(Jugador.activo == activo)
    total = db.scalar(select(func.count()).select_from(Jugador).where(*filtro)) or 0
    items = db.scalars(
        select(Jugador)
        .where(*filtro)
        .order_by(Jugador.apellidos, Jugador.nombres)
        .limit(limite)
        .offset(desplazamiento)
    ).all()
    return total, list(items)


def obtener(db: Session, id_jugador: int) -> Jugador:
    j = db.get(Jugador, id_jugador)
    if j is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jugador no encontrado")
    return j


def crear(db: Session, datos: JugadorIn) -> Jugador:
    _validar_catalogos(db, datos)
    if _existe_duplicado(db, datos):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un jugador con ese nombre")
    for _ in range(3):  # reintento ante carrera por el código seudónimo
        jugador = Jugador(codigo_seudonimo=_siguiente_codigo(db), **datos.model_dump())
        db.add(jugador)
        try:
            db.flush()
            return jugador
        except IntegrityError:
            db.rollback()
    raise HTTPException(status.HTTP_409_CONFLICT, "No se pudo asignar un código seudónimo")


def actualizar(db: Session, id_jugador: int, datos: JugadorIn) -> Jugador:
    jugador = obtener(db, id_jugador)
    _validar_catalogos(db, datos)
    if _existe_duplicado(db, datos, excluir_id=id_jugador):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un jugador con ese nombre")
    for campo, valor in datos.model_dump().items():
        setattr(jugador, campo, valor)
    db.flush()
    db.refresh(jugador)
    return jugador
