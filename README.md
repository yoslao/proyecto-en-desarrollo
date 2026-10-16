# VoleyIA Coach

Sistema web de recomendación de entrenamiento con IA para la selección universitaria masculina de voleibol (PFC, Ingeniería de Sistemas, UCSM). **La IA recomienda, el entrenador decide.**

> Estado: Fase 1 (modelos, migración, semillas y autenticación). Este README se completará en la Fase 8.

## Requisitos
- Docker Desktop, Python 3.11+, Node 20+.

## Puesta en marcha (desarrollo)
```bash
cp .env.example .env          # completar contraseñas y claves (si el puerto 3306 está ocupado, usar DB_PORT=3307)
docker compose up -d db       # MariaDB 10.11

cd backend
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Linux/Mac: .venv/bin/pip
.venv/Scripts/alembic upgrade head        # migraciones
.venv/Scripts/python -m app.seed --dev    # catálogos + usuarios y 12 jugadores FICTICIOS (imprime las contraseñas una vez)
.venv/Scripts/uvicorn app.main:app --reload   # API en http://localhost:8000/docs
.venv/Scripts/python -m pytest            # pruebas (usan la base voleyia_test)
```
Usuario real: `python -m app.seed usuario --correo X --nombre "Y" --rol entrenador`.
(ETL y frontend: se documentarán en sus fases.)

## Modelo de consentimiento informado (borrador)
> Yo, ____________________ (DNI ________), declaro haber sido informado/a de que, en el marco del proyecto "VoleyIA Coach", se registrarán mis datos de evaluación física (sprint 20 m, Course Navette, saltos, carga de entrenamiento y bienestar), así como mi posición, edad, talla y peso, con la finalidad de personalizar mi entrenamiento y monitorear mi carga, conforme a la Ley N.º 29733.
>
> Se me ha informado que, para generar recomendaciones, **datos seudonimizados** (un código, mi posición, edad, mediciones e indicadores; **sin mi nombre ni mi universidad**) serán procesados por un proveedor de inteligencia artificial en el extranjero (**OpenRouter** y los modelos que este enruta), lo que constituye un flujo transfronterizo de datos. Las recomendaciones son orientativas y el entrenador toma la decisión final.
>
> Entiendo que mi participación es voluntaria y que puedo ejercer mis derechos de acceso, rectificación, cancelación y oposición, y retirar mi consentimiento en cualquier momento, escribiendo a ____________________.
>
> Firma: __________  Fecha: __/__/____

**Nota de riesgo residual:** aun sin nombre, la combinación posición + edad + mediciones podría permitir reidentificar a una persona en un equipo de ~24 jugadores. Se mitiga con consentimiento explícito y minimización de datos.
