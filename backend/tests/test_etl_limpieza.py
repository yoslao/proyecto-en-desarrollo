from datetime import time
from decimal import Decimal

import pytest

from app.analytics.fisiologia import velocidad_sprint_ms, vo2max_leger
from app.etl.limpieza import (
    a_horas,
    a_horas_detallado,
    a_numero,
    clave_nombre,
    dividir_nombre,
    es_atipico,
    limites_iqr,
    nombre_propio,
)


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        ("65kg", "65"),
        ("3.10s", "3.10"),
        ("23cm", "23"),
        ("74min", "74"),
        ("6.45m/s", "6.45"),
        ("3,10s", "3.10"),
        (42.3, "42.3"),
        (10, "10"),
        ("  12 ", "12"),
    ],
)
def test_a_numero_quita_unidades(crudo, esperado):
    assert a_numero(crudo) == Decimal(esperado)


@pytest.mark.parametrize("vacio", ["-", "", "  ", None, float("nan"), "N/A"])
def test_a_numero_vacios_dan_none(vacio):
    assert a_numero(vacio) is None


def test_a_numero_rechaza_texto_no_numerico():
    with pytest.raises(ValueError):
        a_numero("abc")


def test_clave_nombre_ignora_orden_tildes_mayusculas_y_espacios():
    assert clave_nombre("Franco silva muñoz ") == clave_nombre("MUNOZ  Silva franco")


def test_nombre_propio_conserva_tildes():
    assert nombre_propio("  franco silva MUÑOZ ") == "Franco Silva Muñoz"


def test_dividir_nombre_regla_por_defecto():
    assert dividir_nombre("Huayllani carrasco vladimir cesar") == (
        "Vladimir Cesar",
        "Huayllani Carrasco",
    )
    assert dividir_nombre("Apaza Gonzales Santiago") == ("Santiago", "Apaza Gonzales")
    assert dividir_nombre("Perez Juan") == ("Juan", "Perez")


def test_horas_con_y_sin_meridiano():
    assert a_horas("7:00PM a 7:15 PM") == [time(19, 0), time(19, 15)]
    assert a_horas("8:30 AM a 9:00") == [time(8, 30), time(9, 0)]
    assert a_horas("12:00 PM") == [time(12, 0)]
    assert a_horas("12:30 AM") == [time(0, 30)]


def test_hora_sin_meridiano_se_marca_como_asumida():
    det = a_horas_detallado("7:30 a 7:45")
    assert det[0] == (time(19, 30), True)
    assert det[1][1] is False  # hereda del anterior


def test_iqr_marca_atipicos_y_necesita_4_valores():
    assert limites_iqr([1, 2, 3]) is None
    lim = limites_iqr([20, 21, 22, 23, 24, 25, 26, 70])
    assert es_atipico(70, lim) and not es_atipico(25, lim)
    assert not es_atipico(5, None)


def test_formula_de_leger_reproduce_el_excel():
    # Valores reales del Excel original (palier, edad → VO2max informado)
    assert vo2max_leger(10, 22) == pytest.approx(42.3, abs=0.1)
    assert vo2max_leger(8.5, 24) == pytest.approx(34.4, abs=0.1)
    assert vo2max_leger(11.5, 23) == pytest.approx(46.0, abs=0.1)


def test_velocidad_es_distancia_sobre_tiempo():
    assert velocidad_sprint_ms(Decimal("3.10")) == pytest.approx(6.45, abs=0.01)
