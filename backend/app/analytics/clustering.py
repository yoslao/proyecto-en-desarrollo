"""K-means para perfiles de rendimiento (Fase 4). Núcleo numérico sin acceso a BD.

Con ~24 jugadores el resultado es un PERFIL DESCRIPTIVO del grupo, no un modelo predictivo.

- Estandariza con StandardScaler y prueba cada k candidato (por defecto 2, 3 y 4) con
  `n_init=10` y `random_state` fijo; elige el k de mayor índice de silueta (empate → k menor).
- Los clusters se renumeran de mejor a peor perfil global (0 = el mejor), de modo que el número
  tenga significado estable entre ejecuciones.
- Etiquetas legibles: se compara el centroide con la media del grupo (puntaje z orientado, donde
  positivo = mejor; en el sprint se invierte porque menos tiempo es mejor). Por dimensión
  (velocidad, resistencia, salto) se usa z > +0.5 «alto», z < −0.5 «bajo», y se omite lo medio.
"""

from dataclasses import dataclass

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

N_INIT = 10
UMBRAL_Z = 0.5

# Dimensión de cada prueba, para resumir varias variables de salto en un solo descriptor.
DIMENSION = {"SPRINT_20M": "velocidad", "NAVETTE": "resistencia"}
DESCRIPTORES = {
    ("velocidad", "alto"): "Rápido",
    ("velocidad", "bajo"): "Lento",
    ("resistencia", "alto"): "resistencia alta",
    ("resistencia", "bajo"): "resistencia baja",
    ("salto", "alto"): "salto alto",
    ("salto", "bajo"): "salto bajo",
}
ORDEN_DIMENSIONES = ("velocidad", "resistencia", "salto")


def dimension_de(codigo: str) -> str:
    return DIMENSION.get(codigo, "salto")


@dataclass
class ResultadoKMeans:
    k: int
    silueta: float
    silueta_por_k: dict[int, float]
    cluster: np.ndarray  # cluster (ya renumerado) de cada fila
    distancias: np.ndarray  # distancia euclídea al centroide, en espacio estandarizado
    centroides_z: np.ndarray  # (k, n_vars) estandarizado, renumerado
    centroides: np.ndarray  # (k, n_vars) en unidades originales
    tamanos: list[int]


def k_validos(candidatos: list[int], n: int) -> list[int]:
    """La silueta exige 2 ≤ k ≤ n−1."""
    return sorted({k for k in candidatos if 2 <= k <= n - 1})


def ajustar(
    X: np.ndarray, signos: list[int], candidatos: list[int], random_state: int
) -> ResultadoKMeans:
    """Ajusta K-means para cada k válido y devuelve el de mayor silueta.

    `signos[j]` es +1 si más es mejor en la variable j y −1 si menos es mejor.
    """
    n = X.shape[0]
    ks = k_validos(candidatos, n)
    if not ks:
        raise ValueError(f"Con {n} jugadores no hay ningún k válido entre {candidatos}")
    escalador = StandardScaler().fit(X)
    Z = escalador.transform(X)

    ajustes: dict[int, tuple[KMeans, float]] = {}
    for k in ks:
        km = KMeans(n_clusters=k, n_init=N_INIT, random_state=random_state).fit(Z)
        if len(set(km.labels_)) < 2:  # degenerado (p. ej. datos repetidos)
            continue
        ajustes[k] = (km, float(silhouette_score(Z, km.labels_)))
    if not ajustes:
        raise ValueError("Los datos no permiten formar al menos 2 grupos distintos")

    k_mejor = max(ajustes, key=lambda k: (round(ajustes[k][1], 6), -k))
    km, sil = ajustes[k_mejor]

    # Renumera de mejor a peor perfil global (media del z orientado del centroide).
    puntaje = (km.cluster_centers_ * np.array(signos)).mean(axis=1)
    orden = sorted(range(k_mejor), key=lambda c: (-puntaje[c], c))
    nuevo = {viejo: i for i, viejo in enumerate(orden)}
    cluster = np.array([nuevo[c] for c in km.labels_])
    centroides_z = km.cluster_centers_[orden]
    distancias = np.linalg.norm(Z - centroides_z[cluster], axis=1)
    return ResultadoKMeans(
        k=k_mejor,
        silueta=sil,
        silueta_por_k={k: v[1] for k, v in ajustes.items()},
        cluster=cluster,
        distancias=distancias,
        centroides_z=centroides_z,
        centroides=escalador.inverse_transform(centroides_z),
        tamanos=[int((cluster == c).sum()) for c in range(k_mejor)],
    )


def etiquetar(z_orientado: dict[str, float]) -> str:
    """Etiqueta legible a partir del z orientado (positivo = mejor) de cada variable.

    Ej.: {'SPRINT_20M': 0.9, 'NAVETTE': -0.8} → 'Rápido / resistencia baja'.
    """
    por_dim: dict[str, list[float]] = {}
    for codigo, z in z_orientado.items():
        por_dim.setdefault(dimension_de(codigo), []).append(z)
    partes = []
    for dim in ORDEN_DIMENSIONES:
        if dim not in por_dim:
            continue
        z = float(np.mean(por_dim[dim]))
        if z > UMBRAL_Z:
            partes.append(DESCRIPTORES[(dim, "alto")])
        elif z < -UMBRAL_Z:
            partes.append(DESCRIPTORES[(dim, "bajo")])
    if not partes:
        return "Perfil medio"
    texto = " / ".join(partes)
    return texto[0].upper() + texto[1:]


def etiquetas_unicas(etiquetas: list[str]) -> list[str]:
    """Si dos clusters comparten etiqueta, se distinguen con el número de grupo."""
    return [
        f"{e} (grupo {i + 1})" if etiquetas.count(e) > 1 else e for i, e in enumerate(etiquetas)
    ]


def advertencias_de_tamano(tamanos: list[int], etiquetas: list[str]) -> list[str]:
    """Avisa de los grupos con un solo jugador: son perfiles individuales, no grupos."""
    return [
        f"El grupo «{etiquetas[c]}» tiene un solo jugador: es un perfil individual, no un grupo."
        for c, n in enumerate(tamanos)
        if n == 1
    ]
