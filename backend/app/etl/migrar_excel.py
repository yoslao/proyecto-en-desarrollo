"""ETL del Excel original → MariaDB.

    python -m app.etl.migrar_excel --archivo ../data/raw/datos_plandetesis_completo.xlsx \\
        --consentimientos ../data/raw/consentimientos.csv --fecha-hoy 2026-09-07 --dry-run

Con --dry-run se ejecuta todo (incluida la validación) dentro de una transacción que se revierte.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.db import nueva_sesion
from app.etl.carga import cargar
from app.etl.extraccion import leer_excel
from app.etl.reporte import escribir_mapa_seudonimos, generar_reporte
from app.etl.transformacion import Decisiones, construir_plan, leer_consentimientos
from app.models import Posicion, TipoPrueba

UNIVERSIDAD_DEFECTO = "Universidad Católica de Santa María"
SIGLAS_DEFECTO = "UCSM"


def parsear_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Migra el Excel de datos de jugadores a la BD.")
    ap.add_argument("--archivo", type=Path, required=True, help="Excel original (solo lectura)")
    ap.add_argument("--dry-run", action="store_true", help="hace todo sin escribir en la BD")
    ap.add_argument(
        "--fecha-hoy", type=date.fromisoformat, help="fecha (AAAA-MM-DD) de la hoja 'hoy'"
    )
    ap.add_argument(
        "--consentimientos",
        type=Path,
        help="CSV nombre_normalizado,consentimiento_informado,fecha_consentimiento",
    )
    ap.add_argument("--decisiones", type=Path, help="CSV tipo,clave,valor con decisiones manuales")
    ap.add_argument("--universidad", default=UNIVERSIDAD_DEFECTO)
    ap.add_argument("--siglas", default=SIGLAS_DEFECTO)
    ap.add_argument("--anio", type=int, default=2026, help="año si el título de la hoja no lo trae")
    ap.add_argument("--reporte", type=Path, default=Path("../data/processed/reporte_migracion.md"))
    ap.add_argument("--mapa", type=Path, default=Path("../data/processed/mapa_seudonimos.csv"))
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parsear_argumentos(argv)
    if not a.archivo.exists():
        print(f"No existe el archivo {a.archivo}", file=sys.stderr)
        return 2

    hojas, omitidas, problemas = leer_excel(a.archivo, a.fecha_hoy, a.anio)
    consentimientos = leer_consentimientos(a.consentimientos)
    decisiones = Decisiones.leer(a.decisiones)

    with nueva_sesion() as db:
        try:
            catalogo = [p.nombre for p in db.scalars(select(Posicion))]
            rangos = {
                t.codigo: (t.valor_minimo, t.valor_maximo) for t in db.scalars(select(TipoPrueba))
            }
            if not catalogo or not rangos:
                print(
                    "La BD no tiene catálogos: ejecute `python -m app.seed` primero.",
                    file=sys.stderr,
                )
                return 2
            plan = construir_plan(
                hojas, omitidas, problemas, consentimientos, decisiones, catalogo, rangos
            )
            res = cargar(db, plan, a.universidad, a.siglas)
            texto, ok = generar_reporte(
                db,
                plan,
                res,
                archivo=a.archivo,
                universidad=a.universidad,
                dry_run=a.dry_run,
                fecha_hoy=a.fecha_hoy,
            )
            a.reporte.parent.mkdir(parents=True, exist_ok=True)
            a.reporte.write_text(texto, encoding="utf-8")
            escribir_mapa_seudonimos(plan, a.mapa)
            if a.dry_run:
                db.rollback()
            elif ok:
                db.commit()
            else:
                db.rollback()
                print(
                    "La validación de integridad falló: transacción revertida, no se cargó nada.",
                    file=sys.stderr,
                )
                return 1
        except Exception:
            db.rollback()
            raise

    modo = "DRY-RUN (sin cambios en la BD)" if a.dry_run else "carga confirmada"
    print(
        f"{modo}. Integridad de la carga: {'OK' if ok else 'CON DIFERENCIAS'}. Reporte: {a.reporte}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
