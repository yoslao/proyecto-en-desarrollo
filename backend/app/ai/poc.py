"""Prueba de concepto: compara los modelos candidatos con casos FICTICIOS.

    python -m app.ai.poc                       # modelos de OPENROUTER_MODEL_* (o --modelos a,b)
    python -m app.ai.poc --modelos openai/x,anthropic/y --repeticiones 3
    python -m app.ai.poc --simulado            # sin red ni clave: valida el propio script

Por modelo envía 5 casos de rutina (1 vez cada uno) y 5 de riesgo (repetidos 3 veces para medir la
consistencia), y guarda en un CSV la latencia, el cumplimiento de esquema y el costo.
Cada modelo se evalúa solo (sin respaldo) para que las métricas sean suyas. Usa solo datos
ficticios con códigos J-9x: no toca a ningún jugador real.
"""

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

from app.ai import prompts
from app.ai.esquemas import RiesgoLLM, RutinaLLM, validar_seguridad_rutina
from app.ai.openrouter_client import ErrorIA, MetaLlamada, OpenRouterClient
from app.ai.seudonimizador import hash_contexto, serializar
from app.core.config import RAIZ, get_settings

UMBRALES = {
    "acwr_alto": 1.5,
    "caida_cmj_pct": 10,
    "bienestar_bajo": 8,
    "max_sesiones_semana": 6,
    "max_duracion_sesion": 120,
}
ND = "no disponible"


def _med(actual, base, ant):
    return {"actual": actual, "var_linea_base_pct": base, "var_anterior_pct": ant}


# 5 jugadores ficticios con situaciones distintas (J-9x no existen en la BD)
CASOS: dict[str, dict] = {
    "carga_normal": {
        "jugador": "J-95",
        "posicion": "central",
        "edad": 22,
        "perfil_cluster": {"cluster": 0, "etiqueta": "Rápido / resistencia alta"},
        "mediciones": {
            "sprint_20m_s": _med(3.08, 1.9, 0.6),
            "navette_paliers": _med(10.5, 5.0, 0.0),
            "cmj_cm": _med(44, 2.3, 2.3),
        },
        "carga": {
            "sRPE_semana": 2200,
            "acwr": 1.1,
            "sesiones_semana": 4,
            "caida_cmj_pct": -2.3,
            "semanas_desde_ultimo_registro": 0,
        },
        "bienestar": {"indice": 15, "var_semana_anterior": 1},
        "percentil_equipo": {"sprint": 75, "navette": 70, "cmj": 60},
    },
    "acwr_alto_y_caida_cmj": {
        "jugador": "J-96",
        "posicion": "punta receptor",
        "edad": ND,
        "perfil_cluster": {"cluster": 2, "etiqueta": "Resistencia baja / salto alto"},
        "mediciones": {
            "sprint_20m_s": _med(3.31, -2.4, -1.2),
            "navette_paliers": _med(9.5, 4.4, ND),
            "cmj_cm": _med(41, -12.8, -6.8),
        },
        "carga": {
            "sRPE_semana": 3100,
            "acwr": 1.7,
            "sesiones_semana": 6,
            "caida_cmj_pct": 12.8,
            "semanas_desde_ultimo_registro": 0,
        },
        "bienestar": {"indice": 7, "var_semana_anterior": -4},
        "percentil_equipo": {"sprint": 40, "navette": 55, "cmj": 65},
    },
    "bienestar_bajo_carga_ok": {
        "jugador": "J-97",
        "posicion": "armador",
        "edad": 24,
        "perfil_cluster": {"cluster": 1, "etiqueta": "Perfil medio"},
        "mediciones": {"sprint_20m_s": _med(3.2, 0.5, 0.0), "cmj_cm": _med(40, 0.0, 0.0)},
        "carga": {
            "sRPE_semana": 1800,
            "acwr": 1.0,
            "sesiones_semana": 3,
            "caida_cmj_pct": 0.0,
            "semanas_desde_ultimo_registro": 0,
        },
        "bienestar": {"indice": 6, "var_semana_anterior": -3},
        "percentil_equipo": {"sprint": 50, "cmj": 45},
    },
    "sin_linea_base_ni_carga": {
        "jugador": "J-98",
        "posicion": "líbero",
        "edad": 20,
        "perfil_cluster": ND,
        "mediciones": {"sprint_20m_s": _med(3.4, ND, ND), "navette_paliers": _med(8.0, ND, ND)},
        "carga": ND,
        "bienestar": ND,
        "percentil_equipo": {"sprint": 25, "navette": 30},
    },
    "mejora_sostenida": {
        "jugador": "J-99",
        "posicion": "opuesto",
        "edad": 23,
        "perfil_cluster": {"cluster": 0, "etiqueta": "Rápido / resistencia alta / salto alto"},
        "mediciones": {
            "sprint_20m_s": _med(3.0, 4.0, 1.0),
            "navette_paliers": _med(12.0, 9.0, 2.0),
            "cmj_cm": _med(48, 6.0, 2.0),
        },
        "carga": {
            "sRPE_semana": 2600,
            "acwr": 1.3,
            "sesiones_semana": 5,
            "caida_cmj_pct": -6.0,
            "semanas_desde_ultimo_registro": 0,
        },
        "bienestar": {"indice": 17, "var_semana_anterior": 2},
        "percentil_equipo": {"sprint": 90, "navette": 95, "cmj": 92},
    },
}

OBJETIVOS = ["salto", "resistencia", "velocidad", "salto", "resistencia"]
SEMANAS = 2


def contexto_rutina(i: int, nombre: str) -> dict:
    return {
        **CASOS[nombre],
        "umbrales": UMBRALES,
        "objetivo_prioritario": OBJETIVOS[i],
        "semanas": SEMANAS,
    }


def contexto_riesgo(nombre: str) -> dict:
    c = CASOS[nombre]
    salida = {}
    for k, v in c.items():
        salida[k] = v
        if k == "carga":
            salida["rpe_reciente"] = {"ultimo": 7, "promedio_ultimas_3": 7.0} if v != ND else ND
    salida["umbrales"] = UMBRALES
    return salida


# ------------------------------------------------------------------ modo simulado
def _transporte_simulado() -> httpx.MockTransport:
    rutina = prompts.cargar("rutina_v1").ejemplos[0][1]
    riesgo = prompts.cargar("riesgo_v1").ejemplos[0][1]

    def manejar(request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        es_riesgo = "control de carga" in cuerpo["messages"][0]["content"]
        texto = riesgo if es_riesgo else rutina
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": texto}}],
                "usage": {
                    "prompt_tokens": 1800,
                    "completion_tokens": len(texto) // 4,
                    "cost": 0.0004,
                },
            },
        )

    return httpx.MockTransport(manejar)


# ------------------------------------------------------------------ ejecución
def _fila(modelo, tarea, caso, rep, meta: MetaLlamada, nivel="", seguridad=""):
    return {
        "modelo": modelo,
        "tarea": tarea,
        "caso": caso,
        "repeticion": rep,
        "latencia_ms": meta.latencia_ms,
        "cumple_esquema": int(meta.estado == "exito"),
        "valido_primer_intento": int(meta.valido_primer_intento),
        "reintentos": meta.num_reintentos,
        "estado": meta.estado,
        "tokens_entrada": meta.tokens_entrada,
        "tokens_salida": meta.tokens_salida,
        "costo_usd": f"{meta.costo_usd:.5f}",
        "nivel_riesgo": nivel,
        "cumple_seguridad": seguridad,
    }


async def evaluar_modelo(
    cliente: OpenRouterClient, modelo: str, repeticiones: int, registrar
) -> list[dict]:
    p_rutina, p_riesgo = prompts.cargar("rutina_v1"), prompts.cargar("riesgo_v1")
    filas: list[dict] = []
    s = get_settings()

    for i, nombre in enumerate(CASOS):
        ctx = contexto_rutina(i, nombre)
        texto = serializar(ctx)

        def validar(r: RutinaLLM) -> None:
            validar_seguridad_rutina(
                r, SEMANAS, UMBRALES["max_sesiones_semana"], UMBRALES["max_duracion_sesion"]
            )

        try:
            r = await cliente.completar(
                mensajes=p_rutina.mensajes(texto, semanas=SEMANAS),
                schema=RutinaLLM,
                modelos=[modelo],
                max_tokens=s.openrouter_max_tokens_rutina,
                validador_extra=validar,
            )
            meta, seg = r.meta, "1"
        except ErrorIA as e:
            meta, seg = e.meta, "0"
        filas.append(_fila(modelo, "rutina", nombre, 1, meta, seguridad=seg))
        registrar(meta, "rutina", p_rutina.version, hash_contexto(texto))

    for nombre in CASOS:
        ctx = contexto_riesgo(nombre)
        texto = serializar(ctx)
        for rep in range(1, repeticiones + 1):
            nivel = ""
            try:
                r = await cliente.completar(
                    mensajes=p_riesgo.mensajes(texto),
                    schema=RiesgoLLM,
                    modelos=[modelo],
                    max_tokens=s.openrouter_max_tokens_riesgo,
                )
                meta, nivel = r.meta, r.datos.nivel_riesgo
            except ErrorIA as e:
                meta = e.meta
            filas.append(_fila(modelo, "riesgo", nombre, rep, meta, nivel=nivel))
            registrar(meta, "riesgo", p_riesgo.version, hash_contexto(texto))
    return filas


def resumir(filas: list[dict]) -> list[dict]:
    por_modelo: dict[str, list[dict]] = defaultdict(list)
    for f in filas:
        por_modelo[f["modelo"]].append(f)
    resumen = []
    for modelo, fs in por_modelo.items():
        lat = sorted(f["latencia_ms"] for f in fs)
        riesgo = [f for f in fs if f["tarea"] == "riesgo"]
        grupos: dict[str, list[str]] = defaultdict(list)
        for f in riesgo:
            if f["nivel_riesgo"]:
                grupos[f["caso"]].append(f["nivel_riesgo"])
        consistentes = sum(1 for niveles in grupos.values() if len(set(niveles)) == 1)
        resumen.append(
            {
                "modelo": modelo,
                "llamadas": len(fs),
                "cumplimiento_esquema_pct": round(
                    100 * sum(f["cumple_esquema"] for f in fs) / len(fs), 1
                ),
                "primer_intento_pct": round(
                    100 * sum(f["valido_primer_intento"] for f in fs) / len(fs), 1
                ),
                "latencia_p50_ms": int(statistics.median(lat)),
                "latencia_p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))],
                "costo_total_usd": round(sum(float(f["costo_usd"]) for f in fs), 5),
                "consistencia_riesgo_pct": (
                    round(100 * consistentes / len(grupos), 1) if grupos else 0.0
                ),
            }
        )
    return resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--modelos", help="modelos separados por coma (por defecto, los del .env)")
    ap.add_argument(
        "--repeticiones", type=int, default=3, help="repeticiones de cada caso de riesgo"
    )
    ap.add_argument(
        "--salida", type=Path, default=RAIZ / "data" / "processed" / "poc_resultados.csv"
    )
    ap.add_argument("--simulado", action="store_true", help="usa respuestas simuladas (sin red)")
    ap.add_argument(
        "--registrar-bd",
        action="store_true",
        help="guarda cada llamada en llamada_ia (es_caso_control)",
    )
    a = ap.parse_args(argv)
    s = get_settings()

    if a.simulado:
        modelos = (a.modelos or "simulado/modelo-a,simulado/modelo-b").split(",")
        cliente = OpenRouterClient("simulado", transport=_transporte_simulado())
    else:
        candidatos = (
            a.modelos
            or os.getenv("OPENROUTER_MODELS_CANDIDATOS")
            or ",".join(m for m in (s.openrouter_model_principal, s.openrouter_model_respaldo) if m)
        )
        modelos = [m.strip() for m in candidatos.split(",") if m.strip()]
        if not s.openrouter_api_key or not modelos:
            print(
                "Falta OPENROUTER_API_KEY o los modelos (OPENROUTER_MODEL_PRINCIPAL/RESPALDO en .env, "
                "o --modelos). Use --simulado para probar el script sin red.",
                file=sys.stderr,
            )
            return 2
        cliente = OpenRouterClient(
            s.openrouter_api_key,
            base_url=s.openrouter_base_url,
            app_url=s.app_url,
            timeout_s=s.openrouter_timeout_s,
        )

    registrar = _registrador_bd() if a.registrar_bd else (lambda *args: None)
    filas: list[dict] = []
    for modelo in dict.fromkeys(modelos):
        print(f"→ {modelo} ...", flush=True)
        filas += asyncio.run(evaluar_modelo(cliente, modelo, a.repeticiones, registrar))

    a.salida.parent.mkdir(parents=True, exist_ok=True)
    with open(a.salida, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    print(f"\nDetalle: {a.salida}\n")
    for r in resumir(filas):
        print(" · ".join(f"{k}={v}" for k, v in r.items()))
    return 0


def _registrador_bd():
    from app.core.db import nueva_sesion
    from app.services.ia_llamadas import guardar_llamada

    def registrar(meta, tarea, version, hash_):
        with nueva_sesion() as db:
            guardar_llamada(
                db,
                meta,
                tarea=tarea,
                version_prompt=version,
                hash_entrada=hash_,
                es_caso_control=True,
            )
            db.commit()

    return registrar


if __name__ == "__main__":
    raise SystemExit(main())
