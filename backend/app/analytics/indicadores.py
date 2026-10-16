"""Calculadora de indicadores (RF-09). Determinista: sin modelos entrenados.

Convenciones (acordadas, ver CLAUDE.md):
- Solo cuentan resultados con estado 'valido' o 'confirmado' (los 'pendiente_confirmacion' se excluyen).
- Variación con signo de DESEMPEÑO: positivo = mejora, usando `mayor_es_mejor` (en el sprint,
  menos tiempo es mejor, así que una caída del tiempo da variación positiva).
- Línea base = primera medición de una jornada con momento 'pre'. Las 'seguimiento' no lo son.
- sRPE = RPE × minutos efectivos. Carga semanal = Σ sRPE de la semana (lunes a domingo).
- ACWR = carga de la semana / promedio de las 4 últimas semanas (incluida la actual). Las semanas
  sin sesiones cuentan 0; con menos de 4 semanas de historia (desde la primera con RPE) → None.
- Caída del CMJ = (CMJ de línea base − mejor CMJ de la semana) / CMJ de línea base × 100. None si
  no hay línea base o no hubo CMJ esa semana. Un valor negativo es una mejora.
- Percentil: posición de media-rango dentro del equipo (jugadores activos con consentimiento),
  orientado para que un percentil alto siempre sea mejor (se invierte en el sprint).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models import (
    ConfiguracionUmbral,
    CuestionarioBienestar,
    IndicadorCargaSemanal,
    JornadaEvaluacion,
    Jugador,
    RegistroSesionJugador,
    ResultadoPrueba,
    Sesion,
    TipoPrueba,
)

VENTANA_ACWR = 4
ESTADOS_VALIDOS = ("valido", "confirmado")


# ---------------------------------------------------------------- funciones puras
def lunes_de(fecha: date) -> date:
    return fecha - timedelta(days=fecha.weekday())


def mejor_intento(valores: Iterable[float | Decimal], mayor_es_mejor: bool = True) -> float:
    vals = [float(v) for v in valores]
    return max(vals) if mayor_es_mejor else min(vals)


def srpe(rpe: int | None, minutos: int) -> int | None:
    return None if rpe is None else rpe * minutos


def mejora_pct(
    actual: float | None, referencia: float | None, mayor_es_mejor: bool
) -> float | None:
    """Variación porcentual con signo de desempeño (positivo = mejora)."""
    if actual is None or referencia is None or referencia == 0:
        return None
    cambio = (float(actual) - float(referencia)) / float(referencia) * 100
    return round(cambio if mayor_es_mejor else -cambio, 1)


def acwr(carga_por_semana: dict[date, int], semana: date) -> float | None:
    """ACWR de `semana`. `carga_por_semana` solo contiene semanas con RPE registrado."""
    if not carga_por_semana:
        return None
    primera = min(carga_por_semana)
    semanas_de_historia = (semana - primera).days // 7 + 1
    if semanas_de_historia < VENTANA_ACWR:
        return None
    ventana = [semana - timedelta(weeks=i) for i in range(VENTANA_ACWR)]
    cronica = sum(carga_por_semana.get(s, 0) for s in ventana) / VENTANA_ACWR
    if cronica == 0:
        return None
    return round(carga_por_semana.get(semana, 0) / cronica, 2)


def caida_cmj_pct(linea_base: float | None, mejor_semana: float | None) -> float | None:
    if linea_base is None or mejor_semana is None or linea_base == 0:
        return None
    return round((linea_base - mejor_semana) / linea_base * 100, 2)


def percentil(valor: float, equipo: list[float], mayor_es_mejor: bool) -> int | None:
    """Percentil de media-rango (0-100); alto = mejor. None con menos de 2 jugadores."""
    if len(equipo) < 2:
        return None
    peores = sum(1 for v in equipo if (v < valor if mayor_es_mejor else v > valor))
    iguales = sum(1 for v in equipo if v == valor)
    return int(
        Decimal((peores + 0.5 * iguales) / len(equipo) * 100).quantize(Decimal(1), ROUND_HALF_UP)
    )


# ---------------------------------------------------------------- lectura desde la BD
@dataclass
class Punto:
    """Mejor valor de una prueba en una jornada para un jugador."""

    id_jornada: int
    fecha: date
    momento: str
    valor: float


def puntos_por_jugador(
    db: Session, ids_jugador: list[int] | None = None
) -> dict[int, dict[str, list[Punto]]]:
    """{jugador: {codigo_prueba: [Punto...]}} ordenado por fecha. Solo resultados válidos."""
    q = (
        select(
            ResultadoPrueba.id_jugador,
            TipoPrueba.codigo,
            TipoPrueba.mayor_es_mejor,
            JornadaEvaluacion.id_jornada,
            JornadaEvaluacion.fecha,
            JornadaEvaluacion.momento,
            ResultadoPrueba.valor,
        )
        .join(JornadaEvaluacion, JornadaEvaluacion.id_jornada == ResultadoPrueba.id_jornada)
        .join(TipoPrueba, TipoPrueba.id_tipo_prueba == ResultadoPrueba.id_tipo_prueba)
        .where(ResultadoPrueba.estado.in_(ESTADOS_VALIDOS))
    )
    if ids_jugador is not None:
        q = q.where(ResultadoPrueba.id_jugador.in_(ids_jugador or [-1]))
    intentos: dict[tuple, list[float]] = defaultdict(list)
    meta: dict[tuple, tuple] = {}
    for jug, cod, mayor, id_jor, fecha, momento, valor in db.execute(q):
        k = (jug, cod, id_jor)
        intentos[k].append(float(valor))
        meta[k] = (mayor, fecha, momento)
    salida: dict[int, dict[str, list[Punto]]] = defaultdict(lambda: defaultdict(list))
    for (jug, cod, id_jor), vals in intentos.items():
        mayor, fecha, momento = meta[(jug, cod, id_jor)]
        salida[jug][cod].append(Punto(id_jor, fecha, momento, mejor_intento(vals, mayor)))
    for por_cod in salida.values():
        for lista in por_cod.values():
            lista.sort(key=lambda p: (p.fecha, p.id_jornada))
    return salida


def linea_base(puntos: list[Punto]) -> Punto | None:
    return next((p for p in puntos if p.momento == "pre"), None)


def variaciones(puntos: list[Punto], mayor_es_mejor: bool) -> dict:
    """Valor actual y su variación frente a la línea base y a la medición anterior."""
    if not puntos:
        return {}
    actual = puntos[-1]
    base = linea_base(puntos)
    anterior = puntos[-2] if len(puntos) >= 2 else None
    return {
        "actual": actual.valor,
        "fecha": actual.fecha,
        "momento": actual.momento,
        "linea_base": base.valor if base else None,
        "var_linea_base_pct": mejora_pct(
            actual.valor, base.valor if base else None, mayor_es_mejor
        ),
        "anterior": anterior.valor if anterior else None,
        "var_anterior_pct": mejora_pct(
            actual.valor, anterior.valor if anterior else None, mayor_es_mejor
        ),
    }


# ---------------------------------------------------------------- indicadores semanales
@dataclass
class SemanaCalculada:
    semana_inicio: date
    carga_semanal: int
    num_sesiones: int
    acwr: float | None
    caida_cmj_pct: float | None


def cargas_semanales(db: Session, id_jugador: int) -> tuple[dict[date, int], dict[date, int]]:
    """({semana: Σ sRPE}, {semana: nº de sesiones con RPE}). Ignora registros sin RPE."""
    carga: dict[date, int] = defaultdict(int)
    n: dict[date, int] = defaultdict(int)
    filas = db.execute(
        select(Sesion.fecha, RegistroSesionJugador.rpe, RegistroSesionJugador.duracion_efectiva_min)
        .join(Sesion, Sesion.id_sesion == RegistroSesionJugador.id_sesion)
        .where(
            RegistroSesionJugador.id_jugador == id_jugador, RegistroSesionJugador.rpe.is_not(None)
        )
    )
    for fecha, rpe, minutos in filas:
        s = lunes_de(fecha)
        carga[s] += rpe * minutos
        n[s] += 1
    return dict(carga), dict(n)


def calcular_semanas(db: Session, id_jugador: int) -> list[SemanaCalculada]:
    carga, n = cargas_semanales(db, id_jugador)
    cmj = puntos_por_jugador(db, [id_jugador]).get(id_jugador, {}).get("CMJ", [])
    base = linea_base(cmj)
    mejor_cmj_semana: dict[date, float] = {}
    for p in cmj:
        s = lunes_de(p.fecha)
        mejor_cmj_semana[s] = max(mejor_cmj_semana.get(s, p.valor), p.valor)
    semanas = sorted(set(carga) | set(mejor_cmj_semana))
    return [
        SemanaCalculada(
            semana_inicio=s,
            carga_semanal=carga.get(s, 0),
            num_sesiones=n.get(s, 0),
            acwr=acwr(carga, s),
            caida_cmj_pct=caida_cmj_pct(base.valor if base else None, mejor_cmj_semana.get(s)),
        )
        for s in semanas
    ]


def _dec(x: float | None, exp: str) -> Decimal | None:
    return None if x is None else Decimal(str(x)).quantize(Decimal(exp), ROUND_HALF_UP)


def recalcular(db: Session, id_jugador: int) -> list[IndicadorCargaSemanal]:
    """Recalcula y persiste los indicadores semanales del jugador (única escritura permitida).

    Se llama en cada alta o edición de sesión, de CMJ o de la jornada (cambio de momento).
    Como el ACWR de una semana depende de las 3 anteriores y la caída del CMJ de la línea base,
    se recalculan todas las semanas del jugador: es barato y evita estados intermedios.
    """
    calculadas = {s.semana_inicio: s for s in calcular_semanas(db, id_jugador)}
    previas = {
        r.semana_inicio: r
        for r in db.scalars(
            select(IndicadorCargaSemanal).where(IndicadorCargaSemanal.id_jugador == id_jugador)
        )
    }
    for semana, fila in previas.items():
        if semana not in calculadas:
            db.delete(fila)
    resultado = []
    ahora = utcnow()
    for semana, c in calculadas.items():
        fila = previas.get(semana)
        if fila is None:
            fila = IndicadorCargaSemanal(id_jugador=id_jugador, semana_inicio=semana)
            db.add(fila)
        fila.carga_semanal = c.carga_semanal
        fila.num_sesiones = c.num_sesiones
        fila.acwr = _dec(c.acwr, "0.01")
        fila.caida_cmj_pct = _dec(c.caida_cmj_pct, "0.01")
        fila.calculado_en = ahora
        resultado.append(fila)
    db.flush()
    return resultado


# ---------------------------------------------------------------- bienestar y equipo
def serie_bienestar(db: Session, id_jugador: int) -> list[dict]:
    filas = db.scalars(
        select(CuestionarioBienestar)
        .where(CuestionarioBienestar.id_jugador == id_jugador)
        .order_by(CuestionarioBienestar.semana_inicio)
    ).all()
    salida, previo = [], None
    for f in filas:
        indice = f.sueno + f.fatiga + f.dolor_muscular + f.animo
        salida.append(
            {
                "semana_inicio": f.semana_inicio,
                "indice": indice,
                "var_semana_anterior": None if previo is None else indice - previo,
            }
        )
        previo = indice
    return salida


def umbrales(db: Session) -> dict[str, float]:
    return {u.clave: float(u.valor) for u in db.scalars(select(ConfiguracionUmbral))}


def percentiles_equipo(db: Session, id_jugador: int) -> dict[str, int | None]:
    """Percentil del último valor de cada prueba dentro del equipo (activos con consentimiento)."""
    ids = list(
        db.scalars(
            select(Jugador.id_jugador).where(
                Jugador.activo.is_(True), Jugador.consentimiento_informado.is_(True)
            )
        )
    )
    mayor = {t.codigo: t.mayor_es_mejor for t in db.scalars(select(TipoPrueba))}
    puntos = puntos_por_jugador(db, ids)
    salida: dict[str, int | None] = {}
    propio = puntos.get(id_jugador, {})
    for cod, lista in propio.items():
        equipo = [p[cod][-1].valor for p in puntos.values() if cod in p and p[cod]]
        salida[cod] = percentil(lista[-1].valor, equipo, mayor[cod])
    return salida


def resumen_indicadores(db: Session, id_jugador: int) -> dict:
    """Foto actual del jugador: base del contexto que luego se seudonimiza para la IA."""
    mayor = {t.codigo: t.mayor_es_mejor for t in db.scalars(select(TipoPrueba))}
    puntos = puntos_por_jugador(db, [id_jugador]).get(id_jugador, {})
    mediciones = {cod: variaciones(lista, mayor[cod]) for cod, lista in puntos.items()}
    filas = db.scalars(
        select(IndicadorCargaSemanal)
        .where(IndicadorCargaSemanal.id_jugador == id_jugador)
        .order_by(IndicadorCargaSemanal.semana_inicio)
    ).all()
    ultima = filas[-1] if filas else None
    bienestar = serie_bienestar(db, id_jugador)
    um = umbrales(db)
    acwr_v = float(ultima.acwr) if ultima and ultima.acwr is not None else None
    caida_v = float(ultima.caida_cmj_pct) if ultima and ultima.caida_cmj_pct is not None else None
    bien_v = bienestar[-1]["indice"] if bienestar else None
    return {
        "mediciones": mediciones,
        "carga": (
            None
            if ultima is None
            else {
                "semana_inicio": ultima.semana_inicio,
                "srpe_semana": ultima.carga_semanal,
                "sesiones_semana": ultima.num_sesiones,
                "acwr": acwr_v,  # None = «no disponible»
                "caida_cmj_pct": caida_v,
            }
        ),
        "bienestar": bienestar[-1] if bienestar else None,
        "percentil_equipo": percentiles_equipo(db, id_jugador),
        "umbrales": um,
        "superan_umbral": {
            "acwr_alto": None if acwr_v is None else acwr_v > um.get("ACWR_ALTO", 1.5),
            "caida_cmj": None if caida_v is None else caida_v > um.get("CAIDA_CMJ_PCT", 10),
            "bienestar_bajo": None if bien_v is None else bien_v < um.get("BIENESTAR_BAJO", 8),
        },
    }
