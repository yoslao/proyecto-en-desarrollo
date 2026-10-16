"""Gestión de resultados ya registrados: atípicos, correcciones y momento de la jornada."""

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import indicadores
from app.models import JornadaEvaluacion, Jugador, ResultadoPrueba, TipoPrueba
from app.schemas.registro import JornadaOut, ResultadoPendienteOut
from app.services.registro_tests import MARCA_MIGRACION, origen_jornada


def listar_jornadas(db: Session) -> list[JornadaOut]:
    conteo = {
        id_j: (jug, res)
        for id_j, jug, res in db.execute(
            select(
                ResultadoPrueba.id_jornada,
                func.count(func.distinct(ResultadoPrueba.id_jugador)),
                func.count(),
            ).group_by(ResultadoPrueba.id_jornada)
        )
    }
    salida = []
    for j in db.scalars(
        select(JornadaEvaluacion).order_by(
            JornadaEvaluacion.fecha.desc(), JornadaEvaluacion.id_jornada.desc()
        )
    ):
        origen = origen_jornada(j)
        jug, res = conteo.get(j.id_jornada, (0, 0))
        salida.append(
            JornadaOut(
                id_jornada=j.id_jornada,
                fecha=j.fecha,
                momento=j.momento,
                hora_inicio=j.hora_inicio,
                origen=origen,
                # En jornadas migradas el campo guarda una marca técnica, no texto del usuario.
                observaciones=None if origen == "migracion" else j.observaciones,
                jugadores=jug,
                resultados=res,
            )
        )
    return salida


def cambiar_momento(
    db: Session, id_jornada: int, momento: str, confirmar_advertencia: bool
) -> dict:
    j = db.get(JornadaEvaluacion, id_jornada)
    if j is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jornada no encontrada")
    if j.momento == momento:
        return {
            "id_jornada": j.id_jornada,
            "momento": j.momento,
            "cambio": False,
            "recalculados": 0,
        }
    if (
        momento == "pre"
        and (j.observaciones or "").startswith(MARCA_MIGRACION)
        and not confirmar_advertencia
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "codigo": "advertencia_migracion",
                "mensaje": (
                    "Esta jornada proviene de la migración del Excel (datos de seguimiento). "
                    "Convertirla en 'pre' la usará como línea base de todos sus jugadores. "
                    "Reenvíe con confirmar_advertencia=true si es lo que desea."
                ),
            },
        )
    j.momento = momento
    db.flush()
    jugadores = list(
        db.scalars(
            select(ResultadoPrueba.id_jugador)
            .where(ResultadoPrueba.id_jornada == id_jornada)
            .distinct()
        )
    )
    for id_jugador in jugadores:  # cambia la línea base: se recalculan los indicadores
        indicadores.recalcular(db, id_jugador)
    return {
        "id_jornada": j.id_jornada,
        "momento": j.momento,
        "cambio": True,
        "recalculados": len(jugadores),
    }


def pendientes(db: Session, limite: int, desplazamiento: int) -> list[ResultadoPendienteOut]:
    filas = db.execute(
        select(
            ResultadoPrueba, JornadaEvaluacion.fecha, Jugador.codigo_seudonimo, TipoPrueba.codigo
        )
        .join(JornadaEvaluacion, JornadaEvaluacion.id_jornada == ResultadoPrueba.id_jornada)
        .join(Jugador, Jugador.id_jugador == ResultadoPrueba.id_jugador)
        .join(TipoPrueba, TipoPrueba.id_tipo_prueba == ResultadoPrueba.id_tipo_prueba)
        .where(ResultadoPrueba.estado == "pendiente_confirmacion")
        .order_by(JornadaEvaluacion.fecha, Jugador.codigo_seudonimo, TipoPrueba.codigo)
        .limit(limite)
        .offset(desplazamiento)
    )
    return [
        ResultadoPendienteOut(
            id_resultado=r.id_resultado,
            id_jornada=r.id_jornada,
            fecha=fecha,
            id_jugador=r.id_jugador,
            codigo_seudonimo=codigo_jug,
            prueba=prueba,
            valor=float(r.valor),
            intento=r.intento,
        )
        for r, fecha, codigo_jug, prueba in filas
    ]


def confirmar(db: Session, ids: list[int]) -> tuple[int, list[int]]:
    """Confirma atípicos: pasan a 'confirmado' y vuelven a contar en los indicadores."""
    filas = db.scalars(select(ResultadoPrueba).where(ResultadoPrueba.id_resultado.in_(ids))).all()
    encontrados = {f.id_resultado for f in filas}
    ignorados = [i for i in ids if i not in encontrados]
    afectados = set()
    n = 0
    for f in filas:
        if f.estado == "pendiente_confirmacion":
            f.estado = "confirmado"
            afectados.add(f.id_jugador)
            n += 1
        else:
            ignorados.append(f.id_resultado)
    db.flush()
    for id_jugador in afectados:
        indicadores.recalcular(db, id_jugador)
    return n, ignorados


def corregir(db: Session, id_resultado: int, valor: Decimal) -> ResultadoPrueba:
    r = db.get(ResultadoPrueba, id_resultado)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resultado no encontrado")
    t = db.get(TipoPrueba, r.id_tipo_prueba)
    if not t.valor_minimo <= valor <= t.valor_maximo:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{valor} fuera de rango [{t.valor_minimo}, {t.valor_maximo}] {t.unidad}",
        )
    r.valor = valor.quantize(Decimal("0.01"))
    r.es_atipico, r.estado = False, "confirmado"  # decisión explícita del entrenador
    db.flush()
    indicadores.recalcular(db, r.id_jugador)
    return r
