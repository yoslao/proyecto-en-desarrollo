"""Detección de valores atípicos por IQR (Tukey)."""

import numpy as np


def limites_iqr(valores: list[float]) -> tuple[float, float] | None:
    """(Q1 − 1.5·IQR, Q3 + 1.5·IQR). None si hay menos de 4 valores."""
    if len(valores) < 4:
        return None
    q1, q3 = np.percentile(valores, [25, 75])
    iqr = q3 - q1
    return float(q1 - 1.5 * iqr), float(q3 + 1.5 * iqr)


def es_atipico(valor: float, limites: tuple[float, float] | None) -> bool:
    return limites is not None and (valor < limites[0] or valor > limites[1])
