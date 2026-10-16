"""Registro de sesiones (sRPE) y bienestar semanal (RF-08)."""

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import indicadores
from app.models import CuestionarioBienestar, RegistroSesionJugador, Sesion, Usuario
from app.schemas.registro import (
    BienestarEdicionIn,
    BienestarIn,
    RegistroSesionEdicionIn,
    RegistroSesionIn,
    RegistroSesionOut,
    SesionRegistradaOut,
)
from app.services.registro_tests import jugador_registrable


def registrar_sesion(
    db: Session,
    usuario: Usuario,
    fecha: date,
    tipo: str,
    duracion_planificada_min: int | None,
    descripcion: str | None,
    registros: list[RegistroSesionIn],
) -> SesionRegistradaOut:
    errores: list[dict] = []
    vistos: set[int] = set()
    for r in registros:
        if r.id_jugador in vistos:
            errores.append({"id_jugador": r.id_jugador, "mensaje": "Jugador repetido en el lote"})
        vistos.add(r.id_jugador)
        try:
            jugador_registrable(db, r.id_jugador)
        except HTTPException as e:
            errores.append({"id_jugador": r.id_jugador, "mensaje": str(e.detail)})

    sesion = db.scalar(
        select(Sesion).where(
            Sesion.fecha == fecha,
            Sesion.tipo == tipo,
            Sesion.origen == "sistema",
            (
                Sesion.descripcion == descripcion
                if descripcion is not None
                else Sesion.descripcion.is_(None)
            ),
        )
    )
    if sesion is not None and not errores:
        ya = set(
            db.scalars(
                select(RegistroSesionJugador.id_jugador).where(
                    RegistroSesionJugador.id_sesion == sesion.id_sesion
                )
            )
        )
        for r in registros:
            if r.id_jugador in ya:
                errores.append(
                    {"id_jugador": r.id_jugador, "mensaje": "Ya tiene registro en esta sesión"}
                )
    if errores:
        todos_dup = all("Ya tiene registro" in e["mensaje"] for e in errores)
        raise HTTPException(
            status.HTTP_409_CONFLICT if todos_dup else status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"mensaje": "No se pudo registrar la sesión", "errores": errores},
        )

    nueva = sesion is None
    if nueva:
        sesion = Sesion(
            fecha=fecha,
            tipo=tipo,
            duracion_planificada_min=duracion_planificada_min,
            descripcion=descripcion,
            origen="sistema",
            id_usuario=usuario.id_usuario,
        )
        db.add(sesion)
        db.flush()

    creados = []
    for r in registros:
        fila = RegistroSesionJugador(
            id_sesion=sesion.id_sesion,
            id_jugador=r.id_jugador,
            duracion_efectiva_min=r.duracion_efectiva_min,
            rpe=r.rpe,
        )
        db.add(fila)
        creados.append(fila)
    db.flush()
    for r in registros:
        indicadores.recalcular(db, r.id_jugador)
    db.refresh(sesion)
    for fila in creados:
        db.refresh(fila)  # recarga la columna generada srpe
    return SesionRegistradaOut(
        id_sesion=sesion.id_sesion,
        fecha=sesion.fecha,
        tipo=sesion.tipo,
        sesion_nueva=nueva,
        registros=[RegistroSesionOut.model_validate(f) for f in creados],
    )


def editar_registro(
    db: Session, id_registro: int, cambios: RegistroSesionEdicionIn
) -> RegistroSesionOut:
    fila = db.get(RegistroSesionJugador, id_registro)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registro no encontrado")
    sesion = db.get(Sesion, fila.id_sesion)
    enviados = cambios.model_fields_set
    if "rpe" in enviados and cambios.rpe is None and sesion.origen != "migracion":
        # El RPE solo puede ser NULL en registros migrados.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "El RPE es obligatorio salvo en registros migrados",
        )
    if "duracion_efectiva_min" in enviados and cambios.duracion_efectiva_min is not None:
        fila.duracion_efectiva_min = cambios.duracion_efectiva_min
    if "rpe" in enviados:
        fila.rpe = cambios.rpe
    db.flush()
    indicadores.recalcular(db, fila.id_jugador)
    db.refresh(fila)
    return RegistroSesionOut.model_validate(fila)


# ------------------------------------------------------------------ bienestar
def _a_salida(c: CuestionarioBienestar) -> dict:
    return {
        "id_cuestionario": c.id_cuestionario,
        "id_jugador": c.id_jugador,
        "semana_inicio": c.semana_inicio,
        "sueno": c.sueno,
        "fatiga": c.fatiga,
        "dolor_muscular": c.dolor_muscular,
        "animo": c.animo,
        "indice_bienestar": c.sueno + c.fatiga + c.dolor_muscular + c.animo,
    }


def registrar_bienestar(db: Session, id_jugador: int, datos: BienestarIn) -> dict:
    jugador_registrable(db, id_jugador)
    previo = db.scalar(
        select(CuestionarioBienestar).where(
            CuestionarioBienestar.id_jugador == id_jugador,
            CuestionarioBienestar.semana_inicio == datos.semana_inicio,
        )
    )
    if previo is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Ya existe el cuestionario de la semana del {datos.semana_inicio:%d/%m/%Y}",
        )
    c = CuestionarioBienestar(id_jugador=id_jugador, **datos.model_dump())
    db.add(c)
    db.flush()
    return _a_salida(c)


def editar_bienestar(db: Session, id_cuestionario: int, datos: BienestarEdicionIn) -> dict:
    c = db.get(CuestionarioBienestar, id_cuestionario)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cuestionario no encontrado")
    for campo, valor in datos.model_dump().items():
        setattr(c, campo, valor)
    db.flush()
    return _a_salida(c)
