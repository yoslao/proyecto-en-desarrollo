"""Generación asíncrona de rutinas con IA (RF-03) y su aprobación por el entrenador.

Principio: «la IA recomienda, el entrenador decide». Toda rutina nace en estado 'propuesta'.
"""

import asyncio
import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import prompts, proveedor, seudonimizador
from app.ai.esquemas import RutinaLLM, validar_seguridad_rutina
from app.ai.openrouter_client import ErrorIA, MetaLlamada
from app.analytics import indicadores
from app.core.db import nueva_sesion
from app.core.security import utcnow
from app.models import Jugador, LlamadaIA, Rutina, Usuario
from app.schemas.ia import AprobacionIn, RutinaOut
from app.services import bitacora, tareas
from app.services.ia_llamadas import guardar_llamada, meta_sin_respuesta
from app.services.registro_tests import jugador_registrable

log = logging.getLogger(__name__)
PLANTILLA = "rutina_v1"


# ------------------------------------------------------------------ generación
def _preparar(id_jugador: int, semanas: int, objetivo: str) -> dict:
    """Contexto seudonimizado, límites de seguridad y modelos. Corre en un hilo."""
    with nueva_sesion() as db:
        jugador_registrable(db, id_jugador)  # activo y con consentimiento
        contexto = seudonimizador.contexto_rutina(db, id_jugador, semanas, objetivo)
        texto, hash_ = seudonimizador.preparar(db, contexto)
        um = indicadores.umbrales(db)
        return {
            "texto": texto,
            "hash": hash_,
            "max_sesiones": int(um.get("MAX_SESIONES_SEMANA", 6)),
            "max_duracion": int(um.get("MAX_DURACION_SESION", 120)),
            "modelos": proveedor.modelos_configurados(db),
        }


def _guardar_llamada_sola(meta: MetaLlamada, hash_: str, version: str) -> None:
    with nueva_sesion() as db:
        guardar_llamada(db, meta, tarea="rutina", version_prompt=version, hash_entrada=hash_)
        db.commit()


def _guardar_exito(
    id_usuario: int,
    id_jugador: int,
    semanas: int,
    objetivo: str,
    resultado,
    hash_: str,
    version: str,
) -> int:
    with nueva_sesion() as db:
        llamada = guardar_llamada(
            db, resultado.meta, tarea="rutina", version_prompt=version, hash_entrada=hash_
        )
        rutina = Rutina(
            id_jugador=id_jugador,
            id_llamada=llamada.id_llamada,
            id_usuario_solicita=id_usuario,
            semanas=semanas,
            objetivo_prioritario=objetivo,
            contenido=resultado.datos.model_dump(),
            estado="propuesta",
        )
        db.add(rutina)
        db.flush()
        bitacora.registrar(
            db, "crear", "rutina", id_usuario=id_usuario, id_registro=rutina.id_rutina
        )
        db.commit()
        return rutina.id_rutina


async def ejecutar_generacion(
    tarea: tareas.Tarea, id_jugador: int, semanas: int, objetivo: str
) -> None:
    """Tarea en segundo plano: actualiza `tarea` con el progreso y el resultado o el error."""
    plantilla = prompts.cargar(PLANTILLA)
    hash_ = ""
    try:
        tareas.avanzar(tarea, "preparando_contexto", 10)
        prep = await asyncio.to_thread(_preparar, id_jugador, semanas, objetivo)
        hash_ = prep["hash"]

        tareas.avanzar(tarea, "consultando_ia", 30)
        mensajes = plantilla.mensajes(prep["texto"], semanas=semanas)

        def validar(r: RutinaLLM) -> None:
            validar_seguridad_rutina(r, semanas, prep["max_sesiones"], prep["max_duracion"])

        resultado = await proveedor.get_cliente().completar(
            mensajes=mensajes,
            schema=RutinaLLM,
            modelos=prep["modelos"],
            max_tokens=proveedor.max_tokens("rutina"),
            validador_extra=validar,
        )

        tareas.avanzar(tarea, "guardando", 90)
        id_rutina = await asyncio.to_thread(
            _guardar_exito,
            tarea.id_usuario,
            id_jugador,
            semanas,
            objetivo,
            resultado,
            hash_,
            plantilla.version,
        )
        tareas.completar(tarea, {"id_rutina": id_rutina})
    except ErrorIA as e:
        meta = e.meta or meta_sin_respuesta("")
        await asyncio.to_thread(_guardar_llamada_sola, meta, hash_ or "0" * 64, plantilla.version)
        tareas.fallar(tarea, e.http_status, str(e))
    except HTTPException as e:  # jugador sin consentimiento, inactivo, etc.
        tareas.fallar(tarea, e.status_code, str(e.detail))
    except seudonimizador.ErrorSeudonimizacion:
        log.error("Contexto bloqueado por la verificación de privacidad")
        tareas.fallar(tarea, 500, "No se pudo preparar el contexto de forma segura")
    except Exception:  # noqa: BLE001  (no se registra el contenido, solo el tipo)
        log.exception("Error inesperado generando la rutina")
        tareas.fallar(tarea, 500, "Error interno al generar la rutina")


# ------------------------------------------------------------------ consulta y aprobación
def a_salida(db: Session, r: Rutina) -> RutinaOut:
    llamada = db.get(LlamadaIA, r.id_llamada)
    return RutinaOut(
        id_rutina=r.id_rutina,
        id_jugador=r.id_jugador,
        codigo_seudonimo=db.get(Jugador, r.id_jugador).codigo_seudonimo,
        fecha_generacion=r.fecha_generacion,
        semanas=r.semanas,
        objetivo_prioritario=r.objetivo_prioritario,
        contenido=r.contenido,
        estado=r.estado,
        calificacion_pertinencia=r.calificacion_pertinencia,
        calificacion_personalizacion=r.calificacion_personalizacion,
        comentario_revision=r.comentario_revision,
        fecha_revision=r.fecha_revision,
        modelo_ia=llamada.modelo,
        version_prompt=llamada.version_prompt,
        es_respaldo=llamada.es_respaldo,
    )


def obtener(db: Session, id_rutina: int) -> Rutina:
    r = db.get(Rutina, id_rutina)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rutina no encontrada")
    return r


def listar_de_jugador(db: Session, id_jugador: int) -> list[Rutina]:
    return list(
        db.scalars(
            select(Rutina)
            .where(Rutina.id_jugador == id_jugador)
            .order_by(Rutina.fecha_generacion.desc(), Rutina.id_rutina.desc())
        )
    )


def revisar(db: Session, usuario: Usuario, id_rutina: int, datos: AprobacionIn) -> Rutina:
    r = obtener(db, id_rutina)
    if r.estado != "propuesta":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"La rutina ya fue {r.estado}; no se puede revisar de nuevo"
        )
    r.estado = "aprobada" if datos.decision == "aprobar" else "rechazada"
    r.calificacion_pertinencia = datos.calificacion_pertinencia
    r.calificacion_personalizacion = datos.calificacion_personalizacion
    r.comentario_revision = datos.comentario  # texto del entrenador: nunca se envía a la IA
    r.id_usuario_revisa = usuario.id_usuario
    r.fecha_revision = utcnow()
    db.flush()
    return r
