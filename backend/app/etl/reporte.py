"""Reporte de validación de la migración (Markdown). Nunca imprime nombres: solo códigos J-xx."""

import csv
import math
import random
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.fisiologia import velocidad_sprint_ms, vo2max_leger
from app.etl.carga import ResultadoCarga
from app.etl.modelos import Plan
from app.models import (
    Jugador,
    MedicionAntropometrica,
    RegistroSesionJugador,
    ResultadoPrueba,
    TipoPrueba,
)

TOLERANCIA_DERIVADAS = 0.1
TOLERANCIA_SUMA = Decimal("0.005")
PRUEBAS = ("SPRINT_20M", "NAVETTE", "SALTO_SC", "SALTO_BC", "SALTO_AI", "SALTO_PF")


def _f(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _ok(cond: bool) -> str:
    return "✅" if cond else "❌"


def _tabla(cabecera: list[str], filas: list[list]) -> str:
    out = ["| " + " | ".join(cabecera) + " |", "|" + "|".join(["---"] * len(cabecera)) + "|"]
    out += ["| " + " | ".join(str(c) for c in f) + " |" for f in filas]
    return "\n".join(out)


def _origen_primera_aparicion(plan: Plan) -> dict[str, str]:
    ref: dict[str, str] = {}
    for h in plan.hojas:
        for fj in h.filas:
            ref.setdefault(fj.clave, f"fila {fj.fila} de '{h.nombre}'")
    return ref


class _Render:
    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        self.ref = _origen_primera_aparicion(plan)

    def __call__(self, texto: str) -> str:
        def sub(m: re.Match) -> str:
            jp = self.plan.jugadores.get(m.group(1))
            if jp is not None and jp.codigo:
                return jp.codigo
            return f"jugador sin código ({self.ref.get(m.group(1), '?')})"

        return re.sub(r"⟦(.+?)⟧", sub, texto)


def _unicos_por_hoja(plan: Plan):
    """Filas de jugador sin duplicados dentro de cada hoja."""
    for h in plan.hojas:
        vistos: set[str] = set()
        for fj in h.filas:
            if fj.clave not in vistos:
                vistos.add(fj.clave)
                yield h, fj


def generar_reporte(
    db: Session,
    plan: Plan,
    res: ResultadoCarga,
    *,
    archivo: Path,
    universidad: str,
    dry_run: bool,
    fecha_hoy: date | None,
) -> tuple[str, bool]:
    render = _Render(plan)
    verificaciones: list[bool] = []
    L: list[str] = []

    # ------------------------------------------------------------------ cabecera
    cargables = [j for j in plan.jugadores.values() if j.cargable]
    con_cons = [j for j in cargables if j.consentimiento]
    L += [
        "# Reporte de migración del Excel",
        "",
        f"- **Generado:** {datetime.now():%d/%m/%Y %H:%M}",
        f"- **Modo:** {'DRY-RUN (todo se ejecutó y se revirtió; la BD no cambió)' if dry_run else 'CARGA REAL (transacción confirmada)'}",
        f"- **Archivo:** `{archivo.name}`",
        f"- **Universidad:** {universidad}",
        f"- **Hoja «hoy»:** {_f(fecha_hoy) if fecha_hoy else 'sin fecha (omitida si existe)'}",
        f"- **Hojas leídas:** {len(plan.hojas)} "
        f"({sum(h.tipo == 'salto' for h in plan.hojas)} de salto, "
        f"{sum(h.tipo == 'velocidad' for h in plan.hojas)} de velocidad); "
        f"**omitidas:** {', '.join(plan.hojas_omitidas) or 'ninguna'}",
        f"- **Jugadores en el Excel:** {len(plan.jugadores)} · cargados: {len(cargables)} "
        f"({res.jugadores_nuevos} nuevos, {res.jugadores_existentes} ya existentes) · "
        f"con consentimiento: {len(con_cons)} · omitidos por posición pendiente: "
        f"{len(plan.jugadores) - len(cargables)}",
        f"- **Filas insertadas:** {res.insertados} · **ya existían (idempotencia):** {res.ya_existian}",
        "",
    ]

    # ------------------------------------------------------------------ 1. Conteos por hoja
    origen: dict[str, Counter] = defaultdict(Counter)
    for h, fj in _unicos_por_hoja(plan):
        for v in fj.valores:
            origen[h.nombre]["dur" if v.codigo == "DURACION" else "prueba"] += 1
        if fj.talla is not None or fj.peso is not None:
            origen[h.nombre]["antro"] += 1
    excl: dict[str, Counter] = defaultdict(Counter)
    for e in plan.exclusiones:
        excl[e.hoja][
            (
                "dur"
                if e.codigo == "DURACION"
                else "antro" if e.codigo == "ANTROPOMETRIA" else "prueba"
            )
        ] += 1

    n_bd_jornada = dict(
        db.execute(
            select(ResultadoPrueba.id_jornada, func.count())
            .where(ResultadoPrueba.id_jornada.in_(res.jornadas.values()))
            .group_by(ResultadoPrueba.id_jornada)
        ).all()
    )
    n_bd_sesion = dict(
        db.execute(
            select(RegistroSesionJugador.id_sesion, func.count())
            .where(RegistroSesionJugador.id_sesion.in_(res.sesiones.values()))
            .group_by(RegistroSesionJugador.id_sesion)
        ).all()
    )
    filas = []
    for h in plan.hojas:
        o, x = origen[h.nombre], excl[h.nombre]
        esp_p, esp_d = o["prueba"] - x["prueba"], o["dur"] - x["dur"]
        bd_p = n_bd_jornada.get(res.jornadas[h.nombre], 0)
        bd_d = n_bd_sesion.get(res.sesiones.get(h.nombre, -1), 0)
        ok = esp_p == bd_p and esp_d == bd_d
        verificaciones.append(ok)
        filas.append(
            [
                h.nombre,
                _f(h.fecha),
                h.tipo,
                o["prueba"],
                x["prueba"],
                esp_p,
                bd_p,
                o["dur"],
                x["dur"],
                esp_d,
                bd_d,
                _ok(ok),
            ]
        )
    L += [
        "## 1. Conteos por hoja: origen frente a base de datos",
        "",
        "Esperado = origen − excluidos (sin consentimiento, fuera de rango o jugador omitido).",
        "",
        _tabla(
            [
                "Hoja",
                "Fecha",
                "Tipo",
                "Pruebas origen",
                "Excl.",
                "Esperadas",
                "BD",
                "Duración origen",
                "Excl.",
                "Esperadas",
                "BD",
                "OK",
            ],
            filas,
        ),
        "",
    ]

    # ------------------------------------------------------------------ 2. Conteos y sumas por prueba
    tipos = {t.id_tipo_prueba: t.codigo for t in db.scalars(select(TipoPrueba))}
    bd_prueba = {
        tipos[i]: (n, s, a)
        for i, n, s, a in db.execute(
            select(
                ResultadoPrueba.id_tipo_prueba,
                func.count(),
                func.sum(ResultadoPrueba.valor),
                func.avg(ResultadoPrueba.valor),
            )
            .where(ResultadoPrueba.id_jornada.in_(res.jornadas.values()))
            .group_by(ResultadoPrueba.id_tipo_prueba)
        ).all()
    }
    o_cod, x_cod = Counter(), Counter()
    for _h, fj in _unicos_por_hoja(plan):
        for v in fj.valores:
            if v.codigo in PRUEBAS:
                o_cod[v.codigo] += 1
    for e in plan.exclusiones:
        if e.codigo in PRUEBAS:
            x_cod[e.codigo] += 1
    suma_o: dict[str, Decimal] = defaultdict(Decimal)
    n_o: Counter = Counter()
    for m in plan.mediciones:
        suma_o[m.codigo] += m.valor
        n_o[m.codigo] += 1
    filas_n, filas_s = [], []
    for cod in PRUEBAS:
        if o_cod[cod] == 0 and cod not in bd_prueba:
            continue
        n_bd, s_bd, a_bd = bd_prueba.get(cod, (0, Decimal(0), Decimal(0)))
        ok_n = n_o[cod] == n_bd
        filas_n.append([cod, o_cod[cod], x_cod[cod], n_o[cod], n_bd, _ok(ok_n)])
        media_o = suma_o[cod] / n_o[cod] if n_o[cod] else Decimal(0)
        ok_s = abs(suma_o[cod] - Decimal(s_bd or 0)) <= TOLERANCIA_SUMA and (
            n_o[cod] == 0 or abs(media_o - Decimal(a_bd or 0)) <= TOLERANCIA_SUMA
        )
        filas_s.append(
            [cod, suma_o[cod], s_bd or 0, f"{media_o:.3f}", f"{Decimal(a_bd or 0):.3f}", _ok(ok_s)]
        )
        verificaciones += [ok_n, ok_s]

    # duración, talla y peso (restringidos a los jugadores/fechas/sesiones migrados)
    ids_jug = [j.id_jugador for j in cargables if j.consentimiento and j.id_jugador]
    fechas = {a.fecha for a in plan.antropometrias}
    dur_bd = db.execute(
        select(
            func.count(),
            func.sum(RegistroSesionJugador.duracion_efectiva_min),
            func.avg(RegistroSesionJugador.duracion_efectiva_min),
        ).where(RegistroSesionJugador.id_sesion.in_(res.sesiones.values()))
    ).one()
    suma_d = sum(Decimal(d.minutos) for d in plan.duraciones)
    media_d = suma_d / len(plan.duraciones) if plan.duraciones else Decimal(0)
    ok_dn = len(plan.duraciones) == dur_bd[0]
    ok_ds = abs(suma_d - Decimal(dur_bd[1] or 0)) <= TOLERANCIA_SUMA
    n_dur_o = sum(
        1 for _h, f in _unicos_por_hoja(plan) for v in f.valores if v.codigo == "DURACION"
    )
    n_dur_x = sum(1 for e in plan.exclusiones if e.codigo == "DURACION")
    filas_n.append(
        ["DURACION (min)", n_dur_o, n_dur_x, len(plan.duraciones), dur_bd[0], _ok(ok_dn)]
    )
    filas_s.append(
        [
            "DURACION (min)",
            suma_d,
            dur_bd[1] or 0,
            f"{media_d:.3f}",
            f"{Decimal(dur_bd[2] or 0):.3f}",
            _ok(ok_ds),
        ]
    )
    ant_bd = db.execute(
        select(
            func.count(),
            func.sum(MedicionAntropometrica.talla_m),
            func.sum(MedicionAntropometrica.peso_kg),
        ).where(
            MedicionAntropometrica.id_jugador.in_(ids_jug or [-1]),
            MedicionAntropometrica.fecha.in_(fechas or [date.min]),
        )
    ).one()
    suma_t = sum(a.talla for a in plan.antropometrias)
    suma_p = sum(a.peso for a in plan.antropometrias)
    ok_an = len(plan.antropometrias) == ant_bd[0]
    ok_at = (
        abs(suma_t - Decimal(ant_bd[1] or 0)) <= TOLERANCIA_SUMA
        and abs(suma_p - Decimal(ant_bd[2] or 0)) <= TOLERANCIA_SUMA
    )
    n_ant_o = sum(1 for h, f in _unicos_por_hoja(plan) if f.talla is not None or f.peso is not None)
    n_ant_x = sum(1 for e in plan.exclusiones if e.codigo == "ANTROPOMETRIA")
    filas_n.append(
        [
            "ANTROPOMETRÍA (talla+peso)",
            n_ant_o,
            n_ant_x,
            len(plan.antropometrias),
            ant_bd[0],
            _ok(ok_an),
        ]
    )
    filas_s.append(
        [
            "Talla (m), suma",
            suma_t,
            ant_bd[1] or 0,
            "—",
            "—",
            _ok(abs(suma_t - Decimal(ant_bd[1] or 0)) <= TOLERANCIA_SUMA),
        ]
    )
    filas_s.append(
        [
            "Peso (kg), suma",
            suma_p,
            ant_bd[2] or 0,
            "—",
            "—",
            _ok(abs(suma_p - Decimal(ant_bd[2] or 0)) <= TOLERANCIA_SUMA),
        ]
    )
    verificaciones += [ok_dn, ok_ds, ok_an, ok_at]

    L += [
        "## 2. Conteos por prueba: origen frente a base de datos",
        "",
        _tabla(["Prueba", "Origen", "Excluidos", "Esperados", "BD", "OK"], filas_n),
        "",
        "## 3. Suma y promedio por columna: origen (cargables) frente a base de datos",
        "",
        _tabla(["Columna", "Σ origen", "Σ BD", "Promedio origen", "Promedio BD", "OK"], filas_s),
        "",
    ]

    # ------------------------------------------------------------------ 4. Derivadas
    comparadas_v = comparadas_vo2 = 0
    fuera: list[list] = []
    max_dv = max_dvo2 = 0.0
    sin_vo2 = 0
    for fj in plan.filas_derivadas:
        v = {x.codigo: x for x in fj.valores}
        ref = render(f"⟦{fj.clave}⟧")
        if "SPRINT_20M" in v and fj.velocidad_excel is not None:
            calc = velocidad_sprint_ms(v["SPRINT_20M"].valor)
            d = abs(calc - float(fj.velocidad_excel))
            comparadas_v += 1
            max_dv = max(max_dv, d)
            if d > TOLERANCIA_DERIVADAS:
                fuera.append(
                    [
                        fj.hoja,
                        ref,
                        "Velocidad (m/s)",
                        fj.celda_velocidad,
                        fj.velocidad_excel,
                        f"{calc:.2f}",
                        f"{d:.2f}",
                    ]
                )
        if "NAVETTE" in v and fj.edad is not None:
            if fj.vo2_excel is None:
                sin_vo2 += 1
            else:
                calc = vo2max_leger(v["NAVETTE"].valor, fj.edad)
                d = abs(calc - float(fj.vo2_excel))
                comparadas_vo2 += 1
                max_dvo2 = max(max_dvo2, d)
                if d > TOLERANCIA_DERIVADAS:
                    fuera.append(
                        [
                            fj.hoja,
                            ref,
                            "VO₂máx",
                            fj.celda_vo2,
                            fj.vo2_excel,
                            f"{calc:.1f}",
                            f"{d:.2f}",
                        ]
                    )
    calidad = bool(fuera)
    L += [
        "## 4. Velocidad y VO₂máx recalculados frente a los del Excel",
        "",
        "Velocidad = 20 / tiempo; VO₂máx por Léger (1988) con V = 7.5 + 0.5·palier y la edad del Excel. "
        f"Tolerancia: diferencia ≤ {TOLERANCIA_DERIVADAS}.",
        "",
        f"- Velocidad: {comparadas_v} comparadas, diferencia máxima {max_dv:.3f} m/s.",
        f"- VO₂máx: {comparadas_vo2} comparadas, diferencia máxima {max_dvo2:.3f} ml/kg/min; "
        f"{sin_vo2} fila(s) sin VO₂máx en el Excel (no comparables).",
        f"- Resultado: {_ok(not fuera)} {len(fuera)} valor(es) fuera de tolerancia "
        "(es un hallazgo sobre los datos de origen, no un fallo de la carga).",
        "",
    ]
    if fuera:
        L += [
            _tabla(["Hoja", "Jugador", "Variable", "Celda", "Excel", "Recalculado", "Dif."], fuera),
            "",
        ]

    # ------------------------------------------------------------------ 5. Muestra del 10 %
    rng = random.Random(42)
    n_muestra = math.ceil(0.10 * len(plan.mediciones)) if plan.mediciones else 0
    muestra = rng.sample(plan.mediciones, n_muestra)
    orden = {h.nombre: i for i, h in enumerate(plan.hojas)}
    muestra.sort(key=lambda m: (orden[m.hoja], m.celda))
    tipo_id = {c: i for i, c in tipos.items()}
    filas_m = []
    ok_muestra = True
    for m in muestra:
        jp = plan.jugadores[m.clave]
        bd = db.scalar(
            select(ResultadoPrueba.valor).where(
                ResultadoPrueba.id_jornada == res.jornadas[m.hoja],
                ResultadoPrueba.id_jugador == jp.id_jugador,
                ResultadoPrueba.id_tipo_prueba == tipo_id[m.codigo],
                ResultadoPrueba.intento == 1,
            )
        )
        coincide = bd is not None and Decimal(bd) == m.valor
        ok_muestra &= coincide
        filas_m.append(
            [m.hoja, m.celda, jp.codigo, m.codigo, f"`{m.texto}`", bd, _ok(coincide), "[ ]"]
        )
    verificaciones.append(ok_muestra)
    L += [
        f"## 5. Muestra aleatoria del 10 % para verificación manual ({n_muestra} de {len(plan.mediciones)})",
        "",
        "Semilla fija (42): la muestra es reproducible. Abra el Excel en la celda indicada y marque la casilla.",
        "",
        _tabla(
            ["Hoja", "Celda", "Jugador", "Prueba", "Excel", "BD", "Coincide", "Verificado"], filas_m
        ),
        "",
    ]

    # ------------------------------------------------------------------ 6. Atípicos
    atip = [m for m in plan.mediciones if m.atipico]
    L += [
        "## 6. Valores atípicos (IQR de Tukey, por prueba y jornada)",
        "",
        f"{len(atip)} valor(es) marcados como `pendiente_confirmacion` (excluidos de indicadores, "
        "K-means y estadística hasta que el entrenador los confirme).",
        "",
        f"Los {len(atip)} marcados se concentran en {len({m.clave for m in atip})} jugador(es): "
        f"{', '.join(sorted({plan.jugadores[m.clave].codigo for m in atip}))}. Un valor atípico respecto "
        "al equipo no implica un error de medición (puede ser un rendimiento real distinto); "
        "el entrenador debe confirmarlos o corregirlos.",
        "",
    ]
    if atip:
        L += [
            _tabla(
                ["Hoja", "Celda", "Jugador", "Prueba", "Valor"],
                [
                    [m.hoja, m.celda, plan.jugadores[m.clave].codigo, m.codigo, f"`{m.texto}`"]
                    for m in atip
                ],
            ),
            "",
        ]

    # ------------------------------------------------------------------ 7. Pendientes y problemas
    ids_cargados = [j.id_jugador for j in cargables if j.id_jugador]
    sin_nac = set(
        db.scalars(
            select(Jugador.id_jugador).where(
                Jugador.id_jugador.in_(ids_cargados or [-1]), Jugador.fecha_nacimiento.is_(None)
            )
        )
    )
    pend_nac = [j.codigo for j in cargables if j.id_jugador in sin_nac]
    retro = [
        (m, plan.jugadores[m.clave])
        for m in plan.mediciones
        if plan.jugadores[m.clave].fecha_consentimiento
        and next(h.fecha for h in plan.hojas if h.nombre == m.hoja)
        < plan.jugadores[m.clave].fecha_consentimiento
    ]
    L += [
        "## 7. Decisiones y datos pendientes",
        "",
        f"- **Fecha de nacimiento pendiente** (el Excel solo trae la edad): {len(pend_nac)} jugador(es) "
        f"({', '.join(sorted(pend_nac)) or '—'}). Complétela desde la ficha del jugador; mientras tanto la "
        "edad se informa como «no disponible» a la IA.",
        f"- **Posición sin resolver:** {len(plan.jugadores) - len(cargables)} jugador(es) no cargados "
        "(ver problemas, categoría `posicion`).",
        f"- **Consentimiento posterior a la medición:** {len(retro)} medición(es) con fecha anterior a la "
        "fecha de consentimiento registrada. Se cargan por decisión del investigador, pero conviene "
        "documentar que el consentimiento fue retroactivo.",
        "- **División nombres/apellidos:** se aplicó la regla «2 primeras palabras = apellidos». "
        "Revise `data/processed/mapa_seudonimos.csv` y corrija las excepciones con el archivo de decisiones.",
        "",
        "## 8. Problemas encontrados",
        "",
    ]
    todos = plan.problemas + res.problemas
    if fuera:
        from app.etl.modelos import Problema

        todos = todos + [
            Problema(
                "derivadas",
                f"{f[3]}: {f[2]} del Excel ({f[4]}) difiere del recalculado ({f[5]}) en {f[6]}; "
                f"revise el dato de origen (¿tiempo o valor mal digitado?). Jugador: {f[1]}.",
                f[0],
            )
            for f in fuera
        ]
    if not todos:
        L.append("Ninguno.")
    por_cat: dict[str, list] = defaultdict(list)
    for p in todos:
        por_cat[p.categoria].append(p)
    for cat in sorted(por_cat):
        L += [f"### {cat} ({len(por_cat[cat])})", ""]
        L += [
            f"- {('**' + p.hoja + '**: ') if p.hoja else ''}{render(p.detalle)}"
            for p in por_cat[cat]
        ]
        L.append("")

    ok_global = all(verificaciones)
    L += [
        "## Resultado de la validación",
        "",
        f"- Integridad de la carga (conteos, sumas, promedios y muestra): {_ok(ok_global)} "
        + (
            "todo coincide con el origen."
            if ok_global
            else "HAY DIFERENCIAS: revisar las secciones marcadas con ❌."
        ),
        "- Calidad de los datos de origen (velocidad y VO₂máx recalculados): "
        f"{_ok(not calidad)} {len(fuera)} valor(es) fuera de tolerancia.",
        "",
    ]
    return "\n".join(L), ok_global


def escribir_mapa_seudonimos(plan: Plan, ruta: Path) -> None:
    """Tabla código ↔ nombre. Contiene datos personales: queda en data/processed (fuera de git)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "codigo",
                "nombre_en_excel",
                "nombres",
                "apellidos",
                "posicion",
                "consentimiento",
                "edad_excel",
                "fecha_nacimiento",
            ]
        )
        for j in sorted((j for j in plan.jugadores.values() if j.codigo), key=lambda j: j.codigo):
            w.writerow(
                [
                    j.codigo,
                    j.nombre_completo,
                    j.nombres,
                    j.apellidos,
                    j.posicion,
                    "si" if j.consentimiento else "no",
                    j.edad,
                    "PENDIENTE",
                ]
            )
