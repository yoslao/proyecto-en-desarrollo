"""Pruebas contra una MariaDB real (base `voleyia_test`), creada con la migración de Alembic."""

import os
from pathlib import Path

from dotenv import dotenv_values

RAIZ = Path(__file__).resolve().parents[2]
_env = dotenv_values(RAIZ / ".env")
os.environ["DB_NAME"] = "voleyia_test"  # antes de importar la app

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Base, get_engine, nueva_sesion  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Universidad  # noqa: E402
from app.seed import crear_usuario, sembrar_catalogos  # noqa: E402

CLAVE_ADMIN = "clave-admin-de-prueba"
CLAVE_ENTRENADOR = "clave-entrenador-de-prueba"


@pytest.fixture(scope="session", autouse=True)
def base_de_datos():
    s = get_settings()
    root = create_engine(
        f"mysql+pymysql://root:{_env['DB_ROOT_PASSWORD']}@{s.db_host}:{s.db_port}/",
        isolation_level="AUTOCOMMIT",
    )
    with root.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS voleyia_test"))
        c.execute(
            text("CREATE DATABASE voleyia_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )
        c.execute(text(f"GRANT ALL ON voleyia_test.* TO '{s.db_user}'@'%'"))
    cfg = Config(str(RAIZ / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "backend" / "alembic"))
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def datos_base(base_de_datos):
    """Vacía todas las tablas y deja catálogos + un administrador y un entrenador."""
    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for t in reversed(Base.metadata.sorted_tables):
            c.execute(text(f"TRUNCATE TABLE `{t.name}`"))
        c.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    with nueva_sesion() as db:
        sembrar_catalogos(db)
        crear_usuario(db, "admin@test.pe", "Admin Test", "administrador", CLAVE_ADMIN)
        crear_usuario(db, "entrenador@test.pe", "Entrenador Test", "entrenador", CLAVE_ENTRENADOR)
        db.add(Universidad(nombre="Universidad de Prueba", siglas="UDP"))
        db.commit()


@pytest.fixture(autouse=True)
def sin_proveedor_real():
    """Ninguna prueba debe llamar a OpenRouter: cliente sin clave (cada prueba puede poner un mock)."""
    from app.ai import proveedor
    from app.ai.openrouter_client import OpenRouterClient
    from app.services import tareas

    proveedor.fijar_cliente(OpenRouterClient(""))
    tareas.limpiar()
    yield
    proveedor.fijar_cliente(None)


@pytest.fixture
def client():
    return TestClient(app)


def _login(client, correo, clave):
    r = client.post("/auth/login", json={"correo": correo, "contrasena": clave})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def h_admin(client):
    return _login(client, "admin@test.pe", CLAVE_ADMIN)


@pytest.fixture
def h_entrenador(client):
    return _login(client, "entrenador@test.pe", CLAVE_ENTRENADOR)


@pytest.fixture
def db():
    with nueva_sesion() as s:
        yield s


@pytest.fixture
def jugador_payload():
    def _f(**kw):
        d = {
            "nombres": "Prueba",
            "apellidos": "Sintetico Uno",
            "fecha_nacimiento": "2003-05-10",
            "id_universidad": 1,
            "id_posicion": 1,
            "consentimiento_informado": True,
            "fecha_consentimiento": "2026-08-01",
        }
        d.update(kw)
        return d

    return _f
