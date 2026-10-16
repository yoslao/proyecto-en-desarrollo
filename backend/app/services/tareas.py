"""Registro en memoria de tareas asíncronas (generación de rutinas).

La aplicación corre con un solo worker (monolito), por eso el registro vive en memoria.
El frontend consulta `GET /tareas/{id}` para mostrar el indicador de progreso.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.security import utcnow

VIGENCIA = timedelta(hours=2)
_lock = threading.Lock()


@dataclass
class Tarea:
    id: str
    id_usuario: int
    tipo: str
    estado: str = "pendiente"  # pendiente | en_curso | completada | error
    paso: str = "en_cola"
    progreso: int = 0
    resultado: dict | None = None
    error: dict | None = None  # {"http_status": 502|503|500, "mensaje": str}
    creada: datetime = field(default_factory=utcnow)


_tareas: dict[str, Tarea] = {}


def _purgar() -> None:
    limite = utcnow() - VIGENCIA
    for k in [k for k, t in _tareas.items() if t.creada < limite]:
        del _tareas[k]


def crear(id_usuario: int, tipo: str) -> Tarea:
    with _lock:
        _purgar()
        t = Tarea(id=uuid.uuid4().hex, id_usuario=id_usuario, tipo=tipo)
        _tareas[t.id] = t
        return t


def obtener(id_tarea: str, id_usuario: int) -> Tarea | None:
    """Solo el usuario que la creó puede consultarla."""
    t = _tareas.get(id_tarea)
    return t if t is not None and t.id_usuario == id_usuario else None


def avanzar(t: Tarea, paso: str, progreso: int) -> None:
    t.estado, t.paso, t.progreso = "en_curso", paso, progreso


def completar(t: Tarea, resultado: dict) -> None:
    t.estado, t.paso, t.progreso, t.resultado = "completada", "listo", 100, resultado


def fallar(t: Tarea, http_status: int, mensaje: str) -> None:
    t.estado, t.paso = "error", "error"
    t.error = {"http_status": http_status, "mensaje": mensaje}


def limpiar() -> None:
    with _lock:
        _tareas.clear()
