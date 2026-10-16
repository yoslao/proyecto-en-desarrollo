import pytest

# (método, ruta, cuerpo, roles permitidos)
CASOS = [
    ("GET", "/jugadores", None, {"administrador", "entrenador"}),
    ("GET", "/catalogos/posiciones", None, {"administrador", "entrenador"}),
    ("GET", "/catalogos/universidades", None, {"administrador", "entrenador"}),
    ("POST", "/catalogos/universidades", {"nombre": "Otra Universidad"}, {"administrador"}),
    ("GET", "/auth/yo", None, {"administrador", "entrenador"}),
]


@pytest.mark.parametrize("metodo,ruta,cuerpo,permitidos", CASOS)
@pytest.mark.parametrize("rol", ["administrador", "entrenador"])
def test_rbac_en_backend(client, h_admin, h_entrenador, metodo, ruta, cuerpo, permitidos, rol):
    headers = h_admin if rol == "administrador" else h_entrenador
    r = client.request(metodo, ruta, json=cuerpo, headers=headers)
    if rol in permitidos:
        assert r.status_code < 300, r.text
    else:
        assert r.status_code == 403


@pytest.mark.parametrize("metodo,ruta,cuerpo,permitidos", CASOS)
def test_sin_token_nada_es_accesible(client, metodo, ruta, cuerpo, permitidos):
    assert client.request(metodo, ruta, json=cuerpo).status_code == 401
