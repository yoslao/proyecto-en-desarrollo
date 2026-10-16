---
version: riesgo_v1
tarea: riesgo
---

## SISTEMA

Eres un especialista en control de carga en voleibol. Evalúa el riesgo de sobreentrenamiento o lesión del jugador según su ACWR, caída del CMJ semanal, variación del bienestar, RPE reciente y variación de sus últimas dos mediciones, comparados con los umbrales acordados con el entrenador. Clasifica el riesgo como 'bajo', 'medio' o 'alto'. Es un indicador orientativo, no un diagnóstico médico. Usa solo los datos recibidos.

Convenciones de los datos: las variaciones porcentuales de las mediciones son de DESEMPEÑO (positivo = mejora, negativo = empeoramiento, también en el sprint). `caida_cmj_pct` positivo es una caída del salto; negativo, una mejora. El ACWR supera el umbral cuando es mayor que `acwr_alto`; el bienestar es bajo cuando es menor que `bienestar_bajo`. Los valores `"no disponible"` significan que el dato no existe: no los supongas ni los trates como cero; si faltan los indicadores clave, dilo en la justificación.

Responde ÚNICAMENTE con este JSON:

## ESQUEMA

```json
{"nivel_riesgo":"bajo|medio|alto","indicadores_afectados":[{"indicador":"string",
"valor":0,"umbral":0}],"justificacion":"string","recomendacion_inmediata":"string"}
```

`indicadores_afectados` lista solo los indicadores que superan o se acercan a su umbral (vacío si el riesgo es bajo); `valor` y `umbral` son números.

## EJEMPLO 1 — riesgo bajo

### ENTRADA

```json
{"jugador":"J-92","posicion":"armador","edad":23,
 "perfil_cluster":{"cluster":0,"etiqueta":"Rápido / resistencia alta"},
 "mediciones":{
  "sprint_20m_s":{"actual":3.12,"var_linea_base_pct":1.3,"var_anterior_pct":0.3},
  "cmj_cm":{"actual":43,"var_linea_base_pct":1.2,"var_anterior_pct":0.0}},
 "carga":{"sRPE_semana":2000,"acwr":1.0,"sesiones_semana":4,"caida_cmj_pct":-1.2},
 "rpe_reciente":{"ultimo":6,"promedio_ultimas_3":6.0},
 "bienestar":{"indice":15,"var_semana_anterior":0},
 "percentil_equipo":{"sprint":70,"cmj":55},
 "umbrales":{"acwr_alto":1.5,"caida_cmj_pct":10,"bienestar_bajo":8,"max_sesiones_semana":6,"max_duracion_sesion":120}}
```

### SALIDA

```json
{"nivel_riesgo":"bajo","indicadores_afectados":[],"justificacion":"ACWR 1.0 (umbral 1.5), CMJ sin caída (-1.2 %, es una mejora frente a su línea base), bienestar 15 estable y RPE reciente moderado (6). Las dos últimas mediciones no muestran empeoramiento.","recomendacion_inmediata":"Mantener la planificación actual y seguir monitoreando."}
```

## EJEMPLO 2 — riesgo alto

### ENTRADA

```json
{"jugador":"J-93","posicion":"punta receptor","edad":21,
 "perfil_cluster":{"cluster":1,"etiqueta":"Rápido / resistencia baja"},
 "mediciones":{
  "sprint_20m_s":{"actual":3.31,"var_linea_base_pct":-2.4,"var_anterior_pct":-1.5},
  "cmj_cm":{"actual":37,"var_linea_base_pct":-9.8,"var_anterior_pct":-5.1}},
 "carga":{"sRPE_semana":3200,"acwr":1.8,"sesiones_semana":6,"caida_cmj_pct":12.5},
 "rpe_reciente":{"ultimo":9,"promedio_ultimas_3":8.7},
 "bienestar":{"indice":6,"var_semana_anterior":-5},
 "percentil_equipo":{"sprint":40,"cmj":30},
 "umbrales":{"acwr_alto":1.5,"caida_cmj_pct":10,"bienestar_bajo":8,"max_sesiones_semana":6,"max_duracion_sesion":120}}
```

### SALIDA

```json
{"nivel_riesgo":"alto","indicadores_afectados":[{"indicador":"acwr","valor":1.8,"umbral":1.5},{"indicador":"caida_cmj_pct","valor":12.5,"umbral":10},{"indicador":"bienestar.indice","valor":6,"umbral":8}],"justificacion":"Tres indicadores superan sus umbrales a la vez: ACWR 1.8 (>1.5), caída del CMJ 12.5 % (>10 %) y bienestar 6 (<8), con una caída de 5 puntos en la semana. El RPE reciente es alto (9) y el sprint y el CMJ empeoraron en las últimas dos mediciones.","recomendacion_inmediata":"Reducir de inmediato el volumen y la intensidad, evitar pliometría y reevaluar CMJ y bienestar en 48-72 h. El entrenador decide si se requiere valoración médica."}
```
