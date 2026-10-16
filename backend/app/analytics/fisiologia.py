"""Fórmulas fisiológicas derivadas. Nunca se almacenan: se calculan al consultar.

Course Navette — ecuación de Léger et al. (1988):

    VO2max = 31.025 + 3.238·V − 3.248·E + 0.1536·E·V

    V = velocidad del último palier completado (km/h)
    E = edad (años)

Convención del palier (constante configurable): V = 7.5 + 0.5 × palier.
Esta convención reproduce los valores de VO2max del Excel original
(p. ej. palier 10, 22 años → 42.3 ml/kg/min).
"""

from decimal import Decimal

LEGER_V_BASE_KMH = 7.5
LEGER_V_PASO_KMH = 0.5
DISTANCIA_SPRINT_M = 20.0


def velocidad_palier_kmh(palier: float | Decimal) -> float:
    return LEGER_V_BASE_KMH + LEGER_V_PASO_KMH * float(palier)


def vo2max_leger(palier: float | Decimal, edad: float | int) -> float:
    """VO2max estimado (ml/kg/min) por la ecuación de Léger (1988)."""
    v = velocidad_palier_kmh(palier)
    return 31.025 + 3.238 * v - 3.248 * edad + 0.1536 * edad * v


def velocidad_sprint_ms(
    tiempo_s: float | Decimal, distancia_m: float = DISTANCIA_SPRINT_M
) -> float:
    """Velocidad media (m/s) = distancia / tiempo."""
    return distancia_m / float(tiempo_s)
