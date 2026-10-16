from datetime import UTC, datetime, timedelta

from jose import jwt
from sqlalchemy import select

from app.core.config import get_settings
from app.models import BitacoraAuditoria, Usuario
from tests.conftest import CLAVE_ENTRENADOR

ENTRENADOR = "entrenador@test.pe"


def _login(client, clave):
    return client.post("/auth/login", json={"correo": ENTRENADOR, "contrasena": clave})


def test_login_correcto_devuelve_token_de_30_min(client):
    r = _login(client, CLAVE_ENTRENADOR)
    assert r.status_code == 200
    assert r.json()["rol"] == "entrenador"
    assert r.json()["expira_en_s"] == 30 * 60


def test_contrasena_incorrecta_y_correo_inexistente_dan_mismo_mensaje(client):
    a = _login(client, "x" * 12)
    b = client.post("/auth/login", json={"correo": "nadie@test.pe", "contrasena": "x" * 12})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_bloqueo_tras_5_intentos_fallidos(client, db):
    for _ in range(5):
        assert _login(client, "mala-clave").status_code == 401
    # Ni siquiera la contraseña correcta entra mientras dura el bloqueo.
    assert _login(client, CLAVE_ENTRENADOR).status_code == 423
    u = db.scalar(select(Usuario).where(Usuario.correo == ENTRENADOR))
    assert u.bloqueado_hasta is not None


def test_bloqueo_expirado_permite_entrar(client, db):
    for _ in range(5):
        _login(client, "mala-clave")
    u = db.scalar(select(Usuario).where(Usuario.correo == ENTRENADOR))
    u.bloqueado_hasta = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    assert _login(client, CLAVE_ENTRENADOR).status_code == 200


def test_login_exitoso_resetea_intentos(client, db):
    for _ in range(3):
        _login(client, "mala-clave")
    _login(client, CLAVE_ENTRENADOR)
    u = db.scalar(select(Usuario).where(Usuario.correo == ENTRENADOR))
    assert u.intentos_fallidos == 0


def test_login_y_fallos_quedan_en_bitacora(client, db):
    _login(client, "mala-clave")
    _login(client, CLAVE_ENTRENADOR)
    filas = db.scalars(select(BitacoraAuditoria).order_by(BitacoraAuditoria.id_bitacora))
    assert [b.accion for b in filas] == ["login_fallido", "login"]


def test_usuario_inactivo_no_entra(client, db):
    u = db.scalar(select(Usuario).where(Usuario.correo == ENTRENADOR))
    u.activo = False
    db.commit()
    assert _login(client, CLAVE_ENTRENADOR).status_code == 401


def test_token_invalido_o_ausente_da_401(client):
    assert client.get("/jugadores").status_code == 401
    assert client.get("/jugadores", headers={"Authorization": "Bearer basura"}).status_code == 401


def _token(clave, delta):
    return jwt.encode(
        {"sub": "1", "rol": "administrador", "exp": datetime.now(UTC) + delta},
        clave,
        algorithm="HS256",
    )


def test_token_expirado_da_401(client):
    t = _token(get_settings().jwt_secret_key, timedelta(minutes=-1))
    assert client.get("/jugadores", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_token_firmado_con_otra_clave_da_401(client):
    t = _token("otra-clave", timedelta(minutes=5))
    assert client.get("/jugadores", headers={"Authorization": f"Bearer {t}"}).status_code == 401
