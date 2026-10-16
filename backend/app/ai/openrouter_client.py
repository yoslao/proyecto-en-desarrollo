"""Cliente asíncrono de OpenRouter (solo inferencia).

Flujo por llamada lógica:
  modelo principal → respuesta inválida (JSON roto o fuera de esquema): 1 reintento
                   → sigue inválida / error 5xx / timeout / red: modelo de respaldo (mismo trato)
  - Ninguna respuesta superó la validación        → `RespuestaInvalida`     (el API responde 502)
  - Ningún modelo respondió (5xx, timeout, red)   → `ProveedorNoDisponible` (el API responde 503)

Privacidad: este módulo NUNCA registra ni guarda los mensajes (que contienen datos del jugador);
solo devuelve metadatos (modelo, latencia, tokens, costo, reintentos).
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ------------------------------------------------------------------ resultados y errores
@dataclass
class MetaLlamada:
    modelo: str
    es_respaldo: bool
    latencia_ms: int
    tokens_entrada: int
    tokens_salida: int
    costo_usd: Decimal
    valido_primer_intento: bool
    num_reintentos: int
    estado: str  # 'exito' | 'invalida' | 'error_proveedor'


@dataclass
class ResultadoIA(Generic[T]):
    datos: T
    meta: MetaLlamada


class ErrorIA(Exception):
    http_status = 500

    def __init__(self, mensaje: str, meta: MetaLlamada | None = None) -> None:
        super().__init__(mensaje)
        self.meta = meta


class RespuestaInvalida(ErrorIA):
    """Hubo respuestas, pero ninguna superó la validación de esquema/seguridad."""

    http_status = 502


class ProveedorNoDisponible(ErrorIA):
    """Ningún modelo respondió (timeout, 5xx, red) o no hay modelos/clave configurados."""

    http_status = 503


class _ErrorHTTP(Exception):
    def __init__(self, tipo: str, codigo: int | None = None) -> None:
        super().__init__(tipo)
        self.tipo, self.codigo = tipo, codigo


@dataclass
class _Respuesta:
    texto: str
    tokens_entrada: int
    tokens_salida: int
    costo: Decimal


# ------------------------------------------------------------------ utilidades
_RE_CERCA = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extraer_json(texto: str) -> dict:
    """JSON de la respuesta, aunque venga con ```fences``` o texto alrededor (modelos sin
    `response_format`). Lanza ValueError si no hay un objeto JSON."""
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("Respuesta vacía")
    limpio = _RE_CERCA.sub("", texto.strip())
    for candidato in (limpio, limpio[limpio.find("{") : limpio.rfind("}") + 1]):
        try:
            valor = json.loads(candidato)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(valor, dict):
            return valor
    raise ValueError("La respuesta no contiene un objeto JSON válido")


def _texto_de(cuerpo: dict) -> str:
    contenido = cuerpo["choices"][0]["message"]["content"]
    if isinstance(contenido, list):  # algunos proveedores devuelven partes
        contenido = "".join(p.get("text", "") for p in contenido if isinstance(p, dict))
    return contenido or ""


def _costo(uso: dict) -> Decimal:
    try:
        return Decimal(str(uso.get("cost", 0) or 0)).quantize(Decimal("0.00001"))
    except Exception:
        return Decimal("0")


# ------------------------------------------------------------------ cliente
class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        app_url: str = "http://localhost:5173",
        titulo: str = "VoleyIA Coach",
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.app_url = app_url
        self.titulo = titulo
        self.timeout_s = timeout_s
        self._transport = transport
        self._sin_response_format: set[str] = set()

    @property
    def configurado(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.app_url,
            "X-Title": self.titulo,
        }

    async def _post(
        self, http: httpx.AsyncClient, modelo: str, mensajes: list[dict], max_tokens: int,
        temperature: float, con_response_format: bool,
    ) -> httpx.Response:  # fmt: skip
        cuerpo = {
            "model": modelo,
            "messages": mensajes,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},  # pide tokens y costo en la respuesta
        }
        if con_response_format:
            cuerpo["response_format"] = {"type": "json_object"}
        try:
            async with asyncio.timeout(self.timeout_s):
                return await http.post(self.url, json=cuerpo, headers=self._headers())
        except (TimeoutError, httpx.TimeoutException) as e:
            raise _ErrorHTTP("timeout") from e
        except httpx.HTTPError as e:
            raise _ErrorHTTP("red") from e

    async def _una_llamada(
        self, http: httpx.AsyncClient, modelo: str, mensajes: list[dict], max_tokens: int,
        temperature: float,
    ) -> _Respuesta:  # fmt: skip
        usar_rf = modelo not in self._sin_response_format
        resp = await self._post(http, modelo, mensajes, max_tokens, temperature, usar_rf)
        if usar_rf and resp.status_code == 400 and _rechaza_response_format(resp):
            # El modelo no admite response_format: se recuerda y se pide sin él (el JSON se
            # extrae del texto y se valida igual).
            self._sin_response_format.add(modelo)
            resp = await self._post(http, modelo, mensajes, max_tokens, temperature, False)
        if resp.status_code >= 500 or resp.status_code in (408, 429):
            raise _ErrorHTTP("5xx" if resp.status_code >= 500 else "limite", resp.status_code)
        if resp.status_code >= 400:
            raise _ErrorHTTP("4xx", resp.status_code)
        try:
            cuerpo = resp.json()
            uso = cuerpo.get("usage") or {}
            texto = _texto_de(cuerpo)
        except (ValueError, KeyError, IndexError, TypeError):
            texto, uso = "", {}
        return _Respuesta(
            texto=texto,
            tokens_entrada=int(uso.get("prompt_tokens", 0) or 0),
            tokens_salida=int(uso.get("completion_tokens", 0) or 0),
            costo=_costo(uso),
        )

    async def completar(
        self,
        *,
        mensajes: list[dict],
        schema: type[T],
        modelos: list[str],
        max_tokens: int,
        temperature: float = 0.2,
        validador_extra: Callable[[T], None] | None = None,
    ) -> ResultadoIA[T]:
        """Pide una respuesta JSON validada. `modelos` = [principal, respaldo]."""
        candidatos = [m for i, m in enumerate(modelos) if m and m not in modelos[:i]]
        if not self.configurado or not candidatos:
            raise ProveedorNoDisponible("OpenRouter no está configurado (clave o modelos)")

        inicio = time.monotonic()
        intentos = tok_in = tok_out = 0
        costo = Decimal("0")
        respondio = False
        ultimo_modelo, ultimo_es_respaldo = candidatos[0], False
        primer_intento_valido = False

        def meta(estado: str) -> MetaLlamada:
            return MetaLlamada(
                modelo=ultimo_modelo,
                es_respaldo=ultimo_es_respaldo,
                latencia_ms=int((time.monotonic() - inicio) * 1000),
                tokens_entrada=tok_in,
                tokens_salida=tok_out,
                costo_usd=costo,
                valido_primer_intento=primer_intento_valido,
                num_reintentos=max(intentos - 1, 0),
                estado=estado,
            )

        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as http:
            for idx, modelo in enumerate(candidatos):
                ultimo_modelo, ultimo_es_respaldo = modelo, idx > 0
                for _ in range(2):  # intento + 1 reintento por respuesta inválida
                    intentos += 1
                    try:
                        r = await self._una_llamada(http, modelo, mensajes, max_tokens, temperature)
                    except _ErrorHTTP as e:
                        log.warning("OpenRouter %s: %s (%s)", modelo, e.tipo, e.codigo)
                        break  # 5xx / timeout / red / 4xx → siguiente modelo
                    respondio = True
                    tok_in += r.tokens_entrada
                    tok_out += r.tokens_salida
                    costo += r.costo
                    try:
                        datos = schema.model_validate(extraer_json(r.texto))
                        if validador_extra is not None:
                            validador_extra(datos)
                    except (ValueError, ValidationError) as e:
                        log.warning("Respuesta de %s inválida: %s", modelo, type(e).__name__)
                        continue  # reintenta una vez
                    primer_intento_valido = intentos == 1
                    return ResultadoIA(datos=datos, meta=meta("exito"))

        if respondio:
            raise RespuestaInvalida(
                "Ninguna respuesta del proveedor superó la validación", meta("invalida")
            )
        raise ProveedorNoDisponible("Ningún modelo respondió", meta("error_proveedor"))


def _rechaza_response_format(resp: httpx.Response) -> bool:
    texto = resp.text.lower()
    return any(
        p in texto for p in ("response_format", "json_object", "structured output", "json mode")
    )
