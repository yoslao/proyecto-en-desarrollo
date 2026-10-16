# VoleyIA Coach — contexto del proyecto

PFC de Ingeniería de Sistemas (UCSM, Arequipa). Sistema web de recomendación de entrenamiento con IA para la selección universitaria masculina de voleibol. Diseño cuasi experimental pre/post, 24 jugadores (datos reales de 13). Usuarios: entrenador y administrador.

**Principio obligatorio: "La IA recomienda, el entrenador decide".** Toda rutina nace como `propuesta`; el entrenador la aprueba/rechaza con calificaciones 1–5 (pertinencia, personalización). Toda evaluación de riesgo (incluida la de riesgo bajo) la confirma o descarta el entrenador.

## Stack y estructura
- Backend: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, httpx async, pandas, SciPy, statsmodels (AnovaRM), scikit-learn, passlib[bcrypt], python-jose, pytest. Linters: ruff + black.
- BD: MariaDB 10.11+ (InnoDB, utf8mb4) vía docker-compose.
- Frontend: Vue 3 + Vite + TS + Vue Router + Pinia + vue-chartjs + Axios. eslint + prettier. Figma Make es solo referencia visual (está en React): no copiar código.
- Monorepo: `/backend` (app/{api,core,models,schemas,services,ai,analytics,etl}, alembic, tests), `/frontend`, `/prompts` (plantillas versionadas), `/data/raw` (Excel original, NUNCA se modifica), `/data/processed`, `/scripts`.
- Capas: Presentación → API (routers+Pydantic) → Servicios → Analítica/Motor IA → Repositorios SQLAlchemy. Monolito modular.
- Secretos solo en `.env` (gitignored); mantener `.env.example` sin valores reales. Modelos de IA solo en variables de entorno / `configuracion_sistema`, nunca en código.

## Motor de recomendación
K-means (perfiles descriptivos, no predictivo) + calculadora determinista de indicadores (sin entrenar modelos) + LLM vía OpenRouter (solo inferencia) para rutina y riesgo (bajo/medio/alto).

## Ley 29733 (reglas duras)
- Al proveedor de IA solo: código seudónimo (J-07), posición, edad, mediciones, indicadores, cluster+etiqueta, objetivo, umbrales. NUNCA nombre ni universidad. Test que falle si aparecen.
- Nunca guardar ni loguear el prompt con datos personales (solo hash SHA-256 del contexto).
- Nunca insertar texto libre del usuario en el prompt.
- No registrar mediciones de jugadores sin consentimiento informado (el ETL omite mediciones de quienes no lo tengan, vía `--consentimientos`, y lo reporta).
- Datos reales (`data/raw`, `data/processed`) fuera de git. Reportes y tests usan códigos J-xx; la semilla y los tests usan jugadores FICTICIOS, nunca nombres del Excel.

## Decisiones de diseño acordadas
- **Permisos (RBAC en cada endpoint del backend):** Administrador gestiona usuarios, umbrales, modelo IA, K-means; ve bitácora, alertas, comparaciones, métricas; NO registra tests ni genera rutinas ni revisa alertas. Entrenador registra jugadores/tests/sesiones/bienestar, genera y aprueba rutinas, revisa alertas. El admin ve **nombres solo en el módulo Jugadores** (puede registrar/editar, RF-01); en alertas, métricas, comparaciones y bitácora ve solo códigos J-xx.
- `jugador.fecha_nacimiento` es nullable (el Excel solo trae edad); sin ella, edad = "no disponible" para el LLM. VO₂máx por Léger (1988), V = 7.5 + 0.5·palier, constante configurable y documentada.
- Posiciones normalizadas al catálogo (central, punta receptor, opuesto, armador, líbero); "opuesto-central" se reporta, no se adivina.
- Datos migrados: jornadas `momento='seguimiento'` (no son línea base). Se puede reclasificar el momento de una jornada (bitácora); advertir si se intenta pasar a `pre` una jornada con origen de migración. Registros migrados tienen RPE NULL → no entran en carga/ACWR. RPE obligatorio para origen `sistema` (validar en servicio con join a `sesion.origen`).
- Atípicos IQR → `pendiente_confirmacion`, excluidos de indicadores, K-means y estadística hasta confirmarse.
- **ACWR** = carga última semana / promedio de las 4 últimas semanas (incluida la actual); semanas sin sesiones = 0; con <4 semanas → NULL. **Caída CMJ semanal** = respecto a la línea base del jugador (su primer CMJ en jornada `pre`); NULL si no hay línea base. Sprint: menor tiempo es mejor (`mayor_es_mejor`).
- **K-means:** variables configurables en `configuracion_sistema`; por defecto SPRINT_20M, NAVETTE, CMJ (mejor de 3), SJ; análisis exploratorio alterno: SPRINT_20M, NAVETTE, SALTO_AI. NO equiparar SALTO_* con CMJ/SJ. Se toma la **última medición de cada variable dentro de un rango de fechas** indicado (no una sola jornada) e informa qué jornadas se usaron por jugador. StandardScaler, k=2,3,4 por silueta, random_state fijo, n_init=10; silueta <0.25 → advertencia visible. Jugadores sin datos completos quedan sin asignar y se informan.
- **Estadística (RF-06):** inferencial (Shapiro-Wilk sobre diferencias, t relacionada o Wilcoxon, d_z de Cohen, ANOVA RM/Friedman si hay intermedia; α=0.05) **solo para el equipo completo**. Por posición y por cluster: solo descriptiva (media, mediana, DE, CV) con advertencia de n pequeño.
- **Métricas de alertas:** alerta confirmada=VP, alerta descartada=FP, riesgo bajo confirmado=VN, riesgo bajo descartado (el entrenador sí veía riesgo)=FN. Bandeja de "evaluaciones pendientes" incluye las de riesgo bajo; `GET /alertas` solo las alertas.
- **IA:** timeout 15 s por llamada; 1 reintento si JSON/esquema inválido; luego modelo de respaldo; 502 (nada válido) / 503 (ningún modelo respondió). `max_tokens` configurable: 4000 rutina, 2000 riesgo. Generación asíncrona: POST devuelve 202 + `id_tarea`; `GET /tareas/{id}` (registro en memoria, 1 worker). Rutina que excede MAX_SESIONES_SEMANA/MAX_DURACION_SESION se rechaza y se regenera (1 vez, dentro de `num_reintentos`). `es_caso_control` = llamadas de la PoC/consistencia, excluidas de métricas de producción. Riesgo `cierre_semana`: endpoint manual `POST /riesgo/cierre-semana` + tarea programada opcional.
- Login: bloqueo 15 min tras 5 fallos; JWT 30 min sin refresh (el frontend avisa 2 min antes de expirar).
- Tests de registro: `POST /jugadores/{id}/tests` recibe fecha+momento y busca/crea la jornada.
- Objetivo prioritario lo elige el entrenador (sugerido: menor percentil).

## ETL (Fase 2) — hechos del Excel real
- 21 hojas (12 de salto, 9 de velocidad) y no solo las de la spec; el título trae la fecha («VIERNES 21 DE AGOSTO DEL 2026») y manda sobre el nombre de la hoja. «hoy» no tiene título: exige `--fecha-hoy`. Ausencias = «-».
- Orden de nombres en el Excel: casi siempre «Apellido Apellido Nombre(s)»; regla por defecto = 2 primeras palabras apellidos; excepciones en `data/raw/decisiones.csv`. Emparejamiento por clave insensible al orden/tildes/mayúsculas.
- Posición «opuesto-central» NO se mapea: el jugador no se carga hasta que haya decisión (`posicion,opuesto-central,<catálogo>`).
- Integridad (conteos/sumas/muestra) controla el commit; las discrepancias en velocidad/VO₂máx del Excel son hallazgos de calidad, no bloquean.
- Consentimiento del CSV es del 16/10/2026, posterior a las mediciones (21/08–07/10): se carga y se advierte en el reporte (consentimiento retroactivo).
- Los atípicos IQR se concentran en los jugadores de menor salto; deben confirmarse en bloque en la Fase 3 (endpoint de confirmación).
- **Carga real hecha (16/10/2026)** en la BD `voleyia` tras borrar los 12 ficticios (decisión del usuario): 13 jugadores J-01…J-13, 21 jornadas 'seguimiento', 806 resultados, 273 antropometrías, 111 registros de sesión. Hoja «hoy» = 07/09/2026. Decisiones en `data/raw/decisiones.csv`: opuesto-central→central, 2 nombres corregidos, talla unificada a la moda (empate→1.87), velocidad F15 de «31 Agosto» aceptada (se usa el tiempo). Consentimiento de los 13 = sí.
- No volver a sembrar `--dev` sobre `voleyia` (los J-01… chocarían con los reales); para pruebas manuales usar otra BD (`voleyia_scratch`).
- Quedan 30 resultados atípicos `pendiente_confirmacion` y 13 fechas de nacimiento pendientes.

## Fase 3 — registro e indicadores (hechos)
- Solo el **entrenador** registra tests/sesiones/bienestar y consulta `carga`/`indicadores`/atípicos; el admin solo lista `jornadas` y catálogos (pruebas, umbrales). Todo registro exige jugador activo y con consentimiento (422 si no).
- `POST /jugadores/{id}/tests` y `POST /jornadas` buscan o crean la jornada por (fecha, momento); lote atómico; errores por celda en `detail.errores`; duplicado → 409. Disparan `riesgo_hooks.programar_evaluaciones` (segundo plano); la Fase 5 registra su callback con `registrar_callback`.
- Atípicos (IQR por prueba y jornada, n≥4) → `pendiente_confirmacion`, excluidos de indicadores hasta `PATCH /resultados/confirmacion` (en bloque) o `PUT /resultados/{id}` (corrige y confirma). Nunca se toca un resultado ya `confirmado`.
- `PATCH /jornadas/{id}/momento` reclasifica (bitácora); pasar a 'pre' una jornada migrada exige `confirmar_advertencia=true` (409 `advertencia_migracion`).
- Calculadora (`analytics/indicadores.py`) es la única que escribe `indicador_carga_semanal`; recalcula TODAS las semanas del jugador en cada alta/edición. Semana sin RPE ni CMJ no genera fila. Registros migrados (RPE NULL) no generan carga.
- **Variaciones con signo de desempeño: positivo = mejora** (sprint: bajar el tiempo es +). Al armar el contexto del LLM (Fase 5) etiquetarlo así y ajustar el ejemplo de `/prompts`.
- Umbrales de disparo: ACWR > ACWR_ALTO, caída CMJ > CAIDA_CMJ_PCT, bienestar < BIENESTAR_BAJO. Bienestar: los 4 ítems orientados 5 = mejor (índice 4-20).

## Fase 4 — K-means (hechos)
- Jornadas `pre` reales (decisión del usuario, 16/10/2026): **28/09 para salto** (SALTO_*) y **07/10 para velocidad y resistencia** (SPRINT_20M, NAVETTE). Reclasificadas con advertencia confirmada; bitácora con id_usuario NULL.
- `POST /analitica/clustering` (solo admin; body `modo` principal|exploratorio, `fecha_desde`, `fecha_hasta` opcionales) toma la ÚLTIMA medición válida de cada variable en el rango y guarda las jornadas usadas por jugador. Variables, k candidatos, random_state y silueta mínima salen de `configuracion_sistema`.
- Clusters renumerados de mejor (0) a peor perfil global; etiquetas por dimensión (velocidad/resistencia/salto) con z orientado y umbral ±0.5. Se advierte silueta < mínimo, grupos de un solo jugador, sin asignar y el carácter descriptivo (no predictivo).
- `GET /analitica/clustering/ultimo` y `/analitica/clustering` (historial): ambos roles, solo códigos J-xx. `GET /jugadores/{id}/cluster` (solo entrenador) alimentará el contexto de la IA.
- Los atípicos `pendiente_confirmacion` NO entran al K-means (el jugador queda sin asignar hasta confirmarlos).
- Con los datos migrados no hay CMJ ni SJ: el modo `principal` falla con 422 explicativo; usar `exploratorio` (SPRINT_20M, NAVETTE, SALTO_AI). Ejecución real nº 1 (exploratorio): k=4, silueta 0.349, un grupo unitario; J-07 sin asignar por atípico pendiente.
- Migración 0002: `ejecucion_clustering.diagnostico` (JSON) y fechas desde/hasta como DATE.

## Fase 5 — IA (hechos)
- Cliente `app/ai/openrouter_client.py`: 15 s por llamada; JSON/esquema inválido → 1 reintento → modelo de respaldo; 5xx/timeout/red → respaldo directo. 502 si hubo respuestas pero ninguna válida; 503 si ningún modelo respondió o no hay clave/modelos. Sin `response_format` soportado: reintenta sin él y extrae el JSON del texto. Registra tokens/costo de `usage` (pide `usage.include`). NUNCA registra ni guarda el prompt (solo hash SHA-256 en `llamada_ia`).
- Modelos: `configuracion_sistema` (IA_MODELO_PRINCIPAL/RESPALDO) y, si están vacíos, variables de entorno. Clave solo en `.env`. `proveedor.fijar_cliente()` para mocks; conftest pone un cliente SIN clave en todas las pruebas (jamás llaman a OpenRouter).
- Plantillas `/prompts/rutina_v1.md` y `riesgo_v1.md` (versionadas, con 2 ejemplos few-shot ficticios J-9x); `app/ai/prompts.py` las carga. Variaciones etiquetadas «positivo = mejora».
- Seudonimizador (`app/ai/seudonimizador.py`): lista blanca de claves, cadenas con formato cerrado, `"no disponible"` para nulos, y guardia final que bloquea (ErrorSeudonimizacion) si aparece un nombre o universidad. Nunca texto libre (observaciones, descripciones, comentarios).
- Rutinas: `POST /jugadores/{id}/rutinas` → 202 + `id_tarea`; `GET /tareas/{id}` (progreso; devuelve 502/503 si falló). Validación de seguridad (máx. sesiones/semana y minutos/sesión, nº de semanas, un día por sesión) integrada como validador del cliente → rechaza y regenera. Nace 'propuesta'; `PATCH /rutinas/{id}/aprobacion` exige ambas calificaciones 1-5 (también al rechazar); no se revisa dos veces.
- Riesgo: se evalúa en segundo plano al registrar tests (gancho `riesgo_hooks`) y con `POST /riesgo/cierre-semana`. `GET /alertas` (medio/alto, ambos roles, solo códigos), `GET /evaluaciones-riesgo/pendientes` (bandeja con TODOS los niveles, solo entrenador), `PATCH /evaluaciones-riesgo/{id}/revision` (confirmar|descartar, solo entrenador).
- PoC: `python -m app.ai.poc [--modelos a,b] [--simulado] [--registrar-bd]` → `data/processed/poc_resultados.csv` (latencia, cumplimiento de esquema, costo, consistencia; riesgo ×3).
- Pruebas: NO ejecutar dos pytest a la vez (comparten `voleyia_test`; bloquea por metadata lock). Suite completa ≈ 7-8 min.

## Convenciones
- Marcas de tiempo (DATETIME) se guardan en **UTC** (`app.core.security.utcnow`); las fechas de negocio (`DATE`) son locales de Perú.
- Columnas generadas de MariaDB (`srpe`, `indice_bienestar`, `es_alerta`) no admiten NOT NULL: son nullable en el esquema.
- Pruebas contra MariaDB real (`voleyia_test`, creada por conftest con la migración); requieren el contenedor `db` arriba. En esta máquina el puerto 3306 está ocupado: `.env` usa DB_PORT=3307.
- Escribir archivos con Write/Edit (los heredocs largos de bash fallan con comillas).
- Código y esquema en español (tablas/columnas según la spec); identificadores Python en snake_case. Interfaz en español (Perú), fechas dd/mm/aaaa.
- Permisos verificados en backend, no solo ocultando botones. Bitácora de auditoría solo-inserción.
- Tests con pytest; OpenRouter siempre mockeado. Ruff + black (línea 100) en backend; eslint + prettier en frontend.
- Commits pequeños y descriptivos por fase. Al terminar cada fase: resumen al usuario y esperar confirmación.
- Scripts `.sh` se ejecutan con Git Bash/WSL/contenedor (host Windows). HTTPS solo en producción (proxy inverso).

## Fases
0 Estructura · 1 Modelos/migración/semillas/auth · 2 ETL · 3 Tests/sesiones/bienestar + Calculadora · 4 K-means · 5 OpenRouter/prompts/seudonimizador/rutinas/riesgo/PoC · 6 Historial/estadística/métricas · 7 Frontend Vue (Figma MCP: requiere autorizar con /mcp) · 8 Respaldos/bitácora/seguridad/README.
