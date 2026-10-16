"""ETL completo con un Excel ficticio (sin datos reales) contra MariaDB."""

import re
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.etl.extraccion import leer_excel
from app.etl.limpieza import clave_nombre
from app.etl.migrar_excel import main
from app.etl.transformacion import Decisiones, construir_plan, leer_consentimientos
from app.models import (
    JornadaEvaluacion,
    Jugador,
    MedicionAntropometrica,
    RegistroSesionJugador,
    ResultadoPrueba,
    Sesion,
)
from tests.etl_excel import (
    CONSENTIMIENTOS_CSV,
    DECISIONES_CSV,
    NOMBRES,
    crear_excel_ficticio,
)

RANGOS = {
    "SPRINT_20M": (Decimal("2.5"), Decimal("5")),
    "NAVETTE": (Decimal("1"), Decimal("21")),
    "SALTO_SC": (Decimal("5"), Decimal("90")),
    "SALTO_BC": (Decimal("5"), Decimal("90")),
    "SALTO_AI": (Decimal("5"), Decimal("90")),
    "SALTO_PF": (Decimal("5"), Decimal("90")),
}
CATALOGO = ["central", "punta receptor", "opuesto", "armador", "líbero"]


@pytest.fixture
def archivos(tmp_path):
    excel = crear_excel_ficticio(tmp_path / "ficticio.xlsx")
    cons = tmp_path / "consentimientos.csv"
    cons.write_text(CONSENTIMIENTOS_CSV, encoding="utf-8")
    dec = tmp_path / "decisiones.csv"
    dec.write_text(DECISIONES_CSV, encoding="utf-8")
    return {"excel": excel, "cons": cons, "dec": dec, "tmp": tmp_path}


def _plan(archivos, fecha_hoy=None, con_decisiones=False):
    hojas, omitidas, problemas = leer_excel(archivos["excel"], fecha_hoy, 2026)
    return construir_plan(
        hojas,
        omitidas,
        problemas,
        leer_consentimientos(archivos["cons"]),
        Decisiones.leer(archivos["dec"] if con_decisiones else None),
        CATALOGO,
        RANGOS,
    )


def _cli(archivos, *extra):
    return main(
        [
            "--archivo", str(archivos["excel"]),
            "--consentimientos", str(archivos["cons"]),
            "--reporte", str(archivos["tmp"] / "reporte.md"),
            "--mapa", str(archivos["tmp"] / "mapa.csv"),
            *extra,
        ]
    )  # fmt: skip


def _conteos(db):
    return {
        "jugador": db.scalar(select(func.count()).select_from(Jugador)),
        "jornada": db.scalar(select(func.count()).select_from(JornadaEvaluacion)),
        "resultado": db.scalar(select(func.count()).select_from(ResultadoPrueba)),
        "antropometria": db.scalar(select(func.count()).select_from(MedicionAntropometrica)),
        "sesion": db.scalar(select(func.count()).select_from(Sesion)),
        "registro": db.scalar(select(func.count()).select_from(RegistroSesionJugador)),
    }


# ------------------------------------------------------------------ extracción
def test_hoja_hoy_sin_fecha_se_omite_y_avisa(archivos):
    hojas, omitidas, problemas = leer_excel(archivos["excel"], None, 2026)
    assert omitidas == ["hoy"]
    assert "hoy" not in [h.nombre for h in hojas]
    assert any(p.categoria == "fecha" and p.hoja == "hoy" for p in problemas)


def test_hoja_hoy_con_fecha_se_incluye(archivos):
    hojas, omitidas, _ = leer_excel(archivos["excel"], date(2026, 8, 15), 2026)
    assert omitidas == []
    assert next(h for h in hojas if h.nombre == "hoy").fecha == date(2026, 8, 15)


def test_metadatos_de_hora_no_son_jugadores_y_dan_hora_inicio(archivos):
    hojas, _, _ = leer_excel(archivos["excel"], None, 2026)
    vel = next(h for h in hojas if h.nombre == "14 Agosto")
    assert len(vel.filas) == 8  # sin las 3 filas "HORA ..."
    assert vel.hora_inicio == time(17, 30)  # la más temprana (registro de entrenamiento)
    salto = next(h for h in hojas if h.nombre == "10 Agosto")
    assert salto.hora_inicio == time(19, 0)
    assert vel.observaciones.startswith("migracion:14 Agosto|")


def test_unidades_y_guiones_se_limpian(archivos):
    hojas, _, _ = leer_excel(archivos["excel"], None, 2026)
    h12 = next(h for h in hojas if h.nombre == "12 Agosto")
    zeta = next(f for f in h12.filas if f.clave == clave_nombre(NOMBRES["F"]))
    assert zeta.valores == []  # todo '-'
    alfa = next(f for f in h12.filas if f.clave == clave_nombre(NOMBRES["A"]))
    assert alfa.peso == Decimal("65") and {v.codigo for v in alfa.valores} >= {"SALTO_SC"}


def test_nombres_con_distinta_escritura_se_emparejan(archivos):
    plan = _plan(archivos)
    assert len(plan.jugadores) == 8  # en la hoja 12 vienen en MAYÚSCULAS y con espacio final


def test_titulo_manda_sobre_nombre_de_hoja_y_se_informa(archivos):
    hojas, _, problemas = leer_excel(archivos["excel"], None, 2026)
    assert next(h for h in hojas if h.nombre == "17 Agosto").fecha == date(2026, 8, 18)
    assert any(p.categoria == "fecha" and p.hoja == "17 Agosto" for p in problemas)


# ------------------------------------------------------------------ plan
def test_opuesto_central_exige_decision_y_no_se_adivina(archivos):
    plan = _plan(archivos)
    c = plan.jugadores[clave_nombre(NOMBRES["C"])]
    assert c.posicion is None and not c.cargable
    assert any(p.categoria == "posicion" for p in plan.problemas)
    assert all(m.clave != c.clave for m in plan.mediciones)


def test_decision_manual_resuelve_posicion_y_nombre(archivos):
    plan = _plan(archivos, con_decisiones=True)
    assert plan.jugadores[clave_nombre(NOMBRES["C"])].posicion == "opuesto"
    e = plan.jugadores[clave_nombre(NOMBRES["E"])]
    assert (e.nombres, e.apellidos) == ("Ruben Mateo", "Epsilon Prueba")


def test_sin_consentimiento_no_hay_mediciones(archivos):
    plan = _plan(archivos, con_decisiones=True)
    d = plan.jugadores[clave_nombre(NOMBRES["D"])]
    assert d.cargable and not d.consentimiento
    assert all(m.clave != d.clave for m in plan.mediciones)
    assert all(a.clave != d.clave for a in plan.antropometrias)
    assert any(e.clave == d.clave for e in plan.exclusiones)


def test_atipicos_por_prueba_y_jornada(archivos):
    plan = _plan(archivos, con_decisiones=True)
    theta = clave_nombre(NOMBRES["H"])
    atip = {(m.hoja, m.codigo) for m in plan.mediciones if m.atipico}
    assert {m.clave for m in plan.mediciones if m.atipico} == {theta}
    assert ("10 Agosto", "SALTO_SC") in atip


def test_talla_inconsistente_se_lista(archivos):
    plan = _plan(archivos, con_decisiones=True)
    assert any(p.categoria == "talla" for p in plan.problemas)


def test_el_reporte_nunca_imprime_nombres(archivos, db):
    assert _cli(archivos, "--dry-run", "--decisiones", str(archivos["dec"])) == 0
    texto = (archivos["tmp"] / "reporte.md").read_text(encoding="utf-8").lower()
    for nombre in NOMBRES.values():
        for parte in nombre.lower().split():
            if parte != "prueba":
                assert not re.search(rf"{parte}", texto), parte


# ------------------------------------------------------------------ carga
def test_dry_run_no_escribe_nada(archivos, db):
    antes = _conteos(db)
    assert _cli(archivos, "--dry-run", "--decisiones", str(archivos["dec"])) == 0
    db.expire_all()
    assert _conteos(db) == antes
    assert "DRY-RUN" in (archivos["tmp"] / "reporte.md").read_text(encoding="utf-8")


def test_carga_real_y_valores_en_bd(archivos, db):
    assert _cli(archivos, "--decisiones", str(archivos["dec"]), "--fecha-hoy", "2026-08-15") == 0
    db.expire_all()
    c = _conteos(db)
    assert c["jugador"] == 8 and c["jornada"] == 5 and c["sesion"] == 3
    # Delta no tiene consentimiento: no hay nada suyo en resultados
    delta = db.scalar(select(Jugador).where(Jugador.nombres == "Juan"))
    assert delta is not None and not delta.consentimiento_informado
    n_delta = db.scalar(
        select(func.count())
        .select_from(ResultadoPrueba)
        .where(ResultadoPrueba.id_jugador == delta.id_jugador)
    )
    assert n_delta == 0
    # Fecha de nacimiento no se inventa; códigos J-xx correlativos
    assert (
        db.scalar(
            select(func.count()).select_from(Jugador).where(Jugador.fecha_nacimiento.is_not(None))
        )
        == 0
    )
    codigos = sorted(db.scalars(select(Jugador.codigo_seudonimo)))
    assert codigos == [f"J-{i:02d}" for i in range(1, 9)]
    # Registros de sesión migrados: RPE NULL, origen 'migracion', sRPE NULL
    assert (
        db.scalar(select(func.count()).select_from(Sesion).where(Sesion.origen == "migracion")) == 3
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(RegistroSesionJugador)
            .where(RegistroSesionJugador.rpe.is_not(None))
        )
        == 0
    )
    # todas las jornadas son 'seguimiento'
    assert set(db.scalars(select(JornadaEvaluacion.momento))) == {"seguimiento"}
    # el atípico queda pendiente de confirmación
    assert (
        db.scalar(
            select(func.count())
            .select_from(ResultadoPrueba)
            .where(ResultadoPrueba.estado == "pendiente_confirmacion")
        )
        > 0
    )


def test_carga_es_idempotente(archivos, db):
    args = ("--decisiones", str(archivos["dec"]), "--fecha-hoy", "2026-08-15")
    assert _cli(archivos, *args) == 0
    db.expire_all()
    primera = _conteos(db)
    assert _cli(archivos, *args) == 0
    db.expire_all()
    assert _conteos(db) == primera


def test_segunda_carga_reutiliza_jugadores_y_codigos(archivos, db):
    args = ("--decisiones", str(archivos["dec"]), "--fecha-hoy", "2026-08-15")
    _cli(archivos, *args)
    codigos1 = dict(
        db.execute(
            select(Jugador.apellidos, Jugador.codigo_seudonimo).where(
                Jugador.nombres == "Mario Luis"
            )
        ).all()
    )
    _cli(archivos, *args)
    db.expire_all()
    codigos2 = dict(
        db.execute(
            select(Jugador.apellidos, Jugador.codigo_seudonimo).where(
                Jugador.nombres == "Mario Luis"
            )
        ).all()
    )
    assert codigos1 == codigos2


def test_conflicto_no_sobrescribe_y_se_reporta(archivos, db):
    args = ("--decisiones", str(archivos["dec"]), "--fecha-hoy", "2026-08-15")
    _cli(archivos, *args)
    db.execute(text("UPDATE resultado_prueba SET valor = valor + 1 LIMIT 1"))
    db.commit()
    # La BD difiere del origen: la integridad falla, se revierte y el conflicto queda en el reporte.
    assert _cli(archivos, *args) == 1
    reporte = (archivos["tmp"] / "reporte.md").read_text(encoding="utf-8")
    assert "no se sobrescribe" in reporte


def test_reporte_valida_conteos_y_derivadas(archivos, db):
    _cli(archivos, "--dry-run", "--decisiones", str(archivos["dec"]), "--fecha-hoy", "2026-08-15")
    reporte = (archivos["tmp"] / "reporte.md").read_text(encoding="utf-8")
    assert (
        "❌" in reporte.split("## 4.")[1].split("## 5.")[0]
    )  # la velocidad mal digitada se detecta
    assert "Integridad de la carga" in reporte and "todo coincide con el origen" in reporte
    assert "## 5. Muestra aleatoria del 10 %" in reporte


def test_sin_catalogos_no_corre(archivos, db):
    db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    db.execute(text("TRUNCATE TABLE posicion"))
    db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    db.commit()
    assert _cli(archivos, "--dry-run") == 2


# ------------------------------------------------------------------ decisiones de calidad
def _decisiones_extra(archivos, lineas):
    ruta = archivos["tmp"] / "decisiones_extra.csv"
    ruta.write_text("tipo,clave,valor\n" + "\n".join(lineas) + "\n", encoding="utf-8")
    archivos["dec"] = ruta
    return ruta


def _tallas_de(plan, clave):
    return {a.talla for a in plan.antropometrias if a.clave == clave}


def test_talla_se_deja_tal_cual_sin_decision(archivos):
    _decisiones_extra(archivos, ["posicion,opuesto-central,central"])
    plan = _plan(archivos, fecha_hoy=date(2026, 8, 15), con_decisiones=True)
    assert len(_tallas_de(plan, clave_nombre(NOMBRES["C"]))) == 3


def test_talla_unificada_a_la_mas_frecuente(archivos):
    # Gamma: 1.88 (2 hojas), 1.87 (2 hojas), 1.86 (1 hoja) → empate resuelto con 1.87
    _decisiones_extra(
        archivos, ["posicion,opuesto-central,central", "talla,unificar,moda", "talla,empate,1.87"]
    )
    plan = _plan(archivos, fecha_hoy=date(2026, 8, 15), con_decisiones=True)
    assert _tallas_de(plan, clave_nombre(NOMBRES["C"])) == {Decimal("1.87")}
    assert any("RESUELTO" in p.detalle for p in plan.problemas if p.categoria == "talla")


def test_talla_moda_sin_empate(archivos):
    _decisiones_extra(archivos, ["posicion,opuesto-central,central", "talla,unificar,moda"])
    plan = _plan(archivos, fecha_hoy=date(2026, 8, 15), con_decisiones=True)
    # con empate y sin valor de desempate no se adivina
    assert len(_tallas_de(plan, clave_nombre(NOMBRES["C"]))) == 3


def test_derivada_aceptada_no_cuenta_como_fuera_de_tolerancia(archivos, db):
    _decisiones_extra(
        archivos, ["posicion,opuesto-central,central", "derivada,17 Agosto!F4,aceptar"]
    )
    assert (
        _cli(
            archivos, "--dry-run", "--decisiones", str(archivos["dec"]), "--fecha-hoy", "2026-09-07"
        )
        == 0
    )
    reporte = (archivos["tmp"] / "reporte.md").read_text(encoding="utf-8")
    assert "0 valor(es) fuera de tolerancia" in reporte
    assert "Aceptadas por decisión" in reporte
