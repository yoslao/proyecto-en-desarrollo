"""Punto de enlace con la evaluación de riesgo (RF-04, Fase 5).

POST /jugadores/{id}/tests dispara la evaluación en segundo plano. La Fase 5 registrará aquí su
callback (`registrar_callback`) sin tocar los endpoints. Mientras no haya ninguno, no ocurre nada.
"""

import logging
from collections.abc import Callable

from fastapi import BackgroundTasks

log = logging.getLogger(__name__)

# fn(id_jugador, origen) con origen 'nuevo_test' | 'cierre_semana'
Callback = Callable[[int, str], None]
_callbacks: list[Callback] = []


def registrar_callback(fn: Callback) -> None:
    if fn not in _callbacks:
        _callbacks.append(fn)


def limpiar_callbacks() -> None:
    _callbacks.clear()


def programar_evaluaciones(
    background: BackgroundTasks, ids_jugador: list[int], origen: str = "nuevo_test"
) -> None:
    for id_jugador in dict.fromkeys(ids_jugador):
        if not _callbacks:
            log.debug("Evaluación de riesgo no configurada (jugador %s)", id_jugador)
        for fn in _callbacks:
            background.add_task(fn, id_jugador, origen)
