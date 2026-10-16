"""Evaluación de riesgo de sobreentrenamiento/lesión con IA (RF-04) y su revisión por el entrenador.

Toda evaluación —incluidas las de riesgo bajo— nace 'pendiente' y el entrenador la confirma o la
descarta. Eso permite calcular precisión, sensibilidad y F1 de las alertas (Fase 6).
"""

import asyncio
import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import prompts, proveedor, seudonimizador
from app.ai.esquemas import RiesgoLLM
from app.ai.openrouter_client import ErrorIA
from app.analytics import indicadores
from app.core.db import nueva_sesion
from app.core.security import utcnow
from app.models import EvaluacionRiesgo, Jugador, LlamadaIA, Usuario
from app.schemas.ia import EvaluacionRiesgoOut, IndicadorAfectadoOut
from app.services import bitacora
from app.services.ia_llamadas import guardar_llamada, meta_sin_respuesta
from app.services.registro_tests import jugador_registrable

log = logging.getLogger(__name__)
PLANTILLA = "riesgo_v1"


def _preparar(id_jugador: int) -> dict:
    with nueva_sesion() as db:
        jugador_registrable(db, id_jugador)
        contexto = seudonimizador.contexto_riesgo(db, id_jugador)
        texto, hash_ = seudonimizador.preparar(db, contexto)
        return {"texto": texto, "hash": hash_, "modelos": proveedor.modelos_configurados(db)}


def _guardar(
    id_jugador: int, origen: str, resultado, hash_: str, version: str, es_caso_control: bool
) -> int:
    with nueva_sesion() as db:
        llamada = guardar_llamada(
            db,
            resultado.meta,
            tarea="riesgo",
            version_prompt=version,
            hash_entrada=hash_,
            es_caso_control=es_caso_control,
        )
        d: RiesgoLLM = resultado.datos
        ev = EvaluacionRiesgo(
            id_jugador=id_jugador,
            id_llamada=llamada.id_llamada,
            origen=origen,
            nivel_riesgo=d.nivel_riesgo,
            indicadores_afectados=[i.model_dump() for i in d.indicadores_afectados],
            justificacion=d.justificacion,
            recomendacion_inmediata=d.recomendacion_inmediata,
            estado_revision="pendiente",
        )
        db.add(ev)
        db.flush()
        bitacora.registrar(
            db, "crear", "evaluacion_riesgo", id_usuario=None, id_registro=ev.id_evaluacion_riesgo
        )
        db.commit()
        return ev.id_evaluacion_riesgo


def _guardar_fallo(meta, hash_: str, version: str, es_caso_control: bool) -> None:
    with nueva_sesion() as db:
        guardar_llamada(
            db,
            meta,
            tarea="riesgo",
            version_prompt=version,
            hash_entrada=hash_,
            es_caso_control=es_caso_control,
        )
        db.commit()


async def evaluar(
    id_jugador: int, origen: str = "nuevo_test", es_caso_control: bool = False
) -> int | None:
    """Evalúa el riesgo del jugador. Devuelve el id de la evaluación o None si no se pudo."""
    cliente = proveedor.get_cliente()
    if not cliente.configurado:
        log.info("Evaluación de riesgo omitida: OpenRouter no está configurado")
        return None
    plantilla = prompts.cargar(PLANTILLA)
    hash_ = ""
    try:
        prep = await asyncio.to_thread(_preparar, id_jugador)
        hash_ = prep["hash"]
        resultado = await cliente.completar(
            mensajes=plantilla.mensajes(prep["texto"]),
            schema=RiesgoLLM,
            modelos=prep["modelos"],
            max_tokens=proveedor.max_tokens("riesgo"),
        )
        return await asyncio.to_thread(
            _guardar, id_jugador, origen, resultado, hash_, plantilla.version, es_caso_control
        )
    except ErrorIA as e:
        meta = e.meta or meta_sin_respuesta("")
        await asyncio.to_thread(
            _guardar_fallo, meta, hash_ or "0" * 64, plantilla.version, es_caso_control
        )
        log.warning("Evaluación de riesgo fallida (%s)", type(e).__name__)
    except HTTPException as e:
        log.info("Evaluación de riesgo omitida: %s", e.detail)
    except seudonimizador.ErrorSeudonimizacion:
        log.error("Contexto de riesgo bloqueado por la verificación de privacidad")
    except Exception:  # noqa: BLE001
        log.exception("Error inesperado evaluando el riesgo")
    return None


async def evaluar_en_segundo_plano(id_jugador: int, origen: str) -> None:
    """Callback del gancho `riesgo_hooks` (se dispara al registrar tests)."""
    await evaluar(id_jugador, origen)


def jugadores_con_datos(db: Session) -> tuple[list[int], int]:
    """Activos con consentimiento que tienen algo que evaluar, y cuántos se omiten."""
    ids = list(
        db.scalars(
            select(Jugador.id_jugador).where(
                Jugador.activo.is_(True), Jugador.consentimiento_informado.is_(True)
            )
        )
    )
    con_datos = []
    for i in ids:
        r = indicadores.resumen_indicadores(db, i)
        if r["mediciones"] or r["carga"] is not None or r["bienestar"] is not None:
            con_datos.append(i)
    return con_datos, len(ids) - len(con_datos)


# ------------------------------------------------------------------ consulta y revisión
def a_salida(
    db: Session, ev: EvaluacionRiesgo, codigos: dict[int, str] | None = None
) -> EvaluacionRiesgoOut:
    llamada = db.get(LlamadaIA, ev.id_llamada)
    codigo = (codigos or {}).get(ev.id_jugador) or db.get(Jugador, ev.id_jugador).codigo_seudonimo
    return EvaluacionRiesgoOut(
        id_evaluacion_riesgo=ev.id_evaluacion_riesgo,
        id_jugador=ev.id_jugador,
        codigo_seudonimo=codigo,
        origen=ev.origen,
        fecha_hora=ev.fecha_hora,
        nivel_riesgo=ev.nivel_riesgo,
        es_alerta=ev.nivel_riesgo in ("medio", "alto"),
        indicadores_afectados=[IndicadorAfectadoOut(**i) for i in (ev.indicadores_afectados or [])],
        justificacion=ev.justificacion,
        recomendacion_inmediata=ev.recomendacion_inmediata,
        estado_revision=ev.estado_revision,
        fecha_revision=ev.fecha_revision,
        modelo_ia=llamada.modelo,
    )


def listar(
    db: Session, *, solo_alertas: bool, estado: str | None, limite: int, desplazamiento: int
) -> list[EvaluacionRiesgoOut]:
    q = select(EvaluacionRiesgo)
    if solo_alertas:
        q = q.where(EvaluacionRiesgo.nivel_riesgo.in_(["medio", "alto"]))
    if estado:
        q = q.where(EvaluacionRiesgo.estado_revision == estado)
    q = q.order_by(EvaluacionRiesgo.fecha_hora.desc(), EvaluacionRiesgo.id_evaluacion_riesgo.desc())
    filas = db.scalars(q.limit(limite).offset(desplazamiento)).all()
    codigos = {j.id_jugador: j.codigo_seudonimo for j in db.scalars(select(Jugador))}
    return [a_salida(db, ev, codigos) for ev in filas]


def revisar(db: Session, usuario: Usuario, id_evaluacion: int, decision: str) -> EvaluacionRiesgo:
    ev = db.get(EvaluacionRiesgo, id_evaluacion)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluación no encontrada")
    if ev.estado_revision != "pendiente":
        raise HTTPException(status.HTTP_409_CONFLICT, f"La evaluación ya fue {ev.estado_revision}")
    ev.estado_revision = "confirmada" if decision == "confirmar" else "descartada"
    ev.id_usuario_revisa = usuario.id_usuario
    ev.fecha_revision = utcnow()
    db.flush()
    return ev
