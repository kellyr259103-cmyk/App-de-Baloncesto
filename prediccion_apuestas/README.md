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
   Over/Under, y va **separada en etapas**, con una **cabecera de sección** antes
   de cada una, para distinguir de un vistazo lo que ya está en descanso de lo
   que aún no ha llegado ni al 2º cuarto:

   | Sección | Qué es | ¿Se puede apostar? |
   |---|---|---|
   | ⏸ **EN DESCANSO 2Q→3Q** (filas en amarillo) | El partido ha parado tras el 2º cuarto | **Sí — AHORA**, es lo que el bot apuesta |
   | ▶ **EN 1Q** | Falta que termine el 2º cuarto | No: no hay dos cuartos que sumar |
   | ▶ **EN 2Q** | Jugando, pronto llega el descanso | No todavía |
   | … **3Q / 4T / prórroga** | La ventana se cerró | No: la línea ya se ha reajustado |
   | · **Por jugar o terminado** | Aún no empieza o ya acabó | No |

   Dentro de cada etapa, los partidos se ordenan por tamaño de
   `Δ = predicción − línea` (los que no tienen predicción van al final).
   La etapa 0 usa **el mismo criterio que el bot**
   (`descansos.en_ventana_descanso`), así que la tabla y la ventana de apuesta
   nunca se contradicen.

4. La banda superior muestra siempre **la mayor diferencia**. Si hay algo en
   descanso lo anuncia como **"⏱ EN DESCANSO —"**; si no, avisa con qué etapa
   está y por qué todavía no es comparable (*"aún en 1Q: falta que termine el
   2Q"*, *"ya en 3Q/4T: la línea de la casa se ha reajustado"*…).
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
`config.json → api_sports.token` (o variable `API_SPORTS_TOKEN`). Cuota:
300 peticiones/día; una descarga completa cuesta ~65.

#### 4.1 Cobertura total — **todas las ligas del feed tienen motor**

`python cobertura_ligas.py --comprobar` comprueba que **cada liga que publica la
casa** puede apostarse por el bot (sale con código 1 si alguna se queda sin
motor, así sirve de test). Hoy: **33 de 33** — 26 con modelo propio y 7 con el
de otra liga. Total: **44 modelos entrenados con 13.205 partidos**.

> El feed **cambia a lo largo del día**: a mediodía publica 33-34 ligas (muchas
> femeninas y de segunda división) y por la noche 25. `cobertura_ligas.py` avisa
> cuando entra una nueva, y `descubrir_ligas.py` (sin argumentos) lee el feed y
> busca el id de las que falten.

El informe separa cinco casos:

| | Significado | ¿El bot apuesta? |
|---|---|---|
| **A** | Con **su** modelo (n ≥ 30 partidos de esa liga) | Sí |
| **B** | Traducida y con datos en API-Sports, pero sin bajar/entrenar | **No** → `historico.py --ligas <clave> --cubrir 120 --guardar --entrenar` |
| **B'** | Traducida, pero API-Sports aún no publica sus estadísticas | Sí, con el modelo del Excel |
| **C** | Sin traducción a API-Sports | Sí, con el modelo del Excel |
| **D** | Resueltas con el modelo de otra liga (NBA/EUROLIGA) | Sí, y lo avisa |
| **E** | No son ligas (`wnba_series_markets`) | Se ignoran |

Cómo se llegó ahí:

- **Mapa ampliado** (`historico.LIGAS_API_SPORTS`): 20 ligas añadidas tras
  hallarlas con `descubrir_ligas.py`, que baja el listado completo de API-Sports
  (1 petición) y busca por nombre. Por ejemplo: Austria/ÖBL (217),
  Austria/ÖBL Femenina (218), Bélgica Femenina (25), Chequia (32), Hungría
  NB I A (46) y su versión femenina (47), Lituania/LKL (60), México/LNBP (63),
  Polonia 1 Liga (71), Portugal/LPB (74), Reino Unido/SLB (108) y SLB Women
  (109), Suecia/Superettan (99), España/Liga Femenina (94), Japón/B2 (407)…

  Tres ligas quedan **mapeadas sin poder entrenarse** (B') porque API-Sports aún
  no les da estadísticas — cuestan **0 peticiones** y se entrenan solas en cuanto
  las publiquen: Alemania Pro A (42), Alemania DBBL Women (41), Italia A2
  Women (243, 244) y España/Segunda FEB (96).

  Otras tres **no existen en API-Sports** y se quedan con el respaldo del Excel:
  `italy/serie_b`, `morocco/division_1` y
  `club_tournaments/arabian_gulf_club_championship`.

- **Reserva de temporadas** (`historico.descargar_liga(--cubrir N)`): si con las
  temporadas pedidas no llegan las muestras, **sigue bajando temporadas
  anteriores** hasta alcanzar el mínimo o agotarlas. Es lo que permitió entrenar
  las ligas de temporada reciente (una temporada apenas empezada trae 7-30
  partidos):

  | Liga | Temporada actual | Se bajó la anterior | Total |
  |---|---|---|---|
  | Nueva Zelanda / Tauihi | 7 partidos | +33 +33 +34 | 107 |
  | Reino Unido / SLB Women | 9 | +94 +115 | 218 |
  | Austria / ÖBL Femenina | 11 | +69 +46 | 126 |
  | Bélgica Femenina | 12 | +146 | 158 |
  | Polonia / 1 Liga | 28 | +294 | 322 |
  | Lituania / LKL | 18 | +159 | 177 |
  | Portugal / LPB | 9 | +147 | 156 |
  | México / LNBP | 188 (suficiente) | — | 188 |

- **`wnba_series_markets` no es una liga** (son mercados de series): pasa a
  `historico.NO_SON_LIGAS` y se ignora en lugar de buscarle un id inexistente.

- **NBA Pretemporada no tiene liga propia en API-Sports** (se usa el id 12 de la
  NBA, que mezcla pretemporada y temporada regular). Se resuelve con el **modelo
  de respaldo NBA** (n=1340), igual que ya hacía la interfaz — y ahora también
  el bot automático (ver 6.2).


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

### 5. Regla del descanso 2Q→3Q (`descansos.py`)

El descanso **no dura lo mismo en todas las ligas**, y para apostar en ese hueco
hace falta una regla definida. Esta es:

| Pieza | Regla |
|---|---|
| **Ventana** (se detecta por estado, no por reloj) | Empieza cuando el **2Q está completo** y termina en cuanto **arranca el 3Q**. Se cierra por dos vías: hay puntos del 3Q (`q3` con valor) **o** el reloj ya marca 3T/4T/prórroga. |
| **Duración por liga** | Tabla `DURACIONES` (15 min: NBA y todas las FIBA). Se sobreescribe por liga en `config.json → "descansos": {"nba": 15, …}`. Si la liga no aparece, `DEFECTO_MINUTOS = 15`. |
| **Autocalibración** | La app **mide** la duración real de cada descanso (del fin del 2Q al arranque del 3Q) y la guarda en la tabla `descansos_liga`. Con esa medida se puede afinar la tabla. |
| **Seguro anti-datos-viejos** | Si un partido lleva "en descanso" más minutos que la duración oficial **+ 2 de margen**, la ventana **caduca**: no se apuesta con una línea que ya no refleja el partido (evita el caso de la API dejando de actualizar). |

### 6. Apuesta automática en el descanso — **va sola al abrir**

**No hay que marcar nada.** Al abrir la app (`app.pestana_vivo.arrancar_automatico()`)
quedan encendidos, sin depender de la sesión anterior:

- la **vista en vivo cada 5 s** (`Automático cada 5 seg`),
- la **apuesta automática** (`AUTO: apostar en el descanso (2Q→3Q)`).

La única forma de pararla es desmarcarlo tú a mano. El circuito que hace en cada
ping es:

1. **Detecta** los partidos dentro de la ventana 2Q→3Q (regla anterior).
2. **Verifica** que hay modelo de esa liga, que el mercado está **abierto**, que
   hay línea y que `|Δ| ≥ umbral` (por defecto **2,0 pts**, configurable con el
   *spinbox* de al lado).
3. **Registra la apuesta** en paperbet → aparece sola en el panel **APUESTAS**,
   con una nota del tipo `AUTO descanso2Q (euroleague): 1Q=45 2Q=44 Δ=+6.2`.
4. **Nunca repite** la apuesta de un mismo partido (control por `evento_id`).
5. Al **terminar el partido la resuelve sola** (GANADA / PERDIDA / ANULADA), igual
   que las apuestas manuales, y puedes ver el total final y el beneficio.

El contador del panel muestra cuántos partidos hay en descanso y cuántas apuestas
ha hecho.

> ⚠️ **Solo se apuesta en el descanso**, nunca antes (el 2Q podría seguir) ni
> después (la línea ya se habría reajustado al 3Q).

> La casilla `Automatico cada N seg` y la casilla `AUTO` se recuerdan igualmente
> en `config.json → "vivo"`, pero al arrancar la app **se fuerzan a encendidas**.

### 6.1 Ventana responsive — nunca más alta que la pantalla

`App.ajustar_a_pantalla()` mide el monitor al abrir y dimensiona la ventana
**dentro** de la pantalla disponible (por defecto 1240×900 como máximo, y se
centra). En un portátil de 1366×768 queda en torno a 1240×658.

Además, si el contenido no cabe:

| Pestaña | Qué hace |
|---|---|
| **EUROLIGA / NBA** | El contenido va dentro de un canvas con **barra vertical que solo aparece si desborda**; se desplaza con la rueda del ratón. |
| **EN VIVO** | La barra superior (liga + ping + automático) se reparte en **dos filas** si no cabe todo en una; la tabla absorbe el alto que sobra. |
| **APUESTAS** | Las 8 cajas de resumen van en **rejilla 4×2** en vez de una fila de 8, así no se salen en pantallas estrechas. |

### 6.2 Auditoría: ¿y si la casa NO tiene la apuesta disponible? (`auditoria_auto.py`)

`python auditoria_auto.py [umbral]` baja la API (1 petición) y recorre la cadena
de decisiones del bot con datos **reales**, respondiendo: ¿en qué punto se queda
cada partido?, ¿qué ofrece la casa en su sitio?, ¿cuál sería la mejor apuesta
realmente disponible por valor esperado?

Lo que destapó, ya corregido:

| Fallo | Antes | Ahora |
|---|---|---|
| Lado suspendido | Solo se miraba `cuota is not None`. Si la casa suspendía el **Under** pero conservaba su última cuota, el bot **apostaba igual**. | Usa `p.hay_cuota_over / hay_cuota_under` (estado del lado concreto). |
| Estado del Under | El parsing solo comprobaba el estado del outcome **Over**. | Se comprueban los **dos** outcomes y el `suspended` de la oferta. |
| Motivo invisible | Todos los `continue` eran silenciosos: el panel decía *"esperando Δ"* aunque la causa fuera "sin modelo" o "mercado cerrado". | Cada motivo se cuenta y se enseña: *"No aposté por: 1× OVER SUSPENDIDO por la casa"*. |
| Cuotas ignoradas | Se apuesta por `|Δ| ≥ umbral` sin mirar la cuota. Con el ~8% de margen de la casa, un Δ pequeño es **-EV**. | Puerta de **EV mínimo** (`EV = P × cuota − 1`), por defecto 0 %, configurable junto al umbral. |
| Bot ≠ interfaz | El bot miraba `modelos_ligas[clave]` directamente: una liga sin modelo propio (p. ej. NBA Pretemporada) **no se apostaba**, aunque la propia pantalla mostrara VEREDICTO con el modelo del Excel. | Usa `modelo_para_partido()`, el mismo resolvedor que la interfaz: modelo propio o el del Excel (NBA/EUROLIGA). La apuesta guarda en su `nota` qué modelo se usó. |

Lo que descartó la auditoría (por si se retoman):

- **Elegir entre líneas alternas del total** → no existen: cada partido publica
  UNA sola línea (0 de 75 con más de una).
- **Apostar el Hándicap como respaldo** → la casa sí lo publica siempre junto al
  total (tipo 1), pero es un mercado de **margen** y este modelo predice el
  **total**: haría falta entrenar un modelo de margen nuevo.
- **Apostar el lado contrario** si el nuestro está suspendido → sería jugar
  **contra** el propio modelo. Lo correcto es no apostar y avisar (ya hace).

### 6.3 Historial de decisiones — `log/auto_apuestas.log`

La interfaz solo puede enseñar **por qué** no apostó mientras el partido sigue
en el descanso: al pasar al 3Q la fila desaparece y la respuesta se pierde.
Para responder de forma permanente a "¿y por qué no ha apostado?", el bot deja
una línea en `log/auto_apuestas.log` (excluido de git) en cada decisión:

```
2026-10-10 23:14:02  [nba_preseason] Dallas Mavericks - Chicago Bulls · entra en descanso 2Q→3Q · 1Q=26 2Q=35 línea=163.5 O=2.2 U=1.65 mercado=abierto
2026-10-10 23:14:07  [nba_preseason] Dallas Mavericks - Chicago Bulls · APUESTA id=12 UNDER @ 1.65 línea=163.5 stake=10000 Δ=-6.6 EV=+10.9% modelo=NBA (n=1340)  ⚠RESPALDO
2026-10-10 23:19:40  [spain/liga_acb] Joventut - Granada · NO apuesta: |Δ| 1.2 < 2.0
2026-10-10 23:24:11  [germany/bbl] Bayern - Ulm · NO apuesta: OVER SUSPENDIDO por la casa
```

- Se anota la **entrada** en el descanso con el estado del mercado, la
  **apuesta** con todos sus datos, o el **motivo** de no apostar.
- Sin repeticiones: un mismo partido con el mismo motivo escribe **una sola
  línea**, aunque el descanso dure 15 minutos y el bot sondee 180 veces.
- Cada apuesta guarda en su `nota` qué modelo usó; si fue el de otra liga se
  marca `⚠ modelo …` (y en el log, `⚠RESPALDO`).
- El archivo se reinicia solo al superar 400 KB.



### 6.4 Resolución de las apuestas — memoria de resultados (`resultados.py`)

El feed de la casa es la **cartelera del día**: en cuanto un partido termina
sale de la lista **y no deja consultar uno concreto** (`event/{id}.json`
responde 404, verificado). La resolución original solo miraba los partidos
del sondeo, así que si la app no llegaba a leer el marcador en esa ventana
breve —o estaba cerrada en ese momento—, la apuesta se quedaba **PENDIENTE
para siempre**. Ahora hay tres fuentes, en este orden:

| # | fuente | cuándo la usa | coste |
|---|---|---|---|
| 1 | feed del sondeo | siempre, primero | 0 (el ping ya se hace) |
| 2 | tabla `resultados` (memoria) | el partido ya salió del feed | 0 (una consulta) |
| 3 | API-Sports | sigue sin marcador en las dos anteriores | 1 petición/fecha, como mucho cada 10 min |

- **Se guarda en cuanto se ve**: cada sondeo apunta en `resultados` el total
  final de los partidos acabados. Solo si la casa los da por terminados, o si
  los cuatro cuartos cuadran con el marcador: una **prórroga pendiente** no se
  guarda, porque ese marcador aún no es el final.
- **Se resuelve al arrancar**: al abrir la app se resuelven enseguida las
  pendientes que tengan marcador guardado, sin esperar al primer ping.
- **Recuperación**: lo que siga fuera del feed se busca en API-Sports **por
  fecha**, acotando la candidatura a la liga de la apuesta y exigiendo un
  margen claro sobre el segundo mejor. Si no hay coincidencia fiable **no se
  inventa** el resultado: la apuesta sigue pendiente.
- El origen queda anotado en la `nota`: `· resultado recuperado de API-Sports
  (el partido ya había salido del feed)`.
- El botón «✔ Resolver pendientes» antes solo miraba la BBL del prototipo
  (`obtener_partidos()` sin liga), así que casi nunca resolvía nada; ahora
  recorre feed + memoria + API-Sports.

```
python resultados.py                  # informe: qué queda pendiente y por qué
python resultados.py -v               # idem, con la puntuación de cada candidato
python resultados.py --aplicar        # resuelve las recuperables
python diagnostico_resolucion.py      # las 3 fuentes para cada pendiente
```

> **Ejemplo real.** Un día la app estuvo cerrada justo cuando terminaron sus
> partidos y **36 apuestas se quedaron PENDIENTE** sin forma de resolverlas.
> Con este cambio, de las 37 pendientes se recuperaron **35**: las 2 que
> quedan son un partido de una liga que API-Sports no cubre (Marruecos /
> Division 1) y otro que todavía se está jugando.

### 7. Backtest walk-forward (`backtest.py`) — ¿cómo nos habría ido en 2026?

Responde "si hubiéramos usado esta estrategia en 2026, ¿qué winrate tendríamos?",
**sin mirar el futuro**:

- Para cada liga se recorren sus partidos **ordenados por fecha**.
- El modelo de cada partido se entrena **solo con partidos de días anteriores**
  (ventana expansiva). El partido que se evalúa **nunca** está en su entrenamiento.
- Se predice con el **1Q y 2Q reales** de ese partido (lo que se sabe en el descanso)
  y se compara con la línea.
- Si `|Δ| ≥ umbral` → apuesta; gana si el total final cae del mismo lado.

```
python backtest.py                      # 2026, umbral 2.0, línea honesta
python backtest.py --umbrales 0 2 3 5   # comparar umbrales
python backtest.py --umbral 3 --umbral-max 5   # el rango que apuesta la app
python backtest.py --linea media        # proxy inflado (solo informativo)
python backtest.py --ligas nba euroleague --detallado
```

**Resultado en 2026 con línea `(1Q+2Q)×2` y umbral 2,0 pts:**

| umbral | apuestas | winrate |
|---|---|---|
| 0,0 | 5311 | 71,4 % |
| 2,0 | 4458 | 75,3 % |
| 5,0 | 3239 | 80,5 % |
| 8,0 | 2166 | 85,9 % |

> ⚠️ **Léelo con criterio.** El winrate sube al subir el umbral (el modelo es más
> confiable cuando más se aleja), pero **el edge procede sobre todo de la
> reversión a la media entre mitades**, que mide `diagnostico_backtest.py`: con un
> 1º tiempo **bajo** (67 pts) el 2º anota más el **76 %** de las veces, y con un
> 1º tiempo **alto** (117 pts) el 2º anota menos el **71 %**. El MCO detecta eso
> (sus coeficientes salen por debajo del doble ciego), **pero una casa real
> también lo sabe**: la línea de BetPlay en el descanso ya lo incorpora. Contra una
> línea de casa de verdad el winrate sería bastante menor que el de estas tablas.
> El modo `--linea media` (79,9 %) es todavía más optimista: compara contra la media
> de la liga, un mercado que **no se entera del partido**.

### 7.1 ¿Y contra la casa de verdad? (`analisis_delta.py`)

La tabla anterior usa la línea proxy `(1Q+2Q)×2`. La app, en cambio, apuesta
contra la línea **real** de BetPlay. `analisis_delta.py` mide el winrate por
tamaño de Δ con las apuestas que el bot ha hecho de verdad, y sale lo
contrario:

| umbral | backtest (proxy) | real (contra la casa) | n real |
|---|---|---|---|
| Δ > 1 | 73,4 % | 31,1 % | 45 |
| Δ > 2 | 74,9 % | 30,0 % | 40 |
| Δ > 3 | 76,7 % | 20,6 % | 34 |
| Δ > 4 | 78,5 % | 21,2 % | 33 |
| 3 < Δ < 5 | 61,4 % | 0,0 % | 4 |

Con cuota media 1,83 el punto de equilibrio es **54,8 %**: ninguna banda llega
(beneficio global **−185.600** sobre 46 apuestas, ROI −40 %).

Dos lecturas, y las dos hacen falta:

* **El n real es de 46 apuestas**, con intervalos de ±15 a ±40 puntos. No
  saques nada de una banda con menos de 10 apuestas: la 2–3 está al 83 % sobre
  6 apuestas, eso es suerte y no señal.
* **La brecha entre las dos columnas sí es concluyente**, y no es ruido:
  confirma el aviso del §7. El modelo no descubre valor contra la línea de la
  casa porque la casa ya pricea la reversión a la media. Subir o bajar el
  umbral de Δ no arregla eso: mueve el winrate dentro de un sistema que está
  por debajo de su propio punto de equilibrio.

Cuando la tabla `señales` acumule muestras resueltas sustituirá a `apuestas`
aquí, y permitirá medir también las señales que el bot **rechazó**, sin sesgo
de selección.

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
| `descansos.py` | Regla del descanso 2Q→3Q: ventana, duración por liga, caducidad y autocalibración. |
| `resultados.py` | Memoria de los resultados finales: guarda el marcador en cuanto se ve y resuelve (o recupera de API-Sports) las apuestas cuyo partido ya salió del feed. |
| `backtest.py` | Backtest walk-forward de la estrategia del descanso (winrate por liga/umbral). |
| `diagnostico_backtest.py` | Mide la reversión a la media entre mitades (de dónde sale el edge del backtest). |
| `analisis_delta.py` | ¿Cómo se comporta el winrate según el tamaño de Δ? Lo responde con las apuestas REALES del bot (bandas y acumulados). |
| `config.json` | Conexión a PostgreSQL y token de API-Sports (se crea solo). |
| `requirements.txt` | Dependencias para `pip install -r requirements.txt`. |
| `verificar.py` | Comprueba que Python = Excel (23 comprobaciones). |
| `prueba_vivo.py` | Prueba de humo: ping + predicción + resolución de apuesta. |
| `prueba_bd.py` | Prueba de PostgreSQL: insertar → resolver → resumen → limpiar. |
| `prueba_gui.py` | Prueba de la GUI completa: ventana + ping + apuesta + resolución. |
| `prueba_ligas.py` | Prueba del cliente genérico: ligas + deporte entero + predicciones por liga. |
| `prueba_gui_ligas.py` | Prueba de la pestaña EN VIVO con selector de ligas y modo "todas". |
| `prueba_descansos.py` | Prueba de la regla del descanso: ventana, duración, caducidad y decisión. |
| `prueba_auto_descanso.py` | Prueba del circuito automático: descanso → apuesta sola → resolución. |
| `prueba_resultados.py` | Prueba de la memoria de resultados: guardar el marcador y resolver un partido que ya salió del feed. |
| `diagnostico_resolucion.py` | Diagnóstico: por qué sigue pendiente cada apuesta y qué fuente le falta. |
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
python prueba_descansos.py   # regla del descanso 2Q→3Q: ventana, duración, caducidad
python prueba_auto_descanso.py  # circuito automático: descanso → apuesta sola → resolución
python prueba_automatico.py     # arranque solo (AUTO + 5 s) + ventana responsive
python prueba_disponibilidad.py # la casa no tiene el lado disponible / EV negativo
python prueba_resultados.py     # memoria de resultados: marcador guardado -> resolución
python auditoria_auto.py        # auditoría real: motivos, mercados y valor esperado
python cobertura_ligas.py       # qué ligas del feed tienen motor (A/B/C/D/E)
python cobertura_ligas.py --comprobar   # test: exit 1 si alguna liga se queda sin modelo
python descubrir_ligas.py       # halla el id de API-Sports de una liga que falta
python resultados.py            # informe: apuestas pendientes y sus 3 fuentes (1 petición)
python diagnostico_resolucion.py # por qué sigue pendiente cada apuesta
```

Las de GUI (`prueba_bd.py`, `prueba_gui.py`, `prueba_gui_ligas.py`,
`prueba_auto_descanso.py`, `prueba_automatico.py`, `prueba_resultados.py`)
borran las apuestas de prueba al terminar.
`prueba_historico.py` gasta ~4 peticiones de la cuota diaria de API-Sports,
`prueba_modelos.py` ~2 y `resultados.py` 1 por fecha pendiente.

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
| `/event/{id}.json` | **No existe** (404). Por eso hay que guardar el marcador en cuanto se ve: ver §6.4. |

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
| Retirada | La casa **borra cada partido de la lista en cuanto termina** y `event/{id}.json` devuelve 404: el marcador se guarda en la tabla `resultados` apenas se ve (§6.4). |

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
    fecha_resolucion TIMESTAMPTZ,
    verificado BOOLEAN NOT NULL DEFAULT FALSE   -- total contrastado con API
);
```

Y la memoria que hace falta para resolver aunque la casa retire el partido
(§6.4):

```sql
CREATE TABLE IF NOT EXISTS resultados (
    evento_id BIGINT PRIMARY KEY,   -- id del partido en el feed
    partido   TEXT, liga TEXT,
    total     DOUBLE PRECISION NOT NULL,   -- total final (con prórroga)
    fuente    TEXT NOT NULL DEFAULT 'feed',   -- 'feed' | 'apisports'
    capturado TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Hay además `muestras` (cuartos para entrenar), `modelos_liga` (un MCO por
liga) y `descansos_liga` (autocalibración del descanso).

---

# Integridad de datos y calibración (11-10-2026)

Auditoría del primer día de apuestas reales (41 paperbet): 10G/13L, −23.2 % de
ROI. Los fallos encontrados y su solución, ya implementada:

## 1. Candado anti-duplicados PERSISTENTE
El "no repetir partido" vivía solo en memoria: al reiniciar la app se perdía y
el bot volvía a apostar partidos que seguían en su descanso de ~15 min
(duplicados reales: ids 6/14, 8/13, 9/17, 10/16, 11/15). Ahora, antes de
apostar, `_auto_apostar_en_descanso` comprueba `base_datos.existe_apuesta()`.

## 2. Liquidación con resultado OFICIAL (`resultados_api.py` + `reconciliacion.py`)
Kambi deja `running=True` en el pitido, a veces da totales imprecisos
(prórrogas, reloj con retraso: hubo un 176 registrado sobre un partido de 205
y un U21 colándose por el senior) y elimina el evento sin estado final (las
apuestas quedaban colgadas). Ahora:

- La app **reconcilia cada ~10 min** (en hilo): busca el resultado en
  API-Sports (`games?date=…`, emparejado de equipos por Jaccard) y:
  - cierra los PENDIENTE cuyo evento ya no está en el feed (≥1 h) o lleva
    ≥2 h sin cerrarse;
  - **corrige** los totales ya resueltos que no cuadran (columna `verificado`).
- Botón **✍ Liquidar manual…** en APUESTAS para ligas sin datos oficiales
  (escribe el total y recalcula con `paperbet.recalcular`).
- CLI: `python reconciliacion.py --horas 48`.

## 3. Histórico de líneas reales (`cuotas_historial`)
En cada ping se guardan a PostgreSQL los cambios de línea/cuotas del feed
(una fila por cambio + cierre con total). Es la base para backtests con
**cuotas reales** (hoy solo había proxy `(1Q+2Q)×2`).

## 4. Calibración de probabilidades (`calibracion.py`)
Mide con walk-forward si `P = Φ(Δ/RMSE)` es honesta:
`python calibracion.py --desde 2025-10-11 --hasta 2026-10-11 --aplicar`.
Resultado 11-10: **bien calibrada** (ECE 1.4 pp; k=1.10 aplicado en
`config.json → calibracion.factor_rmse`; `motor.FACTOR_RMSE` lo usa la app).

## 5. Umbrales honestos y política de ligas
- **Piso Δ ≥ 2.0 y EV ≥ 0 %** en el código (`UMBRAL_MINIMO`/`EV_MINIMA`):
  el estado −23.2 % vino de operar con Δ=0.5 contra la línea real.
- `config.json → auto.solo_modelo_propio` (por defecto **true**): no apuesta
  con modelos de respaldo (el respaldo hizo 0/4 en el día real).
- `auto.ligas_validadas` (lista blanca opcional): si tiene ligas, solo se
  apuesta en ellas. La sugiere `python roi_ligas.py` (n≥20 y ROI>0).

## 6. Cobertura y modelos
- `python descubrir_ligas.py --auto`: ligas del feed sin id + candidato
  validado **por equipos** (evita el caso "1 Liga (F)" ≠ Energa W).
- `modelos.entrenar_todo`: ligas con 8-29 muestras ahora reciben un modelo
  **mezcla con el global (pooling)** en vez de quedarse con el de otra liga
  (p. ej. NBA Cup). Con <8 muestras, respaldo (y la app ya no lo apuesta).
- `python roi_ligas.py`: winrate y ROI reales por liga con la cuota apostada.

## 7. Robustez de los backtests
`_ajustar` puede fallar con datos previos insuficientes en el arranque de una
liga; backtest.py / backtest_cuota_justa.py / calibracion.py ahora lo capturan
y saltan ese partido en vez de romper la corrida.

## 8. Diagnóstico de las pérdidas (`diagnostico.py`)
Con las 40 primeras apuestas resueltas (total: 50.0 % de acierto, ROI −10.8 %)
el desglose dice:

| grupo | n | winrate | err. modelo | err. casa | ROI |
|---|---|---|---|---|---|
| modelo PROPIO | 29 | 58.6 % | 11.07 | 11.12 | +4.4 % |
| respaldo EUROLIGA | 11 | 27.3 % | 16.90 | 10.59 | −50.8 % |

Lectura: con ligas propias el modelo empata con la casa (mismo error medio);
el desastre vino del modelo de respaldo (Δ fantasma de 20-30 pts en ligas con
otro ritmo). Contra la línea REAL el Δ es ruido de estimación entre dos
estimadores equivalentes → ≈50 % de acierto → el margen de la cuota (~1.8
paga a 55.6 % de breakeven) lo convierte en −EV. El backtest contra la línea
proxy `(1Q+2Q)×2` parecía bueno porque el proxy NO sabe nada del partido.

## 9. Plan de validación (11-10-2026)

Verificado con datos: añadir información de equipo al modelo no mejora la
predicción (`backtest_mejoras.py`: RMSE 11.66 → 11.59 en 8.552 partidos
2026; deltas por liga dentro del ruido). La "escalera de oponentes" mide el
edge evaporándose: contra la línea naive 77 % de winrate, contra un oponente
informado en ritmo de equipos 55.8 % (|Δ|≥2), contra la casa real 50.0 %.

Como no existe histórico de cuotas reales, el edge SOLO se puede validar
prospectivamente. Protocolo en marcha (mientras, paperbet sin dinero):

1. El bot apuesta solo con modelo propio y los pisos activos (Δ≥2, EV≥0).
2. Se acumulan `cuotas_historial` (movimientos de línea) y resultados.
3. KPIs por liga para "graduarse": n ≥ 50 decididas · ROI > 0 · CLV > 0
   (`roi_ligas.py`, `clv.py` — CLV = ¿cerramos mejor que la línea de cierre?).
4. Las ligas graduadas entran en `config.json → auto.ligas_validadas` (solo
   esas se apostarían con dinero real; stake fraccional).
5. Si ninguna graduía en 2-4 semanas: conclusión honesta — el modelo no bate
   el mercado del total al descanso con estos datos; la app queda como
   herramienta de análisis. Mejorar exigiría datos que la casa no tenga
   (lesiones/alineaciones) o mercados más ilíquidos, a testear aparte.

Reglas: ningún cambio de estrategia sin re-validar en walk-forward (y contra
cuota real cuando haya muestra); prohibido el respaldo (0/11 real); el CLV es
el juez — ROI positivo con CLV negativo = suerte, no edge.