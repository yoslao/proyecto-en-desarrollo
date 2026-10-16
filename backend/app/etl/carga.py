"""Carga: escribe el plan en la BD. Idempotente; el llamador controla la transacción."""

import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hashear
from app.etl.limpieza import clave_nombre
from app.etl.modelos import Plan, Problema
from app.etl.transformacion import ref_jugador
from app.models import (
    JornadaEvaluacion,
    Jugador,
    MedicionAntropometrica,
    Posicion,
    RegistroSesionJugador,
    ResultadoPrueba,
    Sesion,
    TipoPrueba,
    Universidad,
    Usuario,
)
from app.services.jugadores import siguiente_codigo

CORREO_MIGRACION = "migracion@voleyia.local"


@dataclass
class ResultadoCarga:
    id_universidad: int = 0
    id_usuario_migracion: int = 0
    jornadas: dict[str, int] = field(default_factory=dict)  # hoja → id_jornada
    sesiones: dict[str, int] = field(default_factory=dict)  # hoja → id_sesion
    jugadores_nuevos: int = 0
    jugadores_existentes: int = 0
    insertados: dict[str, int] = field(
        default_factory=lambda: {"resultado": 0, "antropometria": 0, "registro_sesion": 0}
    )
    ya_existian: dict[str, int] = field(
        default_factory=lambda: {"resultado": 0, "antropometria": 0, "registro_sesion": 0}
    )
    problemas: list[Problema] = field(default_factory=list)


def _usuario_migracion(db: Session) -> Usuario:
    u = db.scalar(select(Usuario).where(Usuario.correo == CORREO_MIGRACION))
    if u is None:
        # Cuenta de sistema, inactiva y sin contraseña utilizable: solo atribuye la autoría.
        u = Usuario(
            nombre_completo="Migración de datos (sistema)",
            correo=CORREO_MIGRACION,
            hash_contrasena=hashear(secrets.token_urlsafe(32)),
            rol="administrador",
            activo=False,
        )
        db.add(u)
        db.flush()
    return u


def cargar(db: Session, plan: Plan, nombre_universidad: str, siglas: str | None) -> ResultadoCarga:
    res = ResultadoCarga()

    uni = db.scalar(select(Universidad).where(Universidad.nombre == nombre_universidad))
    if uni is None:
        uni = Universidad(nombre=nombre_universidad, siglas=siglas)
        db.add(uni)
        db.flush()
    res.id_universidad = uni.id_universidad
    res.id_usuario_migracion = _usuario_migracion(db).id_usuario

    posiciones = {p.nombre: p.id_posicion for p in db.scalars(select(Posicion))}
    tipos = {t.codigo: t.id_tipo_prueba for t in db.scalars(select(TipoPrueba))}

    # --- Jugadores: reutiliza los existentes (emparejados por nombre) y crea los nuevos
    existentes = {
        clave_nombre(f"{j.apellidos} {j.nombres}"): j for j in db.scalars(select(Jugador))
    }
    for clave, jp in plan.jugadores.items():
        if not jp.cargable:
            continue
        j = existentes.get(clave)
        if j is None:
            j = Jugador(
                codigo_seudonimo=siguiente_codigo(db),
                nombres=jp.nombres,
                apellidos=jp.apellidos,
                fecha_nacimiento=None,  # el Excel solo trae la edad: pendiente, no se inventa
                id_universidad=uni.id_universidad,
                id_posicion=posiciones[jp.posicion],
                consentimiento_informado=jp.consentimiento,
                fecha_consentimiento=jp.fecha_consentimiento,
            )
            db.add(j)
            db.flush()
            res.jugadores_nuevos += 1
        else:
            res.jugadores_existentes += 1
            if jp.consentimiento and not j.consentimiento_informado:
                j.consentimiento_informado = True
                j.fecha_consentimiento = jp.fecha_consentimiento
        jp.codigo, jp.id_jugador = j.codigo_seudonimo, j.id_jugador
    db.flush()

    # --- Jornadas (una por hoja de salto/velocidad) y sesiones (una por hoja de velocidad)
    for h in plan.hojas:
        marca = f"migracion:{h.nombre}|"
        jor = db.scalar(
            select(JornadaEvaluacion).where(
                JornadaEvaluacion.observaciones.startswith(marca, autoescape=True)
            )
        )
        if jor is None:
            jor = JornadaEvaluacion(
                fecha=h.fecha,
                momento="seguimiento",
                hora_inicio=h.hora_inicio,
                observaciones=h.observaciones,
                id_usuario=res.id_usuario_migracion,
            )
            db.add(jor)
            db.flush()
        elif jor.fecha != h.fecha:
            res.problemas.append(
                Problema(
                    "conflicto",
                    f"La jornada ya existía con fecha {jor.fecha:%d/%m/%Y} y ahora se calcula "
                    f"{h.fecha:%d/%m/%Y}; no se modifica.",
                    h.nombre,
                )
            )
        res.jornadas[h.nombre] = jor.id_jornada

    hojas_con_duracion = {d.hoja for d in plan.duraciones}
    for h in plan.hojas:
        if h.nombre not in hojas_con_duracion:
            continue
        desc = f"migracion:{h.nombre}"
        s = db.scalar(
            select(Sesion).where(Sesion.descripcion == desc, Sesion.origen == "migracion")
        )
        if s is None:
            s = Sesion(
                fecha=h.fecha,
                tipo="entrenamiento",
                descripcion=desc,
                origen="migracion",
                id_usuario=res.id_usuario_migracion,
            )
            db.add(s)
            db.flush()
        res.sesiones[h.nombre] = s.id_sesion

    # --- Resultados de pruebas
    ids_jornada = list(res.jornadas.values())
    previos = {
        (r.id_jornada, r.id_jugador, r.id_tipo_prueba, r.intento): r
        for r in db.scalars(
            select(ResultadoPrueba).where(ResultadoPrueba.id_jornada.in_(ids_jornada))
        )
    }
    for m in plan.mediciones:
        jp = plan.jugadores[m.clave]
        clave_bd = (res.jornadas[m.hoja], jp.id_jugador, tipos[m.codigo], 1)
        if clave_bd in previos:
            res.ya_existian["resultado"] += 1
            if previos[clave_bd].valor != m.valor:
                res.problemas.append(
                    Problema(
                        "conflicto",
                        f"{m.celda}: {ref_jugador(m.clave)} {m.codigo}: la BD tiene "
                        f"{previos[clave_bd].valor} y el Excel {m.valor}; no se sobrescribe.",
                        m.hoja,
                    )
                )
            continue
        db.add(
            ResultadoPrueba(
                id_jornada=clave_bd[0],
                id_jugador=clave_bd[1],
                id_tipo_prueba=clave_bd[2],
                intento=1,
                valor=m.valor,
                es_atipico=m.atipico,
                estado="pendiente_confirmacion" if m.atipico else "valido",
            )
        )
        res.insertados["resultado"] += 1

    # --- Antropometría
    for a in plan.antropometrias:
        jp = plan.jugadores[a.clave]
        previa = db.scalar(
            select(MedicionAntropometrica).where(
                MedicionAntropometrica.id_jugador == jp.id_jugador,
                MedicionAntropometrica.fecha == a.fecha,
            )
        )
        if previa is not None:
            res.ya_existian["antropometria"] += 1
            if (previa.talla_m, previa.peso_kg) != (a.talla, a.peso):
                res.problemas.append(
                    Problema(
                        "conflicto",
                        f"{ref_jugador(a.clave)}: antropometría del {a.fecha:%d/%m/%Y} distinta en "
                        f"la BD; no se sobrescribe.",
                        a.hoja,
                    )
                )
            continue
        db.add(
            MedicionAntropometrica(
                id_jugador=jp.id_jugador, fecha=a.fecha, talla_m=a.talla, peso_kg=a.peso
            )
        )
        res.insertados["antropometria"] += 1

    # --- Tiempo de entrenamiento efectivo → registro_sesion_jugador (RPE NULL, origen migración)
    for d in plan.duraciones:
        jp = plan.jugadores[d.clave]
        id_sesion = res.sesiones[d.hoja]
        previo = db.scalar(
            select(RegistroSesionJugador).where(
                RegistroSesionJugador.id_sesion == id_sesion,
                RegistroSesionJugador.id_jugador == jp.id_jugador,
            )
        )
        if previo is not None:
            res.ya_existian["registro_sesion"] += 1
            if previo.duracion_efectiva_min != d.minutos:
                res.problemas.append(
                    Problema(
                        "conflicto",
                        f"{d.celda}: {ref_jugador(d.clave)} duración distinta en la BD; "
                        f"no se sobrescribe.",
                        d.hoja,
                    )
                )
            continue
        db.add(
            RegistroSesionJugador(
                id_sesion=id_sesion,
                id_jugador=jp.id_jugador,
                duracion_efectiva_min=d.minutos,
                rpe=None,
            )
        )
        res.insertados["registro_sesion"] += 1

    db.flush()
    return res
