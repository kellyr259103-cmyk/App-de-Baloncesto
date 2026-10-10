# apuestas_cache — API Kambi/BetPlay (todas las ligas)

Scraper genérico que descubre **todas las ligas de un deporte** en la API pública de
BetPlay (Kambi CDN) y descarga **todos sus partidos con las cuotas** a un JSON.

```bash
# baloncesto (el valor por defecto)
python apuestas_cache/kambi_ligas.py

# con el menú COMPLETO de mercados (incluye líneas por cuarto y por 1.ª parte)
python apuestas_cache/kambi_ligas.py --mercados completos

# listar los deportes disponibles
python apuestas_cache/kambi_ligas.py --lista

# otros deportes (sigue siendo genérico)
python apuestas_cache/kambi_ligas.py football
```

En Windows, si las tildes salen raras en consola: `$env:PYTHONIOENCODING="utf-8"`.

Salida: `apuestas_cache/kambi_{deporte}_ligas_{YYYY-MM-DD}.json`

---

## La API

| | |
|---|---|
| Base | `https://us.offering-api.kambicdn.com/offering/v2018/betplay` |
| Params | **siempre** `channel_id=1&client_id=200&lang=es_CO&market=CO&useCombined=true&useCombinedLive=true` |
| Headers | `Accept: application/json`, `Origin/Referer: https://betplay.com.co/` |
| Auth | ninguna (pública, sin cookies ni login) |

### Endpoints

| Endpoint | Para qué sirve |
|---|---|
| `/group.json` | Árbol deporte → región → liga. Fuente única de los `termKey`. |
| `/listView/{dep}/{region}/{liga}/all/matches.json` | Partidos de una liga **bajo un país** (3 niveles). |
| `/listView/{dep}/{liga}.json` | Partidos de una liga **terminal sin país** (2 niveles). |
| `/listView/{dep}.json` | Todo el deporte de una vez. Se usa de respaldo y para el contraste. |
| `/betoffer/event/{id,id}.json` | Menú **completo** de mercados de uno o varios eventos. |

### Conversiones obligatorias

`cuota = odds / 1000` · `línea = line / 1000`

---

## Datos verificados el 10-10-2026

- **36 deportes** en `/group.json`; basketball con 33-36 ligas (el número varía según el día).
- `/listView/basketball.json` → **~100-110 eventos** (el `eventCount` del árbol, 1998, cuenta
  también mercados a futuro: no sirve para estimar partidos).
- Cobertura medida: **97-98 / 97-98** eventos del deporte cubiertos por las ligas descargadas.
- Duración de la corrida completa de basketball: **~23 s** con `--mercados completos`
  (~37 peticiones: 35 ligas + 6 lotes de eventos + 1 contraste).

### Gotchas que el script respeta

1. **No inventar `termKey`**: siempre se leen de `/group.json`
   (`b1__league`, `super_league`, `top_division__w_`… el doble guion bajo marca lo femenino).
2. **Terminal vs región**: un nodo sin campo `groups` es liga terminal → URL de **2 niveles**.
   Probarla con 3 da **404**. El script prueba el patrón principal y cae al alternativo si hace falta.
3. **El mismo slug se repite entre países** (`nbl` en Australia y Bulgaria): la clave única es
   `region_tk / liga_tk`.
4. **0 eventos es una respuesta válida**, no un error (hay ligas sin partidos programados).
5. **El árbol cambia entre corridas**: una liga puede desaparecer de `/group.json` mientras sus
   partidos siguen publicados. El script lo detecta en el contraste y los descarga igual
   (*auto-sanado*; el 10-10-2026 pasó con `spain/leb_plata`, `belgium/top_division__w_`,
   `portugal/taca_de_portugal__w_` e `italy/serie_b`).
6. **eSports**: existe el deporte `esports` y también nodos anidados dentro de otro deporte
   (`esports_football/...` dentro de fútbol). Se excluyen por defecto; `--incluir-esports` los deja.
7. **`betOfferType.id` reales de baloncesto**: `1` Handicap · `2` Partido (moneyline) ·
   `4` Ganador · `6` Más/Menos · `8` Descanso/Final · `124` Winning Margin · `127` Player Occurrence Line.
8. **`/betoffer/event/{id}.json` tiene otra forma**: los mercados están en la **raíz**
   (`betOffers[]`, cada uno con `eventId`), no dentro de `events[]`.

### Mercados de totales que ofrece (baloncesto)

| Criterio | Contenido | Cobertura medida |
|---|---|---|
| `Total de puntos - Prórroga incluida` | total completo del partido | 92 / 106 eventos |
| `Total de puntos - 1.ª parte` | **Q1 + Q2 juntos** | 47 / 106 |
| `Total de puntos - Cuarto 1` | 1er cuarto | 49 / 106 |
| `Total de puntos de <Equipo>` | total de un solo equipo | — |

> **Para el modelo esto es lo interesante**: a la mitad del 2Q ya existen dos líneas
> comparables con la predicción — la de **1.ª parte** (Q1+Q2) y la de **partido completo**.

---

## Formato del JSON de salida

```jsonc
{
  "scrapeado": "2026-10-10T15:24:56-05:00",
  "deporte": "basketball",
  "deporte_nombre": "Baloncesto",
  "n_ligas": 35, "n_eventos": 106, "n_en_vivo": 16,
  "duracion_s": 23.4,
  "fallos": [],
  "contraste_deporte_completo": { "ok": true, "n_eventos": 97, "cubiertos": 97,
                                  "n_esports": 0, "sin_liga_descargada": [] },
  "ligas": [
    { "region": "España", "region_termKey": "spain",
      "liga": "Liga ACB", "liga_termKey": "liga_acb",
      "group_id": 1000094189,
      "patron": "3-niveles", "patron_usado": "3-niveles",
      "url": ".../listView/basketball/spain/liga_acb/all/matches.json",
      "n_eventos": 6, "nota": "",
      "eventos": [
        { "id": 1028750574, "nombre": "Rio Breogán - Club Basquet Girona 2014",
          "local": "Rio Breogán", "visitante": "Club Basquet Girona 2014",
          "inicio_utc": "2026-10-10T19:58:00Z",
          "liga": "Liga ACB", "group_id": 1000094189,
          "ruta": {"deporte": "Baloncesto", "region": "España", "liga": "Liga ACB"},
          "estado": "STARTED", "en_vivo": true, "es_esports": false,
          "marcador": {"local": 84, "visitante": 64, "periodo": "3er Cuarto",
                       "parciales": "Q1: 30-22 | Q2: 31-19 | Q3: 23-23"},
          "reloj": {"minuto": 30, "segundo": 0, "periodo": "3er Cuarto",
                    "periodo_id": "QUARTER3", "corriendo": false,
                    "min_izq": 0, "seg_izq": 0},
          "resumen": {
            "ganador": {"criterio": "Ganador - Prórroga incluida",
                        "apuestas": [{"etiqueta": "Rio Breogán", "cuota": 1.89}]},
            "totales": [ {"criterio": "Total de puntos - Prórroga incluida",
                          "clasificacion": "partido", "linea": 168.5,
                          "over": 1.86, "under": 1.78} ],
            "total_partido": {"criterio": "...", "clasificacion": "partido",
                              "linea": 168.5, "over": 1.86, "under": 1.78},
            "total_1a_parte": null,
            "total_cuarto1": null,
            "total_cuarto2": null,
            "handicap": {"criterio": "...",
                         "apuestas": [{"etiqueta": "Rio Breogán", "cuota": 1.89,
                                       "linea": -19.5}]}
          },
          "mercados": [ {"betOfferId": 2702024021, "criterio": "Total de puntos - Prórroga incluida",
                         "criterio_en": "Total Points - Including Overtime",
                         "tipo": "Más/Menos de", "tipo_id": 6, "suspendido": false,
                         "outcomes": [{"etiqueta": "Más de", "tipo": "OT_OVER",
                                       "cuota": 1.86, "linea": 168.5, "estado": "OPEN"}]} ] }
      ] }
  ]
}
```

`resumen.clasificacion` toma los valores `partido`, `1a_parte`, `2a_parte`,
`cuarto_1..4`, `equipo` u `otro`, y `resumen.total_*` son atajos al mercado más
líquido de cada periodo (solo mercados **abiertos y no suspendidos**).

---

## Archivos

| Archivo | Qué hace |
|---|---|
| `kambi_ligas.py` | El scraper. Genérico para cualquier deporte. |
| `validar_baloncesto.py` | Comprueba estructura, cobertura y mercados del JSON de baloncesto. |
| `analizar_json.py` | Resumen comparado de los JSON generados. |
| `validar_json.py` | Comprobación de la estructura contra la plantilla original. |
| `kambi_*_ligas_*.json` | Datos descargados. |

## Argumentos de `kambi_ligas.py`

| Argumento | Efecto |
|---|---|
| `deporte` | Por defecto `basketball`. |
| `--deportes a b c` | Varios deportes en una corrida. |
| `--mercados completos` | Usa `/betoffer/event/{id}` (menú entero: ~19 mercados/evento, JSON ~3 MB). |
| `--solo-ligas` | Solo lista las ligas descubiertas, sin descargar partidos. |
| `--lista` | Lista los deportes disponibles. |
| `--pausa SEG` | Espera entre peticiones (por defecto 0,4). |
| `--incluir-esports` | No excluir los nodos esports. |
| `--salida DIR` | Carpeta de destino. |
| `--compacto` | JSON en una sola línea. |
