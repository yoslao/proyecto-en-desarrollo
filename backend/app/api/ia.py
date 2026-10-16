"""Rutinas y alertas de riesgo con IA. La IA recomienda; el entrenador decide."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.ai import proveedor
from app.core.db import get_db
from app.core.deps import requiere_roles
from app.models import Jugador, Usuario
from app.schemas.ia import (
    AprobacionIn,
    CierreSemanaOut,
    EvaluacionRiesgoOut,
    GenerarRutinaIn,
    RevisionRiesgoIn,
    RutinaOut,
    TareaCreadaOut,
    TareaOut,
)
from app.services import bitacora, riesgo, rutinas, tareas
from app.services.registro_tests import jugador_registrable

router = APIRouter(tags=["ia"])

_entrenador = requiere_roles("entrenador")
_ambos = requiere_roles("administrador", "entrenador")


# ------------------------------------------------------------------ rutinas (RF-03)
@router.post("/jugadores/{id_jugador}/rutinas", response_model=TareaCreadaOut, status_code=202)
def generar_rutina(
    id_jugador: int,
    datos: GenerarRutinaIn,
    background: BackgroundTasks,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    """Inicia la generación (asíncrona, hasta ~15 s). Consulte el avance en `GET /tareas/{id}`."""
    jugador_registrable(db, id_jugador)
    if not proveedor.get_cliente().configurado or not proveedor.modelos_configurados(db):
        raise HTTPException(503, "El proveedor de IA no está configurado")
    tarea = tareas.crear(usuario.id_usuario, "rutina")
    background.add_task(
        rutinas.ejecutar_generacion, tarea, id_jugador, datos.semanas, datos.objetivo_prioritario
    )
    return TareaCreadaOut(id_tarea=tarea.id, consulta=f"/tareas/{tarea.id}")


@router.get("/tareas/{id_tarea}", response_model=TareaOut)
def consultar_tarea(id_tarea: str, usuario: Usuario = Depends(_entrenador)):
    t = tareas.obtener(id_tarea, usuario.id_usuario)
    if t is None:
        raise HTTPException(404, "Tarea no encontrada")
    if t.estado == "error":
        # 502: ninguna respuesta de la IA superó la validación · 503: ningún modelo respondió
        return JSONResponse(
            status_code=t.error["http_status"],
            content={"detail": t.error["mensaje"], "id_tarea": t.id, "estado": "error"},
        )
    return TareaOut(
        id_tarea=t.id,
        tipo=t.tipo,
        estado=t.estado,
        paso=t.paso,
        progreso=t.progreso,
        resultado=t.resultado,
    )


@router.get("/rutinas/{id_rutina}", response_model=RutinaOut)
def detalle_rutina(
    id_rutina: int,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    r = rutinas.obtener(db, id_rutina)
    bitacora.registrar(
        db,
        "consultar",
        "rutina",
        id_usuario=usuario.id_usuario,
        id_registro=id_rutina,
        request=request,
    )
    db.commit()
    return rutinas.a_salida(db, r)


@router.get("/jugadores/{id_jugador}/rutinas", response_model=list[RutinaOut])
def rutinas_del_jugador(
    id_jugador: int,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    if db.get(Jugador, id_jugador) is None:
        raise HTTPException(404, "Jugador no encontrado")
    bitacora.registrar(
        db,
        "consultar",
        "rutina",
        id_usuario=usuario.id_usuario,
        id_registro=id_jugador,
        request=request,
    )
    db.commit()
    return [rutinas.a_salida(db, r) for r in rutinas.listar_de_jugador(db, id_jugador)]


@router.patch("/rutinas/{id_rutina}/aprobacion", response_model=RutinaOut)
def aprobar_o_rechazar_rutina(
    id_rutina: int,
    datos: AprobacionIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    r = rutinas.revisar(db, usuario, id_rutina, datos)
    bitacora.registrar(
        db,
        "modificar",
        "rutina",
        id_usuario=usuario.id_usuario,
        id_registro=id_rutina,
        request=request,
    )
    db.commit()
    return rutinas.a_salida(db, r)


# ------------------------------------------------------------------ riesgo (RF-04)
@router.get("/alertas", response_model=list[EvaluacionRiesgoOut])
def alertas(
    estado: str | None = Query("pendiente", pattern="^(pendiente|confirmada|descartada|todas)$"),
    limite: int = Query(100, ge=1, le=500),
    desplazamiento: int = Query(0, ge=0),
    _: Usuario = Depends(_ambos),
    db: Session = Depends(get_db),
):
    """Alertas = evaluaciones de riesgo medio o alto. Solo códigos J-xx (sin nombres)."""
    return riesgo.listar(
        db,
        solo_alertas=True,
        estado=None if estado == "todas" else estado,
        limite=limite,
        desplazamiento=desplazamiento,
    )


@router.get("/evaluaciones-riesgo/pendientes", response_model=list[EvaluacionRiesgoOut])
def evaluaciones_pendientes(
    limite: int = Query(100, ge=1, le=500),
    desplazamiento: int = Query(0, ge=0),
    _: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    """Bandeja de revisión: TODAS las evaluaciones pendientes, incluidas las de riesgo bajo."""
    return riesgo.listar(
        db, solo_alertas=False, estado="pendiente", limite=limite, desplazamiento=desplazamiento
    )


@router.patch("/evaluaciones-riesgo/{id_evaluacion}/revision", response_model=EvaluacionRiesgoOut)
def revisar_evaluacion(
    id_evaluacion: int,
    datos: RevisionRiesgoIn,
    request: Request,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    ev = riesgo.revisar(db, usuario, id_evaluacion, datos.decision)
    bitacora.registrar(
        db,
        "modificar",
        "evaluacion_riesgo",
        id_usuario=usuario.id_usuario,
        id_registro=id_evaluacion,
        request=request,
    )
    db.commit()
    return riesgo.a_salida(db, ev)


@router.post("/riesgo/cierre-semana", response_model=CierreSemanaOut, status_code=202)
def cierre_de_semana(
    background: BackgroundTasks,
    usuario: Usuario = Depends(_entrenador),
    db: Session = Depends(get_db),
):
    """Evalúa en segundo plano el riesgo de todos los jugadores con datos (origen cierre_semana)."""
    if not proveedor.get_cliente().configurado or not proveedor.modelos_configurados(db):
        raise HTTPException(503, "El proveedor de IA no está configurado")
    ids, omitidos = riesgo.jugadores_con_datos(db)
    for id_jugador in ids:
        background.add_task(riesgo.evaluar, id_jugador, "cierre_semana")
    return CierreSemanaOut(jugadores_programados=len(ids), omitidos_sin_datos=omitidos)
