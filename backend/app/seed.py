"""Semillas. Uso:

python -m app.seed                      # catálogos (idempotente)
python -m app.seed --dev                # + usuarios, universidad y 12 jugadores FICTICIOS
python -m app.seed usuario --correo X --nombre "Y" --rol administrador
"""

import argparse
import getpass
import secrets
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import nueva_sesion
from app.core.security import hashear
from app.models import (
    ConfiguracionSistema,
    ConfiguracionUmbral,
    Jugador,
    Posicion,
    TipoPrueba,
    Universidad,
    Usuario,
)

POSICIONES = [
    ("central", "Bloqueo rápido y ataque de primer tiempo"),
    ("punta receptor", "Recepción, defensa y ataque por las bandas"),
    ("opuesto", "Atacante principal desde la zona 2 y bloqueo"),
    ("armador", "Organiza el juego y distribuye el balón"),
    ("líbero", "Especialista defensivo; recepción y defensa"),
]

# (codigo, nombre, unidad, minimo, maximo, mayor_es_mejor, intentos_max)
TIPOS_PRUEBA = [
    ("SPRINT_20M", "Sprint de 20 m", "s", "2.50", "5.00", False, 1),
    ("NAVETTE", "Course Navette (palier)", "palier", "1", "21", True, 1),
    ("SJ", "Squat Jump", "cm", "5", "90", True, 1),
    ("CMJ", "Countermovement Jump", "cm", "5", "90", True, 3),
    ("SALTO_SC", "Salto sin calentamiento", "cm", "5", "90", True, 1),
    ("SALTO_BC", "Salto tras breve calentamiento", "cm", "5", "90", True, 1),
    ("SALTO_AI", "Salto tras activación intensa", "cm", "5", "90", True, 1),
    ("SALTO_PF", "Salto tras actividad física intensa", "cm", "5", "90", True, 1),
]

UMBRALES = [
    ("ACWR_ALTO", "1.5", "ACWR a partir del cual la carga se considera alta"),
    ("CAIDA_CMJ_PCT", "10", "Caída porcentual del CMJ respecto a la línea base que alerta"),
    ("BIENESTAR_BAJO", "8", "Índice de bienestar (4-20) por debajo del cual se considera bajo"),
    ("MAX_SESIONES_SEMANA", "6", "Máximo de sesiones por semana en una rutina"),
    ("MAX_DURACION_SESION", "120", "Máximo de minutos por sesión en una rutina"),
]

CONFIG_SISTEMA = [
    ("KMEANS_VARIABLES", "SPRINT_20M,NAVETTE,CMJ,SJ", "Variables del K-means (códigos de prueba)"),
    (
        "KMEANS_VARIABLES_EXPLORATORIO",
        "SPRINT_20M,NAVETTE,SALTO_AI",
        "Variables del análisis exploratorio con datos migrados",
    ),
    ("KMEANS_K_CANDIDATOS", "2,3,4", "Valores de k a probar"),
    ("KMEANS_RANDOM_STATE", "42", "Semilla fija del K-means"),
    ("KMEANS_SILUETA_MIN", "0.25", "Silueta mínima antes de advertir"),
    ("IA_MODELO_PRINCIPAL", "", "Modelo principal (vacío = variable de entorno)"),
    ("IA_MODELO_RESPALDO", "", "Modelo de respaldo (vacío = variable de entorno)"),
]

# Jugadores FICTICIOS (no corresponden a personas reales).
JUGADORES_DEV = [
    ("Aldo", "Ficticio Uno", 2003, "punta receptor"),
    ("Bruno", "Ficticio Dos", 2002, "central"),
    ("Cesar", "Ficticio Tres", 2004, "opuesto"),
    ("Dario", "Ficticio Cuatro", 2001, "armador"),
    ("Elio", "Ficticio Cinco", 2003, "líbero"),
    ("Fabio", "Ficticio Seis", 2002, "punta receptor"),
    ("Gael", "Ficticio Siete", 2004, "central"),
    ("Hugo", "Ficticio Ocho", 2001, "punta receptor"),
    ("Ivan", "Ficticio Nueve", 2003, "opuesto"),
    ("Joel", "Ficticio Diez", 2002, "central"),
    ("Kenzo", "Ficticio Once", 2004, "armador"),
    ("Lino", "Ficticio Doce", 2003, "líbero"),
]


def sembrar_catalogos(db: Session) -> None:
    for nombre, desc in POSICIONES:
        if not db.scalar(select(Posicion).where(Posicion.nombre == nombre)):
            db.add(Posicion(nombre=nombre, descripcion=desc))
    for cod, nombre, unidad, mn, mx, mayor, intentos in TIPOS_PRUEBA:
        if not db.scalar(select(TipoPrueba).where(TipoPrueba.codigo == cod)):
            db.add(
                TipoPrueba(
                    codigo=cod,
                    nombre=nombre,
                    unidad=unidad,
                    valor_minimo=Decimal(mn),
                    valor_maximo=Decimal(mx),
                    mayor_es_mejor=mayor,
                    intentos_max=intentos,
                )
            )
    for clave, valor, desc in UMBRALES:
        if not db.scalar(select(ConfiguracionUmbral).where(ConfiguracionUmbral.clave == clave)):
            db.add(ConfiguracionUmbral(clave=clave, valor=Decimal(valor), descripcion=desc))
    for clave, valor, desc in CONFIG_SISTEMA:
        if not db.scalar(select(ConfiguracionSistema).where(ConfiguracionSistema.clave == clave)):
            db.add(ConfiguracionSistema(clave=clave, valor=valor, descripcion=desc))
    db.flush()


def crear_usuario(db: Session, correo: str, nombre: str, rol: str, contrasena: str) -> Usuario:
    u = Usuario(nombre_completo=nombre, correo=correo, hash_contrasena=hashear(contrasena), rol=rol)
    db.add(u)
    db.flush()
    return u


def sembrar_dev(db: Session) -> dict[str, str]:
    """Usuarios de desarrollo, universidad ficticia y 12 jugadores ficticios."""
    credenciales: dict[str, str] = {}
    for correo, nombre, rol in [
        ("admin@voleyia.test", "Administrador de Prueba", "administrador"),
        ("entrenador@voleyia.test", "Entrenador de Prueba", "entrenador"),
    ]:
        if not db.scalar(select(Usuario).where(Usuario.correo == correo)):
            clave = secrets.token_urlsafe(12)
            crear_usuario(db, correo, nombre, rol, clave)
            credenciales[correo] = clave
    uni = db.scalar(select(Universidad).where(Universidad.nombre == "Universidad Ficticia"))
    if not uni:
        uni = Universidad(nombre="Universidad Ficticia", siglas="UFIC")
        db.add(uni)
        db.flush()
    pos = {p.nombre: p.id_posicion for p in db.scalars(select(Posicion))}
    for i, (nombres, apellidos, anio, posicion) in enumerate(JUGADORES_DEV, start=1):
        codigo = f"J-{i:02d}"
        if db.scalar(select(Jugador).where(Jugador.codigo_seudonimo == codigo)):
            continue
        db.add(
            Jugador(
                codigo_seudonimo=codigo,
                nombres=nombres,
                apellidos=apellidos,
                fecha_nacimiento=date(anio, 6, 15),
                id_universidad=uni.id_universidad,
                id_posicion=pos[posicion],
                consentimiento_informado=True,
                fecha_consentimiento=date(2026, 8, 1),
            )
        )
    db.flush()
    return credenciales


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("comando", nargs="?", choices=["usuario"], help="crear un usuario")
    ap.add_argument("--dev", action="store_true", help="datos ficticios de desarrollo")
    ap.add_argument("--correo")
    ap.add_argument("--nombre")
    ap.add_argument("--rol", choices=["administrador", "entrenador"])
    args = ap.parse_args()

    with nueva_sesion() as db:
        if args.comando == "usuario":
            if not (args.correo and args.nombre and args.rol):
                ap.error("usuario requiere --correo, --nombre y --rol")
            clave = getpass.getpass("Contraseña: ")
            if len(clave) < 10:
                ap.error("La contraseña debe tener al menos 10 caracteres")
            crear_usuario(db, args.correo, args.nombre, args.rol, clave)
            db.commit()
            print(f"Usuario {args.correo} creado.")
            return
        sembrar_catalogos(db)
        if args.dev:
            for correo, clave in sembrar_dev(db).items():
                print(f"[DEV] {correo}  contraseña: {clave}")
        db.commit()
        print("Semilla aplicada.")


if __name__ == "__main__":
    main()
