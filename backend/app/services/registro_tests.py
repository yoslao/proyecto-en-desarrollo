"""Registro de tests por jugador y fecha (RF-02)."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import indicadores
from app.analytics.atipicos import es_atipico, limites_iqr
from app.models import JornadaEvaluacion, Jugador, ResultadoPrueba, TipoPrueba, Usuario
from app.schemas.registro import (
    JornadaLoteIn,
    JornadaRegistradaOut,
    ResultadoAtipicoOut,
    ResultadoJugadorIn,
    TestsJugadorIn,
)

MARCA_MIGRACION = "migracion:"


def origen_jornada(j: JornadaEvaluacion) -> str:
    return "migracion" if (j.observaciones or "").startswith(MARCA_MIGRACION) else "sistema"


def jugador_registrable(db: Session, id_jugador: int) -> Jugador:
    """Jugador existente, activo y CON consentimiento informado (Ley 29733)."""
    j = db.get(Jugador, id_jugador)
    if j is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jugador no encontrado")
    if not j.activo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El jugador está inactivo")
    if not j.consentimiento_informado:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{j.codigo_seudonimo} no tiene consentimiento informado: no se pueden registrar "
            "mediciones (Ley N.º 29733)",
        )
    return j


def _error(id_jugador: int | None, prueba: str | None, mensaje: str) -> dict:
    return {"id_jugador": id_jugador, "prueba": prueba, "mensaje": mensaje}


def _validar_entradas(
    db: Session, entradas: list[ResultadoJugadorIn], tipos: dict[str, TipoPrueba]
) -> list[dict]:
    errores: list[dict] = []
    vistos: set[int] = set()
    jugadores = {
        j.id_jugador: j
        for j in db.scalars(
            select(Jugador).where(Jugador.id_jugador.in_([e.id_jugador for e in entradas]))
        )
    }
    for e in entradas:
        if e.id_jugador in vistos:
            errores.append(_error(e.id_jugador, None, "Jugador repetido en el lote"))
            continue
        vistos.add(e.id_jugador)
        j = jugadores.get(e.id_jugador)
        if j is None:
            errores.append(_error(e.id_jugador, None, "Jugador inexistente"))
            continue
        if not j.activo:
            errores.append(_error(e.id_jugador, None, f"{j.codigo_seudonimo} está inactivo"))
            continue
        if not j.consentimiento_informado:
            errores.append(
                _error(
                    e.id_jugador, None, f"{j.codigo_seudonimo} no tiene consentimiento informado"
                )
            )
            continue
        for codigo, valores in e.pruebas.items():
            t = tipos.get(codigo)
            if t is None:
                errores.append(_error(e.id_jugador, codigo, "Prueba desconocida"))
                continue
            if not 1 <= len(valores) <= t.intentos_max:
                errores.append(
                    _error(e.id_jugador, codigo, f"Se esperan entre 1 y {t.intentos_max} valor(es)")
                )
                continue
            for v in valores:
                if not t.valor_minimo <= v <= t.valor_maximo:
                    errores.append(
                        _error(
                            e.id_jugador,
                            codigo,
                            f"{v} fuera de rango [{t.valor_minimo}, {t.valor_maximo}] {t.unidad}",
                        )
                    )
    return errores


def marcar_atipicos(
    db: Session, id_jornada: int, id_tipo_prueba: int, mayor_es_mejor: bool
) -> None:
    """Re-evalúa los atípicos (IQR) de una prueba en una jornada. Nunca toca los 'confirmado'."""
    filas = db.scalars(
        select(ResultadoPrueba).where(
            ResultadoPrueba.id_jornada == id_jornada,
            ResultadoPrueba.id_tipo_prueba == id_tipo_prueba,
        )
    ).all()
    por_jugador: dict[int, list[ResultadoPrueba]] = defaultdict(list)
    for f in filas:
        por_jugador[f.id_jugador].append(f)
        if f.estado != "confirmado":
            f.es_atipico, f.estado = False, "valido"
    # Con varios intentos se evalúa el mejor de cada jugador (el que cuenta para los indicadores).
    representantes = {
        jug: (max if mayor_es_mejor else min)(lista, key=lambda r: r.valor)
        for jug, lista in por_jugador.items()
    }
    lim = limites_iqr([float(r.valor) for r in representantes.values()])
    for r in representantes.values():
        if r.estado != "confirmado" and es_atipico(float(r.valor), lim):
            r.es_atipico, r.estado = True, "pendiente_confirmacion"
    db.flush()


def registrar_jornada(
    db: Session,
    usuario: Usuario,
    fecha: date,
    momento: str,
    hora_inicio,
    observaciones: str | None,
    entradas: list[ResultadoJugadorIn],
) -> tuple[JornadaRegistradaOut, list[int]]:
    tipos = {t.codigo: t for t in db.scalars(select(TipoPrueba))}
    errores = _validar_entradas(db, entradas, tipos)

    jornada = db.scalar(
        select(JornadaEvaluacion).where(
            JornadaEvaluacion.fecha == fecha, JornadaEvaluacion.momento == momento
        )
    )
    if jornada is not None and not errores:
        existentes = {
            (r.id_jugador, r.id_tipo_prueba, r.intento)
            for r in db.scalars(
                select(ResultadoPrueba).where(ResultadoPrueba.id_jornada == jornada.id_jornada)
            )
        }
        for e in entradas:
            for codigo in e.pruebas:
                if (e.id_jugador, tipos[codigo].id_tipo_prueba, 1) in existentes:
                    errores.append(
                        _error(e.id_jugador, codigo, "Ya existe un resultado en esta jornada")
                    )
    if errores:
        codigo_http = (
            status.HTTP_409_CONFLICT
            if all("Ya existe" in e["mensaje"] for e in errores)
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(codigo_http, {"mensaje": "Hay valores inválidos", "errores": errores})

    nueva = jornada is None
    if nueva:
        jornada = JornadaEvaluacion(
            fecha=fecha,
            momento=momento,
            hora_inicio=hora_inicio,
            observaciones=observaciones,
            id_usuario=usuario.id_usuario,
        )
        db.add(jornada)
        db.flush()

    creados: list[ResultadoPrueba] = []
    for e in entradas:
        for codigo, valores in e.pruebas.items():
            for intento, v in enumerate(valores, start=1):
                r = ResultadoPrueba(
                    id_jornada=jornada.id_jornada,
                    id_jugador=e.id_jugador,
                    id_tipo_prueba=tipos[codigo].id_tipo_prueba,
                    intento=intento,
                    valor=Decimal(v).quantize(Decimal("0.01")),
                )
                db.add(r)
                creados.append(r)
    db.flush()

    for codigo in {c for e in entradas for c in e.pruebas}:
        marcar_atipicos(
            db, jornada.id_jornada, tipos[codigo].id_tipo_prueba, tipos[codigo].mayor_es_mejor
        )

    ids_jugador = [e.id_jugador for e in entradas]
    for id_jugador in ids_jugador:
        indicadores.recalcular(db, id_jugador)

    codigo_por_id = {t.id_tipo_prueba: c for c, t in tipos.items()}
    atipicos = [
        ResultadoAtipicoOut(
            id_resultado=r.id_resultado,
            id_jugador=r.id_jugador,
            prueba=codigo_por_id[r.id_tipo_prueba],
            valor=float(r.valor),
        )
        for r in creados
        if r.es_atipico
    ]
    salida = JornadaRegistradaOut(
        id_jornada=jornada.id_jornada,
        fecha=jornada.fecha,
        momento=jornada.momento,
        jornada_nueva=nueva,
        resultados_registrados=len(creados),
        atipicos=atipicos,
    )
    return salida, ids_jugador


def desde_tests_jugador(id_jugador: int, datos: TestsJugadorIn) -> JornadaLoteIn:
    return JornadaLoteIn(
        fecha=datos.fecha,
        momento=datos.momento,
        hora_inicio=datos.hora_inicio,
        observaciones=datos.observaciones,
        resultados=[ResultadoJugadorIn(id_jugador=id_jugador, pruebas=datos.pruebas)],
    )
