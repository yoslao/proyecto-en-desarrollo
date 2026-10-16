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
