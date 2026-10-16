"""Cliente de OpenRouter: reintento, respaldo, extracción de JSON y errores 502/503."""

import logging
from decimal import Decimal

import httpx
import pytest

from app.ai.esquemas import RiesgoLLM, RutinaLLM, validar_seguridad_rutina
from app.ai.openrouter_client import (
    ProveedorNoDisponible,
    RespuestaInvalida,
    extraer_json,
)
from tests.ia_mock import RIESGO_ALTO, RIESGO_BAJO, RUTINA_OK, ServidorIA, respuesta, rutina_con

MENSAJES = [{"role": "system", "content": "control de carga"}, {"role": "user", "content": "{}"}]


async def _pedir(srv, modelos=("principal/m", "respaldo/m"), schema=RiesgoLLM, **kw):
    return await srv.cliente(**kw).completar(
        mensajes=MENSAJES, schema=schema, modelos=list(modelos), max_tokens=500
    )


# ------------------------------------------------------------------ petición
async def test_la_peticion_sigue_el_formato_de_openrouter():
    srv = ServidorIA(lambda p, n: respuesta(RIESGO_BAJO))
    r = await _pedir(srv, app_url="http://localhost:5173")
    p, h = srv.peticiones[0], srv.cabeceras[0]
    assert p["model"] == "principal/m" and p["temperature"] == 0.2 and p["max_tokens"] == 500
    assert p["response_format"] == {"type": "json_object"}
    assert p["messages"] == MENSAJES
    assert h["authorization"] == "Bearer clave-de-prueba"
    assert h["http-referer"] == "http://localhost:5173" and h["x-title"] == "VoleyIA Coach"
    assert r.datos.nivel_riesgo == "bajo" and r.meta.estado == "exito"
    assert r.meta.valido_primer_intento and r.meta.num_reintentos == 0 and not r.meta.es_respaldo


async def test_registra_tokens_y_costo_de_usage():
    srv = ServidorIA(lambda p, n: respuesta(RIESGO_BAJO, 1234, 321, 0.00123))
    m = (await _pedir(srv)).meta
    assert (m.tokens_entrada, m.tokens_salida, m.costo_usd) == (1234, 321, Decimal("0.00123"))
    assert m.latencia_ms >= 0


async def test_sin_clave_o_sin_modelos_es_503():
    srv = ServidorIA()
    with pytest.raises(ProveedorNoDisponible):
        await _pedir(srv, api_key="")
    with pytest.raises(ProveedorNoDisponible):
        await _pedir(srv, modelos=("", ""))
    assert srv.peticiones == []


# ------------------------------------------------------------------ reintento y respaldo
async def test_json_invalido_reintenta_una_vez_con_el_mismo_modelo():
    respuestas = iter([respuesta("esto no es json"), respuesta(RIESGO_ALTO)])
    srv = ServidorIA(lambda p, n: next(respuestas))
    r = await _pedir(srv)
    assert srv.modelos_pedidos() == ["principal/m", "principal/m"]
    assert r.meta.num_reintentos == 1 and r.meta.valido_primer_intento is False
    assert r.meta.es_respaldo is False and r.datos.nivel_riesgo == "alto"


async def test_fuera_de_esquema_tambien_reintenta():
    malo = '{"nivel_riesgo":"catastrofico","justificacion":"x","recomendacion_inmediata":"y"}'
    respuestas = iter([respuesta(malo), respuesta(RIESGO_BAJO)])
    srv = ServidorIA(lambda p, n: next(respuestas))
    assert (await _pedir(srv)).meta.num_reintentos == 1


async def test_dos_respuestas_invalidas_pasan_al_modelo_de_respaldo():
    def reglas(p, n):
        return respuesta("basura") if p["model"] == "principal/m" else respuesta(RIESGO_BAJO)

    srv = ServidorIA(reglas)
    r = await _pedir(srv)
    assert srv.modelos_pedidos() == ["principal/m", "principal/m", "respaldo/m"]
    assert r.meta.es_respaldo is True and r.meta.modelo == "respaldo/m"
    assert r.meta.num_reintentos == 2 and r.meta.valido_primer_intento is False


@pytest.mark.parametrize(
    "error",
    [
        httpx.Response(500),
        httpx.Response(503),
        httpx.Response(429),
        httpx.ReadTimeout("lento"),
        httpx.ConnectError("sin red"),
    ],
)
async def test_error_5xx_timeout_o_red_va_directo_al_respaldo_sin_reintentar(error):
    def reglas(p, n):
        return error if p["model"] == "principal/m" else respuesta(RIESGO_BAJO)

    srv = ServidorIA(reglas)
    r = await _pedir(srv)
    assert srv.modelos_pedidos() == ["principal/m", "respaldo/m"]  # sin reintento en el principal
    assert r.meta.es_respaldo is True


async def test_ningun_modelo_respondio_es_503_con_metadatos():
    srv = ServidorIA(lambda p, n: httpx.ReadTimeout("lento"))
    with pytest.raises(ProveedorNoDisponible) as e:
        await _pedir(srv)
    assert e.value.http_status == 503 and e.value.meta.estado == "error_proveedor"
    assert srv.modelos_pedidos() == ["principal/m", "respaldo/m"]


async def test_ninguna_respuesta_valida_es_502():
    srv = ServidorIA(lambda p, n: respuesta("{}"))
    with pytest.raises(RespuestaInvalida) as e:
        await _pedir(srv)
    assert e.value.http_status == 502 and e.value.meta.estado == "invalida"
    assert len(srv.peticiones) == 4  # 2 modelos × (intento + 1 reintento)
    assert e.value.meta.num_reintentos == 3


async def test_invalida_en_uno_y_caido_el_otro_es_502():
    def reglas(p, n):
        return respuesta("basura") if p["model"] == "principal/m" else httpx.Response(500)

    with pytest.raises(RespuestaInvalida):
        await _pedir(ServidorIA(reglas))


async def test_modelo_repetido_no_se_consulta_dos_veces_como_respaldo():
    srv = ServidorIA(lambda p, n: httpx.Response(500))
    with pytest.raises(ProveedorNoDisponible):
        await _pedir(srv, modelos=("mismo/m", "mismo/m"))
    assert srv.modelos_pedidos() == ["mismo/m"]


async def test_costo_y_tokens_se_acumulan_entre_reintentos():
    respuestas = iter([respuesta("basura", 100, 10, 0.001), respuesta(RIESGO_BAJO, 100, 20, 0.002)])
    srv = ServidorIA(lambda p, n: next(respuestas))
    m = (await _pedir(srv)).meta
    assert (m.tokens_entrada, m.tokens_salida, m.costo_usd) == (200, 30, Decimal("0.00300"))


# ------------------------------------------------------------------ sin response_format
async def test_modelo_sin_response_format_se_reintenta_sin_el_y_se_extrae_el_json():
    def reglas(p, n):
        if "response_format" in p:
            return httpx.Response(
                400, json={"error": {"message": "response_format is not supported"}}
            )
        return respuesta(f"Claro, aquí está:\n```json\n{RIESGO_ALTO}\n```\nEspero que sirva.")

    srv = ServidorIA(reglas)
    cliente = srv.cliente()
    r = await cliente.completar(
        mensajes=MENSAJES, schema=RiesgoLLM, modelos=["viejo/m"], max_tokens=300
    )
    assert r.datos.nivel_riesgo == "alto" and r.meta.num_reintentos == 0
    assert ["response_format" in p for p in srv.peticiones] == [True, False]
    # el cliente lo recuerda: la siguiente llamada ya no lo envía
    await cliente.completar(
        mensajes=MENSAJES, schema=RiesgoLLM, modelos=["viejo/m"], max_tokens=300
    )
    assert "response_format" not in srv.peticiones[-1] and len(srv.peticiones) == 3


@pytest.mark.parametrize(
    "texto",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Aquí va: {"a": 1} ¡listo!',
        '  \n {"a": 1}\n ',
    ],
)
def test_extraer_json(texto):
    assert extraer_json(texto) == {"a": 1}


@pytest.mark.parametrize("texto", ["", "   ", "sin json", "[1, 2]", '{"a": ', None])
def test_extraer_json_rechaza(texto):
    with pytest.raises(ValueError):
        extraer_json(texto)


# ------------------------------------------------------------------ validación de la rutina
async def test_validador_de_seguridad_rechaza_y_regenera():
    respuestas = iter([respuesta(rutina_con(duracion_min=150)), respuesta(RUTINA_OK)])
    srv = ServidorIA(lambda p, n: next(respuestas))

    def validar(r):
        validar_seguridad_rutina(r, 2, 6, 120)

    r = await srv.cliente().completar(
        mensajes=MENSAJES,
        schema=RutinaLLM,
        modelos=["p/m"],
        max_tokens=800,
        validador_extra=validar,
    )
    assert r.meta.num_reintentos == 1 and len(srv.peticiones) == 2


@pytest.mark.parametrize(
    "rutina,limite_sesiones,limite_min,semanas",
    [
        (rutina_con(sesiones_semana1=7), 6, 120, 2),
        (rutina_con(duracion_min=121), 6, 120, 2),
        (RUTINA_OK, 2, 120, 2),  # semana 1 tiene 3 sesiones
        (RUTINA_OK, 6, 120, 3),  # se pidieron 3 semanas y llegaron 2
    ],
)
def test_validar_seguridad_rutina_rechaza(rutina, limite_sesiones, limite_min, semanas):
    r = RutinaLLM.model_validate_json(rutina)
    with pytest.raises(ValueError):
        validar_seguridad_rutina(r, semanas, limite_sesiones, limite_min)


def test_la_rutina_valida_pasa_la_seguridad():
    validar_seguridad_rutina(RutinaLLM.model_validate_json(RUTINA_OK), 2, 6, 120)


def test_dia_con_tilde_o_mayuscula_se_normaliza():
    r = RutinaLLM.model_validate_json(RUTINA_OK.replace('"miercoles"', '"Miércoles"'))
    assert "miercoles" in [s.dia for s in r.semanas[0].sesiones]


# ------------------------------------------------------------------ privacidad
async def test_no_se_registra_en_logs_el_contenido_del_prompt(caplog):
    secreto = "CONTENIDO-SENSIBLE-12345"
    srv = ServidorIA(lambda p, n: respuesta("basura"))
    caplog.set_level(logging.DEBUG)
    mensajes = [
        {"role": "system", "content": "control de carga"},
        {"role": "user", "content": secreto},
    ]
    with pytest.raises(RespuestaInvalida):
        await srv.cliente().completar(
            mensajes=mensajes, schema=RiesgoLLM, modelos=["a/m", "b/m"], max_tokens=100
        )
    assert secreto not in caplog.text and "basura" not in caplog.text
