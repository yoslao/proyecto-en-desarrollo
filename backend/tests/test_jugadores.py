from datetime import date

from sqlalchemy import select

from app.models import BitacoraAuditoria, Jugador


def test_crear_asigna_codigos_correlativos(client, h_entrenador, jugador_payload):
    a = client.post("/jugadores", json=jugador_payload(apellidos="Uno"), headers=h_entrenador)
    b = client.post("/jugadores", json=jugador_payload(apellidos="Dos"), headers=h_entrenador)
    assert (a.status_code, b.status_code) == (201, 201)
    assert (a.json()["codigo_seudonimo"], b.json()["codigo_seudonimo"]) == ("J-01", "J-02")


def test_administrador_puede_registrar_y_ve_nombres(client, h_admin, jugador_payload):
    r = client.post("/jugadores", json=jugador_payload(), headers=h_admin)
    assert r.status_code == 201
    assert r.json()["nombres"] == "Prueba"


def test_edad_se_calcula_y_no_se_almacena(client, h_entrenador, jugador_payload):
    r = client.post(
        "/jugadores", json=jugador_payload(fecha_nacimiento="2003-05-10"), headers=h_entrenador
    )
    nac, hoy = date(2003, 5, 10), date.today()
    esperada = hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))
    assert r.json()["edad"] == esperada
    assert "edad" not in Jugador.__table__.columns


def test_sin_fecha_de_nacimiento_edad_es_nula(client, h_entrenador, jugador_payload):
    r = client.post("/jugadores", json=jugador_payload(fecha_nacimiento=None), headers=h_entrenador)
    assert r.status_code == 201
    assert r.json()["edad"] is None


def test_consentimiento_requiere_fecha(client, h_entrenador, jugador_payload):
    r = client.post(
        "/jugadores",
        json=jugador_payload(consentimiento_informado=True, fecha_consentimiento=None),
        headers=h_entrenador,
    )
    assert r.status_code == 422


def test_sin_consentimiento_se_registra_como_no_consentido(client, h_entrenador, jugador_payload):
    r = client.post(
        "/jugadores",
        json=jugador_payload(consentimiento_informado=False, fecha_consentimiento=None),
        headers=h_entrenador,
    )
    assert r.status_code == 201
    assert r.json()["consentimiento_informado"] is False


def test_nombre_duplicado_ignora_tildes_y_mayusculas(client, h_entrenador, jugador_payload):
    client.post("/jugadores", json=jugador_payload(nombres="José"), headers=h_entrenador)
    r = client.post("/jugadores", json=jugador_payload(nombres="JOSE"), headers=h_entrenador)
    assert r.status_code == 409


def test_catalogos_inexistentes_dan_422(client, h_entrenador, jugador_payload):
    r = client.post("/jugadores", json=jugador_payload(id_posicion=99), headers=h_entrenador)
    assert r.status_code == 422
    r = client.post("/jugadores", json=jugador_payload(id_universidad=99), headers=h_entrenador)
    assert r.status_code == 422


def test_edad_fuera_de_rango_se_rechaza(client, h_entrenador, jugador_payload):
    r = client.post(
        "/jugadores", json=jugador_payload(fecha_nacimiento="2020-01-01"), headers=h_entrenador
    )
    assert r.status_code == 422


def test_actualizar_no_cambia_el_codigo(client, h_entrenador, jugador_payload):
    j = client.post("/jugadores", json=jugador_payload(), headers=h_entrenador).json()
    r = client.put(
        f"/jugadores/{j['id_jugador']}",
        json=jugador_payload(apellidos="Editado", id_posicion=2),
        headers=h_entrenador,
    )
    assert r.status_code == 200
    assert r.json()["apellidos"] == "Editado"
    assert r.json()["posicion"] == "punta receptor"
    assert r.json()["codigo_seudonimo"] == j["codigo_seudonimo"]


def test_busqueda_y_paginacion(client, h_entrenador, jugador_payload):
    for ap in ["Alfa", "Beta", "Gamma"]:
        client.post("/jugadores", json=jugador_payload(apellidos=ap), headers=h_entrenador)
    r = client.get("/jugadores", params={"q": "et", "limite": 10}, headers=h_entrenador).json()
    assert r["total"] == 1 and r["items"][0]["apellidos"] == "Beta"
    r = client.get("/jugadores", params={"limite": 2}, headers=h_entrenador).json()
    assert r["total"] == 3 and len(r["items"]) == 2


def test_jugador_inexistente_da_404(client, h_entrenador):
    assert client.get("/jugadores/999", headers=h_entrenador).status_code == 404


def test_acciones_sobre_jugadores_quedan_auditadas(client, h_entrenador, jugador_payload, db):
    j = client.post("/jugadores", json=jugador_payload(), headers=h_entrenador).json()
    client.get(f"/jugadores/{j['id_jugador']}", headers=h_entrenador)
    client.put(
        f"/jugadores/{j['id_jugador']}",
        json=jugador_payload(apellidos="Otro"),
        headers=h_entrenador,
    )
    filas = db.scalars(select(BitacoraAuditoria).where(BitacoraAuditoria.entidad == "jugador"))
    assert [b.accion for b in filas] == ["crear", "consultar", "modificar"]
