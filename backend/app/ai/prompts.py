"""Carga de plantillas de prompt versionadas desde /prompts (rutina_v1.md, riesgo_v1.md)."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import RAIZ

DIRECTORIO = RAIZ / "prompts"
_RE_BLOQUE_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)
_RE_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class Plantilla:
    version: str
    tarea: str
    sistema: str  # incluye el esquema de respuesta
    ejemplos: tuple[tuple[str, str], ...]  # (entrada JSON compacto, salida JSON compacto)

    def mensajes(self, contexto_json: str, **variables) -> list[dict]:
        """[system, (user, assistant)* few-shot, user]. `variables` sustituyen {nombre} en el sistema."""
        sistema = self.sistema
        for k, v in variables.items():
            sistema = sistema.replace("{" + k + "}", str(v))
        msgs = [{"role": "system", "content": sistema}]
        for entrada, salida in self.ejemplos:
            msgs.append({"role": "user", "content": entrada})
            msgs.append({"role": "assistant", "content": salida})
        msgs.append({"role": "user", "content": contexto_json})
        return msgs


def _compacto(bloque: str) -> str:
    return json.dumps(json.loads(bloque), ensure_ascii=False, separators=(",", ":"))


def _primer_json(texto: str, donde: str) -> str:
    m = _RE_BLOQUE_JSON.search(texto)
    if not m:
        raise ValueError(f"Falta un bloque ```json en {donde}")
    return m.group(1).strip()


@lru_cache
def cargar(nombre: str) -> Plantilla:
    """`nombre` = 'rutina_v1' | 'riesgo_v1'. Valida que todos los ejemplos sean JSON."""
    ruta = DIRECTORIO / f"{nombre}.md"
    texto = ruta.read_text(encoding="utf-8")
    front = _RE_FRONT.match(texto)
    if not front:
        raise ValueError(f"{ruta.name}: falta el encabezado con la versión")
    meta = dict(line.split(":", 1) for line in front.group(1).splitlines() if ":" in line)
    version, tarea = meta["version"].strip(), meta["tarea"].strip()
    if version != nombre:
        raise ValueError(
            f"{ruta.name}: la versión declarada ({version}) no coincide con el archivo"
        )

    cuerpo = texto[front.end() :]
    secciones: dict[str, str] = {}
    for bloque in re.split(r"^## ", cuerpo, flags=re.MULTILINE)[1:]:
        titulo, _, resto = bloque.partition("\n")
        secciones[titulo.strip()] = resto

    sistema = secciones["SISTEMA"].strip()
    esquema = _primer_json(secciones["ESQUEMA"], "ESQUEMA")
    sistema = f"{sistema}\n\n{esquema}"

    ejemplos = []
    for titulo, resto in secciones.items():
        if not titulo.startswith("EJEMPLO"):
            continue
        entrada_txt = resto.split("### SALIDA")[0]
        salida_txt = resto.split("### SALIDA")[1]
        ejemplos.append(
            (
                _compacto(_primer_json(entrada_txt, f"{titulo}/ENTRADA")),
                _compacto(_primer_json(salida_txt, f"{titulo}/SALIDA")),
            )
        )
    return Plantilla(version=version, tarea=tarea, sistema=sistema, ejemplos=tuple(ejemplos))
