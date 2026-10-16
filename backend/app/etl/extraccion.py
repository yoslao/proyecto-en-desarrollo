"""Extracción: lee todas las hojas con pandas (encabezados en la fila 2)."""

import re
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl.utils import get_column_letter

from app.etl.limpieza import (
    a_horas_detallado,
    a_numero,
    clave_nombre,
    es_vacio,
    normalizar_texto,
)
from app.etl.modelos import FilaJugador, Hoja, Problema, Valor

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "setiembre": 9,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

_RE_TITULO = re.compile(
    r"(?:(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+)?"
    r"(\d{1,2})\s+de\s+([a-z]+)\s+del?\s+(\d{4})"
)
_RE_NOMBRE_HOJA = re.compile(r"^(\d{1,2})\s+([a-z]+)$")

# Reglas para reconocer columnas por su encabezado normalizado.
_COLUMNAS = {
    "nombre": r"^nombre",
    "edad": r"^edad",
    "talla": r"^talla",
    "peso": r"^peso",
    "posicion": r"^posi",
    "SPRINT_20M": r"^sprint",
    "velocidad": r"^velocidad",
    "NAVETTE": r"palier|navette",
    "vo2": r"^vo2",
    "DURACION": r"tiempo de entrenamiento",
    "SALTO_SC": r"sin previo calentamiento",
    "SALTO_BC": r"breve calentamiento",
    "SALTO_AI": r"activacion intensa",
    "SALTO_PF": r"actividad fisica intensa",
}


def _mapear_columnas(columnas: list[str]) -> dict[str, int]:
    mapa: dict[str, int] = {}
    for idx, c in enumerate(columnas):
        n = normalizar_texto(c)
        for clave, patron in _COLUMNAS.items():
            if clave not in mapa and re.search(patron, n):
                mapa[clave] = idx
                break
    return mapa


def parsear_titulo(titulo: str | None) -> tuple[date | None, str | None]:
    """'… VIERNES 21 DE AGOSTO DEL 2026' → (date, 'viernes')."""
    if es_vacio(titulo):
        return None, None
    m = _RE_TITULO.search(normalizar_texto(titulo))
    if not m or m.group(3) not in MESES:
        return None, None
    try:
        return date(int(m.group(4)), MESES[m.group(3)], int(m.group(2))), m.group(1)
    except ValueError:
        return None, None


def resolver_fecha(
    nombre: str,
    titulo: str | None,
    fecha_hoy: date | None,
    anio_defecto: int,
    problemas: list[Problema],
) -> date | None:
    """Fecha de la jornada: título de la hoja; si no hay, el nombre de la hoja; 'hoy' → --fecha-hoy."""
    f_titulo, dia = parsear_titulo(titulo)
    m = _RE_NOMBRE_HOJA.match(normalizar_texto(nombre))
    f_nombre = None
    if m and m.group(2) in MESES:
        try:
            f_nombre = date(anio_defecto, MESES[m.group(2)], int(m.group(1)))
        except ValueError:
            f_nombre = None

    fecha = f_titulo or f_nombre
    if f_titulo and f_nombre and (f_titulo.month, f_titulo.day) != (f_nombre.month, f_nombre.day):
        problemas.append(
            Problema(
                "fecha",
                f"El nombre de la hoja sugiere {f_nombre:%d/%m} pero el título dice "
                f"{f_titulo:%d/%m/%Y}; se usa la fecha del título.",
                nombre,
            )
        )
    if f_titulo and dia and DIAS[f_titulo.weekday()] != dia:
        problemas.append(
            Problema(
                "fecha",
                f"El título dice '{dia}' pero el {f_titulo:%d/%m/%Y} cae "
                f"{DIAS[f_titulo.weekday()]}.",
                nombre,
            )
        )
    if fecha is None and normalizar_texto(nombre) == "hoy":
        fecha = fecha_hoy
    return fecha


def _celda(idx_col: int, fila_excel: int) -> str:
    return f"{get_column_letter(idx_col + 1)}{fila_excel}"


def leer_excel(
    ruta: Path, fecha_hoy: date | None, anio_defecto: int
) -> tuple[list[Hoja], list[str], list[Problema]]:
    """Devuelve (hojas leídas, nombres de hojas omitidas, problemas)."""
    problemas: list[Problema] = []
    omitidas: list[str] = []
    hojas: list[Hoja] = []

    datos = pd.read_excel(ruta, sheet_name=None, header=1, dtype=object)
    cabeceras = pd.read_excel(ruta, sheet_name=None, header=None, nrows=1, dtype=object)

    for nombre, df in datos.items():
        nombre = str(nombre)
        titulo = None
        if nombre in cabeceras and cabeceras[nombre].shape[1] > 0:
            v = cabeceras[nombre].iloc[0, 0] if len(cabeceras[nombre]) else None
            titulo = None if es_vacio(v) else str(v).strip()

        mapa = _mapear_columnas([str(c) for c in df.columns])
        if "nombre" not in mapa:
            problemas.append(Problema("formato", "Hoja sin columna de nombre; se omite.", nombre))
            omitidas.append(nombre)
            continue
        if "SPRINT_20M" in mapa or "NAVETTE" in mapa:
            tipo = "velocidad"
        elif "SALTO_SC" in mapa:
            tipo = "salto"
        else:
            problemas.append(Problema("formato", "Hoja de tipo desconocido; se omite.", nombre))
            omitidas.append(nombre)
            continue

        fecha = resolver_fecha(nombre, titulo, fecha_hoy, anio_defecto, problemas)
        if fecha is None:
            motivo = (
                "La hoja 'hoy' no tiene fecha: indíquela con --fecha-hoy. Se omite la hoja."
                if normalizar_texto(nombre) == "hoy"
                else "No se pudo determinar la fecha de la hoja; se omite."
            )
            problemas.append(Problema("fecha", motivo, nombre))
            omitidas.append(nombre)
            continue

        hoja = Hoja(nombre, tipo, titulo, fecha, None, "")
        horas_texto: list[str] = []
        inicios = []
        sin_meridiano: list[str] = []
        n_cols = len(df.columns)

        for i, (_, fila) in enumerate(df.iterrows()):
            fila_excel = i + 3  # encabezados en la fila 2 → datos desde la 3
            crudo = fila.iloc[mapa["nombre"]]
            if es_vacio(crudo):
                continue
            etiqueta = normalizar_texto(crudo)

            # Filas de metadatos ("HORA DE LA TOMA DE LOS DATOS", "HORA - SPRINT 20m", ...)
            if etiqueta.startswith("hora"):
                partes = []
                for j in range(n_cols):
                    if j == mapa["nombre"] or es_vacio(fila.iloc[j]):
                        continue
                    partes.append(str(fila.iloc[j]).strip())
                    for h, asumida in a_horas_detallado(fila.iloc[j]):
                        inicios.append(h)
                        if asumida:
                            sin_meridiano.append(_celda(j, fila_excel))
                        break  # solo la hora de inicio de cada celda
                horas_texto.append(f"{str(crudo).strip()}: {' | '.join(partes)}")
                continue

            def num(clave: str, fila=fila, fila_excel=fila_excel, nombre=nombre, mapa=mapa):
                if clave not in mapa:
                    return None, None, None
                bruto = fila.iloc[mapa[clave]]
                celda = _celda(mapa[clave], fila_excel)
                try:
                    return a_numero(bruto), celda, bruto
                except ValueError as e:
                    problemas.append(Problema("valor", f"{celda}: {e}", nombre))
                    return None, celda, bruto

            edad, _, _ = num("edad")
            talla, c_talla, _ = num("talla")
            peso, c_peso, _ = num("peso")
            pos = None if "posicion" not in mapa else fila.iloc[mapa["posicion"]]
            fj = FilaJugador(
                hoja=nombre,
                fila=fila_excel,
                nombre_original=str(crudo).strip(),
                clave=clave_nombre(str(crudo)),
                edad=int(edad) if edad is not None else None,
                talla=talla,
                peso=peso,
                posicion_texto=None if es_vacio(pos) else str(pos).strip(),
                celda_talla=c_talla,
                celda_peso=c_peso,
            )
            columnas_valor = (
                ("SPRINT_20M", "NAVETTE", "DURACION")
                if tipo == "velocidad"
                else ("SALTO_SC", "SALTO_BC", "SALTO_AI", "SALTO_PF")
            )
            for codigo in columnas_valor:
                valor, celda, bruto = num(codigo)
                if valor is not None:
                    fj.valores.append(Valor(codigo, valor, celda, str(bruto).strip()))
            if tipo == "velocidad":
                fj.velocidad_excel, fj.celda_velocidad, _ = num("velocidad")
                fj.vo2_excel, fj.celda_vo2, _ = num("vo2")
            hoja.filas.append(fj)

        if sin_meridiano:
            problemas.append(
                Problema(
                    "formato",
                    f"Horas sin AM/PM en {', '.join(sin_meridiano)}: se asumió PM "
                    f"(solo afecta a hora_inicio de la jornada).",
                    nombre,
                )
            )
        hoja.hora_inicio = min(inicios) if inicios else None
        hoja.observaciones = f"migracion:{nombre}|" + "; ".join(horas_texto)
        hoja.observaciones = hoja.observaciones[:500]
        hojas.append(hoja)

    return hojas, omitidas, problemas
