# Predicciones en vivo — refactorización del Excel a Python

Aplicación de escritorio que reproduce **exactamente** el funcionamiento de la hoja
**PREDICCION** del prototipo en Excel:

> `C:\Users\kelly\OneDrive\Documentos\Predicciones en vivo basque.xlsx`

Se entra el **1Q**, el **2Q** (opcional) y la **línea de la casa**, igual que en las
celdas amarillas del Excel, y se obtiene la misma predicción de total y el mismo
veredicto **OVER / UNDER**.

---

## Requisitos

- Windows
- Python 3.13 (instalado con `winget install Python.Python.3.13`)
- **PostgreSQL** local (base de datos `paperbet`) para guardar las apuestas paperbet
- Librería de driver: `pip install psycopg2-binary` (ya instalada en este equipo)

El motor de predicción puro (pestañas EUROLIGA/NBA) usa **solo la biblioteca estándar**
(`tkinter`, `zipfile`, `xml`, `math`); `psycopg2` solo lo necesita la pestaña APUESTAS.

### Instalación de PostgreSQL (ya hecha en este equipo)

```
winget install --id PostgreSQL.PostgreSQL.17 --silent
powershell -ExecutionPolicy Bypass -File configurar_postgres.ps1
```

El script fija la contraseña del superusuario (`postgres`) y crea la base `paperbet`.
Si la instalación pidió otra contraseña, se cambia en el menú
**Base de datos → Configurar PostgreSQL...** (se guarda en `config.json`).

## Cómo ejecutar

```
python app.py
```
o doble clic en **`iniciar.bat`**.

## Cómo se usa

### 1. Predicción manual (igual que el Excel)

1. Selecciona la pestaña **EUROLIGA** o **NBA**.
2. Escribe en las celdas **amarillas**:
   - `1Q jugado (ambos equipos)` → suma de los dos equipos en el 1er cuarto
   - `2Q jugado (ambos equipos)` → déjalo vacío si aún no hay 2º cuarto
   - `Linea del bookmaker (total)` → total que ofrece la casa
3. La predicción se recalcula automáticamente (también con **Enter**).
4. Botones:
   - **Calcular (Enter)** – fuerza el recálculo
   - **Limpiar** – vacía las entradas (el veredicto pasa a `faltan datos`)
   - **Ejemplo del Excel** – carga los valores de ejemplo del propio archivo
5. Menú **Archivo**:
   - `Abrir Excel de datos...` → usar otro libro
   - `Recargar datos` → vuelve a leer el Excel (útil tras añadir filas nuevas)

### 2. Pestaña ⚡ EN VIVO — ping a la API de BetPlay

1. Elige la **liga** en el desplegable (se descubren solas al arrancar, leyendo
   `/group.json`: hoy **35 ligas de baloncesto**: NBA, Euroliga, ACB, Lega A, BBL,
   WNBA, NBL, ligas femeninas…), o deja **⚽ TODAS LAS LIGAS**.
   - *Todas las ligas* barre el deporte entero con **una sola petición**
     (`/listView/basketball.json`), no 35.
2. Pulsa **PING AHORA** (o marca **Automático cada N s**, por defecto 5 s).
3. La tabla se lista con **Liga**, 1Q, 2Q, total, línea, **Pred.**, **Δ** y cuotas
   Over/Under, y se ordena sola:
   - **Grupo 0** (fila azul = *oportunidad*): 1Q **y** 2Q acabados y con la línea
     todavía comparable → ordenado por tamaño de `Δ = predicción − línea`.
   - **Grupo 1**: señal solo con 1Q, o partido ya en 4º cuarto (entonces la línea
     de la casa ya se ha reajustado y no es comparable).
   - **Grupo 2**: sin predicción todavía.
4. La banda superior muestra siempre **la mayor diferencia** del grupo 0.
5. Selecciona un partido: se rellenan solos **1Q**, **2Q** y **línea**, y se calcula
   la predicción con el modelo del desplegable **Modelo**, que por defecto está en
   **AUTO (por liga)** (ver más abajo).
6. Cuando el 2º cuarto ha terminado aparece *"En descanso del 2Q: Q1 y Q2 finales ✅"*
   y se habilita **💰 APOSTAR (paperbet)**.

### 4. Modelos por liga (menú 🧠 MODELOS)

Cada liga tiene su propio modelo MCO entrenado con **sus** partidos reales:

| Pieza | Qué hace |
|---|---|
| `historico.py` | Descarga partidos acabados con sus 4 cuartos desde **API-Sports** (la API del usuario). |
| `modelos.py` | Entrena un modelo por liga (`TOTAL = b0 + b1·1Q + b2·2Q`), con **holdout** (80 % ajusta / 20 % valida) y lo guarda en PostgreSQL. |
| Tablas `muestras` / `modelos_liga` | Partidos acabados y coeficientes por liga (se crean solas). |

- **Desplegable AUTO (por liga)**: usa el modelo entrenado con partidos de la liga
  del partido. Si esa liga aún no tiene modelo (menos de 30 muestras), cae al del
  Excel (EUROLIGA/NBA) y **lo avisa en rojo**.
- Menú **Modelos**: `Ver modelos por liga…` (muestras, R², RMSE de test y fechas),
  `Entrenar modelos…` y `Descargar histórico (API-Sports)…`.
- **Recolector**: cada ping guarda en `muestras` los partidos de la liga que acaban
  de terminar, así las ligas sin archivo histórico se entrenan solas con el tiempo.
- Eligen AUTO también las pestañas fijas EUROLIGA/NBA (siguen disponibles a mano
  si prefieres forzar un modelo concreto).

Fuente de datos: **API-Sports** (`https://v1.basketball.api-sports.io`), token en
`config.json → api_sports.token` (o variable `API_SPORTS_TOKEN`). Trae **29 de las
35 ligas** de BetPlay con sus cuartos, en **una petición por liga** (Euroliga 2025:
385 partidos, NBA 2025-26: 1315, ACB: 332, Chile: 334, Portugal: 30+…). Cuota:
300 peticiones/día; una descarga completa cuesta ~65.

**Guardas del mercado** (los mercados no abiertos no se pueden apostar):

- El parser selecciona **solo** el total del partido completo
  (`Total de puntos - Prórroga incluida`); ignora los totales **por cuarto**, los de
  **1.ª parte** y los de un **solo equipo** (`Total de puntos de <Equipo>`), que la
  API mezcla en el mismo listado.
- Si el mercado está `SUSPENDIDO`, la línea que aparece es **obsoleta**: la tabla lo
  marca, el botón de apostar queda **inhabilitado** y la app no deja guardar nada.

**Guarda de activación del flujo** — solo se considera que el 2Q está completo si:

```
score.info contiene Q2   y
    ( periodId != QUARTER2                       → ya se pasó del 2Q
      o  running == false y reloj en 0:0 )        → descanso tras el 2Q
```

Como `score.info` conserva `Q1: … | Q2: …` durante **todo** el partido, basta un
**ping en cualquier momento del descanso** (o después) para tener 1Q, 2Q y línea.

### 3. Pestaña 💰 APUESTAS — paperbet en PostgreSQL

- Cada apuesta se guarda con: partido, selección (OVER/UNDER), línea, cuota, stake,
  predicción, diferencia, probabilidad y modelo usado.
- Al terminar el partido se **resuelve sola** (en cada ping) comparando el total final
  con la línea:
  - `GANADA` → `+stake × (cuota − 1)`
  - `PERDIDA` → `−stake`
  - `ANULADA` → `0` (total exactamente en la línea: *push*)
- Resumen con apuestas, pendientes, ganadas/perdidas, % aciertos, ROI y beneficio.
- Botones: **Actualizar**, **Resolver pendientes**, **Eliminar seleccionada**.
- Menú **Base de datos**: `Probar conexión`, `Configurar PostgreSQL...`, `Ver esquema SQL...`

---

## Archivos

| Archivo | Qué hace |
|---|---|
| `app.py` | Interfaz gráfica (tkinter): EUROLIGA, NBA, EN VIVO y APUESTAS. |
| `motor.py` | Motor de cálculo: lee el `.xlsx`, ajusta los modelos MCO y predice. |
| `api_betplay.py` | Cliente REST genérico de la API de BetPlay (Kambi): descubre ligas, trae un partido o todo el deporte, y parsea reloj/cuartos/mercados. |
| `paperbet.py` | Lógica de la apuesta simulada: validar, resolver, resumir. |
| `base_datos.py` | PostgreSQL: configuración, esquema, CRUD de apuestas (+ tablas de modelos). |
| `historico.py` | Descarga el histórico de partidos acabados (con cuartos) desde API-Sports. |
| `modelos.py` | Entrena un modelo MCO por liga con holdout y lo guarda/recupera de PostgreSQL. |
| `config.json` | Conexión a PostgreSQL y token de API-Sports (se crea solo). |
| `verificar.py` | Comprueba que Python = Excel (23 comprobaciones). |
| `prueba_vivo.py` | Prueba de humo: ping + predicción + resolución de apuesta. |
| `prueba_bd.py` | Prueba de PostgreSQL: insertar → resolver → resumen → limpiar. |
| `prueba_gui.py` | Prueba de la GUI completa: ventana + ping + apuesta + resolución. |
| `prueba_ligas.py` | Prueba del cliente genérico: ligas + deporte entero + predicciones por liga. |
| `prueba_gui_ligas.py` | Prueba de la pestaña EN VIVO con selector de ligas y modo "todas". |
| `configurar_postgres.ps1` | Prepara PostgreSQL (contraseña y base `paperbet`). |
| `iniciar.bat` | Lanza la aplicación. |

## Cómo funciona (igual que el Excel)

**1. Datos** — `motor.py` abre el propio `.xlsx` y lee `EUROLIGA_DATOS` (402 partidos)
y `NBA_DATOS` (1380 partidos). Reproduce el rango dinámico del Excel
(`COUNTA($A:$A)`), así que si añades filas a las hojas, al pulsar *Recargar datos*
el modelo se amplía solo.

**2. Modelo** — reconstruye las funciones del Excel:

| Excel | Python |
|---|---|
| `INDEX(LINEST(G, C:D, V, V), 1, 1)` | coeficiente de la **2Q** (`b2`) |
| `INDEX(LINEST(G, C:D, V, V), 1, 2)` | coeficiente de la **1Q** (`b1`) |
| `INDEX(LINEST(G, C:D, V, V), 1, 3)` | intercepto (`b0`) |
| `INDEX(LINEST(...), 3, 1)` | R² |
| `FORECAST(1Q, G, C)` | modelo de una sola variable (`a0 + a1·1Q`) |

Modelos resultantes (ya verificados):

```
EUROLIGA: TOTAL = 68,6109 + 1,30595·(1Q) + 1,09074·(2Q)   R² = 0,520
          TOTAL = 107,3668 + 1,49537·(1Q)                 R² = 0,332

NBA     : TOTAL = 102,8402 + 1,15339·(1Q) + 1,04417·(2Q)  R² = 0,478
          TOTAL = 155,8176 + 1,27339·(1Q)                 R² = 0,287
```

**3. Lógica condicional** (idéntica a la fórmula `B8`/`F8` del Excel):

```
si 1Q vacío        → "faltan datos"
si 2Q vacío        → se usa el modelo solo con 1Q (FORECAST)
si hay 1Q y 2Q     → b2·2Q + b1·1Q + b0
si falta la línea  → "faltan datos"
diferencia = predicción − línea
veredicto  = OVER si diferencia ≥ 0, si no UNDER
```

---

## Verificación

```
python verificar.py
```

Compara los valores de Python contra los que dejó calculados Excel en el propio
archivo (B8, B9, B10, B11, B12, B14, B15, B19, B20 y sus equivalentes de NBA):

```
RESULTADO: TODAS las 23 comprobaciones OK — Python = Excel.
```

Diferencias máximas observadas: **~10⁻¹³** (ruido de coma flotante; a los decimales
que muestra Excel son idénticos).

### Pruebas de las partes nuevas

```
python prueba_vivo.py        # ping a la API + predicción + resolución de la apuesta
python prueba_bd.py          # PostgreSQL: insertar, resolver, resumen, borrar
python prueba_gui.py         # GUI completa: ventana + ping + apuesta + resolución
python prueba_ligas.py       # cliente genérico: ligas + deporte entero + predicciones
python prueba_gui_ligas.py   # selector de ligas + modo "todas las ligas"
python prueba_historico.py   # descarga de histórico desde API-Sports (cuota: 4 peticiones)
python prueba_modelos.py     # pipeline: descargar → entrenar (con holdout) → guardar → predecir
```

Las de GUI (`prueba_bd.py`, `prueba_gui.py`, `prueba_gui_ligas.py`) borran las
apuestas de prueba al terminar. `prueba_historico.py` gasta ~4 peticiones de la
cuota diaria de API-Sports y `prueba_modelos.py` ~2.

## Diferencias respecto al Excel (a propósito)

- **`RMSE de test`** son valores que el Excel tenía fijados a mano (13.1 / 15.3 / 25.5
  y 14.7 / 17.2 / 28.6). Se muestran igual, y además se recalculan los RMSE en muestra.
- **Extra: probabilidad de OVER** `Φ((predicción − línea) / RMSE)`. No estaba en el
  Excel; va etiquetada como *EXTRA* y no afecta al veredicto.
- **Valor exacto** de la predicción se muestra en letra pequeña bajo el resultado
  (el Excel solo muestra 1 decimal por el formato `0.0`).

## Actualizar los datos

1. Abrir `Predicciones en vivo basque.xlsx` y añadir partidos al final de
   `EUROLIGA_DATOS` / `NBA_DATOS` (columnas FECHA, PARTIDO, 1C, 2C, 3C, 4C, TOTAL).
2. Guardar y **cerrar** el Excel.
3. En la aplicación: **Archivo → Recargar datos**.

---

## La API de BetPlay (verificada con sondeo real)

```
Base    https://us.offering-api.kambicdn.com/offering/v2018/betplay
Params  channel_id=1&client_id=200&lang=es_CO&market=CO
        &useCombined=true&useCombinedLive=true      (fijos: no rotarlos)
Headers Origin y Referer: https://betplay.com.co/    (sin cookies ni login)
```

| Endpoint | Para qué sirve |
|---|---|
| `/group.json` | Árbol deporte → región → liga. Fuente única de los `termKey`; la app lo lee al arrancar para llenar el desplegable. |
| `/listView/basketball/germany/bbl/all/matches.json` | Partidos de una liga **bajo un país** (3 niveles). |
| `/listView/basketball/nba.json` | Partidos de una liga **terminal sin país** (2 niveles). |
| `/listView/basketball.json` | **Todo el deporte** en una petición (~95 partidos). Es lo que usa el modo *TODAS LAS LIGAS*. |

Los mismos `termKey` sirven para descargar todas las ligas con cuotas a disco:
el script `apuestas_cache/kambi_ligas.py` (ver `apuestas_cache/README.md`).

| Hallazgo | Detalle |
|---|---|
| **No hay WebSocket ni SSE** | Se probaron 4 rutas; `/stream` → 404 y el *upgrade* se ignora (200 JSON). **Solo polling REST.** |
| Caché | `X-Cache: Miss` siempre (CloudFront no cachea) → siempre va al origen. |
| CORS | `Access-Control-Allow-Origin: *` |
| Límite | Sin 429 a 1 req/s ni 1 req/3 s (solo en `/`). Latencia 0,32–0,42 s, payload 15,8 KB. |
| Reloj | Se actualiza cada 3-4 s → un intervalo de 10 min es inviable (el 2Q dura 10 min). |
| Estructura | `events[] → event` (id, homeName, awayName, state, **path**) + `betOffers[]` (tipo 2 = Moneyline, **tipo 6 = Over/Under**) → `outcomes[]` (`odds`÷1000, `line`÷1000, `status`) + `liveData` (`score.info`, `matchClock`). |
| Línea elegida | En el mismo listado hay **varias** ofertas de total del partido (líneas alternas) más totales **por cuarto**, **de 1.ª parte** y **de un solo equipo**; el parser se queda con el total completo **abierto**. |
| Clave de liga | El mismo slug se repite entre países (`nbl` en Australia y Bulgaria): la clave única es `region_tk/liga_tk`. |
| Descanso 2Q | `periodId=QUARTER2`, `running=false`, `left=0:0`, línea y mercados `OPEN`. |
| Transición capturada | `QUARTER2` (`running=false`, `left=0:0`) → `QUARTER3` 25 s después. |
| Resolución | Al final: `state != STARTED/NOT_STARTED`, o bien `Q4` con reloj parado a ≥ 40:00. |

**Intervalo recomendado:** 5 s en vivo / 60 s prepartido, con *backoff* si llega 429.

> ℹ️ **Estado de los modelos:** la API de BetPlay expone hoy **35 ligas de baloncesto**
> y ahora mismo **27 tienen su propio modelo** entrenado con partidos reales
> (NBA 1340 partidos, Euroliga 425, ACB 332, Japón 839…, ver menú *Modelos → Ver
> modelos por liga*). Solo quedan sin modelo las ligas con menos de 30 partidos
> descargados (NBA Cup, Taca de Portugal Femenina) o las que no están en API-Sports
> (Serie B italiana, Paulista, Super Liga del Reino Unido); para esas, el desplegable
> AUTO avisa en rojo y usa el modelo más parecido del Excel, y el recolector de la
> app les va guardando partidos acabados para entrenarlas en cuanto lleguen a 30.

## Estructura de la tabla `apuestas`

```sql
CREATE TABLE IF NOT EXISTS apuestas (
    id BIGSERIAL PRIMARY KEY,
    fecha TIMESTAMPTZ NOT NULL DEFAULT now(),
    evento_id BIGINT NOT NULL,
    partido TEXT NOT NULL,
    liga_apuesta TEXT NOT NULL DEFAULT 'BBL',
    liga_modelo TEXT NOT NULL,
    mercado TEXT NOT NULL DEFAULT 'TOTAL',
    seleccion TEXT NOT NULL CHECK (seleccion IN ('OVER','UNDER')),
    linea DOUBLE PRECISION NOT NULL,
    cuota DOUBLE PRECISION NOT NULL,
    stake DOUBLE PRECISION NOT NULL,
    prediccion DOUBLE PRECISION,
    diferencia DOUBLE PRECISION,
    probabilidad DOUBLE PRECISION,
    estado TEXT NOT NULL DEFAULT 'PENDIENTE'
            CHECK (estado IN ('PENDIENTE','GANADA','PERDIDA','ANULADA')),
    total_final DOUBLE PRECISION,
    beneficio DOUBLE PRECISION,
    nota TEXT DEFAULT '',
    fecha_resolucion TIMESTAMPTZ
);
```
