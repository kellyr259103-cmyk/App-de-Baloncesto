# App de Baloncesto — predicción de totales (1Q + 2Q)

Aplicación de escritorio (Python + tkinter) que predice el **total de puntos** de
un partido de baloncesto a partir del 1er y 2º cuarto, con la misma matemática que
un Excel de paperbet, y guarda las apuestas simuladas (paperbet) en **PostgreSQL**.

```
Proyecto predeterminado/
├── prediccion_apuestas/   ← la app: GUI, API de BetPlay, modelos por liga, PostgreSQL
└── apuestas_cache/        ← scraper genérico multi-liga de la API de BetPlay (Kambi)
```

| Parte | Qué es | Detalle |
|---|---|---|
| [`prediccion_apuestas/`](prediccion_apuestas/) | App tkinter: pestañas EUROLIGA / NBA / ⚡EN VIVO / 💰APUESTAS, selector de liga + modo *todas las ligas*, **un modelo MCO por liga** (AUTO), paperbet en PostgreSQL. | [README](prediccion_apuestas/README.md) |
| [`apuestas_cache/`](apuestas_cache/) | Scraper genérico de la API de BetPlay (Kambi): descubre las ligas de un deporte sin listas fijas y deja el árbol en JSON. | [README](apuestas_cache/README.md) |

## Cómo funciona la predicción

```
TOTAL = b0 + b1·(1Q) + b2·(2Q)
```

Modelo de mínimos cuadrados entrenado **con los partidos reales de cada liga**
(Euroliga 425, NBA 1340, ACB 332, Japón 839… 27 ligas), con validación *holdout*
(80 % ajusta / 20 % valida) y un mínimo de 30 muestras para publicarse. Si la liga
del partido aún no tiene modelo, la app avisa en rojo y usa el más parecido.

- Los datos en vivo salen de la API de **BetPlay** (Kambi): cuartos, línea y cuotas.
- El histórico de entrenamiento sale de **API-Sports** (una petición por liga).

## Arranque rápido

```powershell
# 1) PostgreSQL (una vez): crea la base 'paperbet'
powershell -ExecutionPolicy Bypass -File prediccion_apuestas\configurar_postgres.ps1

# 2) Configurar secretos (NO se suben al repo)
copy prediccion_apuestas\config.ejemplo.json prediccion_apuestas\config.json
#    → editar config.json: contraseña de PostgreSQL y token de API-Sports

# 3) Arrancar la app
prediccion_apuestas\iniciar.bat
```

Requisitos: Python 3.13+ con `requests` y `psycopg2` (`pip install requests psycopg2-binary`),
y PostgreSQL 17 en local. El archivo de datos del Excel
(`~/OneDrive/Documents/Predicciones en vivo basque.xlsx`) se lee directamente;
las pestañas EUROLIGA/NBA funcionan solo con él.

## Verificación

```powershell
cd prediccion_apuestas
python verificar.py          # Python = Excel, 23 comprobaciones exactas
python prueba_gui.py         # GUI completa: ping + flujo 1Q/2Q + apuesta en PostgreSQL
python prueba_modelos.py     # pipeline: descargar → entrenar (holdout) → guardar → predecir
```

## Seguridad

`config.json` (token de API-Sports y contraseña de PostgreSQL) está en `.gitignore`
y **nunca** se sube; para otros developers existe `config.ejemplo.json`.
