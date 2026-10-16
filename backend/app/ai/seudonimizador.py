"""Seudonimizador: construye el contexto que se envía al proveedor de IA (Ley N.º 29733).

Al proveedor SOLO llega: código seudónimo (J-07), posición, edad, mediciones con su variación,
indicadores de carga y bienestar, percentiles, cluster con su etiqueta, objetivo y umbrales.
Nunca el nombre, la universidad ni texto libre del usuario (prevención de inyección de prompt):
el contexto se arma con una lista blanca y cada cadena se valida contra un formato cerrado.
"""

import hashlib
import json
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import clustering as km
from app.analytics import indicadores
from app.etl.limpieza import normalizar_texto
from app.models import Jugador, Posicion, RegistroSesionJugador, Sesion, Universidad
from app.services import clustering as svc_clustering
from app.services.jugadores import calcular_edad

NO_DISPONIBLE = "no disponible"
OBJETIVOS = ("velocidad", "resistencia", "salto")

CLAVE_MEDICION = {
    "SPRINT_20M": "sprint_20m_s",
    "NAVETTE": "navette_paliers",
    "CMJ": "cmj_cm",
    "SJ": "sj_cm",
    "SALTO_SC": "salto_sc_cm",
    "SALTO_BC": "salto_bc_cm",
    "SALTO_AI": "salto_ai_cm",
    "SALTO_PF": "salto_pf_cm",
}
CLAVE_PERCENTIL = {
    "SPRINT_20M": "sprint",
    "NAVETTE": "navette",
    "CMJ": "cmj",
    "SJ": "sj",
    "SALTO_SC": "salto_sc",
    "SALTO_BC": "salto_bc",
    "SALTO_AI": "salto_ai",
    "SALTO_PF": "salto_pf",
}
CLAVE_UMBRAL = {
    "ACWR_ALTO": "acwr_alto",
    "CAIDA_CMJ_PCT": "caida_cmj_pct",
    "BIENESTAR_BAJO": "bienestar_bajo",
    "MAX_SESIONES_SEMANA": "max_sesiones_semana",
    "MAX_DURACION_SESION": "max_duracion_sesion",
}

_RE_CODIGO = re.compile(r"^J-\d{1,4}$")
_RE_ETIQUETA = re.compile(r"^[A-Za-zÁÉÍÓÚáéíóúñÑ0-9 /()]{1,80}$")


class ErrorSeudonimizacion(Exception):
    """El contexto contiene algo que no debería salir hacia el proveedor de IA."""


def _nd(x):
    return NO_DISPONIBLE if x is None else x


def _num(x):
    """Número JSON limpio: enteros sin '.0'."""
    if x is None:
        return NO_DISPONIBLE
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def _cadena_cerrada(valor: str, patron: re.Pattern | None = None, permitidas=None) -> str:
    if permitidas is not None and valor not in permitidas:
        raise ErrorSeudonimizacion(f"Valor categórico no permitido: {valor!r}")
    if patron is not None and not patron.match(valor):
        raise ErrorSeudonimizacion("Cadena con formato no permitido en el contexto")
    return valor


# ------------------------------------------------------------------ construcción
def _mediciones(resumen: dict) -> dict:
    salida = {}
    for codigo, v in resumen["mediciones"].items():
        clave = CLAVE_MEDICION.get(codigo)
        if clave is None:
            continue
        salida[clave] = {
            "actual": _num(v.get("actual")),
            "var_linea_base_pct": _num(v.get("var_linea_base_pct")),
            "var_anterior_pct": _num(v.get("var_anterior_pct")),
        }
    return salida


def _carga(resumen: dict, hoy: date) -> dict | str:
    c = resumen["carga"]
    if c is None:
        return NO_DISPONIBLE
    semanas = (indicadores.lunes_de(hoy) - c["semana_inicio"]).days // 7
    return {
        "sRPE_semana": _num(c["srpe_semana"]),
        "acwr": _num(c["acwr"]),
        "sesiones_semana": _num(c["sesiones_semana"]),
        "caida_cmj_pct": _num(c["caida_cmj_pct"]),
        "semanas_desde_ultimo_registro": max(semanas, 0),
    }


def _bienestar(resumen: dict) -> dict | str:
    b = resumen["bienestar"]
    if b is None:
        return NO_DISPONIBLE
    return {"indice": b["indice"], "var_semana_anterior": _num(b["var_semana_anterior"])}


def _rpe_reciente(db: Session, id_jugador: int) -> dict | str:
    filas = db.execute(
        select(RegistroSesionJugador.rpe)
        .join(Sesion, Sesion.id_sesion == RegistroSesionJugador.id_sesion)
        .where(
            RegistroSesionJugador.id_jugador == id_jugador, RegistroSesionJugador.rpe.is_not(None)
        )
        .order_by(Sesion.fecha.desc(), RegistroSesionJugador.id_registro.desc())
        .limit(3)
    ).all()
    if not filas:
        return NO_DISPONIBLE
    rpes = [r[0] for r in filas]
    return {"ultimo": rpes[0], "promedio_ultimas_3": round(sum(rpes) / len(rpes), 1)}


def _base(db: Session, id_jugador: int, hoy: date) -> dict:
    j = db.get(Jugador, id_jugador)
    resumen = indicadores.resumen_indicadores(db, id_jugador)
    posiciones = {p.nombre for p in db.scalars(select(Posicion))}
    cl = svc_clustering.cluster_de_jugador(db, id_jugador)
    perfil = (
        NO_DISPONIBLE
        if cl is None
        else {
            "cluster": cl.cluster,
            "etiqueta": _cadena_cerrada(cl.etiqueta, _RE_ETIQUETA),
        }
    )
    edad = calcular_edad(j.fecha_nacimiento)
    umbrales = {
        CLAVE_UMBRAL[k]: _num(v) for k, v in resumen["umbrales"].items() if k in CLAVE_UMBRAL
    }
    return {
        "jugador": _cadena_cerrada(j.codigo_seudonimo, _RE_CODIGO),
        "posicion": _cadena_cerrada(j.posicion.nombre, permitidas=posiciones),
        "edad": _nd(edad),
        "perfil_cluster": perfil,
        "mediciones": _mediciones(resumen),
        "carga": _carga(resumen, hoy),
        "bienestar": _bienestar(resumen),
        "percentil_equipo": {
            CLAVE_PERCENTIL[c]: _nd(p)
            for c, p in resumen["percentil_equipo"].items()
            if c in CLAVE_PERCENTIL
        },
        "umbrales": umbrales,
    }


def contexto_rutina(
    db: Session, id_jugador: int, semanas: int, objetivo: str, hoy: date | None = None
) -> dict:
    ctx = _base(db, id_jugador, hoy or date.today())
    ctx["objetivo_prioritario"] = _cadena_cerrada(objetivo, permitidas=OBJETIVOS)
    ctx["semanas"] = int(semanas)
    return ctx


def contexto_riesgo(db: Session, id_jugador: int, hoy: date | None = None) -> dict:
    ctx = _base(db, id_jugador, hoy or date.today())
    # Orden de claves estable: el RPE reciente va junto a la carga.
    salida = {}
    for k, v in ctx.items():
        salida[k] = v
        if k == "carga":
            salida["rpe_reciente"] = _rpe_reciente(db, id_jugador)
    return salida


def serializar(contexto: dict) -> str:
    return json.dumps(contexto, ensure_ascii=False, separators=(",", ":"))


def hash_contexto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ verificación final
def vocabulario_permitido(db: Session) -> set[str]:
    palabras: set[str] = set()
    for p in db.scalars(select(Posicion)):
        palabras.update(normalizar_texto(p.nombre).split())
    for d in km.DESCRIPTORES.values():
        palabras.update(normalizar_texto(d).split())
    palabras.update({"perfil", "medio", "grupo", "no", "disponible"})
    return palabras


def verificar_sin_datos_personales(db: Session, texto: str) -> None:
    """Falla si el texto que saldrá hacia el proveedor contiene un nombre o una universidad."""
    plano = normalizar_texto(texto)
    permitido = vocabulario_permitido(db)
    for nombres, apellidos in db.execute(select(Jugador.nombres, Jugador.apellidos)):
        for token in normalizar_texto(f"{nombres} {apellidos}").split():
            if (
                len(token) >= 3
                and token not in permitido
                and re.search(rf"\b{re.escape(token)}\b", plano)
            ):
                raise ErrorSeudonimizacion("El contexto contiene el nombre de un jugador")
    for (universidad,) in db.execute(select(Universidad.nombre)):
        if normalizar_texto(universidad) in plano:
            raise ErrorSeudonimizacion("El contexto contiene el nombre de una universidad")
        for token in normalizar_texto(universidad).split():
            if (
                len(token) >= 5
                and token not in permitido
                and re.search(rf"\b{re.escape(token)}\b", plano)
            ):
                raise ErrorSeudonimizacion("El contexto contiene el nombre de una universidad")


def preparar(db: Session, contexto: dict) -> tuple[str, str]:
    """(texto JSON, hash SHA-256) tras verificar que no hay datos personales."""
    texto = serializar(contexto)
    verificar_sin_datos_personales(db, texto)
    return texto, hash_contexto(texto)
