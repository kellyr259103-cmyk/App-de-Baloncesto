# -*- coding: utf-8 -*-
"""
api_betplay.py — Cliente de la API en vivo de BetPlay (Kambi CDN), genérico.

Endpoints (verificados con sondeo real el 10-10-2026; REST sobre CloudFront,
sin WebSocket ni SSE -> hay que hacer polling):

  Base      https://us.offering-api.kambicdn.com/offering/v2018/betplay
  Params    SIEMPRE: channel_id=1&client_id=200&lang=es_CO&market=CO
                      &useCombined=true&useCombinedLive=true
  Headers   Origin/Referer: https://betplay.com.co/

  /group.json                                          árbol deporte -> región -> liga
  /listView/{dep}/{region}/{liga}/all/matches.json     liga bajo una región (3 niveles)
  /listView/{dep}/{liga}.json                          liga terminal sin país (2 niveles)
  /listView/{dep}.json                                 TODO el deporte en una sola petición

Notas de comportamiento:
  * X-Cache: Miss siempre -> CloudFront no cachea, va al origen.
  * CORS: Access-Control-Allow-Origin: *  (también se puede llamar desde navegador).
  * Una liga pequeña pesa ~15 KB y tarda 0,32-0,42 s; el deporte entero ~380 KB.
  * score.info conserva Q1 y Q2 durante TODO el partido:
        "Q1: 20-29 | Q2: 19-24 | Q3: 8-2"
  * Durante el descanso: periodId=QUARTER2, running=false, izq=0:0, Q1 y Q2 finales.
  * El mismo slug se repite entre países (p. ej. nbl en Australia y Bulgaria):
    la clave única es region_tk + liga_tk.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

BASE = "https://us.offering-api.kambicdn.com/offering/v2018/betplay"

# Fijos para BetPlay: no inventarlos ni rotarlos.
PARAMS = ("channel_id=1&client_id=200&lang=es_CO&market=CO"
          "&useCombined=true&useCombinedLive=true")

HEADERS = {
    "User-Agent": "PrediccionPaperbet/1.0 (prototipo local)",
    "Accept": "application/json",
    "Origin": "https://betplay.com.co",
    "Referer": "https://betplay.com.co/",
}

DEPORTE_POR_DEFECTO = "basketball"

# URL por defecto: se mantiene la liga con la que arrancó el prototipo.
URL_BASE = (f"{BASE}/listView/basketball/germany/bbl/all/matches.json?{PARAMS}")

USER_AGENT = HEADERS["User-Agent"]

# Mercados que nos interesan (betOfferType.id)
ID_MONEYLINE = 2
ID_HANDICAP = 1
ID_TOTAL = 6


class ErrorAPI(Exception):
    """Error al consultar la API de BetPlay."""


# --------------------------------------------------------------------------- #
#  Liga (descubrimiento en /group.json)                                        #
# --------------------------------------------------------------------------- #
@dataclass
class Liga:
    deporte: str
    region: Optional[str]          # nombre legible, None si es liga terminal
    region_tk: Optional[str]       # termKey del país, None si es terminal
    liga: str                      # nombre legible
    liga_tk: str                   # termKey de la liga
    group_id: Optional[int] = None
    event_count: Optional[int] = None
    es_esports: bool = False
    notas: list[str] = field(default_factory=list)

    @property
    def clave(self) -> str:
        """Clave única (el mismo slug se repite entre países)."""
        return f"{self.region_tk or '-'}/{self.liga_tk}"

    @property
    def nombre(self) -> str:
        """Nombre para mostrar en la interfaz: 'España / Liga ACB'."""
        return f"{self.region} / {self.liga}" if self.region else self.liga

    @property
    def url(self) -> str:
        if self.region_tk:
            return f"{BASE}/listView/{self.deporte}/{self.region_tk}/{self.liga_tk}/all/matches.json"
        return f"{BASE}/listView/{self.deporte}/{self.liga_tk}.json"

    @property
    def patron(self) -> str:
        return "3-niveles" if self.region_tk else "2-niveles"


def url_deporte(deporte: str = DEPORTE_POR_DEFECTO) -> str:
    """Todo el deporte en una sola petición (~100 eventos, ~380 KB)."""
    return f"{BASE}/listView/{deporte}.json"


def _es_esports(nodo: dict) -> bool:
    """Nodos de esports anidados dentro de otro deporte (gotcha verificado)."""
    tks = ((nodo.get("termKey") or ""), (nodo.get("name") or ""),
           (nodo.get("englishName") or ""))
    return any("esport" in t.lower() or "e-sport" in t.lower() for t in tks)


def obtener_ligas(
    deporte: str = DEPORTE_POR_DEFECTO,
    timeout: float = 15.0,
    incluir_esports: bool = False,
) -> list[Liga]:
    """
    Lee /group.json y devuelve TODAS las ligas de un deporte.

    Recorre el árbol deporte -> [región ->] liga. Un nodo con campo `groups`
    es una región con ligas dentro; un nodo sin ese campo es una liga
    terminal (sin país) y su URL es de 2 niveles.
    """
    datos = _get_json(f"{BASE}/group.json", timeout=timeout)
    raiz = datos.get("group", datos)
    deportes = raiz.get("groups", []) or []

    nodo_deporte = next((d for d in deportes if d.get("termKey") == deporte), None)
    if nodo_deporte is None:
        disponibles = ", ".join(sorted(d.get("termKey", "?") for d in deportes))
        raise ErrorAPI(f"No existe el deporte '{deporte}'. Disponibles: {disponibles}")

    ligas: list[Liga] = []
    for nodo in nodo_deporte.get("groups", []) or []:
        if not nodo.get("termKey"):
            continue
        esports_nodo = _es_esports(nodo)
        hijos = nodo.get("groups") or []
        if hijos:
            for lg in hijos:
                if not lg.get("termKey"):
                    continue
                ligas.append(Liga(
                    deporte=deporte,
                    region=nodo.get("name"),
                    region_tk=nodo.get("termKey"),
                    liga=lg.get("name", lg.get("termKey")),
                    liga_tk=lg["termKey"],
                    group_id=lg.get("id"),
                    event_count=lg.get("eventCount"),
                    es_esports=esports_nodo or _es_esports(lg),
                ))
        else:
            ligas.append(Liga(
                deporte=deporte,
                region=None,
                region_tk=None,
                liga=nodo.get("name", nodo.get("termKey")),
                liga_tk=nodo["termKey"],
                group_id=nodo.get("id"),
                event_count=nodo.get("eventCount"),
                es_esports=esports_nodo,
            ))

    if not incluir_esports:
        ligas = [l for l in ligas if not l.es_esports]
    return ligas


# --------------------------------------------------------------------------- #
#  Partido                                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class Partido:
    evento_id: int
    nombre: str
    local: str
    visitante: str
    state: str                      # NOT_STARTED / STARTED / (otros = terminado)
    inicio: str
    # --- liga (desde event.path: [deporte, región?, liga]) ---
    liga_nombre: str = ""           # "España / Liga ACB"
    liga_tk: str = ""
    region_tk: str = ""
    # --- reloj / periodo ---
    periodo: str = ""
    period_id: str = ""
    reloj_min: int = 0
    reloj_seg: int = 0
    corriendo: Optional[bool] = None
    izq_min: Optional[int] = None
    izq_seg: Optional[int] = None
    # --- marcador ---
    marcador_local: Optional[int] = None
    marcador_visitante: Optional[int] = None
    info_cuartos: str = ""
    q1: Optional[int] = None        # suma de AMBOS equipos en el 1er cuarto
    q2: Optional[int] = None        # suma de AMBOS equipos en el 2º cuarto
    q3: Optional[int] = None
    q4: Optional[int] = None
    total_parcial: Optional[int] = None
    # --- mercado ---
    linea: Optional[float] = None
    cuota_over: Optional[float] = None
    cuota_under: Optional[float] = None
    mercado_abierto: bool = False
    cuota_over_abierta: bool = False
    cuota_under_abierta: bool = False

    # ------------------------------------------------------------- estados
    @property
    def clave_liga(self) -> str:
        """
        Clave única de la liga del partido (la misma liga se repite entre
        países: 'nbl' es Australia y Bulgaria). Es la clave bajo la que
        guarda `modelos.py` el modelo MCO entrenado de esa liga.
        """
        return f"{self.region_tk}/{self.liga_tk}" if self.region_tk else self.liga_tk

    @property
    def es_vivo(self) -> bool:
        return self.state == "STARTED"

    @property
    def es_pretemporada(self) -> bool:
        return self.state == "NOT_STARTED"

    @property
    def en_descanso_2q(self) -> bool:
        """True si el partido está en el descanso tras el 2º cuarto."""
        return (
            self.period_id == "QUARTER2"
            and self.corriendo is False
            and (self.izq_min or 0) == 0
            and (self.izq_seg or 0) == 0
        )

    @property
    def q2_completo(self) -> bool:
        """
        ¿El 2º cuarto ya terminó (y por tanto `q2` es el valor final)?

        - Si periodId ya NO es QUARTER2 (3Q, 4Q, prórroga) -> sí.
        - Si sigue en QUARTER2 pero el reloj está parado en 0:0 -> sí (descanso).
        - Si el 2Q está en curso -> NO (score.info trae el 2Q parcial).
        """
        if self.q2 is None:
            return False
        if self.state == "NOT_STARTED":
            return False
        if self.period_id != "QUARTER2":
            return True
        return self.corriendo is False and (self.izq_min or 0) == 0 and (self.izq_seg or 0) == 0

    @property
    def listo_para_prediccion(self) -> bool:
        """Se puede activar el flujo: hay 1Q y 2Q finales."""
        return self.q1 is not None and self.q2_completo

    @property
    def finalizado(self) -> bool:
        """
        ¿El partido ya terminó? (para resolver la apuesta y recolectar).

        Reglas:
          * cualquier estado distinto de NOT_STARTED/STARTED = terminado;
          * con la Q4 en el marcador, el tiempo restante a 0:0 y el reloj
            acumulado ≥ 40 min, el partido solo puede continuar si hay
            EMPATE al final del cuarto (prórroga). Sin empate está terminado
            aunque la API aún marque `running=True`: Kambi deja la bandera
            como estaba en el pitido final y retira el evento minutos
            después, así que exigir `running=False` dejaba apuestas colgadas.
        """
        if self.state not in ("NOT_STARTED", "STARTED"):
            # cualquier otro estado (RESULT / ENDED / FINISHED...) = terminado
            return True
        if "Q4" not in self.info_cuartos:
            return False
        if (self.izq_min or 0) != 0 or (self.izq_seg or 0) != 0:
            return False
        if self.reloj_min < 40:                 # baloncesto: 4 cuartos x 10 min
            return False
        empate = (self.marcador_local is not None
                  and self.marcador_local == self.marcador_visitante)
        return not empate

    @property
    def en_ultimo_periodo(self) -> bool:
        """
        ¿Se está jugando el 4º cuarto o prórroga?

        En ese tramo la línea de la casa ya no es la de prepartido (se ha
        reajustado muchas veces), así que la predicción 1Q+2Q deja de ser
        comparable con ella: no debe encabezar el ranking de oportunidades.
        """
        if not self.es_vivo:
            return False
        if (self.period_id or "").upper() in ("QUARTER4", "OVERTIME", "OT", "PERIOD4"):
            return True
        return self.q4 is not None

    @property
    def total_final(self) -> Optional[int]:
        if self.marcador_local is None or self.marcador_visitante is None:
            return None
        if self.finalizado:
            return self.marcador_local + self.marcador_visitante
        return None

    @property
    def hay_cuota_over(self) -> bool:
        return self.mercado_abierto and self.cuota_over_abierta and self.cuota_over is not None

    @property
    def hay_cuota_under(self) -> bool:
        return self.mercado_abierto and self.cuota_under_abierta and self.cuota_under is not None


# --------------------------------------------------------------------------- #
#  Parsing                                                                     #
# --------------------------------------------------------------------------- #
_RE_CUARTO = re.compile(r"Q([1-4]|OT)\s*:\s*(\d+)\s*-\s*(\d+)", re.IGNORECASE)


def _parsear_cuartos(info: str) -> dict[str, int]:
    """'Q1: 20-29 | Q2: 19-24' -> {'Q1': 49, 'Q2': 43}  (suma de ambos equipos)."""
    salida: dict[str, int] = {}
    if not info:
        return salida
    for clave, local, visitante in _RE_CUARTO.findall(info):
        clave = "QOT" if clave.upper() == "OT" else f"Q{clave}"
        salida[clave] = int(local) + int(visitante)
    return salida


def _num(valor, divisor=1000.0) -> Optional[float]:
    if valor is None:
        return None
    try:
        return float(valor) / divisor
    except (TypeError, ValueError):
        return None


def _liga_del_evento(ev: dict) -> tuple[str, str, str]:
    """event.path = [deporte, región?, liga] -> (nombre, region_tk, liga_tk)."""
    ruta = ev.get("path") or []
    nodos = [n for n in ruta if isinstance(n, dict)]
    region_tk = nodos[1].get("termKey", "") if len(nodos) > 2 else ""
    liga_tk = nodos[-1].get("termKey", "") if len(nodos) >= 2 else ""
    nombre = " / ".join(n.get("name", "") for n in nodos[1:] if n.get("name"))
    if not nombre:
        nombre = ev.get("group", "") or ""
    return nombre, region_tk, liga_tk


def _es_total_partido(criterio: str) -> bool:
    """
    ¿Es el mercado de totales del PARTIDO COMPLETO?

    Ojo con lo que trae la API (verificado):
      * "Total de puntos - Prórroga incluida"              -> SÍ (partido)
      * "Total de puntos de <Equipo> - Prórroga incluida"  -> NO (un solo equipo)
      * "Total de puntos - Cuarto 4" / "- 1.ª parte"       -> NO (un periodo)

    NOTA (auditado con `auditoria_auto.py`): en este feed NO hay líneas
    alternas — cada partido publica UN solo total (0 de 75 con más de una).
    Sí publica a la vez los otros dos mercados: Hándicap (tipo 1) y Ganador
    (tipo 2). Ese sí es el respaldo natural si el total se cierra, pero es un
    mercado de MARGEN y este modelo predice el TOTAL, así que hoy no sirve.
    """
    c = (criterio or "").lower()
    if c.startswith("total de puntos de ") or c.startswith("total points of "):
        return False                                   # total de un solo equipo
    if not ("prórroga incluida" in c or "incluyendo prórroga" in c
            or "including overtime" in c):
        return False
    return not any(k in c for k in
                   ("parte", "cuarto", "periodo", "quarter", "half", "period"))


def _parsear_evento(bloque: dict) -> Partido:
    ev = bloque.get("event", {})
    ld = bloque.get("liveData") or {}
    ck = ld.get("matchClock") or {}
    sc = ld.get("score") or {}

    nombre_liga, region_tk, liga_tk = _liga_del_evento(ev)

    p = Partido(
        evento_id=int(ev.get("id", 0)),
        nombre=ev.get("name", ""),
        local=ev.get("homeName", ""),
        visitante=ev.get("awayName", ""),
        state=ev.get("state", ""),
        inicio=ev.get("start", ""),
        liga_nombre=nombre_liga,
        liga_tk=liga_tk,
        region_tk=region_tk,
        periodo=ck.get("period", "") or "",
        period_id=ck.get("periodId", "") or "",
        reloj_min=int(ck.get("minute", 0) or 0),
        reloj_seg=int(ck.get("second", 0) or 0),
        corriendo=ck.get("running"),
        izq_min=ck.get("minutesLeftInPeriod"),
        izq_seg=ck.get("secondsLeftInMinute"),
        info_cuartos=sc.get("info", "") or "",
    )

    if sc.get("home") not in (None, ""):
        try:
            p.marcador_local = int(sc["home"])
            p.marcador_visitante = int(sc["away"])
        except (TypeError, ValueError):
            pass
    if p.marcador_local is not None:
        p.total_parcial = p.marcador_local + p.marcador_visitante

    cuartos = _parsear_cuartos(p.info_cuartos)
    p.q1 = cuartos.get("Q1")
    p.q2 = cuartos.get("Q2")
    p.q3 = cuartos.get("Q3")
    p.q4 = cuartos.get("Q4")

    # --- mercados ---
    # (abierto, linea, over, under, over_abierto, under_abierto)
    candidatos_total: list[tuple] = []
    for oferta in bloque.get("betOffers", []) or []:
        tipo = (oferta.get("betOfferType") or {}).get("id")
        if tipo != ID_TOTAL:
            continue
        criterio = (oferta.get("criterion") or {}).get("label", "")
        if not _es_total_partido(criterio):
            continue            # es un total de equipo o de un periodo concreto
        suspendida = bool(oferta.get("suspended"))
        linea = None
        over = under = None
        # OJO: cada lado se abre y se cierra POR SEPARADO. La casa puede dejar
        # el Over operativo y suspender el Under (o al revés) sin tocar la
        # oferta entera, así que se comprueba el estado de los DOS outcomes.
        over_abierto = under_abierto = False
        for oc in oferta.get("outcomes", []) or []:
            abierto_oc = oc.get("status") == "OPEN" and not suspendida
            if oc.get("type") == "OT_OVER":
                linea = _num(oc.get("line")) or linea
                over = _num(oc.get("odds"))
                over_abierto = abierto_oc
            elif oc.get("type") == "OT_UNDER":
                linea = _num(oc.get("line")) or linea
                under = _num(oc.get("odds"))
                under_abierto = abierto_oc
        # "abierto" = se puede jugar ALGÚN lado de esta oferta (no hace falta
        # que los dos lo estén; si no, un solo lado suspendido cerraría el
        # mercado entero y se perdería la apuesta del otro lado)
        abierto = not suspendida and (over_abierto or under_abierto)
        if linea is not None:
            candidatos_total.append((abierto, linea, over, under,
                                     over_abierto, under_abierto))

    if candidatos_total:
        abiertos = [c for c in candidatos_total if c[0]]
        # se prefiere un mercado abierto; si todos están suspendidos, el primero
        (_, p.linea, p.cuota_over, p.cuota_under,
         ov_ab, un_ab) = (abiertos or candidatos_total)[0]
        # mercado_abierto es GLOBAL: basta una oferta abierta. Lo importante
        # para apostar es el estado del lado CONCRETO que se quiere jugar.
        p.mercado_abierto = bool(abiertos)
        p.cuota_over_abierta = p.mercado_abierto and ov_ab and p.cuota_over is not None
        p.cuota_under_abierta = p.mercado_abierto and un_ab and p.cuota_under is not None
    return p


# --------------------------------------------------------------------------- #
#  HTTP                                                                        #
# --------------------------------------------------------------------------- #
def _get_json(url: str, timeout: float = 10.0) -> dict:
    """GET con cache-buster. Lanza ErrorAPI ante cualquier problema."""
    separador = "&" if "?" in url else "?"
    url = f"{url}{separador}{PARAMS}&_={int(time.time() * 1000)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            crudo = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ErrorAPI(f"La API devolvió 404 para {url.split('?')[0]} "
                           f"(URL de liga mal armada).") from exc
        if exc.code == 429:
            raise ErrorAPI("La API respondió 429 (demasiadas peticiones). "
                           "Espera unos segundos.") from exc
        raise ErrorAPI(f"La API respondió HTTP {exc.code}.") from exc
    except ErrorAPI:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ErrorAPI(f"Sin conexión con la API: {exc}") from exc

    try:
        return json.loads(crudo)
    except json.JSONDecodeError as exc:
        raise ErrorAPI("La API devolvió JSON inválido.") from exc


def _desde_datos(datos: dict) -> list[Partido]:
    return [_parsear_evento(b) for b in datos.get("events", []) or []]


# --------------------------------------------------------------------------- #
#  Consulta                                                                    #
# --------------------------------------------------------------------------- #
def obtener_partidos(timeout: float = 10.0, liga: Optional[Liga] = None) -> list[Partido]:
    """
    Hace un PING a la API y devuelve los partidos de una liga.

    Sin `liga` usa la BBL (compatibilidad con el prototipo original).
    """
    return _desde_datos(_get_json(liga.url if liga else URL_BASE, timeout=timeout))


def obtener_partidos_deporte(
    deporte: str = DEPORTE_POR_DEFECTO,
    timeout: float = 20.0,
) -> list[Partido]:
    """
    TODOS los partidos del deporte en UNA sola petición (~380 KB, ~0,5 s).

    Cada partido trae su liga en `liga_nombre` (leída de event.path). Es la
    vía rápida para el modo "todas las ligas": en vez de ~35 peticiones, una.
    """
    return _desde_datos(_get_json(url_deporte(deporte), timeout=timeout))


def obtener_partidos_ligas(
    ligas: list[Liga],
    pausa: float = 0.4,
    timeout: float = 10.0,
    al_avanzar=None,
) -> tuple[list[Partido], list[tuple[Liga, str]]]:
    """
    Descarga liga a liga (usado como respaldo y para contar por liga).

    Devuelve (partidos, errores) donde errores es [(liga, mensaje)].
    `al_avanzar(i, total, liga, n)` se llama tras cada liga para la interfaz.
    """
    partidos: list[Partido] = []
    errores: list[tuple[Liga, str]] = []
    total = len(ligas)
    for i, liga in enumerate(ligas, 1):
        try:
            lista = obtener_partidos(timeout=timeout, liga=liga)
            partidos.extend(lista)
            if al_avanzar:
                al_avanzar(i, total, liga, len(lista))
        except ErrorAPI as exc:
            errores.append((liga, str(exc)))
            if al_avanzar:
                al_avanzar(i, total, liga, -1)
        if pausa:
            time.sleep(pausa)
    return partidos, errores


def obtener_partido(evento_id: int, timeout: float = 10.0) -> Optional[Partido]:
    """Busca un partido concreto por su id en toda la liga por defecto (BBL)."""
    for p in obtener_partidos(timeout=timeout):
        if p.evento_id == evento_id:
            return p
    return None
