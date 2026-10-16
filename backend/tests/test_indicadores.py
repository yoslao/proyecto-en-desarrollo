"""Calculadora de indicadores: funciones puras y cálculo contra la BD."""

from datetime import date, timedelta

import pytest

from app.analytics import indicadores as ind
from app.analytics.indicadores import (
    acwr,
    caida_cmj_pct,
    lunes_de,
    mejor_intento,
    mejora_pct,
    percentil,
    srpe,
)

L1 = date(2026, 9, 7)  # lunes


def semanas(*cargas):
    return {L1 + timedelta(weeks=i): c for i, c in enumerate(cargas) if c is not None}


# ------------------------------------------------------------------ funciones puras
def test_lunes_de():
    assert lunes_de(date(2026, 9, 13)) == L1  # domingo
    assert lunes_de(L1) == L1


def test_srpe_y_none():
    assert srpe(7, 90) == 630
    assert srpe(None, 90) is None


def test_mejor_cmj_de_tres_intentos():
    assert mejor_intento([38, 40, 39]) == 40
    assert mejor_intento([3.2, 3.1], mayor_es_mejor=False) == 3.1


def test_mejora_respeta_mayor_es_mejor():
    assert mejora_pct(42, 40, True) == 5.0
    # sprint: bajar el tiempo es mejorar → variación positiva
    assert mejora_pct(3.0, 3.3, False) == 9.1
    assert mejora_pct(3.6, 3.3, False) == -9.1
    assert mejora_pct(1, 0, True) is None
    assert mejora_pct(None, 5, True) is None


def test_acwr_no_disponible_con_menos_de_4_semanas():
    c = semanas(1000, 1000, 1000)
    assert acwr(c, L1 + timedelta(weeks=2)) is None
    assert acwr({}, L1) is None


def test_acwr_con_4_semanas():
    c = semanas(1000, 1000, 1000, 2000)
    # crónica = (1000+1000+1000+2000)/4 = 1250 → 2000/1250
    assert acwr(c, L1 + timedelta(weeks=3)) == 1.6


def test_acwr_semana_sin_sesiones_cuenta_cero():
    c = semanas(1000, None, 1000, 1000, None)  # semana 2 y 5 sin carga
    # historia desde sem. 1 = 5 semanas; ventana sem.2-5 = 0+1000+1000+0 → 500; actual 0 → 0.0
    assert acwr(c, L1 + timedelta(weeks=4)) == 0.0


def test_acwr_cronica_cero_es_none():
    c = {L1: 500}
    assert acwr(c, L1 + timedelta(weeks=5)) is None


def test_caida_cmj():
    assert caida_cmj_pct(40, 36) == 10.0
    assert caida_cmj_pct(40, 42) == -5.0  # negativo = mejora
    assert caida_cmj_pct(None, 36) is None
    assert caida_cmj_pct(40, None) is None


def test_percentil_orientado():
    equipo = [20, 25, 30, 35, 40]
    assert percentil(40, equipo, True) == 90
    assert percentil(20, equipo, True) == 10
    # sprint: el menor tiempo es el mejor percentil
    tiempos = [3.0, 3.1, 3.2, 3.3]
    assert percentil(3.0, tiempos, False) == 88
    assert percentil(3.3, tiempos, False) == 13
    assert percentil(30, [30], True) is None


# ------------------------------------------------------------------ contra la BD
def _jugador(client, h, jugador_payload, **kw):
    r = client.post("/jugadores", json=jugador_payload(**kw), headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id_jugador"]


@pytest.fixture
def jug(client, h_entrenador, jugador_payload):
    return _jugador(client, h_entrenador, jugador_payload)


def _sesion(client, h, jug, fecha, rpe, minutos=60):
    r = client.post(
        f"/jugadores/{jug}/sesiones",
        json={"fecha": fecha, "duracion_efectiva_min": minutos, "rpe": rpe},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_carga_semanal_y_acwr_desde_la_bd(client, h_entrenador, jug):
    # 4 semanas: 600, 600, 600, 1200 (sRPE = rpe × min)
    for fecha, rpe in [
        ("2026-09-07", 10),
        ("2026-09-14", 10),
        ("2026-09-21", 10),
        ("2026-09-28", 20 // 2),
    ]:
        _sesion(client, h_entrenador, jug, fecha, rpe)
    _sesion(client, h_entrenador, jug, "2026-09-29", 10)  # 2.ª sesión de la 4.ª semana: +600
    r = client.get(f"/jugadores/{jug}/carga", headers=h_entrenador).json()
    cargas = [(s["carga_semanal"], s["num_sesiones"], s["acwr"]) for s in r["semanas"]]
    assert cargas[:3] == [(600, 1, None), (600, 1, None), (600, 1, None)]
    assert cargas[3] == (1200, 2, 1.6)  # 1200 / ((600+600+600+1200)/4)


def test_editar_rpe_recalcula_la_semana(client, h_entrenador, jug):
    s = _sesion(client, h_entrenador, jug, "2026-09-07", 5)
    id_reg = s["registros"][0]["id_registro"]
    r = client.patch(f"/registros-sesion/{id_reg}", json={"rpe": 8}, headers=h_entrenador)
    assert r.status_code == 200 and r.json()["srpe"] == 480
    carga = client.get(f"/jugadores/{jug}/carga", headers=h_entrenador).json()["semanas"]
    assert carga[0]["carga_semanal"] == 480


def test_caida_cmj_respecto_a_linea_base_pre(client, h_entrenador, jug):
    def tests(fecha, momento, cmj):
        r = client.post(
            f"/jugadores/{jug}/tests",
            json={"fecha": fecha, "momento": momento, "pruebas": {"CMJ": cmj}},
            headers=h_entrenador,
        )
        assert r.status_code == 201, r.text

    tests("2026-09-07", "pre", [38, 40, 39])  # línea base = mejor de 3 = 40
    tests("2026-09-14", "seguimiento", [35, 36, 34])  # mejor 36 → caída 10 %
    semanas_ = client.get(f"/jugadores/{jug}/carga", headers=h_entrenador).json()["semanas"]
    assert [s["caida_cmj_pct"] for s in semanas_] == [0.0, 10.0]
    ind_ = client.get(f"/jugadores/{jug}/indicadores", headers=h_entrenador).json()
    assert ind_["mediciones"]["CMJ"]["actual"] == 36.0
    assert ind_["mediciones"]["CMJ"]["var_linea_base_pct"] == -10.0
    assert ind_["mediciones"]["CMJ"]["var_anterior_pct"] == -10.0


def test_sin_linea_base_pre_la_caida_es_nula(client, h_entrenador, jug):
    client.post(
        f"/jugadores/{jug}/tests",
        json={"fecha": "2026-09-07", "momento": "seguimiento", "pruebas": {"CMJ": [38]}},
        headers=h_entrenador,
    )
    sem = client.get(f"/jugadores/{jug}/carga", headers=h_entrenador).json()["semanas"]
    assert sem[0]["caida_cmj_pct"] is None


def test_indicadores_umbrales_y_bienestar(client, h_entrenador, jug):
    for i, fecha in enumerate(["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]):
        _sesion(client, h_entrenador, jug, fecha, 5 if i < 3 else 10)
    client.post(
        f"/jugadores/{jug}/bienestar",
        json={
            "semana_inicio": "2026-09-21",
            "sueno": 4,
            "fatiga": 3,
            "dolor_muscular": 3,
            "animo": 4,
        },
        headers=h_entrenador,
    )
    client.post(
        f"/jugadores/{jug}/bienestar",
        json={
            "semana_inicio": "2026-09-28",
            "sueno": 2,
            "fatiga": 1,
            "dolor_muscular": 2,
            "animo": 2,
        },
        headers=h_entrenador,
    )
    r = client.get(f"/jugadores/{jug}/indicadores", headers=h_entrenador).json()
    assert r["carga"]["acwr"] == 1.6
    assert r["bienestar"] == {"semana_inicio": "2026-09-28", "indice": 7, "var_semana_anterior": -7}
    assert r["superan_umbral"] == {"acwr_alto": True, "caida_cmj": None, "bienestar_bajo": True}
    assert r["umbrales"]["ACWR_ALTO"] == 1.5


def test_percentil_en_el_equipo_con_atipicos_excluidos(client, h_entrenador, jugador_payload, db):
    ids = [
        _jugador(client, h_entrenador, jugador_payload, apellidos=f"Equipo{i}") for i in range(5)
    ]
    r = client.post(
        "/jornadas",
        json={
            "fecha": "2026-09-07",
            "momento": "pre",
            "resultados": [
                {"id_jugador": j, "pruebas": {"SPRINT_20M": [t]}}
                for j, t in zip(ids, [3.0, 3.1, 3.2, 3.3, 3.4], strict=True)
            ],
        },
        headers=h_entrenador,
    )
    assert r.status_code == 201, r.text
    pct = ind.percentiles_equipo(db, ids[0])
    assert pct["SPRINT_20M"] == 90  # el más rápido
    assert ind.percentiles_equipo(db, ids[4])["SPRINT_20M"] == 10
