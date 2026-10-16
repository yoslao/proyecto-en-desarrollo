"""Transformación: de las hojas limpias al plan de carga (sin acceso a BD)."""

import csv
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.etl.limpieza import (
    clave_nombre,
    dividir_nombre,
    es_atipico,
    limites_iqr,
    nombre_propio,
    normalizar_texto,
)
from app.etl.modelos import (
    AntropoPlan,
    DuracionPlan,
    Exclusion,
    FilaJugador,
    Hoja,
    JugadorPlan,
    MedicionPlan,
    Plan,
    Problema,
)

# Rangos fuera de tipo_prueba (la BD valida talla y peso con CHECK; la duración, el servicio).
RANGO_TALLA = (Decimal("1.40"), Decimal("2.30"))
RANGO_PESO = (Decimal("40"), Decimal("150"))
RANGO_DURACION = (Decimal("10"), Decimal("240"))

# Variantes de escritura del Excel que SÍ equivalen a una posición del catálogo.
# Todo lo demás ("opuesto-central", "punta"...) exige decisión manual.
ALIAS_POSICION = {"punta receptora": "punta receptor", "puntas receptores": "punta receptor"}


def ref_jugador(clave: str) -> str:
    """Marca que el reporte sustituye por el código J-xx (los nombres nunca se imprimen)."""
    return f"⟦{clave}⟧"


# ---------------------------------------------------------------- archivos auxiliares
def leer_consentimientos(ruta: Path | None) -> dict[str, tuple[bool, date | None]]:
    """CSV: nombre_normalizado, consentimiento_informado (si/no), fecha_consentimiento."""
    if ruta is None:
        return {}
    salida: dict[str, tuple[bool, date | None]] = {}
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        for fila in csv.DictReader(f):
            nombre = (fila.get("nombre_normalizado") or "").strip()
            if not nombre:
                continue
            si = normalizar_texto(fila.get("consentimiento_informado") or "") in {
                "si",
                "s",
                "1",
                "true",
                "x",
            }
            f_txt = (fila.get("fecha_consentimiento") or "").strip()
            salida[clave_nombre(nombre)] = (si, date.fromisoformat(f_txt) if f_txt else None)
    return salida


class Decisiones:
    """Decisiones manuales. CSV con columnas tipo,clave,valor:

    posicion,opuesto-central,opuesto        (texto del Excel → posición del catálogo)
    nombre,Quispe Riveros Elard Edwin,Elard Edwin|Quispe Riveros   (nombres|apellidos)
    talla,unificar,moda       (una sola talla por jugador: la que aparece en más hojas)
    talla,empate,1.87         (valor a usar si hay empate entre las más frecuentes)
    derivada,31 Agosto!F15,aceptar   (se acepta el valor recalculado de una celda derivada)
    """

    def __init__(self) -> None:
        self.posiciones: dict[str, str] = {}
        self.nombres: dict[str, tuple[str, str]] = {}
        self.unificar_talla = False
        self.talla_empate: Decimal | None = None
        self.derivadas_aceptadas: set[tuple[str, str]] = set()

    @classmethod
    def leer(cls, ruta: Path | None) -> "Decisiones":
        d = cls()
        if ruta is None:
            return d
        with open(ruta, encoding="utf-8-sig", newline="") as f:
            for fila in csv.DictReader(f):
                tipo = normalizar_texto(fila.get("tipo") or "")
                clave, valor = (fila.get("clave") or "").strip(), (fila.get("valor") or "").strip()
                if tipo == "posicion" and clave and valor:
                    d.posiciones[normalizar_texto(clave)] = normalizar_texto(valor)
                elif tipo == "talla" and normalizar_texto(clave) == "unificar":
                    d.unificar_talla = normalizar_texto(valor) in {"moda", "si", "1"}
                elif tipo == "talla" and normalizar_texto(clave) == "empate" and valor:
                    d.talla_empate = Decimal(valor.replace(",", "."))
                elif tipo == "derivada" and "!" in clave:
                    hoja, celda = clave.rsplit("!", 1)
                    d.derivadas_aceptadas.add((normalizar_texto(hoja), celda.strip().upper()))
                elif tipo == "nombre" and clave and "|" in valor:
                    nombres, apellidos = (x.strip() for x in valor.split("|", 1))
                    d.nombres[clave_nombre(clave)] = (
                        nombre_propio(nombres),
                        nombre_propio(apellidos),
                    )
        return d


def mapear_posicion(
    texto: str | None, catalogo: list[str], decisiones: Decisiones
) -> tuple[str | None, str | None]:
    """(nombre de catálogo | None, motivo si no se pudo)."""
    if texto is None:
        return None, "sin posición en el Excel"
    n = normalizar_texto(texto)
    por_norm = {normalizar_texto(c): c for c in catalogo}
    n = decisiones.posiciones.get(n, n)
    n = ALIAS_POSICION.get(n, n)
    if n in por_norm:
        return por_norm[n], None
    return None, f"la posición '{texto.strip()}' no existe en el catálogo"


# ---------------------------------------------------------------- construcción del plan
def construir_plan(
    hojas: list[Hoja],
    omitidas: list[str],
    problemas: list[Problema],
    consentimientos: dict[str, tuple[bool, date | None]],
    decisiones: Decisiones,
    catalogo_posiciones: list[str],
    rangos_prueba: dict[str, tuple[Decimal, Decimal]],
) -> Plan:
    problemas = list(problemas)

    # --- 1. Jugadores (emparejados entre hojas por nombre normalizado)
    filas_por_clave: dict[str, list[FilaJugador]] = defaultdict(list)
    for h in hojas:
        vistos: set[str] = set()
        for fj in h.filas:
            if fj.clave in vistos:
                problemas.append(
                    Problema(
                        "jugador",
                        f"{ref_jugador(fj.clave)} aparece repetido en la fila {fj.fila}; se ignora.",
                        h.nombre,
                    )
                )
                continue
            vistos.add(fj.clave)
            filas_por_clave[fj.clave].append(fj)

    csv_por_conjunto = {frozenset(k.split()): v for k, v in consentimientos.items()}
    usados_csv: set[str] = set()
    jugadores: dict[str, JugadorPlan] = {}

    for clave, filas in filas_por_clave.items():
        primera = filas[0]
        completo = nombre_propio(primera.nombre_original)
        nombres, apellidos = decisiones.nombres.get(clave) or dividir_nombre(completo)

        edades = Counter(f.edad for f in filas if f.edad is not None)
        if len(edades) > 1:
            problemas.append(
                Problema(
                    "jugador", f"{ref_jugador(clave)}: edades distintas entre hojas {dict(edades)}."
                )
            )
        edad = edades.most_common(1)[0][0] if edades else None

        textos = Counter(f.posicion_texto for f in filas if f.posicion_texto)
        mapeadas = {t: mapear_posicion(t, catalogo_posiciones, decisiones) for t in textos}
        validas = Counter({m[0]: textos[t] for t, m in mapeadas.items() if m[0]})
        pos, motivo = None, None
        if len(validas) > 1:
            problemas.append(
                Problema(
                    "posicion",
                    f"{ref_jugador(clave)}: posiciones distintas entre hojas {dict(validas)}.",
                )
            )
        if validas:
            pos = validas.most_common(1)[0][0]
        else:
            t = textos.most_common(1)[0][0] if textos else None
            motivo = mapear_posicion(t, catalogo_posiciones, decisiones)[1] or "sin posición"
            problemas.append(
                Problema(
                    "posicion",
                    f"{ref_jugador(clave)}: {motivo}. Requiere decisión manual (archivo de "
                    f"decisiones, tipo 'posicion'); el jugador NO se carga hasta resolverlo.",
                )
            )
        sin_mapear = [t for t, m in mapeadas.items() if m[0] is None and validas]
        for t in sin_mapear:
            problemas.append(
                Problema("posicion", f"{ref_jugador(clave)}: texto de posición ignorado '{t}'.")
            )

        reg = consentimientos.get(clave)
        if reg is None:
            reg = csv_por_conjunto.get(frozenset(clave.split()))
        if reg is not None:
            usados_csv.add(clave)
        consiente = bool(reg and reg[0])
        if not consiente:
            problemas.append(
                Problema(
                    "consentimiento",
                    f"{ref_jugador(clave)}: sin consentimiento informado registrado; "
                    f"se carga el jugador pero NO sus mediciones.",
                )
            )
        if edad is None:
            problemas.append(Problema("jugador", f"{ref_jugador(clave)}: sin edad en el Excel."))

        jugadores[clave] = JugadorPlan(
            clave=clave,
            nombre_completo=completo,
            nombres=nombres,
            apellidos=apellidos,
            edad=edad,
            posicion=pos,
            motivo_omision=None if pos else motivo,
            consentimiento=consiente,
            fecha_consentimiento=reg[1] if reg and consiente else None,
        )

    huerfanos = [
        k
        for k in consentimientos
        if k not in jugadores
        and frozenset(k.split()) not in {frozenset(c.split()) for c in jugadores}
    ]
    if huerfanos:
        problemas.append(
            Problema(
                "consentimiento",
                f"{len(huerfanos)} entrada(s) del CSV de consentimientos no coinciden con ningún "
                f"jugador del Excel (revise la escritura del nombre).",
            )
        )

    # --- 2. Mediciones, antropometría, duraciones
    mediciones: list[MedicionPlan] = []
    antropos: list[AntropoPlan] = []
    duraciones: list[DuracionPlan] = []
    exclusiones: list[Exclusion] = []
    derivadas: list[FilaJugador] = []
    tallas: dict[str, dict[Decimal, list[str]]] = defaultdict(lambda: defaultdict(list))

    for h in hojas:
        vistos: set[str] = set()
        for fj in h.filas:
            if fj.clave in vistos:
                continue
            vistos.add(fj.clave)
            jp = jugadores[fj.clave]
            if fj.talla is not None:
                tallas[fj.clave][fj.talla].append(h.nombre)

            motivo_global = None
            if not jp.cargable:
                motivo_global = "jugador omitido (posición pendiente)"
            elif not jp.consentimiento:
                motivo_global = "sin consentimiento informado"

            def excluir(celda: str, codigo: str, motivo: str, h=h, fj=fj) -> None:
                exclusiones.append(Exclusion(h.nombre, celda, fj.clave, codigo, motivo))

            for v in fj.valores:
                if motivo_global:
                    excluir(v.celda, v.codigo, motivo_global)
                    continue
                if v.codigo == "DURACION":
                    lo, hi = RANGO_DURACION
                    if v.valor != v.valor.to_integral_value() or not lo <= v.valor <= hi:
                        excluir(v.celda, v.codigo, f"fuera de rango [{lo}, {hi}] o no entero")
                        problemas.append(
                            Problema(
                                "valor", f"{v.celda}: duración inválida '{v.texto}'.", h.nombre
                            )
                        )
                        continue
                    duraciones.append(
                        DuracionPlan(h.nombre, fj.clave, int(v.valor), v.celda, v.texto)
                    )
                    continue
                lo, hi = rangos_prueba[v.codigo]
                if not lo <= v.valor <= hi:
                    excluir(v.celda, v.codigo, f"fuera de rango [{lo}, {hi}]")
                    problemas.append(
                        Problema(
                            "valor",
                            f"{v.celda}: {ref_jugador(fj.clave)} {v.codigo}={v.texto} "
                            f"fuera de rango [{lo}, {hi}]; no se carga.",
                            h.nombre,
                        )
                    )
                    continue
                mediciones.append(
                    MedicionPlan(h.nombre, fj.clave, v.codigo, v.valor, v.celda, v.texto)
                )

            # Antropometría (talla y peso son obligatorias a la vez)
            if fj.talla is not None or fj.peso is not None:
                celda = fj.celda_talla or ""
                if motivo_global:
                    excluir(celda, "ANTROPOMETRIA", motivo_global)
                elif fj.talla is None or fj.peso is None:
                    excluir(celda, "ANTROPOMETRIA", "falta talla o peso")
                elif not (
                    RANGO_TALLA[0] <= fj.talla <= RANGO_TALLA[1]
                    and RANGO_PESO[0] <= fj.peso <= RANGO_PESO[1]
                ):
                    excluir(celda, "ANTROPOMETRIA", "talla o peso fuera de rango")
                    problemas.append(
                        Problema(
                            "valor",
                            f"{celda}: talla/peso fuera de rango ({fj.talla} m, {fj.peso} kg).",
                            h.nombre,
                        )
                    )
                else:
                    antropos.append(AntropoPlan(h.nombre, fj.clave, h.fecha, fj.talla, fj.peso))

            if h.tipo == "velocidad" and not motivo_global:
                derivadas.append(fj)

    # --- 3. Atípicos por prueba y jornada (IQR de Tukey)
    grupos: dict[tuple[str, str], list[MedicionPlan]] = defaultdict(list)
    for m in mediciones:
        grupos[(m.hoja, m.codigo)].append(m)
    for ms in grupos.values():
        lim = limites_iqr([float(m.valor) for m in ms])
        for m in ms:
            m.atipico = es_atipico(float(m.valor), lim)

    # --- 4. Inconsistencias de talla entre fechas
    for clave, por_valor in tallas.items():
        if len(por_valor) > 1:
            detalle = "; ".join(
                f"{v} m en {len(hs)} hoja(s) ({', '.join(hs[:3])}{'…' if len(hs) > 3 else ''})"
                for v, hs in sorted(por_valor.items())
            )
            elegida = None
            if decisiones.unificar_talla:
                conteo = {v: len(hs) for v, hs in por_valor.items()}
                maximo = max(conteo.values())
                empatadas = [v for v, c in conteo.items() if c == maximo]
                if len(empatadas) == 1:
                    elegida = empatadas[0]
                elif decisiones.talla_empate in empatadas:
                    elegida = decisiones.talla_empate
            if elegida is not None:
                for a in antropos:
                    if a.clave == clave:
                        a.talla = elegida
                resolucion = (
                    f"RESUELTO por decisión: talla unificada a {elegida} m (la más frecuente)."
                )
            else:
                resolucion = "Se cargan tal cual por fecha; revisar cuál es la correcta."
            problemas.append(
                Problema(
                    "talla",
                    f"{ref_jugador(clave)}: talla inconsistente entre fechas: {detalle}. {resolucion}",
                )
            )

    return Plan(
        hojas=hojas,
        jugadores=jugadores,
        mediciones=mediciones,
        antropometrias=antropos,
        duraciones=duraciones,
        exclusiones=exclusiones,
        problemas=problemas,
        hojas_omitidas=omitidas,
        filas_derivadas=derivadas,
        derivadas_aceptadas=decisiones.derivadas_aceptadas,
    )
