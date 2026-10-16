"""Registro de cada llamada al LLM en `llamada_ia` (nunca guarda el prompt: solo su hash)."""

from sqlalchemy.orm import Session

from app.ai.openrouter_client import MetaLlamada
from app.models import LlamadaIA


def guardar_llamada(
    db: Session,
    meta: MetaLlamada,
    *,
    tarea: str,
    version_prompt: str,
    hash_entrada: str,
    es_caso_control: bool = False,
) -> LlamadaIA:
    fila = LlamadaIA(
        tarea=tarea,
        modelo=meta.modelo[:100],
        es_respaldo=meta.es_respaldo,
        version_prompt=version_prompt,
        latencia_ms=meta.latencia_ms,
        tokens_entrada=meta.tokens_entrada,
        tokens_salida=meta.tokens_salida,
        costo_usd=meta.costo_usd,
        valido_primer_intento=meta.valido_primer_intento,
        num_reintentos=meta.num_reintentos,
        estado=meta.estado,
        es_caso_control=es_caso_control,
        hash_entrada=hash_entrada,
    )
    db.add(fila)
    db.flush()
    return fila


def meta_sin_respuesta(modelo: str, estado: str = "error_proveedor") -> MetaLlamada:
    """Para fallos que ocurren antes de llamar (sin modelos configurados)."""
    from decimal import Decimal

    return MetaLlamada(
        modelo=modelo or "sin_configurar",
        es_respaldo=False,
        latencia_ms=0,
        tokens_entrada=0,
        tokens_salida=0,
        costo_usd=Decimal("0"),
        valido_primer_intento=False,
        num_reintentos=0,
        estado=estado,
    )
