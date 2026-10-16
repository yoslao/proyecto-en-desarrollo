"""OpenRouter simulado para las pruebas: nada sale a la red."""

import json
from collections.abc import Callable

import httpx

from app.ai import prompts, proveedor
from app.ai.openrouter_client import OpenRouterClient

RUTINA_OK = prompts.cargar("rutina_v1").ejemplos[0][1]  # 2 semanas, cumple los límites
RIESGO_BAJO = prompts.cargar("riesgo_v1").ejemplos[0][1]
RIESGO_ALTO = prompts.cargar("riesgo_v1").ejemplos[1][1]


def respuesta(texto: str, tokens_in: int = 1500, tokens_out: int = 400, costo: float = 0.0006):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": texto}}],
            "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "cost": costo},
        },
    )


def rutina_con(**cambios) -> str:
    """RUTINA_OK modificada: p. ej. sesiones_extra=5 o duracion_min=150."""
    r = json.loads(RUTINA_OK)
    if "duracion_min" in cambios:
        r["semanas"][0]["sesiones"][0]["duracion_min"] = cambios["duracion_min"]
    if "sesiones_semana1" in cambios:
        base = r["semanas"][0]["sesiones"][0]
        dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        r["semanas"][0]["sesiones"] = [
            {**base, "dia": d} for d in dias[: cambios["sesiones_semana1"]]
        ]
    return json.dumps(r, ensure_ascii=False)


class ServidorIA:
    """`reglas`: función (peticion_json, n_llamada) -> httpx.Response | Exception.

    Por defecto responde RUTINA_OK a las rutinas y RIESGO_BAJO a las evaluaciones de riesgo.
    Guarda cada petición recibida en `peticiones` (para verificar qué datos salen hacia el proveedor).
    """

    def __init__(self, reglas: Callable | None = None) -> None:
        self.peticiones: list[dict] = []
        self.cuerpos_crudos: list[str] = []
        self.cabeceras: list[dict] = []
        self.reglas = reglas or self.por_defecto

    @staticmethod
    def es_riesgo(peticion: dict) -> bool:
        return "control de carga" in peticion["messages"][0]["content"]

    def por_defecto(self, peticion: dict, n: int):
        return respuesta(RIESGO_BAJO if self.es_riesgo(peticion) else RUTINA_OK)

    def _manejar(self, request: httpx.Request) -> httpx.Response:
        self.cuerpos_crudos.append(request.content.decode("utf-8"))
        peticion = json.loads(request.content)
        self.peticiones.append(peticion)
        self.cabeceras.append(dict(request.headers))
        r = self.reglas(peticion, len(self.peticiones))
        if isinstance(r, Exception):
            raise r
        return r

    @property
    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._manejar)

    def cliente(self, api_key: str = "clave-de-prueba", **kw) -> OpenRouterClient:
        return OpenRouterClient(api_key, transport=self.transporte, **kw)

    def instalar(self) -> "ServidorIA":
        proveedor.fijar_cliente(self.cliente())
        return self

    def modelos_pedidos(self) -> list[str]:
        return [p["model"] for p in self.peticiones]
