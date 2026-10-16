"""Ejecución y consulta del K-means (Fase 4)."""

from datetime import date
from decimal import Decimal

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import clustering as km
from app.analytics.indicadores import puntos_por_jugador
from app.models import (
    AsignacionCluster,
    ConfiguracionSistema,
    EjecucionClustering,
    Jugador,
    TipoPrueba,
    Usuario,
)
from app.schemas.clustering import (
    AsignacionOut,
    ClusteringIn,
    ClusteringOut,
    ClusteringResumenOut,
    ClusterJugadorOut,
    ClusterOut,
    SiluetaK,
    SinAsignarOut,
)

CLAVE_VARIABLES = {
    "principal": "KMEANS_VARIABLES",
    "exploratorio": "KMEANS_VARIABLES_EXPLORATORIO",
}
MINIMO_JUGADORES = 3


def leer_config(db: Session) -> dict[str, str]:
    return {c.clave: c.valor for c in db.scalars(select(ConfiguracionSistema))}


def _lista(valor: str) -> list[str]:
    return [x.strip().upper() for x in valor.split(",") if x.strip()]


def parametros(db: Session, modo: str) -> dict:
    cfg = leer_config(db)
    try:
        return {
            "variables": _lista(cfg.get(CLAVE_VARIABLES[modo], "")),
            "k_candidatos": [int(x) for x in _lista(cfg.get("KMEANS_K_CANDIDATOS", "2,3,4"))],
            "random_state": int(cfg.get("KMEANS_RANDOM_STATE", "42")),
            "silueta_minima": float(cfg.get("KMEANS_SILUETA_MIN", "0.25")),
        }
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Configuración del K-means inválida: {e}",
        ) from e


def _ultimo_en_rango(puntos, desde: date | None, hasta: date | None):
    dentro = [
        p
        for p in puntos
        if (desde is None or p.fecha >= desde) and (hasta is None or p.fecha <= hasta)
    ]
    return dentro[-1] if dentro else None  # `puntos` ya viene ordenado por fecha


def ejecutar(db: Session, usuario: Usuario, datos: ClusteringIn) -> ClusteringOut:
    prm = parametros(db, datos.modo)
    variables: list[str] = prm["variables"]
    tipos = {t.codigo: t for t in db.scalars(select(TipoPrueba))}
    desconocidas = [v for v in variables if v not in tipos]
    if len(variables) < 2 or desconocidas:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Variables del K-means inválidas: se necesitan al menos 2 pruebas válidas"
            + (f" (desconocidas: {', '.join(desconocidas)})" if desconocidas else ""),
        )

    jugadores = list(
        db.scalars(
            select(Jugador)
            .where(Jugador.activo.is_(True), Jugador.consentimiento_informado.is_(True))
            .order_by(Jugador.codigo_seudonimo)
        )
    )
    puntos = puntos_por_jugador(db, [j.id_jugador for j in jugadores])

    completos, sin_asignar = [], []
    faltan_por_var = dict.fromkeys(variables, 0)
    for j in jugadores:
        elegidos = {}
        for v in variables:
            p = _ultimo_en_rango(
                puntos.get(j.id_jugador, {}).get(v, []), datos.fecha_desde, datos.fecha_hasta
            )
            if p is not None:
                elegidos[v] = p
        faltantes = [v for v in variables if v not in elegidos]
        for v in faltantes:
            faltan_por_var[v] += 1
        if faltantes:
            sin_asignar.append((j, faltantes))
        else:
            completos.append((j, elegidos))

    if len(completos) < MINIMO_JUGADORES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "mensaje": (
                    f"Solo {len(completos)} jugador(es) tienen todas las variables en el rango "
                    f"indicado; se necesitan al menos {MINIMO_JUGADORES}."
                ),
                "variables": variables,
                "jugadores_sin_dato_por_variable": faltan_por_var,
            },
        )

    X = np.array([[p[v].valor for v in variables] for _, p in completos], dtype=float)
    signos = [1 if tipos[v].mayor_es_mejor else -1 for v in variables]
    try:
        r = km.ajustar(X, signos, prm["k_candidatos"], prm["random_state"])
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    z_or = r.centroides_z * np.array(signos)
    etiquetas = km.etiquetas_unicas(
        [km.etiquetar(dict(zip(variables, z_or[c], strict=True))) for c in range(r.k)]
    )

    advertencias = [
        f"Con {len(completos)} jugadores el resultado es un perfil descriptivo del grupo, "
        "no un modelo predictivo."
    ]
    advertencias += km.advertencias_de_tamano(r.tamanos, etiquetas)
    silueta_baja = r.silueta < prm["silueta_minima"]
    if silueta_baja:
        advertencias.insert(
            0,
            f"Índice de silueta {r.silueta:.3f} < {prm['silueta_minima']:.2f}: los grupos no están "
            "bien separados; interprételos con cautela.",
        )
    if datos.modo == "exploratorio":
        advertencias.append(
            "Análisis exploratorio: usa variables distintas de las configuradas como principales "
            f"({', '.join(variables)})."
        )
    if sin_asignar:
        advertencias.append(
            f"{len(sin_asignar)} jugador(es) sin todas las variables en el rango quedaron sin asignar."
        )

    ejecucion = EjecucionClustering(
        fecha_desde=datos.fecha_desde,
        fecha_hasta=datos.fecha_hasta,
        k=r.k,
        silueta=Decimal(f"{r.silueta:.3f}"),
        variables=variables,
        centroides=[
            {
                "cluster": c,
                "etiqueta": etiquetas[c],
                "n": r.tamanos[c],
                "centroide": {
                    v: round(float(r.centroides[c][i]), 3) for i, v in enumerate(variables)
                },
                "z": {v: round(float(r.centroides_z[c][i]), 3) for i, v in enumerate(variables)},
            }
            for c in range(r.k)
        ],
        etiquetas={str(c): etiquetas[c] for c in range(r.k)},
        jornadas_usadas={
            str(j.id_jugador): {
                v: {"id_jornada": p.id_jornada, "fecha": p.fecha.isoformat()}
                for v, p in elegidos.items()
            }
            for j, elegidos in completos
        },
        diagnostico={
            "modo": datos.modo,
            "silueta_por_k": {str(k): round(s, 4) for k, s in r.silueta_por_k.items()},
            "silueta_minima": prm["silueta_minima"],
            "random_state": prm["random_state"],
            "advertencias": advertencias,
            "sin_asignar": [
                {"id_jugador": j.id_jugador, "faltantes": faltantes} for j, faltantes in sin_asignar
            ],
        },
        id_usuario=usuario.id_usuario,
    )
    db.add(ejecucion)
    db.flush()
    for i, (j, _) in enumerate(completos):
        db.add(
            AsignacionCluster(
                id_ejecucion=ejecucion.id_ejecucion,
                id_jugador=j.id_jugador,
                cluster=int(r.cluster[i]),
                distancia_centroide=Decimal(f"{r.distancias[i]:.3f}"),
            )
        )
    db.flush()
    return a_salida(db, ejecucion)


def a_salida(db: Session, e: EjecucionClustering) -> ClusteringOut:
    codigos = {j.id_jugador: j.codigo_seudonimo for j in db.scalars(select(Jugador))}
    diag = e.diagnostico or {}
    etiquetas = {int(k): v for k, v in (e.etiquetas or {}).items()}
    asign = db.scalars(
        select(AsignacionCluster).where(AsignacionCluster.id_ejecucion == e.id_ejecucion)
    ).all()
    asign.sort(key=lambda a: (a.cluster, codigos.get(a.id_jugador, "")))
    minima = float(diag.get("silueta_minima", 0.25))
    return ClusteringOut(
        id_ejecucion=e.id_ejecucion,
        fecha_hora=e.fecha_hora,
        modo=diag.get("modo", "principal"),
        k=e.k,
        silueta=float(e.silueta),
        silueta_minima=minima,
        silueta_baja=float(e.silueta) < minima,
        variables=list(e.variables),
        fecha_desde=e.fecha_desde,
        fecha_hasta=e.fecha_hasta,
        k_evaluados=[
            SiluetaK(k=int(k), silueta=s) for k, s in sorted(diag.get("silueta_por_k", {}).items())
        ],
        advertencias=diag.get("advertencias", []),
        clusters=[ClusterOut(**c) for c in e.centroides],
        asignaciones=[
            AsignacionOut(
                id_jugador=a.id_jugador,
                codigo_seudonimo=codigos.get(a.id_jugador, "?"),
                cluster=a.cluster,
                etiqueta=etiquetas.get(a.cluster, ""),
                distancia_centroide=float(a.distancia_centroide),
                jornadas_usadas=(e.jornadas_usadas or {}).get(str(a.id_jugador), {}),
            )
            for a in asign
        ],
        sin_asignar=[
            SinAsignarOut(
                id_jugador=s["id_jugador"],
                codigo_seudonimo=codigos.get(s["id_jugador"], "?"),
                faltantes=s["faltantes"],
            )
            for s in diag.get("sin_asignar", [])
        ],
    )


def ultima_ejecucion(db: Session) -> EjecucionClustering | None:
    return db.scalar(
        select(EjecucionClustering).order_by(
            EjecucionClustering.fecha_hora.desc(), EjecucionClustering.id_ejecucion.desc()
        )
    )


def historial(db: Session, limite: int) -> list[ClusteringResumenOut]:
    n_asig = dict(
        db.execute(
            select(AsignacionCluster.id_ejecucion, func.count()).group_by(
                AsignacionCluster.id_ejecucion
            )
        ).all()
    )
    filas = db.scalars(
        select(EjecucionClustering)
        .order_by(EjecucionClustering.fecha_hora.desc(), EjecucionClustering.id_ejecucion.desc())
        .limit(limite)
    )
    return [
        ClusteringResumenOut(
            id_ejecucion=e.id_ejecucion,
            fecha_hora=e.fecha_hora,
            modo=(e.diagnostico or {}).get("modo", "principal"),
            k=e.k,
            silueta=float(e.silueta),
            variables=list(e.variables),
            n_asignados=n_asig.get(e.id_ejecucion, 0),
        )
        for e in filas
    ]


def cluster_de_jugador(db: Session, id_jugador: int) -> ClusterJugadorOut | None:
    """Cluster y etiqueta del jugador en la última ejecución (None si no quedó asignado)."""
    e = ultima_ejecucion(db)
    if e is None:
        return None
    a = db.scalar(
        select(AsignacionCluster).where(
            AsignacionCluster.id_ejecucion == e.id_ejecucion,
            AsignacionCluster.id_jugador == id_jugador,
        )
    )
    j = db.get(Jugador, id_jugador)
    if a is None or j is None:
        return None
    return ClusterJugadorOut(
        id_jugador=id_jugador,
        codigo_seudonimo=j.codigo_seudonimo,
        id_ejecucion=e.id_ejecucion,
        fecha_hora=e.fecha_hora,
        cluster=a.cluster,
        etiqueta=(e.etiquetas or {}).get(str(a.cluster), ""),
        modo=(e.diagnostico or {}).get("modo", "principal"),
    )
