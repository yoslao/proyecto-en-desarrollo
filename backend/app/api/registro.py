"""Endpoints de registro (RF-02, RF-08) y carga (RF-09). Solo el entrenador registra datos."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import indicadores
from app.core.db import get_db
from app.core.deps import requiere_roles
from app.models import IndicadorCargaSemanal, Jugador, TipoPrueba, Usuario
from app.schemas.registro import (
    BienestarEdicionIn,
    BienestarIn,
    BienestarOut,
    CambioMomentoIn,
    CargaOut,
    ConfirmacionIn,
    ConfirmacionOut,
    CorreccionIn,
    JornadaLoteIn,
    JornadaOut,
    JornadaRegistradaOut,
    RegistroSesionEdicionIn,
    RegistroSesionIn,
    RegistroSesionOut,
    ResultadoPendienteOut,
    SemanaCargaOut,
    SesionJugadorIn,
    SesionLoteIn,
    SesionRegistradaOut,
    TestsJugadorIn,
)
from app.services import bitacora, registro_sesiones, registro_tests, resultados, riesgo_hooks

router = APIRouter(tags=["registro"])

_entrenador = requiere_roles("entrenador")
_ambos = requiere_roles("administrador", "entrenador")


# ------------------------------------------------------------------ tests
@router.post("/jornadas", response_model=JornadaRegistradaOut, status_code=201)
def registrar_jornada(
    datos: JornadaLoteIn,
    request: Request,
    background: BackgroundTasks,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    salida, ids = registro_tests.registrar_jornada(
        db,
        usuario,
        datos.fecha,
        datos.momento,
        datos.hora_inicio,
        datos.observaciones,
        datos.resultados,
    )
    bitacora.registrar(
        db,
        "crear",
        "resultado_prueba",
        id_usuario=usuario.id_usuario,
        id_registro=salida.id_jornada,
        request=request,
    )
    db.commit()
    riesgo_hooks.programar_evaluaciones(background, ids)
    return salida


@router.post("/jugadores/{id_jugador}/tests", response_model=JornadaRegistradaOut, status_code=201)
def registrar_tests_jugador(
    id_jugador: int,
    datos: TestsJugadorIn,
    request: Request,
    background: BackgroundTasks,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    registro_tests.jugador_registrable(db, id_jugador)
    lote = registro_tests.desde_tests_jugador(id_jugador, datos)
    salida, ids = registro_tests.registrar_jornada(
        db, usuario, lote.fecha, lote.momento, lote.hora_inicio, lote.observaciones, lote.resultados
    )
    bitacora.registrar(
        db,
        "crear",
        "resultado_prueba",
        id_usuario=usuario.id_usuario,
        id_registro=id_jugador,
        request=request,
    )
    db.commit()
    riesgo_hooks.programar_evaluaciones(background, ids)  # evaluación de riesgo en segundo plano
    return salida


@router.get("/jornadas", response_model=list[JornadaOut])
def listar_jornadas(_: Usuario = Depends(_ambos), db: Session = Depends(get_db)):
    return resultados.listar_jornadas(db)


@router.patch("/jornadas/{id_jornada}/momento")
def cambiar_momento(
    id_jornada: int,
    datos: CambioMomentoIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    salida = resultados.cambiar_momento(db, id_jornada, datos.momento, datos.confirmar_advertencia)
    if salida["cambio"]:
        bitacora.registrar(
            db,
            "modificar",
            "jornada_evaluacion",
            id_usuario=usuario.id_usuario,
            id_registro=id_jornada,
            request=request,
        )
    db.commit()
    return salida


@router.get("/resultados/pendientes", response_model=list[ResultadoPendienteOut])
def resultados_pendientes(
    limite: int = Query(200, ge=1, le=1000),
    desplazamiento: int = Query(0, ge=0),
    _: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    return resultados.pendientes(db, limite, desplazamiento)


@router.patch("/resultados/confirmacion", response_model=ConfirmacionOut)
def confirmar_atipicos(
    datos: ConfirmacionIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    n, ignorados = resultados.confirmar(db, datos.ids)
    bitacora.registrar(
        db, "modificar", "resultado_prueba", id_usuario=usuario.id_usuario, request=request
    )
    db.commit()
    return ConfirmacionOut(confirmados=n, ignorados=ignorados)


@router.put("/resultados/{id_resultado}")
def corregir_resultado(
    id_resultado: int,
    datos: CorreccionIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    r = resultados.corregir(db, id_resultado, datos.valor)
    bitacora.registrar(
        db,
        "modificar",
        "resultado_prueba",
        id_usuario=usuario.id_usuario,
        id_registro=id_resultado,
        request=request,
    )
    db.commit()
    return {"id_resultado": r.id_resultado, "valor": float(r.valor), "estado": r.estado}


# ------------------------------------------------------------------ sesiones
@router.post("/sesiones", response_model=SesionRegistradaOut, status_code=201)
def registrar_sesion_lote(
    datos: SesionLoteIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    salida = registro_sesiones.registrar_sesion(
        db,
        usuario,
        datos.fecha,
        datos.tipo,
        datos.duracion_planificada_min,
        datos.descripcion,
        datos.registros,
    )
    bitacora.registrar(
        db,
        "crear",
        "sesion",
        id_usuario=usuario.id_usuario,
        id_registro=salida.id_sesion,
        request=request,
    )
    db.commit()
    return salida


@router.post(
    "/jugadores/{id_jugador}/sesiones", response_model=SesionRegistradaOut, status_code=201
)
def registrar_sesion_jugador(
    id_jugador: int,
    datos: SesionJugadorIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    registro_tests.jugador_registrable(db, id_jugador)
    salida = registro_sesiones.registrar_sesion(
        db,
        usuario,
        datos.fecha,
        datos.tipo,
        datos.duracion_planificada_min,
        datos.descripcion,
        [
            RegistroSesionIn(
                id_jugador=id_jugador,
                duracion_efectiva_min=datos.duracion_efectiva_min,
                rpe=datos.rpe,
            )
        ],
    )
    bitacora.registrar(
        db,
        "crear",
        "sesion",
        id_usuario=usuario.id_usuario,
        id_registro=salida.id_sesion,
        request=request,
    )
    db.commit()
    return salida


@router.patch("/registros-sesion/{id_registro}", response_model=RegistroSesionOut)
def editar_registro_sesion(
    id_registro: int,
    datos: RegistroSesionEdicionIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    salida = registro_sesiones.editar_registro(db, id_registro, datos)
    bitacora.registrar(
        db,
        "modificar",
        "registro_sesion_jugador",
        id_usuario=usuario.id_usuario,
        id_registro=id_registro,
        request=request,
    )
    db.commit()
    return salida


# ------------------------------------------------------------------ bienestar
@router.post("/jugadores/{id_jugador}/bienestar", response_model=BienestarOut, status_code=201)
def registrar_bienestar(
    id_jugador: int,
    datos: BienestarIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    salida = registro_sesiones.registrar_bienestar(db, id_jugador, datos)
    bitacora.registrar(
        db,
        "crear",
        "cuestionario_bienestar",
        id_usuario=usuario.id_usuario,
        id_registro=salida["id_cuestionario"],
        request=request,
    )
    db.commit()
    return salida


@router.put("/bienestar/{id_cuestionario}", response_model=BienestarOut)
def editar_bienestar(
    id_cuestionario: int,
    datos: BienestarEdicionIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    salida = registro_sesiones.editar_bienestar(db, id_cuestionario, datos)
    bitacora.registrar(
        db,
        "modificar",
        "cuestionario_bienestar",
        id_usuario=usuario.id_usuario,
        id_registro=id_cuestionario,
        request=request,
    )
    db.commit()
    return salida


# ------------------------------------------------------------------ carga e indicadores (RF-09)
@router.get("/jugadores/{id_jugador}/carga", response_model=CargaOut)
def carga_jugador(
    id_jugador: int,
    request: Request,
    semanas: int = Query(12, ge=1, le=104),
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    jugador = db.get(Jugador, id_jugador)
    if jugador is None:
        raise HTTPException(404, "Jugador no encontrado")
    filas = db.scalars(
        select(IndicadorCargaSemanal)
        .where(IndicadorCargaSemanal.id_jugador == id_jugador)
        .order_by(IndicadorCargaSemanal.semana_inicio.desc())
        .limit(semanas)
    ).all()
    bienestar = {b["semana_inicio"]: b for b in indicadores.serie_bienestar(db, id_jugador)}
    semanas_out = [
        SemanaCargaOut(
            semana_inicio=f.semana_inicio,
            carga_semanal=f.carga_semanal,
            num_sesiones=f.num_sesiones,
            acwr=None if f.acwr is None else float(f.acwr),
            caida_cmj_pct=None if f.caida_cmj_pct is None else float(f.caida_cmj_pct),
            indice_bienestar=bienestar.get(f.semana_inicio, {}).get("indice"),
            var_bienestar=bienestar.get(f.semana_inicio, {}).get("var_semana_anterior"),
        )
        for f in reversed(filas)
    ]
    bitacora.registrar(
        db,
        "consultar",
        "indicador_carga_semanal",
        id_usuario=usuario.id_usuario,
        id_registro=id_jugador,
        request=request,
    )
    db.commit()
    return CargaOut(
        id_jugador=id_jugador, codigo_seudonimo=jugador.codigo_seudonimo, semanas=semanas_out
    )


@router.get("/jugadores/{id_jugador}/indicadores")
def indicadores_jugador(
    id_jugador: int,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    if db.get(Jugador, id_jugador) is None:
        raise HTTPException(404, "Jugador no encontrado")
    resumen = indicadores.resumen_indicadores(db, id_jugador)
    bitacora.registrar(
        db,
        "consultar",
        "indicador_carga_semanal",
        id_usuario=usuario.id_usuario,
        id_registro=id_jugador,
        request=request,
    )
    db.commit()
    return resumen


@router.get("/catalogos/pruebas")
def pruebas(_: Usuario = Depends(_ambos), db: Session = Depends(get_db)):
    return [
        {
            "id_tipo_prueba": t.id_tipo_prueba,
            "codigo": t.codigo,
            "nombre": t.nombre,
            "unidad": t.unidad,
            "valor_minimo": float(t.valor_minimo),
            "valor_maximo": float(t.valor_maximo),
            "mayor_es_mejor": t.mayor_es_mejor,
            "intentos_max": t.intentos_max,
        }
        for t in db.scalars(select(TipoPrueba).order_by(TipoPrueba.id_tipo_prueba))
    ]


@router.get("/catalogos/umbrales")
def umbrales(_: Usuario = Depends(_ambos), db: Session = Depends(get_db)):
    return indicadores.umbrales(db)
