"""Registro de tests, sesiones y bienestar (RF-02, RF-08) y gestión de atípicos."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    BitacoraAuditoria,
    JornadaEvaluacion,
    ResultadoPrueba,
)
from app.services import riesgo_hooks


def _nuevo(client, h, jugador_payload, **kw):
    r = client.post("/jugadores", json=jugador_payload(**kw), headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id_jugador"]


@pytest.fixture
def jug(client, h_entrenador, jugador_payload):
    return _nuevo(client, h_entrenador, jugador_payload)


@pytest.fixture
def equipo(client, h_entrenador, jugador_payload):
    return [_nuevo(client, h_entrenador, jugador_payload, apellidos=f"Eq{i}") for i in range(6)]


def _tests(client, h, jug, pruebas, fecha="2026-09-07", momento="pre"):
    return client.post(
        f"/jugadores/{jug}/tests",
        json={"fecha": fecha, "momento": momento, "pruebas": pruebas},
        headers=h,
    )


# ------------------------------------------------------------------ tests (RF-02)
def test_registrar_tests_crea_jornada_y_resultados(client, h_entrenador, jug, db):
    r = _tests(
        client, h_entrenador, jug, {"SPRINT_20M": [3.1], "NAVETTE": [9.5], "CMJ": [38, 40, 39]}
    )
    assert r.status_code == 201, r.text
    assert r.json()["jornada_nueva"] is True and r.json()["resultados_registrados"] == 5
    assert db.scalar(select(func.count()).select_from(ResultadoPrueba)) == 5


def test_misma_fecha_y_momento_reutiliza_la_jornada(client, h_entrenador, equipo, db):
    _tests(client, h_entrenador, equipo[0], {"SPRINT_20M": [3.1]})
    r = _tests(client, h_entrenador, equipo[1], {"SPRINT_20M": [3.2]})
    assert r.json()["jornada_nueva"] is False
    assert db.scalar(select(func.count()).select_from(JornadaEvaluacion)) == 1


def test_resultado_duplicado_da_409(client, h_entrenador, jug):
    _tests(client, h_entrenador, jug, {"SPRINT_20M": [3.1]})
    r = _tests(client, h_entrenador, jug, {"SPRINT_20M": [3.0]})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "pruebas",
    [
        {"SPRINT_20M": [2.0]},  # bajo el mínimo (2.50)
        {"SPRINT_20M": [5.5]},
        {"NAVETTE": [25]},
        {"CMJ": [95]},
        {"CMJ": [30, 31, 32, 33]},  # máx. 3 intentos
        {"SPRINT_20M": [3.1, 3.0]},  # solo 1 intento
        {"INVENTADA": [1]},
    ],
)
def test_valores_invalidos_se_rechazan_con_detalle(client, h_entrenador, jug, pruebas):
    r = _tests(client, h_entrenador, jug, pruebas)
    assert r.status_code == 422
    assert r.json()["detail"]["errores"][0]["id_jugador"] == jug


def test_fecha_futura_se_rechaza(client, h_entrenador, jug):
    manana = (date.today() + timedelta(days=1)).isoformat()
    assert _tests(client, h_entrenador, jug, {"SPRINT_20M": [3.1]}, fecha=manana).status_code == 422


def test_sin_consentimiento_no_se_registra_nada(client, h_entrenador, jugador_payload, db):
    j = _nuevo(
        client, h_entrenador, jugador_payload,
        consentimiento_informado=False, fecha_consentimiento=None,
    )  # fmt: skip
    r = _tests(client, h_entrenador, j, {"SPRINT_20M": [3.1]})
    assert r.status_code == 422 and "consentimiento" in str(r.json()).lower()
    assert db.scalar(select(func.count()).select_from(ResultadoPrueba)) == 0


def test_lote_es_atomico_si_un_jugador_falla(client, h_entrenador, jug, db):
    r = client.post(
        "/jornadas",
        json={
            "fecha": "2026-09-07",
            "momento": "pre",
            "resultados": [
                {"id_jugador": jug, "pruebas": {"SPRINT_20M": [3.1]}},
                {"id_jugador": 9999, "pruebas": {"SPRINT_20M": [3.1]}},
            ],
        },
        headers=h_entrenador,
    )
    assert r.status_code == 422
    assert db.scalar(select(func.count()).select_from(ResultadoPrueba)) == 0
    assert db.scalar(select(func.count()).select_from(JornadaEvaluacion)) == 0


def test_atipicos_se_marcan_y_se_excluyen_hasta_confirmar(client, h_entrenador, equipo):
    valores = [3.1, 3.15, 3.2, 3.25, 3.3, 5.0]  # 5.0 es atípico
    r = client.post(
        "/jornadas",
        json={
            "fecha": "2026-09-07",
            "momento": "pre",
            "resultados": [
                {"id_jugador": j, "pruebas": {"SPRINT_20M": [v]}}
                for j, v in zip(equipo, valores, strict=True)
            ],
        },
        headers=h_entrenador,
    )
    assert r.status_code == 201, r.text
    assert [a["valor"] for a in r.json()["atipicos"]] == [5.0]
    # excluido de los indicadores mientras esté pendiente
    ind = client.get(f"/jugadores/{equipo[-1]}/indicadores", headers=h_entrenador).json()
    assert "SPRINT_20M" not in ind["mediciones"]
    pend = client.get("/resultados/pendientes", headers=h_entrenador).json()
    assert len(pend) == 1 and pend[0]["valor"] == 5.0
    # confirmación en bloque → vuelve a contar
    c = client.patch(
        "/resultados/confirmacion", json={"ids": [pend[0]["id_resultado"]]}, headers=h_entrenador
    )
    assert c.json() == {"confirmados": 1, "ignorados": []}
    ind = client.get(f"/jugadores/{equipo[-1]}/indicadores", headers=h_entrenador).json()
    assert ind["mediciones"]["SPRINT_20M"]["actual"] == 5.0
    # reconfirmar un resultado ya confirmado se ignora
    c = client.patch(
        "/resultados/confirmacion", json={"ids": [pend[0]["id_resultado"]]}, headers=h_entrenador
    )
    assert c.json()["confirmados"] == 0


def test_corregir_un_resultado_lo_confirma(client, h_entrenador, equipo):
    valores = [3.1, 3.15, 3.2, 3.25, 3.3, 5.0]
    client.post(
        "/jornadas",
        json={
            "fecha": "2026-09-07",
            "momento": "pre",
            "resultados": [
                {"id_jugador": j, "pruebas": {"SPRINT_20M": [v]}}
                for j, v in zip(equipo, valores, strict=True)
            ],
        },
        headers=h_entrenador,
    )
    pend = client.get("/resultados/pendientes", headers=h_entrenador).json()[0]
    r = client.put(f"/resultados/{pend['id_resultado']}", json={"valor": 3.4}, headers=h_entrenador)
    assert r.json() == {"id_resultado": pend["id_resultado"], "valor": 3.4, "estado": "confirmado"}
    assert (
        client.put(
            f"/resultados/{pend['id_resultado']}", json={"valor": 9}, headers=h_entrenador
        ).status_code
        == 422
    )


def test_reclasificar_jornada_a_pre_recalcula_la_linea_base(client, h_entrenador, jug):
    _tests(client, h_entrenador, jug, {"CMJ": [40]}, fecha="2026-09-07", momento="seguimiento")
    _tests(client, h_entrenador, jug, {"CMJ": [36]}, fecha="2026-09-14", momento="seguimiento")
    sem = client.get(f"/jugadores/{jug}/carga", headers=h_entrenador).json()["semanas"]
    assert [s["caida_cmj_pct"] for s in sem] == [None, None]  # sin línea base 'pre'
    jornadas = {
        j["fecha"]: j["id_jornada"] for j in client.get("/jornadas", headers=h_entrenador).json()
    }
    r = client.patch(
        f"/jornadas/{jornadas['2026-09-07']}/momento", json={"momento": "pre"}, headers=h_entrenador
    )
    assert r.status_code == 200 and r.json()["cambio"] is True
    sem = client.get(f"/jugadores/{jug}/carga", headers=h_entrenador).json()["semanas"]
    assert [s["caida_cmj_pct"] for s in sem] == [0.0, 10.0]


def test_pasar_a_pre_una_jornada_migrada_advierte(client, h_entrenador, jug, db):
    j = JornadaEvaluacion(
        fecha=date(2026, 8, 21),
        momento="seguimiento",
        observaciones="migracion:21 Agosto|x",
        id_usuario=1,
    )
    db.add(j)
    db.commit()
    url = f"/jornadas/{j.id_jornada}/momento"
    r = client.patch(url, json={"momento": "pre"}, headers=h_entrenador)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "advertencia_migracion"
    db.expire_all()
    assert db.get(JornadaEvaluacion, j.id_jornada).momento == "seguimiento"
    r = client.patch(
        url, json={"momento": "pre", "confirmar_advertencia": True}, headers=h_entrenador
    )
    assert r.status_code == 200
    # otros cambios desde una jornada migrada no piden confirmación
    r = client.patch(url, json={"momento": "post"}, headers=h_entrenador)
    assert r.status_code == 200
    ls = client.get("/jornadas", headers=h_entrenador).json()
    assert ls[0]["origen"] == "migracion" and ls[0]["observaciones"] is None


def test_cambio_de_momento_queda_en_bitacora(client, h_entrenador, jug, db):
    _tests(client, h_entrenador, jug, {"CMJ": [40]}, momento="seguimiento")
    jid = client.get("/jornadas", headers=h_entrenador).json()[0]["id_jornada"]
    client.patch(f"/jornadas/{jid}/momento", json={"momento": "pre"}, headers=h_entrenador)
    assert (
        db.scalar(
            select(func.count())
            .select_from(BitacoraAuditoria)
            .where(
                BitacoraAuditoria.entidad == "jornada_evaluacion",
                BitacoraAuditoria.accion == "modificar",
            )
        )
        == 1
    )


def test_registrar_tests_dispara_la_evaluacion_de_riesgo_en_segundo_plano(
    client, h_entrenador, jug
):
    llamadas = []
    riesgo_hooks.limpiar_callbacks()
    riesgo_hooks.registrar_callback(lambda id_j, origen: llamadas.append((id_j, origen)))
    try:
        _tests(client, h_entrenador, jug, {"SPRINT_20M": [3.1]})
    finally:
        riesgo_hooks.limpiar_callbacks()
    assert llamadas == [(jug, "nuevo_test")]


# ------------------------------------------------------------------ sesiones (RF-08)
def test_sesion_individual_calcula_srpe(client, h_entrenador, jug):
    r = client.post(
        f"/jugadores/{jug}/sesiones",
        json={"fecha": "2026-09-07", "duracion_efectiva_min": 80, "rpe": 7},
        headers=h_entrenador,
    )
    assert r.status_code == 201
    assert r.json()["registros"][0]["srpe"] == 560


def test_sesion_por_lote_comparte_la_sesion(client, h_entrenador, equipo, db):
    cuerpo = {
        "fecha": "2026-09-07",
        "tipo": "entrenamiento",
        "registros": [{"id_jugador": j, "duracion_efectiva_min": 75, "rpe": 6} for j in equipo[:3]],
    }
    r = client.post("/sesiones", json=cuerpo, headers=h_entrenador)
    assert r.status_code == 201 and len(r.json()["registros"]) == 3
    cuerpo["registros"] = [{"id_jugador": equipo[3], "duracion_efectiva_min": 70, "rpe": 5}]
    r2 = client.post("/sesiones", json=cuerpo, headers=h_entrenador)
    assert r2.json()["sesion_nueva"] is False and r2.json()["id_sesion"] == r.json()["id_sesion"]


@pytest.mark.parametrize(
    "extra",
    [{"rpe": 11}, {"rpe": -1}, {"duracion_efectiva_min": 5}, {"duracion_efectiva_min": 300}],
)
def test_rpe_y_duracion_fuera_de_rango(client, h_entrenador, jug, extra):
    cuerpo = {"fecha": "2026-09-07", "duracion_efectiva_min": 80, "rpe": 7, **extra}
    assert (
        client.post(f"/jugadores/{jug}/sesiones", json=cuerpo, headers=h_entrenador).status_code
        == 422
    )


def test_rpe_es_obligatorio_en_sesiones_del_sistema(client, h_entrenador, jug):
    cuerpo = {"fecha": "2026-09-07", "duracion_efectiva_min": 80}
    assert (
        client.post(f"/jugadores/{jug}/sesiones", json=cuerpo, headers=h_entrenador).status_code
        == 422
    )


def test_registro_duplicado_en_la_misma_sesion_da_409(client, h_entrenador, jug):
    cuerpo = {"fecha": "2026-09-07", "duracion_efectiva_min": 80, "rpe": 7}
    client.post(f"/jugadores/{jug}/sesiones", json=cuerpo, headers=h_entrenador)
    assert (
        client.post(f"/jugadores/{jug}/sesiones", json=cuerpo, headers=h_entrenador).status_code
        == 409
    )


def test_rpe_nulo_solo_en_registros_migrados(client, h_entrenador, jug, db):
    from app.models import RegistroSesionJugador, Sesion

    s_mig = Sesion(fecha=date(2026, 8, 31), tipo="entrenamiento", origen="migracion", id_usuario=1)
    db.add(s_mig)
    db.flush()
    reg_mig = RegistroSesionJugador(
        id_sesion=s_mig.id_sesion, id_jugador=jug, duracion_efectiva_min=74, rpe=None
    )
    db.add(reg_mig)
    db.commit()
    # un registro migrado puede quedar sin RPE (rpe: null)…
    r = client.patch(
        f"/registros-sesion/{reg_mig.id_registro}", json={"rpe": None}, headers=h_entrenador
    )
    assert r.status_code == 200 and r.json()["srpe"] is None
    # …y completarse después
    r = client.patch(
        f"/registros-sesion/{reg_mig.id_registro}", json={"rpe": 6}, headers=h_entrenador
    )
    assert r.json()["srpe"] == 444
    # pero uno del sistema no admite RPE nulo
    nuevo = client.post(
        f"/jugadores/{jug}/sesiones",
        json={"fecha": "2026-09-07", "duracion_efectiva_min": 80, "rpe": 7},
        headers=h_entrenador,
    ).json()["registros"][0]["id_registro"]
    r = client.patch(f"/registros-sesion/{nuevo}", json={"rpe": None}, headers=h_entrenador)
    assert r.status_code == 422


def test_registro_migrado_sin_rpe_no_genera_carga(client, h_entrenador, jug, db):
    from app.models import RegistroSesionJugador, Sesion

    s = Sesion(fecha=date(2026, 8, 31), tipo="entrenamiento", origen="migracion", id_usuario=1)
    db.add(s)
    db.flush()
    db.add(
        RegistroSesionJugador(
            id_sesion=s.id_sesion, id_jugador=jug, duracion_efectiva_min=74, rpe=None
        )
    )
    db.commit()
    assert client.get(f"/jugadores/{jug}/carga", headers=h_entrenador).json()["semanas"] == []


def test_edicion_sin_campos_da_422(client, h_entrenador, jug):
    r = client.post(
        f"/jugadores/{jug}/sesiones",
        json={"fecha": "2026-09-07", "duracion_efectiva_min": 80, "rpe": 7},
        headers=h_entrenador,
    )
    idr = r.json()["registros"][0]["id_registro"]
    assert (
        client.patch(f"/registros-sesion/{idr}", json={}, headers=h_entrenador).status_code == 422
    )


# ------------------------------------------------------------------ bienestar
def _bien(**kw):
    return {
        "semana_inicio": "2026-09-07",
        "sueno": 4,
        "fatiga": 3,
        "dolor_muscular": 4,
        "animo": 5,
        **kw,
    }


def test_bienestar_calcula_indice(client, h_entrenador, jug):
    r = client.post(f"/jugadores/{jug}/bienestar", json=_bien(), headers=h_entrenador)
    assert r.status_code == 201 and r.json()["indice_bienestar"] == 16


@pytest.mark.parametrize(
    "extra",
    [{"semana_inicio": "2026-09-08"}, {"sueno": 0}, {"animo": 6}, {"semana_inicio": "2099-01-05"}],
)
def test_bienestar_invalido(client, h_entrenador, jug, extra):
    r = client.post(f"/jugadores/{jug}/bienestar", json=_bien(**extra), headers=h_entrenador)
    assert r.status_code == 422


def test_bienestar_duplicado_y_edicion(client, h_entrenador, jug):
    r = client.post(f"/jugadores/{jug}/bienestar", json=_bien(), headers=h_entrenador)
    assert (
        client.post(f"/jugadores/{jug}/bienestar", json=_bien(), headers=h_entrenador).status_code
        == 409
    )
    nuevo = {"sueno": 1, "fatiga": 1, "dolor_muscular": 1, "animo": 1}
    r2 = client.put(f"/bienestar/{r.json()['id_cuestionario']}", json=nuevo, headers=h_entrenador)
    assert r2.status_code == 200 and r2.json()["indice_bienestar"] == 4


def test_bienestar_sin_consentimiento(client, h_entrenador, jugador_payload):
    j = _nuevo(
        client, h_entrenador, jugador_payload,
        consentimiento_informado=False, fecha_consentimiento=None,
    )  # fmt: skip
    assert (
        client.post(f"/jugadores/{j}/bienestar", json=_bien(), headers=h_entrenador).status_code
        == 422
    )


# ------------------------------------------------------------------ permisos por rol
def test_el_administrador_no_registra_ni_consulta_carga(client, h_admin, h_entrenador, jug):
    assert _tests(client, h_admin, jug, {"SPRINT_20M": [3.1]}).status_code == 403
    assert client.post("/jornadas", json={"fecha": "2026-09-07", "momento": "pre",
                                          "resultados": [{"id_jugador": jug, "pruebas": {"CMJ": [30]}}]},
                       headers=h_admin).status_code == 403  # fmt: skip
    assert client.post(f"/jugadores/{jug}/sesiones",
                       json={"fecha": "2026-09-07", "duracion_efectiva_min": 80, "rpe": 7},
                       headers=h_admin).status_code == 403  # fmt: skip
    assert (
        client.post(f"/jugadores/{jug}/bienestar", json=_bien(), headers=h_admin).status_code == 403
    )
    assert client.post("/sesiones", json={"fecha": "2026-09-07", "registros": [
        {"id_jugador": jug, "duracion_efectiva_min": 80, "rpe": 7}]}, headers=h_admin).status_code == 403  # fmt: skip
    for ruta in (
        f"/jugadores/{jug}/carga",
        f"/jugadores/{jug}/indicadores",
        "/resultados/pendientes",
    ):
        assert client.get(ruta, headers=h_admin).status_code == 403
    assert (
        client.patch("/resultados/confirmacion", json={"ids": [1]}, headers=h_admin).status_code
        == 403
    )
    assert (
        client.patch("/jornadas/1/momento", json={"momento": "pre"}, headers=h_admin).status_code
        == 403
    )


def test_el_administrador_puede_listar_jornadas_y_catalogos(client, h_admin):
    assert client.get("/jornadas", headers=h_admin).status_code == 200
    assert client.get("/catalogos/pruebas", headers=h_admin).status_code == 200
    assert client.get("/catalogos/umbrales", headers=h_admin).json()["ACWR_ALTO"] == 1.5


def test_sin_token_todo_da_401(client):
    for metodo, ruta in [("post", "/jornadas"), ("post", "/sesiones"), ("get", "/jornadas"),
                         ("get", "/jugadores/1/carga"), ("get", "/resultados/pendientes")]:  # fmt: skip
        assert getattr(client, metodo)(ruta).status_code == 401


def test_registros_quedan_auditados(client, h_entrenador, jug, db):
    _tests(client, h_entrenador, jug, {"SPRINT_20M": [3.1]})
    client.post(
        f"/jugadores/{jug}/sesiones",
        json={"fecha": "2026-09-07", "duracion_efectiva_min": 80, "rpe": 7},
        headers=h_entrenador,
    )
    client.post(f"/jugadores/{jug}/bienestar", json=_bien(), headers=h_entrenador)
    client.get(f"/jugadores/{jug}/carga", headers=h_entrenador)
    entidades = {
        (b.accion, b.entidad)
        for b in db.scalars(select(BitacoraAuditoria).where(BitacoraAuditoria.accion != "login"))
    }
    assert {
        ("crear", "resultado_prueba"),
        ("crear", "sesion"),
        ("crear", "cuestionario_bienestar"),
        ("consultar", "indicador_carga_semanal"),
    } <= entidades


def test_jugador_dado_de_baja_no_admite_registros(client, h_entrenador, jug, jugador_payload):
    datos = jugador_payload(activo=False)
    client.put(f"/jugadores/{jug}", json=datos, headers=h_entrenador)
    assert _tests(client, h_entrenador, jug, {"SPRINT_20M": [3.1]}).status_code == 422
