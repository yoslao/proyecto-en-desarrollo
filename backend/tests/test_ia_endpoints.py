"""Rutinas (RF-03) y alertas de riesgo (RF-04) de extremo a extremo, con OpenRouter simulado."""

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.models import (
    BitacoraAuditoria,
    ConfiguracionSistema,
    EvaluacionRiesgo,
    LlamadaIA,
    Rutina,
)
from tests.ia_mock import (
    RIESGO_ALTO,
    RIESGO_BAJO,
    RUTINA_OK,
    ServidorIA,
    respuesta,
    rutina_con,
)


@pytest.fixture(autouse=True)
def modelos(db):
    for clave, valor in (
        ("IA_MODELO_PRINCIPAL", "principal/m"),
        ("IA_MODELO_RESPALDO", "respaldo/m"),
    ):
        db.execute(
            ConfiguracionSistema.__table__.update()
            .where(ConfiguracionSistema.clave == clave)
            .values(valor=valor)
        )
    db.commit()


@pytest.fixture
def jug(client, h_entrenador, jugador_payload):
    return client.post("/jugadores", json=jugador_payload(), headers=h_entrenador).json()[
        "id_jugador"
    ]


def _registrar_tests(client, h, jug, fecha="2026-09-07", momento="pre"):
    return client.post(
        f"/jugadores/{jug}/tests",
        json={
            "fecha": fecha,
            "momento": momento,
            "pruebas": {"SPRINT_20M": [3.2], "CMJ": [38, 40, 39]},
        },
        headers=h,
    )


def _generar(client, h, jug, semanas=2, objetivo="salto"):
    return client.post(
        f"/jugadores/{jug}/rutinas",
        json={"semanas": semanas, "objetivo_prioritario": objetivo},
        headers=h,
    )


def _rutina_generada(client, h, jug):
    t = _generar(client, h, jug).json()
    est = client.get(t["consulta"], headers=h)
    assert est.status_code == 200 and est.json()["estado"] == "completada", est.text
    return est.json()["resultado"]["id_rutina"]


# ------------------------------------------------------------------ generación de rutinas
def test_sin_proveedor_configurado_el_post_da_503(client, h_entrenador, jug):
    r = _generar(client, h_entrenador, jug)
    assert r.status_code == 503


def test_generar_rutina_ok_queda_como_propuesta(client, h_entrenador, jug, db):
    _registrar_tests(client, h_entrenador, jug)
    srv = ServidorIA().instalar()
    r = _generar(client, h_entrenador, jug)
    assert r.status_code == 202 and r.json()["estado"] == "pendiente"
    est = client.get(r.json()["consulta"], headers=h_entrenador).json()
    assert est["estado"] == "completada" and est["progreso"] == 100 and est["paso"] == "listo"
    rutina = db.get(Rutina, est["resultado"]["id_rutina"])
    assert rutina.estado == "propuesta"  # la IA recomienda, el entrenador decide
    assert rutina.semanas == 2 and rutina.objetivo_prioritario == "salto"
    assert rutina.contenido["semanas"][0]["sesiones"][0]["dia"] == "lunes"
    assert rutina.calificacion_pertinencia is None and rutina.id_usuario_revisa is None
    assert srv.modelos_pedidos() == ["principal/m"]


def test_la_llamada_queda_registrada_sin_el_prompt(client, h_entrenador, jug, db):
    _registrar_tests(client, h_entrenador, jug)
    ServidorIA(lambda p, n: respuesta(RUTINA_OK, 1800, 650, 0.0021)).instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    ll = db.get(LlamadaIA, db.get(Rutina, id_rutina).id_llamada)
    assert (ll.tarea, ll.modelo, ll.es_respaldo, ll.version_prompt) == (
        "rutina",
        "principal/m",
        False,
        "rutina_v1",
    )
    assert (ll.tokens_entrada, ll.tokens_salida, float(ll.costo_usd)) == (1800, 650, 0.0021)
    assert ll.valido_primer_intento and ll.num_reintentos == 0 and ll.estado == "exito"
    assert len(ll.hash_entrada) == 64 and ll.es_caso_control is False
    assert not {"prompt", "mensajes", "contexto", "contenido"} & set(
        LlamadaIA.__table__.columns.keys()
    )


def test_hacia_el_proveedor_solo_salen_datos_seudonimizados(
    client, h_entrenador, jug, jugador_payload, db
):
    client.put(
        f"/jugadores/{jug}",
        json=jugador_payload(nombres="Zacarias", apellidos="Pomacahua Yupanqui"),
        headers=h_entrenador,
    )
    _registrar_tests(client, h_entrenador, jug)
    srv = ServidorIA().instalar()
    _generar(client, h_entrenador, jug, objetivo="velocidad")
    enviado = " ".join(srv.cuerpos_crudos).lower()
    for prohibido in ("zacarias", "pomacahua", "yupanqui", "universidad de prueba"):
        assert prohibido not in enviado
    contexto = json.loads(srv.peticiones[0]["messages"][-1]["content"])
    assert contexto["jugador"] == "J-01" and contexto["objetivo_prioritario"] == "velocidad"
    assert contexto["mediciones"]["cmj_cm"]["actual"] == 40
    assert "rutina de 2 semanas" in srv.peticiones[0]["messages"][0]["content"]
    # nada del contexto se guarda en la BD (solo su hash)
    ll = db.scalars(select(LlamadaIA)).one()
    assert ll.hash_entrada != "0" * 64


def test_rutina_que_excede_los_limites_se_rechaza_y_se_regenera(client, h_entrenador, jug, db):
    _registrar_tests(client, h_entrenador, jug)
    respuestas = iter([respuesta(rutina_con(sesiones_semana1=7)), respuesta(RUTINA_OK)])
    srv = ServidorIA(lambda p, n: next(respuestas)).instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    ll = db.get(LlamadaIA, db.get(Rutina, id_rutina).id_llamada)
    assert len(srv.peticiones) == 2 and ll.num_reintentos == 1 and ll.valido_primer_intento is False


def test_rutina_siempre_insegura_da_502_y_no_se_guarda(client, h_entrenador, jug, db):
    _registrar_tests(client, h_entrenador, jug)
    ServidorIA(lambda p, n: respuesta(rutina_con(duracion_min=200))).instalar()
    t = _generar(client, h_entrenador, jug).json()
    r = client.get(t["consulta"], headers=h_entrenador)
    assert r.status_code == 502 and r.json()["estado"] == "error"
    assert db.scalar(select(func.count()).select_from(Rutina)) == 0
    ll = db.scalars(select(LlamadaIA)).one()
    assert ll.estado == "invalida" and ll.tarea == "rutina"


def test_proveedor_caido_da_503_y_se_registra(client, h_entrenador, jug, db):
    _registrar_tests(client, h_entrenador, jug)
    srv = ServidorIA(lambda p, n: httpx.Response(500)).instalar()
    t = _generar(client, h_entrenador, jug).json()
    assert client.get(t["consulta"], headers=h_entrenador).status_code == 503
    assert srv.modelos_pedidos() == ["principal/m", "respaldo/m"]
    assert db.scalars(select(LlamadaIA)).one().estado == "error_proveedor"
    assert db.scalar(select(func.count()).select_from(Rutina)) == 0


def test_modelo_de_respaldo_se_marca_en_la_llamada(client, h_entrenador, jug, db):
    _registrar_tests(client, h_entrenador, jug)
    ServidorIA(
        lambda p, n: httpx.Response(500) if p["model"] == "principal/m" else respuesta(RUTINA_OK)
    ).instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    ll = db.get(LlamadaIA, db.get(Rutina, id_rutina).id_llamada)
    assert ll.es_respaldo is True and ll.modelo == "respaldo/m"
    assert client.get(f"/rutinas/{id_rutina}", headers=h_entrenador).json()["es_respaldo"] is True


def test_jugador_sin_consentimiento_no_genera_rutina(client, h_entrenador, jugador_payload):
    ServidorIA().instalar()
    j = client.post(
        "/jugadores",
        json=jugador_payload(consentimiento_informado=False, fecha_consentimiento=None),
        headers=h_entrenador,
    ).json()["id_jugador"]
    assert _generar(client, h_entrenador, j).status_code == 422


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"semanas": 0, "objetivo_prioritario": "salto"},
        {"semanas": 5, "objetivo_prioritario": "salto"},
        {"semanas": 2, "objetivo_prioritario": "fuerza"},
    ],
)
def test_parametros_invalidos(client, h_entrenador, jug, cuerpo):
    ServidorIA().instalar()
    assert (
        client.post(f"/jugadores/{jug}/rutinas", json=cuerpo, headers=h_entrenador).status_code
        == 422
    )


def test_la_tarea_solo_la_ve_quien_la_creo(client, h_entrenador, h_admin, jug):
    ServidorIA().instalar()
    t = _generar(client, h_entrenador, jug).json()
    assert client.get(t["consulta"], headers=h_admin).status_code == 403
    assert client.get("/tareas/inexistente", headers=h_entrenador).status_code == 404
    assert client.get(t["consulta"]).status_code == 401


def test_progreso_de_la_tarea():
    from app.services import tareas

    t = tareas.crear(1, "rutina")
    assert (t.estado, t.progreso) == ("pendiente", 0)
    tareas.avanzar(t, "consultando_ia", 30)
    assert (t.estado, t.paso, t.progreso) == ("en_curso", "consultando_ia", 30)
    tareas.completar(t, {"id_rutina": 1})
    assert (t.estado, t.progreso) == ("completada", 100)
    assert tareas.obtener(t.id, 1) is t and tareas.obtener(t.id, 2) is None


# ------------------------------------------------------------------ aprobación
def _aprobar(client, h, id_rutina, **kw):
    cuerpo = {
        "decision": "aprobar",
        "calificacion_pertinencia": 4,
        "calificacion_personalizacion": 5,
        **kw,
    }
    return client.patch(f"/rutinas/{id_rutina}/aprobacion", json=cuerpo, headers=h)


def test_aprobar_registra_calificaciones_y_revisor(client, h_entrenador, jug, db):
    ServidorIA().instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    r = _aprobar(client, h_entrenador, id_rutina, comentario="Buena, bajar un poco el volumen")
    assert r.status_code == 200
    j = r.json()
    assert (j["estado"], j["calificacion_pertinencia"], j["calificacion_personalizacion"]) == (
        "aprobada",
        4,
        5,
    )
    fila = db.get(Rutina, id_rutina)
    db.refresh(fila)
    assert fila.id_usuario_revisa is not None and fila.fecha_revision is not None
    assert fila.comentario_revision == "Buena, bajar un poco el volumen"


def test_rechazar_tambien_exige_calificaciones(client, h_entrenador, jug):
    ServidorIA().instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    sin = client.patch(
        f"/rutinas/{id_rutina}/aprobacion", json={"decision": "rechazar"}, headers=h_entrenador
    )
    assert sin.status_code == 422
    r = _aprobar(client, h_entrenador, id_rutina, decision="rechazar", calificacion_pertinencia=1)
    assert r.json()["estado"] == "rechazada" and r.json()["calificacion_pertinencia"] == 1


@pytest.mark.parametrize(
    "extra",
    [{"calificacion_pertinencia": 0}, {"calificacion_personalizacion": 6}, {"decision": "tal vez"}],
)
def test_calificaciones_invalidas(client, h_entrenador, jug, extra):
    ServidorIA().instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    assert _aprobar(client, h_entrenador, id_rutina, **extra).status_code == 422


def test_una_rutina_revisada_no_se_revisa_de_nuevo(client, h_entrenador, jug):
    ServidorIA().instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    assert _aprobar(client, h_entrenador, id_rutina).status_code == 200
    assert _aprobar(client, h_entrenador, id_rutina, decision="rechazar").status_code == 409


def test_el_administrador_no_genera_ni_aprueba_ni_consulta_rutinas(
    client, h_admin, h_entrenador, jug
):
    ServidorIA().instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    assert _generar(client, h_admin, jug).status_code == 403
    assert _aprobar(client, h_admin, id_rutina).status_code == 403
    assert client.get(f"/rutinas/{id_rutina}", headers=h_admin).status_code == 403
    assert client.get(f"/jugadores/{jug}/rutinas", headers=h_admin).status_code == 403


def test_listado_y_detalle_de_rutinas(client, h_entrenador, jug):
    ServidorIA().instalar()
    a = _rutina_generada(client, h_entrenador, jug)
    b = _rutina_generada(client, h_entrenador, jug)
    lista = client.get(f"/jugadores/{jug}/rutinas", headers=h_entrenador).json()
    assert [r["id_rutina"] for r in lista] == [b, a]
    d = client.get(f"/rutinas/{a}", headers=h_entrenador).json()
    assert (
        d["codigo_seudonimo"] == "J-01"
        and d["version_prompt"] == "rutina_v1"
        and d["estado"] == "propuesta"
    )
    assert client.get("/rutinas/9999", headers=h_entrenador).status_code == 404


def test_generacion_y_aprobacion_quedan_en_la_bitacora(client, h_entrenador, jug, db):
    ServidorIA().instalar()
    id_rutina = _rutina_generada(client, h_entrenador, jug)
    _aprobar(client, h_entrenador, id_rutina)
    acciones = {(b.accion, b.entidad) for b in db.scalars(select(BitacoraAuditoria))}
    assert {("crear", "rutina"), ("modificar", "rutina")} <= acciones


# ------------------------------------------------------------------ riesgo (RF-04)
def test_registrar_tests_dispara_la_evaluacion_de_riesgo(client, h_entrenador, jug, db):
    srv = ServidorIA(lambda p, n: respuesta(RIESGO_ALTO)).instalar()
    assert _registrar_tests(client, h_entrenador, jug).status_code == 201
    ev = db.scalars(select(EvaluacionRiesgo)).one()
    assert (ev.nivel_riesgo, ev.es_alerta, ev.estado_revision, ev.origen) == (
        "alto",
        True,
        "pendiente",
        "nuevo_test",
    )
    assert ev.indicadores_afectados[0]["indicador"] == "acwr" and ev.id_usuario_revisa is None
    ll = db.get(LlamadaIA, ev.id_llamada)
    assert ll.tarea == "riesgo" and ll.version_prompt == "riesgo_v1"
    contexto = json.loads(srv.peticiones[0]["messages"][-1]["content"])
    assert (
        contexto["jugador"] == "J-01"
        and "rpe_reciente" in contexto
        and "objetivo_prioritario" not in contexto
    )


def test_sin_proveedor_no_se_evalua_pero_el_registro_funciona(client, h_entrenador, jug, db):
    assert _registrar_tests(client, h_entrenador, jug).status_code == 201
    assert db.scalar(select(func.count()).select_from(EvaluacionRiesgo)) == 0
    assert db.scalar(select(func.count()).select_from(LlamadaIA)) == 0


def test_si_la_ia_falla_el_registro_igual_se_guarda(client, h_entrenador, jug, db):
    ServidorIA(lambda p, n: httpx.Response(500)).instalar()
    assert _registrar_tests(client, h_entrenador, jug).status_code == 201
    assert db.scalar(select(func.count()).select_from(EvaluacionRiesgo)) == 0
    assert db.scalars(select(LlamadaIA)).one().estado == "error_proveedor"


def _con_evaluaciones(client, h, db, niveles):
    """Una evaluación por jugador, con el nivel indicado."""
    respuestas = iter(niveles)
    textos = {
        "bajo": RIESGO_BAJO,
        "alto": RIESGO_ALTO,
        "medio": RIESGO_ALTO.replace('"nivel_riesgo":"alto"', '"nivel_riesgo":"medio"'),
    }
    ServidorIA(lambda p, n: respuesta(textos[next(respuestas)])).instalar()
    ids = []
    for i in range(len(niveles)):
        j = client.post(
            "/jugadores",
            json={
                "nombres": "Nombre",
                "apellidos": f"Ap{i}",
                "id_universidad": 1,
                "id_posicion": 1,
                "consentimiento_informado": True,
                "fecha_consentimiento": "2026-08-01",
            },
            headers=h,
        ).json()["id_jugador"]
        _registrar_tests(client, h, j)
        ids.append(j)
    return ids


def test_alertas_son_solo_riesgo_medio_o_alto_y_el_admin_ve_codigos(
    client, h_entrenador, h_admin, db
):
    _con_evaluaciones(client, h_entrenador, db, ["bajo", "medio", "alto"])
    for h in (h_entrenador, h_admin):
        a = client.get("/alertas", headers=h)
        assert a.status_code == 200
        assert sorted(x["nivel_riesgo"] for x in a.json()) == ["alto", "medio"]
        assert all(x["codigo_seudonimo"].startswith("J-") and x["es_alerta"] for x in a.json())
        assert "nombres" not in a.text and "apellidos" not in a.text and "Ap1" not in a.text


def test_bandeja_de_revision_incluye_el_riesgo_bajo_solo_para_el_entrenador(
    client, h_entrenador, h_admin, db
):
    _con_evaluaciones(client, h_entrenador, db, ["bajo", "medio"])
    r = client.get("/evaluaciones-riesgo/pendientes", headers=h_entrenador).json()
    assert sorted(x["nivel_riesgo"] for x in r) == ["bajo", "medio"]
    assert client.get("/evaluaciones-riesgo/pendientes", headers=h_admin).status_code == 403


def test_confirmar_y_descartar_incluso_el_riesgo_bajo(client, h_entrenador, db):
    _con_evaluaciones(client, h_entrenador, db, ["bajo", "alto"])
    pend = {
        x["nivel_riesgo"]: x["id_evaluacion_riesgo"]
        for x in client.get("/evaluaciones-riesgo/pendientes", headers=h_entrenador).json()
    }
    ok = client.patch(
        f"/evaluaciones-riesgo/{pend['bajo']}/revision",
        json={"decision": "confirmar"},
        headers=h_entrenador,
    )
    assert ok.status_code == 200 and ok.json()["estado_revision"] == "confirmada"
    no = client.patch(
        f"/evaluaciones-riesgo/{pend['alto']}/revision",
        json={"decision": "descartar"},
        headers=h_entrenador,
    )
    assert no.json()["estado_revision"] == "descartada" and no.json()["fecha_revision"] is not None
    assert client.get("/evaluaciones-riesgo/pendientes", headers=h_entrenador).json() == []
    # las alertas descartadas siguen consultables
    assert (
        len(client.get("/alertas", params={"estado": "descartada"}, headers=h_entrenador).json())
        == 1
    )
    assert len(client.get("/alertas", params={"estado": "todas"}, headers=h_entrenador).json()) == 1


def test_una_evaluacion_revisada_no_se_revisa_otra_vez(client, h_entrenador, db):
    _con_evaluaciones(client, h_entrenador, db, ["alto"])
    id_ev = client.get("/alertas", headers=h_entrenador).json()[0]["id_evaluacion_riesgo"]
    url = f"/evaluaciones-riesgo/{id_ev}/revision"
    assert (
        client.patch(url, json={"decision": "confirmar"}, headers=h_entrenador).status_code == 200
    )
    assert (
        client.patch(url, json={"decision": "descartar"}, headers=h_entrenador).status_code == 409
    )
    assert (
        client.patch(
            "/evaluaciones-riesgo/9999/revision",
            json={"decision": "confirmar"},
            headers=h_entrenador,
        ).status_code
        == 404
    )
    assert client.patch(url, json={"decision": "quizas"}, headers=h_entrenador).status_code == 422


def test_el_administrador_no_revisa_alertas(client, h_entrenador, h_admin, db):
    _con_evaluaciones(client, h_entrenador, db, ["alto"])
    id_ev = client.get("/alertas", headers=h_admin).json()[0]["id_evaluacion_riesgo"]
    r = client.patch(
        f"/evaluaciones-riesgo/{id_ev}/revision", json={"decision": "confirmar"}, headers=h_admin
    )
    assert r.status_code == 403
    assert client.get("/alertas").status_code == 401


def test_cierre_de_semana_evalua_a_los_jugadores_con_datos(
    client, h_entrenador, jug, jugador_payload, db
):
    client.post("/jugadores", json=jugador_payload(apellidos="SinDatos"), headers=h_entrenador)
    _registrar_tests(client, h_entrenador, jug)  # sin proveedor: no se evalúa aún
    ServidorIA().instalar()
    r = client.post("/riesgo/cierre-semana", headers=h_entrenador)
    assert r.status_code == 202 and r.json() == {
        "jugadores_programados": 1,
        "omitidos_sin_datos": 1,
    }
    ev = db.scalars(select(EvaluacionRiesgo)).one()
    assert ev.origen == "cierre_semana"


def test_cierre_de_semana_sin_proveedor_da_503_y_solo_el_entrenador(client, h_entrenador, h_admin):
    assert client.post("/riesgo/cierre-semana", headers=h_entrenador).status_code == 503
    assert client.post("/riesgo/cierre-semana", headers=h_admin).status_code == 403
