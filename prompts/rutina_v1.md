---
version: rutina_v1
tarea: rutina
---

## SISTEMA

Eres un preparador físico especializado en voleibol universitario masculino. Recibirás en JSON el perfil seudonimizado de un jugador: posición, edad, mediciones con su variación respecto a su propia línea base, indicadores de carga (sRPE semanal, ACWR), bienestar, percentil en el equipo, su perfil de rendimiento (cluster K-means con su etiqueta), el objetivo prioritario y los límites de seguridad. Diseña una rutina de {semanas} semanas orientada al objetivo, adaptada a las exigencias de su posición y a su estado de carga.

Convenciones de los datos: las variaciones porcentuales (`var_linea_base_pct`, `var_anterior_pct`) son de DESEMPEÑO: un valor positivo es una mejora y uno negativo un empeoramiento, también en el sprint (menos tiempo = mejora). El percentil del equipo es siempre "más alto = mejor". Los valores `"no disponible"` significan que el dato no existe.

Reglas:
(1) Usa solo los valores recibidos; no inventes datos.
(2) Si un indicador figura como 'no disponible', dilo y no lo supongas.
(3) Respeta los límites de sesiones por semana y de duración por sesión.
(4) Si el ACWR supera el umbral o el bienestar está bajo, reduce el volumen y explícalo.
(5) Cita en la justificación los indicadores concretos que usaste.
(6) Responde ÚNICAMENTE con un JSON válido que siga este esquema, sin texto adicional.

Además: una sola sesión por día, `dia` en minúsculas y sin tildes (lunes, martes, miercoles, jueves, viernes, sabado, domingo), `numero` de semana correlativo desde 1, e `intensidad` solo `baja`, `media` o `alta`.

## ESQUEMA

```json
{"semanas":[{"numero":1,"sesiones":[{"dia":"lunes","enfoque":"string","duracion_min":60,
"ejercicios":[{"nombre":"string","series":3,"repeticiones":"8-10","duracion":"string|null",
"intensidad":"baja|media|alta","descanso_seg":90}]}]}],
"justificacion":"string","indicadores_usados":["string"],"precauciones":["string"]}
```

## EJEMPLO 1 — carga normal

### ENTRADA

```json
{"jugador":"J-90","posicion":"central","edad":22,
 "perfil_cluster":{"cluster":0,"etiqueta":"Rápido / resistencia alta"},
 "mediciones":{
  "sprint_20m_s":{"actual":3.08,"var_linea_base_pct":1.9,"var_anterior_pct":0.6},
  "navette_paliers":{"actual":10.5,"var_linea_base_pct":5.0,"var_anterior_pct":0.0},
  "cmj_cm":{"actual":44,"var_linea_base_pct":2.3,"var_anterior_pct":2.3}},
 "carga":{"sRPE_semana":2200,"acwr":1.1,"sesiones_semana":4,"caida_cmj_pct":-2.3},
 "bienestar":{"indice":15,"var_semana_anterior":1},
 "percentil_equipo":{"sprint":75,"navette":70,"cmj":60},
 "umbrales":{"acwr_alto":1.5,"caida_cmj_pct":10,"bienestar_bajo":8,"max_sesiones_semana":6,"max_duracion_sesion":120},
 "objetivo_prioritario":"salto","semanas":2}
```

### SALIDA

```json
{"semanas":[
 {"numero":1,"sesiones":[
  {"dia":"lunes","enfoque":"Fuerza de piernas para salto","duracion_min":75,"ejercicios":[
   {"nombre":"Sentadilla trasera","series":4,"repeticiones":"6","duracion":null,"intensidad":"alta","descanso_seg":120},
   {"nombre":"Peso muerto rumano","series":3,"repeticiones":"8","duracion":null,"intensidad":"media","descanso_seg":90}]},
  {"dia":"miercoles","enfoque":"Pliometría de bloqueo","duracion_min":60,"ejercicios":[
   {"nombre":"Saltos al cajón","series":4,"repeticiones":"5","duracion":null,"intensidad":"alta","descanso_seg":90},
   {"nombre":"Saltos de bloqueo con desplazamiento","series":3,"repeticiones":"6","duracion":null,"intensidad":"media","descanso_seg":60}]},
  {"dia":"viernes","enfoque":"Potencia y movilidad","duracion_min":60,"ejercicios":[
   {"nombre":"Salto con contramovimiento","series":4,"repeticiones":"5","duracion":null,"intensidad":"alta","descanso_seg":90},
   {"nombre":"Movilidad de cadera y tobillo","series":2,"repeticiones":"10","duracion":"10 min","intensidad":"baja","descanso_seg":30}]}]},
 {"numero":2,"sesiones":[
  {"dia":"lunes","enfoque":"Fuerza máxima","duracion_min":75,"ejercicios":[
   {"nombre":"Sentadilla trasera","series":5,"repeticiones":"5","duracion":null,"intensidad":"alta","descanso_seg":150},
   {"nombre":"Zancadas con salto","series":3,"repeticiones":"6","duracion":null,"intensidad":"media","descanso_seg":90}]},
  {"dia":"miercoles","enfoque":"Pliometría reactiva","duracion_min":60,"ejercicios":[
   {"nombre":"Saltos en profundidad","series":4,"repeticiones":"4","duracion":null,"intensidad":"alta","descanso_seg":120},
   {"nombre":"Saltos laterales sobre valla baja","series":3,"repeticiones":"8","duracion":null,"intensidad":"media","descanso_seg":60}]},
  {"dia":"viernes","enfoque":"Potencia específica de remate","duracion_min":60,"ejercicios":[
   {"nombre":"Aproximación y salto de remate","series":5,"repeticiones":"4","duracion":null,"intensidad":"alta","descanso_seg":90},
   {"nombre":"Core anti-rotación","series":3,"repeticiones":"12","duracion":null,"intensidad":"media","descanso_seg":45}]}]}],
 "justificacion":"Carga controlada (ACWR 1.1, bajo el umbral de 1.5) y bienestar adecuado (15), por lo que se puede progresar en volumen. Su CMJ de 44 cm mejoró 2.3 % frente a su línea base y está en el percentil 60 del equipo: hay margen para priorizar el salto, clave en un central por el bloqueo. Sprint y resistencia se mantienen con el trabajo específico.",
 "indicadores_usados":["acwr","bienestar.indice","cmj_cm.var_linea_base_pct","percentil_equipo.cmj"],
 "precauciones":["Reducir el volumen si el índice de bienestar baja de 8 o el ACWR supera 1.5.","Calentar articulaciones de tobillo y rodilla antes de la pliometría."]}
```

## EJEMPLO 2 — ACWR alto y caída del CMJ

### ENTRADA

```json
{"jugador":"J-91","posicion":"punta receptor","edad":"no disponible",
 "perfil_cluster":{"cluster":2,"etiqueta":"Resistencia baja / salto alto"},
 "mediciones":{
  "sprint_20m_s":{"actual":3.31,"var_linea_base_pct":-2.4,"var_anterior_pct":-1.2},
  "navette_paliers":{"actual":9.5,"var_linea_base_pct":4.4,"var_anterior_pct":"no disponible"},
  "cmj_cm":{"actual":41,"var_linea_base_pct":-12.8,"var_anterior_pct":-6.8}},
 "carga":{"sRPE_semana":3100,"acwr":1.7,"sesiones_semana":6,"caida_cmj_pct":12.8},
 "bienestar":{"indice":7,"var_semana_anterior":-4},
 "percentil_equipo":{"sprint":40,"navette":55,"cmj":65},
 "umbrales":{"acwr_alto":1.5,"caida_cmj_pct":10,"bienestar_bajo":8,"max_sesiones_semana":6,"max_duracion_sesion":120},
 "objetivo_prioritario":"resistencia","semanas":2}
```

### SALIDA

```json
{"semanas":[
 {"numero":1,"sesiones":[
  {"dia":"martes","enfoque":"Recuperación activa","duracion_min":40,"ejercicios":[
   {"nombre":"Rodaje suave","series":1,"repeticiones":"1","duracion":"20 min","intensidad":"baja","descanso_seg":0},
   {"nombre":"Movilidad y estiramientos dinámicos","series":2,"repeticiones":"10","duracion":null,"intensidad":"baja","descanso_seg":30}]},
  {"dia":"jueves","enfoque":"Base aeróbica de bajo impacto","duracion_min":45,"ejercicios":[
   {"nombre":"Bicicleta estática en zona 2","series":1,"repeticiones":"1","duracion":"30 min","intensidad":"baja","descanso_seg":0},
   {"nombre":"Core estático","series":3,"repeticiones":"30 s","duracion":null,"intensidad":"media","descanso_seg":45}]}]},
 {"numero":2,"sesiones":[
  {"dia":"lunes","enfoque":"Resistencia aeróbica progresiva","duracion_min":50,"ejercicios":[
   {"nombre":"Carrera continua","series":1,"repeticiones":"1","duracion":"25 min","intensidad":"media","descanso_seg":0},
   {"nombre":"Fuerza de tren superior ligera","series":3,"repeticiones":"12","duracion":null,"intensidad":"baja","descanso_seg":60}]},
  {"dia":"miercoles","enfoque":"Intervalos extensivos","duracion_min":55,"ejercicios":[
   {"nombre":"Intervalos 3 min / 2 min de pausa","series":4,"repeticiones":"1","duracion":"3 min","intensidad":"media","descanso_seg":120}]},
  {"dia":"viernes","enfoque":"Técnica y movilidad","duracion_min":45,"ejercicios":[
   {"nombre":"Trabajo técnico de recepción sin salto","series":4,"repeticiones":"10","duracion":null,"intensidad":"baja","descanso_seg":45}]}]}],
 "justificacion":"El ACWR de 1.7 supera el umbral de 1.5, el CMJ cayó 12.8 % respecto a su línea base (umbral 10 %) y el bienestar es 7 (bajo el umbral de 8), con una caída de 4 puntos. Por eso la semana 1 reduce el volumen a 2 sesiones de baja intensidad y sin saltos; la semana 2 retoma la resistencia, objetivo prioritario, de forma gradual. La edad figura como 'no disponible' y no se supuso. La variación de resistencia frente a la medición anterior tampoco está disponible.",
 "indicadores_usados":["acwr","cmj_cm.var_linea_base_pct","carga.caida_cmj_pct","bienestar.indice","bienestar.var_semana_anterior"],
 "precauciones":["No reintroducir pliometría hasta que el CMJ se recupere.","Reevaluar CMJ y bienestar al cierre de la semana 1 antes de aumentar la carga.","Esta rutina es orientativa; la decisión final es del entrenador."]}
```
