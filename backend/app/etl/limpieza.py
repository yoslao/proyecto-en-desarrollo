"""Funciones puras de limpieza del ETL (sin acceso a BD)."""

import math
import re
import unicodedata
from datetime import time
from decimal import Decimal

import numpy as np

_VACIOS = {"", "-", "—", "–", "n/a", "na", "nan", "none"}
_RE_NUM = re.compile(r"^([-+]?\d+(?:[.,]\d+)?)\s*([a-zµ/²³%]*)$")
_RE_HORA = re.compile(r"(\d{1,2})\s*:\s*(\d{2})(?:\s*([ap])\.?\s?m(?![a-z]))?", re.IGNORECASE)


def quitar_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalizar_texto(s: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados (solo para claves de emparejamiento)."""
    return " ".join(quitar_acentos(str(s)).lower().split())


def clave_nombre(s: str) -> str:
    """Clave de emparejamiento insensible al orden de las palabras, a tildes y a mayúsculas."""
    return " ".join(sorted(normalizar_texto(s).split()))


def nombre_propio(s: str) -> str:
    """'huayllani  carrasco VLADIMIR' → 'Huayllani Carrasco Vladimir' (conserva las tildes)."""
    return " ".join(p[:1].upper() + p[1:].lower() for p in str(s).split())


def dividir_nombre(completo: str) -> tuple[str, str]:
    """Regla por defecto 'Apellido Apellido Nombre(s)': 2 primeras palabras = apellidos.

    Con 2 palabras: 1 apellido y 1 nombre. Las excepciones se corrigen con el archivo de decisiones.
    Devuelve (nombres, apellidos).
    """
    p = nombre_propio(completo).split()
    if len(p) < 2:
        return (p[0] if p else ""), ""
    n_ap = 1 if len(p) == 2 else 2
    return " ".join(p[n_ap:]), " ".join(p[:n_ap])


def es_vacio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    return isinstance(valor, str) and valor.strip().lower() in _VACIOS


def a_numero(valor) -> Decimal | None:
    """Convierte '65kg', '3.10s', '23cm', '74min', '6.45m/s', 42.3 → Decimal.

    Devuelve None si la celda está vacía o es '-'. Lanza ValueError si no es interpretable.
    """
    if es_vacio(valor):
        return None
    if isinstance(valor, bool):
        raise ValueError(f"valor lógico no numérico: {valor!r}")
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    m = _RE_NUM.match(str(valor).strip().lower())
    if not m:
        raise ValueError(f"no se pudo interpretar como número: {valor!r}")
    return Decimal(m.group(1).replace(",", "."))


def a_horas(texto) -> list[time]:
    """Extrae las horas de textos como '7:00PM a 7:15 PM' o '7:30 a 7:45' (formato 24 h).

    Una hora sin AM/PM hereda la del valor anterior de la misma celda; si ninguna la tiene
    se asume PM (los entrenamientos son vespertinos); `a_horas_detallado` informa cuáles se asumieron.
    """
    return [h for h, _ in a_horas_detallado(texto)]


def a_horas_detallado(texto) -> list[tuple[time, bool]]:
    """Como a_horas, pero devuelve (hora, fue_asumida_pm)."""
    if es_vacio(texto):
        return []
    coincidencias = list(_RE_HORA.finditer(str(texto)))
    meridianos = [m.group(3).lower() if m.group(3) else None for m in coincidencias]
    heredado = next((x for x in meridianos if x), None)
    salida: list[tuple[time, bool]] = []
    ultimo = None
    for m, mer in zip(coincidencias, meridianos, strict=True):
        hora, minuto = int(m.group(1)), int(m.group(2))
        asumido = False
        if mer is None:
            mer = ultimo or heredado
            if mer is None:
                mer, asumido = "p", True
        ultimo = mer
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            continue
        if hora <= 12:
            if mer == "p" and hora < 12:
                hora += 12
            elif mer == "a" and hora == 12:
                hora = 0
        salida.append((time(hora, minuto), asumido))
    return salida


def limites_iqr(valores: list[float]) -> tuple[float, float] | None:
    """(Q1 − 1.5·IQR, Q3 + 1.5·IQR). None si hay menos de 4 valores."""
    if len(valores) < 4:
        return None
    q1, q3 = np.percentile(valores, [25, 75])
    iqr = q3 - q1
    return float(q1 - 1.5 * iqr), float(q3 + 1.5 * iqr)


def es_atipico(valor: float, limites: tuple[float, float] | None) -> bool:
    return limites is not None and (valor < limites[0] or valor > limites[1])
