"""K-means: núcleo numérico, etiquetas, ejecución contra la BD y permisos."""

import numpy as np
import pytest
from sqlalchemy import func, select

from app.analytics import clustering as km
from app.models import (
    AsignacionCluster,
    BitacoraAuditoria,
    ConfiguracionSistema,
    EjecucionClustering,
)

# ------------------------------------------------------------------ núcleo puro
VARS = ["SPRINT_20M", "NAVETTE", "SALTO_AI"]
SIGNOS = [-1, 1, 1]  # sprint: menos tiempo es mejor


def _datos():
    i = np.arange(6)
    rapidos = np.column_stack([3.00 + 0.02 * i, 12 + 0.2 * i, 44 + 0.5 * i])
    lentos = np.column_stack([3.50 + 0.02 * i, 7 + 0.2 * i, 30 + 0.5 * i])
    return np.vstack([lentos, rapidos])  # lentos primero a propósito


def test_k_validos_exige_2_a_n_menos_1():
    assert km.k_validos([2, 3, 4], 3) == [2]
    assert km.k_validos([2, 3, 4], 24) == [2, 3, 4]
    assert km.k_validos([1, 2, 30], 5) == [2]


def test_ajustar_elige_el_k_de_mayor_silueta():
    r = km.ajustar(_datos(), SIGNOS, [2, 3, 4], random_state=42)
    assert r.k == 2 and r.silueta > 0.5
    assert r.silueta == max(r.silueta_por_k.values())
    assert set(r.silueta_por_k) == {2, 3, 4}
    assert sorted(r.tamanos) == [6, 6]


def test_clusters_renumerados_de_mejor_a_peor_con_sprint_invertido():
    r = km.ajustar(_datos(), SIGNOS, [2], random_state=42)
    # los 6 últimos son los rápidos / mejores → deben quedar en el cluster 0
    assert set(r.cluster[6:]) == {0} and set(r.cluster[:6]) == {1}
    assert r.centroides[0][0] < r.centroides[1][0]  # centroide 0 = menor tiempo


def test_ajustar_es_reproducible():
    a = km.ajustar(_datos(), SIGNOS, [2, 3, 4], random_state=42)
    b = km.ajustar(_datos(), SIGNOS, [2, 3, 4], random_state=42)
    assert a.k == b.k and (a.cluster == b.cluster).all() and a.silueta == b.silueta


def test_ajustar_sin_k_valido_falla_claro():
    with pytest.raises(ValueError):
        km.ajustar(np.array([[1.0, 2.0], [2.0, 3.0]]), [1, 1], [2, 3], 42)


def test_ajustar_datos_identicos_no_forma_grupos():
    with pytest.raises(ValueError):
        km.ajustar(np.ones((6, 2)), [1, 1], [2, 3], 42)


@pytest.mark.parametrize(
    "z,esperada",
    [
        ({"SPRINT_20M": 0.9, "NAVETTE": -0.8}, "Rápido / resistencia baja"),
        ({"SPRINT_20M": -1.0, "NAVETTE": 1.2}, "Lento / resistencia alta"),
        ({"SPRINT_20M": 0.1, "NAVETTE": -0.2, "CMJ": 0.0}, "Perfil medio"),
        ({"SPRINT_20M": 1, "NAVETTE": 1, "SALTO_AI": 1}, "Rápido / resistencia alta / salto alto"),
        ({"NAVETTE": -1.5}, "Resistencia baja"),
        # varias variables de salto se promedian en una sola dimensión
        ({"CMJ": 1.0, "SJ": -1.0, "SPRINT_20M": 0.8}, "Rápido"),
        ({"CMJ": 1.0, "SJ": 0.6, "SPRINT_20M": 0.0}, "Salto alto"),
    ],
)
def test_etiquetas_legibles(z, esperada):
    assert km.etiquetar(z) == esperada


def test_advierte_de_grupos_con_un_solo_jugador():
    assert km.advertencias_de_tamano([4, 3, 1], ["A", "B", "C"]) == [
        "El grupo «C» tiene un solo jugador: es un perfil individual, no un grupo."
    ]
    assert km.advertencias_de_tamano([6, 6], ["A", "B"]) == []


def test_etiquetas_repetidas_se_distinguen():
    assert km.etiquetas_unicas(["A", "B", "A"]) == ["A (grupo 1)", "B", "A (grupo 3)"]
    assert km.etiquetas_unicas(["A", "B"]) == ["A", "B"]


# ------------------------------------------------------------------ contra la BD
def _equipo(client, h, jugador_payload, n_por_grupo=6):
    ids = []
    for i in range(2 * n_por_grupo):
        r = client.post("/jugadores", json=jugador_payload(apellidos=f"Km{i:02d}"), headers=h)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id_jugador"])
    return ids


def _registrar(client, h, ids, fecha="2026-09-07", momento="pre", valores=None):
    """Primera mitad = rápidos/mejores, segunda mitad = lentos/peores."""
    n = len(ids) // 2
    resultados = []
    for pos, id_j in enumerate(ids):
        i = pos % n
        if pos < n:
            p = {
                "SPRINT_20M": [3.00 + 0.02 * i],
                "NAVETTE": [12 + 0.2 * i],
                "SALTO_AI": [44 + 0.5 * i],
            }
        else:
            p = {
                "SPRINT_20M": [3.50 + 0.02 * i],
                "NAVETTE": [7 + 0.2 * i],
                "SALTO_AI": [30 + 0.5 * i],
            }
        if valores:
            p = valores(id_j, pos, p)
        if p:
            resultados.append({"id_jugador": id_j, "pruebas": p})
    r = client.post(
        "/jornadas",
        json={"fecha": fecha, "momento": momento, "resultados": resultados},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def equipo(client, h_entrenador, jugador_payload):
    ids = _equipo(client, h_entrenador, jugador_payload)
    _registrar(client, h_entrenador, ids)
    return ids


def _ejecutar(client, h, **cuerpo):
    return client.post("/analitica/clustering", json={"modo": "exploratorio", **cuerpo}, headers=h)


def test_clustering_exploratorio_end_to_end(client, h_admin, equipo):
    r = _ejecutar(client, h_admin)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["k"] == 2 and c["silueta"] > 0.5 and c["silueta_baja"] is False
    assert c["variables"] == ["SPRINT_20M", "NAVETTE", "SALTO_AI"]
    assert [x["etiqueta"] for x in c["clusters"]] == [
        "Rápido / resistencia alta / salto alto",
        "Lento / resistencia baja / salto bajo",
    ]
    assert len(c["asignaciones"]) == 12 and c["sin_asignar"] == []
    por_cluster = {0: set(), 1: set()}
    for a in c["asignaciones"]:
        por_cluster[a["cluster"]].add(a["id_jugador"])
    assert por_cluster[0] == set(equipo[:6]) and por_cluster[1] == set(equipo[6:])
    assert any("perfil descriptivo" in a for a in c["advertencias"])
    assert any("exploratorio" in a.lower() for a in c["advertencias"])


def test_informa_las_jornadas_usadas_por_jugador(client, h_admin, equipo):
    c = _ejecutar(client, h_admin).json()
    usadas = c["asignaciones"][0]["jornadas_usadas"]
    assert set(usadas) == {"SPRINT_20M", "NAVETTE", "SALTO_AI"}
    assert usadas["SPRINT_20M"]["fecha"] == "2026-09-07"


def test_toma_la_ultima_medicion_dentro_del_rango(client, h_entrenador, h_admin, jugador_payload):
    ids = _equipo(client, h_entrenador, jugador_payload)
    # 1.ª jornada: velocidad y resistencia; 2.ª y 3.ª: salto en fechas distintas (como el Excel)
    _registrar(
        client, h_entrenador, ids, fecha="2026-09-01",
        valores=lambda j, pos, p: {"SPRINT_20M": p["SPRINT_20M"], "NAVETTE": p["NAVETTE"]},
    )  # fmt: skip

    def peores(j, pos, p):
        return {"SALTO_AI": [x - 5 for x in p["SALTO_AI"]]}

    def solo_salto(j, pos, p):
        return {"SALTO_AI": p["SALTO_AI"]}

    _registrar(client, h_entrenador, ids, fecha="2026-09-03", momento="seguimiento", valores=peores)
    _registrar(
        client, h_entrenador, ids, fecha="2026-09-10", momento="seguimiento", valores=solo_salto
    )  # salto original

    todo = _ejecutar(client, h_admin).json()
    assert {a["jornadas_usadas"]["SALTO_AI"]["fecha"] for a in todo["asignaciones"]} == {
        "2026-09-10"
    }
    assert {a["jornadas_usadas"]["SPRINT_20M"]["fecha"] for a in todo["asignaciones"]} == {
        "2026-09-01"
    }

    acotado = _ejecutar(client, h_admin, fecha_desde="2026-09-01", fecha_hasta="2026-09-05").json()
    assert {a["jornadas_usadas"]["SALTO_AI"]["fecha"] for a in acotado["asignaciones"]} == {
        "2026-09-03"
    }
    assert acotado["fecha_desde"] == "2026-09-01" and acotado["fecha_hasta"] == "2026-09-05"


def test_rango_sin_datos_da_422(client, h_admin, equipo):
    r = _ejecutar(client, h_admin, fecha_desde="2026-01-01", fecha_hasta="2026-01-31")
    assert r.status_code == 422
    assert r.json()["detail"]["jugadores_sin_dato_por_variable"]["SPRINT_20M"] == 12


def test_rango_invertido_da_422(client, h_admin, equipo):
    assert (
        _ejecutar(client, h_admin, fecha_desde="2026-09-10", fecha_hasta="2026-09-01").status_code
        == 422
    )


def test_principal_sin_cmj_ni_sj_explica_que_falta(client, h_admin, equipo):
    r = client.post("/analitica/clustering", json={"modo": "principal"}, headers=h_admin)
    assert r.status_code == 422
    faltan = r.json()["detail"]["jugadores_sin_dato_por_variable"]
    assert faltan["CMJ"] == 12 and faltan["SJ"] == 12 and faltan["SPRINT_20M"] == 0


def test_principal_funciona_con_cmj_y_sj(client, h_entrenador, h_admin, jugador_payload):
    ids = _equipo(client, h_entrenador, jugador_payload)
    _registrar(
        client, h_entrenador, ids,
        valores=lambda j, pos, p: {
            "SPRINT_20M": p["SPRINT_20M"], "NAVETTE": p["NAVETTE"],
            "CMJ": [x - 4 for x in (p["SALTO_AI"] * 3)][:3], "SJ": [x - 8 for x in p["SALTO_AI"]],
        },
    )  # fmt: skip
    c = client.post("/analitica/clustering", json={"modo": "principal"}, headers=h_admin).json()
    assert c["variables"] == ["SPRINT_20M", "NAVETTE", "CMJ", "SJ"] and c["k"] == 2
    assert all(a["jornadas_usadas"]["CMJ"]["fecha"] == "2026-09-07" for a in c["asignaciones"])


def test_jugadores_con_datos_incompletos_quedan_sin_asignar(
    client, h_entrenador, h_admin, jugador_payload
):
    ids = _equipo(client, h_entrenador, jugador_payload)
    _registrar(
        client, h_entrenador, ids,
        valores=lambda j, pos, p: {k: v for k, v in p.items() if not (j == ids[3] and k == "NAVETTE")},
    )  # fmt: skip
    c = _ejecutar(client, h_admin).json()
    assert len(c["asignaciones"]) == 11
    assert c["sin_asignar"] == [
        {"id_jugador": ids[3], "codigo_seudonimo": "J-04", "faltantes": ["NAVETTE"]}
    ]
    assert any("sin asignar" in a for a in c["advertencias"])


def test_menos_de_3_jugadores_completos_da_422(client, h_entrenador, h_admin, jugador_payload):
    ids = _equipo(client, h_entrenador, jugador_payload, n_por_grupo=1)  # 2 jugadores
    _registrar(client, h_entrenador, ids)
    assert _ejecutar(client, h_admin).status_code == 422


def test_silueta_baja_se_advierte_segun_la_configuracion(client, h_admin, equipo, db):
    db.execute(
        ConfiguracionSistema.__table__.update()
        .where(ConfiguracionSistema.clave == "KMEANS_SILUETA_MIN")
        .values(valor="0.99")
    )
    db.commit()
    c = _ejecutar(client, h_admin).json()
    assert c["silueta_baja"] is True and c["silueta_minima"] == 0.99
    assert "Índice de silueta" in c["advertencias"][0]


def test_las_variables_salen_de_la_configuracion(client, h_admin, equipo, db):
    db.execute(
        ConfiguracionSistema.__table__.update()
        .where(ConfiguracionSistema.clave == "KMEANS_VARIABLES_EXPLORATORIO")
        .values(valor="SPRINT_20M, navette")
    )
    db.commit()
    assert _ejecutar(client, h_admin).json()["variables"] == ["SPRINT_20M", "NAVETTE"]


def test_variable_desconocida_en_la_configuracion_da_422(client, h_admin, equipo, db):
    db.execute(
        ConfiguracionSistema.__table__.update()
        .where(ConfiguracionSistema.clave == "KMEANS_VARIABLES_EXPLORATORIO")
        .values(valor="SPRINT_20M,INVENTADA")
    )
    db.commit()
    assert _ejecutar(client, h_admin).status_code == 422


def test_atipicos_pendientes_no_entran_al_clustering(
    client, h_entrenador, h_admin, jugador_payload
):
    ids = _equipo(client, h_entrenador, jugador_payload)
    _registrar(
        client, h_entrenador, ids,
        valores=lambda j, pos, p: {**p, "SALTO_AI": [89.0]} if j == ids[0] else p,
    )  # fmt: skip
    c = _ejecutar(client, h_admin).json()
    # su salto es un atípico pendiente → excluido → queda sin asignar
    assert [s["faltantes"] for s in c["sin_asignar"]] == [["SALTO_AI"]]


def test_se_persiste_ejecucion_y_asignaciones(client, h_admin, equipo, db):
    c = _ejecutar(client, h_admin).json()
    e = db.get(EjecucionClustering, c["id_ejecucion"])
    assert e.k == 2 and float(e.silueta) == pytest.approx(c["silueta"], abs=0.001)
    assert e.variables == ["SPRINT_20M", "NAVETTE", "SALTO_AI"]
    assert e.diagnostico["random_state"] == 42
    assert (
        db.scalar(
            select(func.count())
            .select_from(AsignacionCluster)
            .where(AsignacionCluster.id_ejecucion == e.id_ejecucion)
        )
        == 12
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(BitacoraAuditoria)
            .where(
                BitacoraAuditoria.entidad == "ejecucion_clustering",
                BitacoraAuditoria.accion == "crear",
            )
        )
        == 1
    )


def test_resultado_reproducible_entre_ejecuciones(client, h_admin, equipo):
    a = _ejecutar(client, h_admin).json()
    b = _ejecutar(client, h_admin).json()

    def firma(c):
        return [
            (x["id_jugador"], x["cluster"], x["distancia_centroide"]) for x in c["asignaciones"]
        ]

    assert a["silueta"] == b["silueta"] and firma(a) == firma(b)
    assert a["id_ejecucion"] != b["id_ejecucion"]


def test_ultimo_e_historial(client, h_admin, h_entrenador, equipo):
    assert client.get("/analitica/clustering/ultimo", headers=h_admin).status_code == 404
    _ejecutar(client, h_admin)
    segundo = _ejecutar(client, h_admin).json()
    ultimo = client.get("/analitica/clustering/ultimo", headers=h_entrenador)
    assert ultimo.status_code == 200 and ultimo.json()["id_ejecucion"] == segundo["id_ejecucion"]
    hist = client.get("/analitica/clustering", headers=h_admin).json()
    assert [h["id_ejecucion"] for h in hist][0] == segundo["id_ejecucion"] and len(hist) == 2
    assert hist[0]["n_asignados"] == 12


def test_cluster_del_jugador(client, h_admin, h_entrenador, equipo):
    assert client.get(f"/jugadores/{equipo[0]}/cluster", headers=h_entrenador).status_code == 404
    _ejecutar(client, h_admin)
    r = client.get(f"/jugadores/{equipo[0]}/cluster", headers=h_entrenador)
    assert r.status_code == 200
    assert r.json()["cluster"] == 0 and r.json()["etiqueta"].startswith("Rápido")
    assert (
        client.get(f"/jugadores/{equipo[-1]}/cluster", headers=h_entrenador).json()["cluster"] == 1
    )
    assert client.get("/jugadores/9999/cluster", headers=h_entrenador).status_code == 404


def test_cluster_se_actualiza_con_la_ultima_ejecucion(client, h_admin, h_entrenador, equipo):
    _ejecutar(client, h_admin)
    _ejecutar(client, h_admin, fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
    r = client.get(f"/jugadores/{equipo[0]}/cluster", headers=h_entrenador).json()
    assert (
        r["id_ejecucion"]
        == client.get("/analitica/clustering/ultimo", headers=h_admin).json()["id_ejecucion"]
    )


# ------------------------------------------------------------------ permisos
def test_solo_el_administrador_ejecuta_el_clustering(client, h_entrenador, equipo):
    assert _ejecutar(client, h_entrenador).status_code == 403
    assert client.post("/analitica/clustering", json={}).status_code == 401


def test_el_administrador_solo_ve_codigos_no_nombres(client, h_admin, equipo):
    c = _ejecutar(client, h_admin)
    texto = c.text
    assert "Km0" not in texto and "nombres" not in texto and "apellidos" not in texto
    assert c.json()["asignaciones"][0]["codigo_seudonimo"].startswith("J-")


def test_el_administrador_no_consulta_el_cluster_de_un_jugador(client, h_admin, equipo):
    assert client.get(f"/jugadores/{equipo[0]}/cluster", headers=h_admin).status_code == 403
    assert client.get("/analitica/clustering/ultimo").status_code == 401
