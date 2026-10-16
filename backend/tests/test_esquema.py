from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import (
    BitacoraAuditoria,
    CuestionarioBienestar,
    EvaluacionRiesgo,
    Jugador,
    LlamadaIA,
    MedicionAntropometrica,
    RegistroSesionJugador,
    Rutina,
    Sesion,
    Usuario,
)


@pytest.fixture
def jugador(db):
    j = Jugador(
        codigo_seudonimo="J-01", nombres="A", apellidos="B", id_universidad=1, id_posicion=1
    )
    db.add(j)
    db.commit()
    return j


def _usuario(db):
    return db.query(Usuario).first()


def _llamada(db, tarea):
    ll = LlamadaIA(
        tarea=tarea,
        modelo="m",
        version_prompt="v1",
        latencia_ms=1,
        estado="exito",
        hash_entrada="0" * 64,
    )
    db.add(ll)
    db.flush()
    return ll


def _sesion(db, origen="sistema"):
    s = Sesion(
        fecha=date(2026, 9, 1),
        tipo="entrenamiento",
        origen=origen,
        id_usuario=_usuario(db).id_usuario,
    )
    db.add(s)
    db.flush()
    return s


def test_columnas_generadas_bienestar_y_srpe(db, jugador):
    db.add(
        CuestionarioBienestar(
            id_jugador=jugador.id_jugador,
            semana_inicio=date(2026, 8, 31),
            sueno=4,
            fatiga=3,
            dolor_muscular=2,
            animo=5,
        )
    )
    s = _sesion(db)
    db.add(
        RegistroSesionJugador(
            id_sesion=s.id_sesion, id_jugador=jugador.id_jugador, duracion_efectiva_min=90, rpe=7
        )
    )
    db.commit()
    assert db.query(CuestionarioBienestar).one().indice_bienestar == 14
    assert db.query(RegistroSesionJugador).one().srpe == 630


def test_srpe_nulo_si_rpe_nulo(db, jugador):
    s = _sesion(db, origen="migracion")
    db.add(
        RegistroSesionJugador(
            id_sesion=s.id_sesion, id_jugador=jugador.id_jugador, duracion_efectiva_min=74, rpe=None
        )
    )
    db.commit()
    assert db.query(RegistroSesionJugador).one().srpe is None


def test_bienestar_exige_lunes(db, jugador):
    db.add(
        CuestionarioBienestar(
            id_jugador=jugador.id_jugador,
            semana_inicio=date(2026, 9, 1),
            sueno=3,
            fatiga=3,
            dolor_muscular=3,
            animo=3,
        )
    )  # martes
    with pytest.raises(DBAPIError):
        db.commit()


def test_checks_de_antropometria(db, jugador):
    db.add(
        MedicionAntropometrica(
            id_jugador=jugador.id_jugador, fecha=date(2026, 8, 21), talla_m=3.1, peso_kg=70
        )
    )
    with pytest.raises(DBAPIError):
        db.commit()


def test_unicidad_jugador_fecha_antropometria(db, jugador):
    for _ in range(2):
        db.add(
            MedicionAntropometrica(
                id_jugador=jugador.id_jugador, fecha=date(2026, 8, 21), talla_m=1.88, peso_kg=80
            )
        )
    with pytest.raises(IntegrityError):
        db.commit()


def test_es_alerta_generada_por_nivel(db, jugador):
    for nivel in ["bajo", "medio", "alto"]:
        ll = _llamada(db, "riesgo")
        db.add(
            EvaluacionRiesgo(
                id_jugador=jugador.id_jugador,
                id_llamada=ll.id_llamada,
                origen="nuevo_test",
                nivel_riesgo=nivel,
                justificacion="x",
            )
        )
    db.commit()
    est = {e.nivel_riesgo: bool(e.es_alerta) for e in db.query(EvaluacionRiesgo)}
    assert est == {"bajo": False, "medio": True, "alto": True}


def test_rutina_nace_como_propuesta_y_guarda_json(db, jugador):
    ll = _llamada(db, "rutina")
    r = Rutina(
        id_jugador=jugador.id_jugador,
        id_llamada=ll.id_llamada,
        id_usuario_solicita=_usuario(db).id_usuario,
        semanas=2,
        objetivo_prioritario="salto",
        contenido={"semanas": []},
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    assert r.estado == "propuesta" and r.contenido == {"semanas": []}


def test_rutina_rechaza_calificacion_fuera_de_rango(db, jugador):
    ll = _llamada(db, "rutina")
    db.add(
        Rutina(
            id_jugador=jugador.id_jugador,
            id_llamada=ll.id_llamada,
            id_usuario_solicita=_usuario(db).id_usuario,
            semanas=2,
            objetivo_prioritario="salto",
            contenido={},
            calificacion_pertinencia=6,
        )
    )
    with pytest.raises(DBAPIError):
        db.commit()


def test_bitacora_es_solo_insercion(db):
    db.add(BitacoraAuditoria(id_usuario=None, accion="login", entidad="usuario"))
    db.commit()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE bitacora_auditoria SET entidad='x'"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM bitacora_auditoria"))
    db.rollback()
    assert db.query(BitacoraAuditoria).count() == 1
