# -*- coding: utf-8 -*-
"""
kambi_ligas.py — Descubre TODAS las ligas de un deporte en la API Kambi/BetPlay
(BetPlay Colombia) y descarga todos sus partidos con las cuotas a un JSON agregado.

Genérico: sirve para cualquier deporte que exponga la API (basketball, football,
tennis, baseball, ice_hockey, volleyball, handball, american_football,
rugby_union, rugby_league, esports, …). No hay ninguna lista fija de URLs: las
ligas se leen siempre del árbol /group.json.

Uso:
    python apuestas_cache/kambi_ligas.py basketball
    python apuestas_cache/kambi_ligas.py football --pausa 0.5
    python apuestas_cache/kambi_ligas.py --deportes basketball football tennis
    python apuestas_cache/kambi_ligas.py --lista
    python apuestas_cache/kambi_ligas.py basketball --solo-ligas
    python apuestas_cache/kambi_ligas.py basketball --mercados completos

Salida:
    apuestas_cache/kambi_{deporte}_ligas_{YYYY-MM-DD}.json

API (pública, sin auth) — datos verificados el 10-10-2026:
    Base  https://us.offering-api.kambicdn.com/offering/v2018/betplay
    Params SIEMPRE: channel_id=1&client_id=200&lang=es_CO&market=CO
                    &useCombined=true&useCombinedLive=true
    Headers: Accept: application/json, Origin/Referer: https://betplay.com.co/

    /group.json                                  árbol deporte -> región -> liga
    /listView/{dep}/{region}/{liga}/all/matches.json   liga bajo una región (3 niveles)
    /listView/{dep}/{liga}.json                        liga terminal sin país (2 niveles)
    /listView/{dep}.json                               todo el deporte de una vez (respaldo)
    /betoffer/event/{id}.json                          mercados completos de un evento

Gotchas que este script respeta (verificados):
  1. Nunca se inventan termKeys: siempre se leen de group.json.
  2. Un nodo sin campo "groups" es liga TERMINAL -> URL de 2 niveles (3 da 404).
  3. La clave única de una liga es region_termKey + liga_termKey (o groupId),
     porque el mismo slug se repite entre países (p. ej. nbl en Australia y Bulgaria).
  4. 0 eventos en una liga es una respuesta válida, no un error.
  5. Se respeta el rate limit: espera `--pausa` (0,4 s) entre peticiones.
  6. odds y line vienen multiplicados por 1000 -> se dividen entre 1000.
  7. Un nodo/evento cuyo nombre contenga "esport" se etiqueta y se excluye por defecto.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import requests

# --------------------------------------------------------------------------- #
#  Constantes de la API                                                        #
# --------------------------------------------------------------------------- #
BASE = "https://us.offering-api.kambicdn.com/offering/v2018/betplay"

# Fijos para BetPlay: no inventar ni rotar.
PARAMS: dict[str, Any] = {
    "channel_id": 1,
    "client_id": 200,
    "lang": "es_CO",
    "market": "CO",
    "useCombined": "true",
    "useCombinedLive": "true",
}

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://betplay.com.co",
    "Referer": "https://betplay.com.co/",
}

CARPETA_SALIDA = Path(__file__).resolve().parent

PAUSA_POR_DEFECTO = 0.4       # segundos entre peticiones (ser amable con la API)
TIMEOUT_POR_DEFECTO = 25.0
REINTENTOS_POR_DEFECTO = 3


class ErrorAPI(Exception):
    """Error irrecuperable al consultar la API."""


# --------------------------------------------------------------------------- #
#  HTTP                                                                        #
# --------------------------------------------------------------------------- #
def get_json(
    sesion: requests.Session,
    ruta: str,
    *,
    params: Optional[dict] = None,
    timeout: float = TIMEOUT_POR_DEFECTO,
    reintentos: int = REINTENTOS_POR_DEFECTO,
) -> tuple[Optional[dict], Optional[int], Optional[str]]:
    """
    GET a la API. Devuelve (json, http_status, error).

    - (dict, 200, None)  -> todo bien
    - (None, 404, msg)   -> la URL no existe (patrón mal armado)
    - (None, None, msg)  -> se agotaron los reintentos / error de red
    Otros códigos HTTP también devuelven (None, codigo, msg).
    """
    url = ruta if ruta.startswith("http") else f"{BASE}/{ruta.lstrip('/')}"
    ultimo_error: Optional[str] = None

    for intento in range(1, max(1, reintentos) + 1):
        try:
            r = sesion.get(url, params=params or PARAMS, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            ultimo_error = f"{type(exc).__name__}: {exc}"
            if intento < reintentos:
                time.sleep(0.8 * intento)
            continue

        if r.status_code == 200:
            try:
                return r.json(), 200, None
            except json.JSONDecodeError:
                ultimo_error = "la respuesta no es JSON válido"
                break

        if r.status_code == 404:
            return None, 404, "404 (la URL no existe)"

        if r.status_code == 429:
            ultimo_error = "429 (demasiadas peticiones)"
            time.sleep(2.0 * intento)
            continue

        ultimo_error = f"HTTP {r.status_code}"
        if 500 <= r.status_code < 600 and intento < reintentos:
            time.sleep(0.8 * intento)
            continue
        break

    return None, None, ultimo_error or "error desconocido"


# --------------------------------------------------------------------------- #
#  Modelo de ligas                                                             #
# --------------------------------------------------------------------------- #
@dataclass
class Liga:
    deporte: str
    region: Optional[str]          # nombre legible, None si es liga terminal
    region_tk: Optional[str]       # termKey del país, None si es terminal
    liga: str                      # nombre legible
    liga_tk: str                   # termKey de la liga (clave junto a la región)
    group_id: Optional[int]
    patron: str                    # "3-niveles" | "2-niveles"
    url: str
    nombre_deporte: str = ""
    event_count: Optional[int] = None
    es_esports: bool = False
    # resultados de la descarga
    n_eventos: int = 0
    eventos: list[dict] = field(default_factory=list)
    patron_usado: str = ""
    nota: str = ""

    @property
    def clave(self) -> str:
        """Clave única de la liga (gotcha 3: el slug se repite entre países)."""
        return f"{self.region_tk or '-'}/{self.liga_tk}"

    @property
    def nombre_completo(self) -> str:
        return f"{self.region} / {self.liga}" if self.region else self.liga

    def a_json(self) -> dict:
        return {
            "region": self.region,
            "region_termKey": self.region_tk,
            "liga": self.liga,
            "liga_termKey": self.liga_tk,
            "group_id": self.group_id,
            "patron": self.patron,
            "patron_usado": self.patron_usado or self.patron,
            "url": self.url,
            "n_eventos": self.n_eventos,
            "nota": self.nota,
            "eventos": self.eventos,
        }


def url_3_niveles(deporte: str, region_tk: str, liga_tk: str) -> str:
    return f"listView/{deporte}/{region_tk}/{liga_tk}/all/matches.json"


def url_2_niveles(deporte: str, liga_tk: str) -> str:
    return f"listView/{deporte}/{liga_tk}.json"


def es_esports(nodo: dict) -> bool:
    """Detecta nodos de esports dentro de un deporte (gotcha 7)."""
    tk = (nodo.get("termKey") or "").lower()
    nom = (nodo.get("name") or "").lower()
    ing = (nodo.get("englishName") or "").lower()
    marcas = ("esport", "e-sport", "esports")
    return (tk.startswith("esports") or any(m in tk for m in marcas)
            or any(m in nom for m in marcas) or any(m in ing for m in marcas))


def ligas_del_deporte(
    sesion: requests.Session,
    deporte: str,
    *,
    incluir_esports: bool = False,
    timeout: float = TIMEOUT_POR_DEFECTO,
    reintentos: int = REINTENTOS_POR_DEFECTO,
) -> tuple[list[Liga], list[dict]]:
    """
    Lee /group.json y devuelve (ligas, deportes_disponibles).

    Recorre el árbol deporte -> [región ->] liga y arma la URL que le toca a
    cada liga según tenga o no país (patrón de 3 o de 2 niveles).
    """
    raiz, _, err = get_json(sesion, "group.json", timeout=timeout, reintentos=reintentos)
    if raiz is None:
        raise ErrorAPI(f"No se pudo leer group.json: {err}")

    grupo = raiz.get("group", raiz)
    deportes = grupo.get("groups", [])

    deporte_nodo = next((d for d in deportes if d.get("termKey") == deporte), None)
    if deporte_nodo is None:
        disponibles = ", ".join(sorted(d.get("termKey", "?") for d in deportes))
        raise ErrorAPI(f"No existe el deporte '{deporte}'. Disponibles: {disponibles}")

    ligas: list[Liga] = []
    for nodo in deporte_nodo.get("groups", []):
        if not nodo.get("termKey"):
            continue
        deporte_esports = es_esports(nodo)
        if nodo.get("groups"):
            # Región con ligas -> patrón de 3 niveles
            for lg in nodo["groups"]:
                if not lg.get("termKey"):
                    continue
                ligas.append(Liga(
                    deporte=deporte,
                    region=nodo.get("name"),
                    region_tk=nodo.get("termKey"),
                    liga=lg.get("name", lg.get("termKey")),
                    liga_tk=lg["termKey"],
                    group_id=lg.get("id"),
                    patron="3-niveles",
                    url=url_3_niveles(deporte, nodo["termKey"], lg["termKey"]),
                    nombre_deporte=deporte_nodo.get("name", ""),
                    event_count=lg.get("eventCount"),
                    es_esports=deporte_esports or es_esports(lg),
                ))
        else:
            # Liga terminal (sin país) -> patrón de 2 niveles
            ligas.append(Liga(
                deporte=deporte,
                region=None,
                region_tk=None,
                liga=nodo.get("name", nodo.get("termKey")),
                liga_tk=nodo["termKey"],
                group_id=nodo.get("id"),
                patron="2-niveles",
                url=url_2_niveles(deporte, nodo["termKey"]),
                nombre_deporte=deporte_nodo.get("name", ""),
                event_count=nodo.get("eventCount"),
                es_esports=deporte_esports,
            ))

    if not incluir_esports:
        ligas = [l for l in ligas if not l.es_esports]

    return ligas, deportes


def listar_deportes(sesion: requests.Session) -> list[dict]:
    raiz, _, err = get_json(sesion, "group.json")
    if raiz is None:
        raise ErrorAPI(f"No se pudo leer group.json: {err}")
    deportes = raiz.get("group", raiz).get("groups", [])
    salida = []
    for d in deportes:
        hijos = d.get("groups") or []
        n_ligas = sum(len(h.get("groups") or []) if h.get("groups") else 1 for h in hijos)
        salida.append({
            "termKey": d.get("termKey"),
            "nombre": d.get("name"),
            "englishName": d.get("englishName"),
            "eventos_declarados": d.get("eventCount"),
            "ligas": n_ligas,
        })
    return salida


# --------------------------------------------------------------------------- #
#  Parsing de eventos                                                          #
# --------------------------------------------------------------------------- #
def _num(valor: Any, divisor: float = 1000.0) -> Optional[float]:
    """odds/line vienen x1000 (1400 = 1,40). None si el campo viene vacío."""
    if valor is None or valor == "":
        return None
    try:
        return round(float(valor) / divisor, 4)
    except (TypeError, ValueError):
        return None


def _entero(valor: Any) -> Optional[int]:
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _ruta_legible(path: Any) -> dict:
    """event.path = [deporte, región?, liga] -> nombres legibles + detección esports."""
    vacio = {"deporte": None, "region": None, "liga": None,
             "region_tk": None, "liga_tk": None, "clave_liga": None, "es_esports": False}
    if not isinstance(path, list) or not path:
        return vacio
    nodos = [n for n in path if isinstance(n, dict)]
    nombres = [n.get("name") for n in nodos]
    tks = [n.get("termKey") or "" for n in nodos]
    region_tk = tks[1] if len(tks) > 2 else None
    liga_tk = tks[-1] if len(tks) >= 2 else None
    return {
        "deporte": nombres[0] if len(nombres) > 0 else None,
        "region": nombres[1] if len(nombres) > 2 else None,
        "liga": nombres[-1] if len(nombres) >= 2 else None,
        "region_tk": region_tk,
        "liga_tk": liga_tk,
        "clave_liga": f"{region_tk or '-'}/{liga_tk}" if liga_tk else None,
        # un nodo "esports" en la ruta = deporte electrónico anidado (gotcha 6)
        "es_esports": any("esport" in tk.lower() for tk in tks),
    }


def parsear_mercado(bloque: dict) -> dict:
    """betOffers[] -> mercado con sus outcomes ya convertidos."""
    tipo = bloque.get("betOfferType") or {}
    criterio = bloque.get("criterion") or {}
    outcomes = []
    for oc in bloque.get("outcomes", []) or []:
        outcomes.append({
            "id": oc.get("id"),
            "etiqueta": oc.get("label"),
            "etiqueta_en": oc.get("englishLabel"),
            "tipo": oc.get("type"),
            "cuota": _num(oc.get("odds")),
            "linea": _num(oc.get("line")),
            "estado": oc.get("status"),
            "participante": oc.get("participant"),
        })
    return {
        "betOfferId": bloque.get("id"),
        "criterio": criterio.get("label"),
        "criterio_en": criterio.get("englishLabel"),
        "ocurrencia": criterio.get("occurrenceType"),
        "vigencia": criterio.get("lifetime"),
        "tipo": tipo.get("name"),
        "tipo_en": tipo.get("englishName"),
        "tipo_id": tipo.get("id"),
        "suspendido": bool(bloque.get("suspended")),
        "tags": bloque.get("tags") or [],
        "outcomes": outcomes,
    }


def _es_total(m: dict) -> bool:
    """Mercado de totales (Más/Menos). tipo_id 6 o englishName 'Over/Under'."""
    return (m["tipo_id"] == 6
            or "over/under" in (m["tipo_en"] or "").lower()
            or "total" in (m["criterio"] or "").lower())


def _es_handicap(m: dict) -> bool:
    """Mercado con hándicap. tipo_id 1 o englishName 'Handicap'/'Spread'."""
    return (m["tipo_id"] == 1
            or "handicap" in (m["tipo_en"] or "").lower()
            or "spread" in (m["tipo_en"] or "").lower())


def _es_ganador(m: dict) -> bool:
    """Ganador del partido (moneyline). tipo_id 2 o englishName 'Match'/'Winner'."""
    return (m["tipo_id"] == 2
            or "match" in (m["tipo_en"] or "").lower()
            or "winner" in (m["tipo_en"] or "").lower())


def _clasificar_total(criterio: str, criterio_en: str) -> str:
    """
    Clasifica un mercado de totales según el periodo al que se refiere.

    partido    -> total completo con prórroga ("Total de puntos - Prórroga incluida")
    1a_parte   -> Q1 + Q2 juntos  (mercado útil a la mitad del 2Q)
    2a_parte   -> Q3 + Q4 juntos
    cuarto_N   -> un cuarto concreto
    equipo     -> total de un solo equipo ("Total de puntos de <Equipo>")
    otro       -> cualquier otra cosa
    """
    c = (criterio or "").lower()
    e = (criterio_en or "").lower()
    # 1) totales de un SOLO equipo: "Total de puntos de <Equipo> ..." -> van aparte
    if c.startswith("total de puntos de "):
        return "equipo"
    if e.startswith("total points - ") and not any(
            k in e for k in ("including overtime", "overtime", "half", "quarter")):
        return "equipo"
    # 2) total completo del partido con prórroga
    if ("prórroga incluida" in c or "including overtime" in e
            or "incluyendo prórroga" in c) and "parte" not in c and "cuarto" not in c \
            and "half" not in e and "quarter" not in e:
        return "partido"
    if "1.ª parte" in c or "primera parte" in c or "1st half" in e:
        return "1a_parte"
    if ("2ª parte" in c or "segunda parte" in c or "2nd half" in e
            or "second half" in e):
        return "2a_parte"
    for n in (1, 2, 3, 4):
        if f"cuarto {n}" in c or f"quarter {n}" in e:
            return f"cuarto_{n}"
    if "prórroga" in c or "overtime" in e or "parte" in c or "half" in e:
        return "otro"
    return "equipo"


def _resumen_mercados(mercados: list[dict]) -> dict:
    """Atajos útiles: ganador, totales por periodo y hándicap ya convertidos."""
    ganador = None
    totales = []
    handicap = None
    for m in mercados:
        if m["suspendido"]:
            continue
        abiertos = [o for o in m["outcomes"] if o["estado"] == "OPEN" and o["cuota"]]
        if not abiertos:
            continue
        if _es_ganador(m) and ganador is None:
            ganador = {
                "criterio": m["criterio"],
                "apuestas": [{"etiqueta": o["etiqueta"], "cuota": o["cuota"]}
                             for o in abiertos],
            }
        elif _es_total(m):
            linea = next((o["linea"] for o in abiertos if o["linea"] is not None), None)
            over = next((o["cuota"] for o in abiertos if o["tipo"] == "OT_OVER"), None)
            under = next((o["cuota"] for o in abiertos if o["tipo"] == "OT_UNDER"), None)
            if over is not None or under is not None:
                totales.append({
                    "criterio": m["criterio"],
                    "clasificacion": _clasificar_total(m["criterio"], m["criterio_en"]),
                    "linea": linea,
                    "over": over,
                    "under": under,
                })
        elif _es_handicap(m) and handicap is None:
            handicap = {
                "criterio": m["criterio"],
                "apuestas": [{"etiqueta": o["etiqueta"], "cuota": o["cuota"],
                              "linea": o["linea"]} for o in abiertos],
            }
    por_tipo = {}
    for t in totales:
        por_tipo.setdefault(t["clasificacion"], t)
    return {
        "ganador": ganador,
        "totales": totales,
        # atajos: el primero de cada periodo (el más líquido)
        "total_partido": por_tipo.get("partido"),
        "total_1a_parte": por_tipo.get("1a_parte"),     # = Q1 + Q2
        "total_cuarto1": por_tipo.get("cuarto_1"),
        "total_cuarto2": por_tipo.get("cuarto_2"),
        "handicap": handicap,
    }


def parsear_evento(bloque: dict, deporte: str) -> dict:
    ev = bloque.get("event", {}) or {}
    ld = bloque.get("liveData") or {}
    ck = ld.get("matchClock") or {}
    sc = ld.get("score") or {}
    ruta = _ruta_legible(ev.get("path"))

    marcador = None
    if sc.get("home") not in (None, "") or sc.get("away") not in (None, ""):
        marcador = {
            "local": _entero(sc.get("home")),
            "visitante": _entero(sc.get("away")),
            "periodo": ck.get("period"),
            "parciales": sc.get("info"),
        }

    mercados = [parsear_mercado(bo) for bo in bloque.get("betOffers", []) or []]
    deporte_evento = ev.get("sport") or ""

    return {
        "id": ev.get("id"),
        "nombre": ev.get("name"),
        "local": ev.get("homeName"),
        "visitante": ev.get("awayName"),
        "nombre_en": ev.get("englishName"),
        "inicio_utc": ev.get("start"),
        "liga": ev.get("group"),
        "group_id": ev.get("groupId"),
        "ruta": ruta,
        "deporte": deporte_evento,
        "tags": ev.get("tags") or [],
        "estado": ev.get("state"),
        "en_vivo": ev.get("state") == "STARTED",
        "mercados_no_vivos": ev.get("nonLiveBoCount"),
        "mercados_vivos": ev.get("liveBoCount"),
        "es_esports": (not deporte_evento.upper().startswith(deporte.upper()))
                      or ruta["es_esports"],
        "marcador": marcador,
        "reloj": {
            "minuto": ck.get("minute"),
            "segundo": ck.get("second"),
            "periodo": ck.get("period"),
            "periodo_id": ck.get("periodId"),
            "corriendo": ck.get("running"),
            "min_izq": ck.get("minutesLeftInPeriod"),
            "seg_izq": ck.get("secondsLeftInMinute"),
        } if ck else None,
        "resumen": _resumen_mercados(mercados),
        "mercados": mercados,
    }


# --------------------------------------------------------------------------- #
#  Descarga de una liga                                                        #
# --------------------------------------------------------------------------- #
def descargar_liga(
    sesion: requests.Session,
    liga: Liga,
    *,
    timeout: float,
    reintentos: int,
) -> tuple[list[dict], dict]:
    """
    Descarga los partidos de una liga.

    Si la URL del patrón principal da 404, se intenta el otro patrón (3 <-> 2
    niveles) y se anota en `patron_usado`, cubriendo el caso de un nodo cuya
    clasificación en group.json no coincida con la del endpoint.
    """
    alternativas: list[tuple[str, str]] = [(liga.url, liga.patron)]
    if liga.patron == "3-niveles" and liga.region_tk:
        alternativas.append((url_2_niveles(liga.deporte, liga.liga_tk), "2-niveles"))
    elif liga.patron == "2-niveles" and liga.region_tk:
        alternativas.append((url_3_niveles(liga.deporte, liga.region_tk, liga.liga_tk),
                             "3-niveles"))

    errores: list[str] = []
    for url, patron in alternativas:
        datos, status, err = get_json(sesion, url, timeout=timeout, reintentos=reintentos)
        if datos is not None:
            eventos = [parsear_evento(b, liga.deporte) for b in datos.get("events", []) or []]
            liga.patron_usado = patron
            if errores:
                liga.nota = _juntar_nota(liga.nota, "URL principal 404; usada la alternativa")
            return eventos, {"ok": True, "patron": patron, "url": url}
        errores.append(f"{patron}: {err}")

    liga.patron_usado = ""
    liga.nota = _juntar_nota(liga.nota, "; ".join(errores))
    return [], {"ok": False, "error": "; ".join(errores)}


def descargar_mercados_completos(
    sesion: requests.Session,
    ids: list[int],
    *,
    timeout: float,
    reintentos: int,
    lote: int = 20,
) -> dict[int, list[dict]]:
    """
    Opcional: /betoffer/event/{id}.json trae el menú COMPLETO de mercados de un
    evento (el listView solo trae los inline del layout combinado).

    Estructura (verificada): los mercados están en la RAÍZ de la respuesta
    (`betOffers[]`) y cada uno lleva `eventId`; `events[]` es solo metadato.
    Acepta varios ids separados por coma en la misma petición.
    """
    salida: dict[int, list[dict]] = {}
    for i in range(0, len(ids), lote):
        trozo = ids[i:i + lote]
        datos, _, err = get_json(
            sesion, f"betoffer/event/{','.join(map(str, trozo))}.json",
            timeout=timeout, reintentos=reintentos)
        if datos is None:
            for eid in trozo:
                salida.setdefault(eid, [])
            continue
        for bo in datos.get("betOffers", []) or []:
            eid = bo.get("eventId")
            if eid:
                salida.setdefault(eid, []).append(parsear_mercado(bo))
        # el endpoint no devuelve mercados para ids sin cuotas: completar vacío
        for eid in trozo:
            salida.setdefault(eid, [])
        time.sleep(PAUSA_POR_DEFECTO)
    return salida


# --------------------------------------------------------------------------- #
#  Contraste con el deporte completo (respaldo)                                #
# --------------------------------------------------------------------------- #
def contraste_deporte(
    sesion: requests.Session,
    deporte: str,
    ligas: list[Liga],
    *,
    timeout: float,
    reintentos: int,
) -> dict:
    """
    Descarga /listView/{deporte}.json y comprueba que cada evento de ahí aparece
    en alguna de las ligas descargadas. Lo que no aparezca queda listado en
    `sin_liga_descargada` (suele ser una liga que no está en group.json).
    """
    datos, status, err = get_json(sesion, f"listView/{deporte}.json",
                                  timeout=timeout, reintentos=reintentos)
    if datos is None:
        return {"ok": False, "error": err, "n_eventos": 0, "cubiertos": 0,
                "sin_liga_descargada": []}

    eventos = datos.get("events", []) or []
    vistos = {l.clave for l in ligas}
    descargados: set[int] = set()
    for l in ligas:
        descargados.update(e["id"] for e in l.eventos if e.get("id"))

    huerfanos = []
    n_esports = 0
    for bloque in eventos:
        ev = bloque.get("event", {}) or {}
        if ev.get("id") in descargados:
            continue
        ruta = _ruta_legible(ev.get("path"))
        clave = ruta["clave_liga"] or "-"
        deporte_ev = ev.get("sport") or ""
        es_esports_h = ruta["es_esports"] or not deporte_ev.upper().startswith(deporte.upper())
        n_esports += 1 if es_esports_h else 0
        huerfanos.append({
            "id": ev.get("id"),
            "nombre": ev.get("name"),
            "liga": ev.get("group"),
            "clave_liga": clave,
            "region": ruta["region"],
            "region_tk": ruta["region_tk"],
            "liga_tk": ruta["liga_tk"],
            "liga_en_group_json": clave in vistos,
            "deporte": deporte_ev,
            "es_esports": es_esports_h,
            "estado": ev.get("state"),
        })

    return {
        "ok": True,
        "n_eventos": len(eventos),
        "cubiertos": len(eventos) - len(huerfanos),
        "n_esports": n_esports,
        "sin_liga_descargada": huerfanos,
    }


# --------------------------------------------------------------------------- #
#  Normalización de nombres (solo para mensajes)                               #
# --------------------------------------------------------------------------- #
def _juntar_nota(previa: str, nueva: str) -> str:
    """Une notas de una liga sin duplicar ni perder la anterior."""
    partes = [p.strip() for p in (previa or "").split(";") if p.strip()]
    texto = (nueva or "").strip()
    if texto and texto not in partes:
        partes.append(texto)
    return "; ".join(partes)


def _ascii(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def ligas_huerfanas(deporte: str, contraste: dict) -> list[Liga]:
    """
    Ligas que aparecen en /listView/{deporte}.json pero NO están en group.json.

    Puede pasar de verdad: el árbol de group.json se recarga y una liga
    desaparece mientras sus partidos siguen publicados (pasó con
    spain/leb_plata y belgium/top_division__w_ el 10-10-2026). Aquí se
    reconstruye la URL a partir de la `path` del evento y se descarga igual.
    """
    if not contraste.get("ok"):
        return []
    por_clave: dict[str, Liga] = {}
    for h in contraste["sin_liga_descargada"]:
        if h.get("es_esports") or not h.get("liga_tk") or h.get("liga_en_group_json"):
            continue
        clave = h.get("clave_liga") or h["liga_tk"]
        if clave in por_clave:
            continue
        region_tk = h.get("region_tk")
        por_clave[clave] = Liga(
            deporte=deporte,
            region=h.get("region"),
            region_tk=region_tk,
            liga=h.get("liga") or h["liga_tk"],
            liga_tk=h["liga_tk"],
            group_id=None,
            patron="3-niveles" if region_tk else "2-niveles",
            url=(url_3_niveles(deporte, region_tk, h["liga_tk"]) if region_tk
                 else url_2_niveles(deporte, h["liga_tk"])),
            nota="no estaba en group.json; encontrada por el contraste",
        )
    return list(por_clave.values())


# --------------------------------------------------------------------------- #
#  Principal                                                                    #
# --------------------------------------------------------------------------- #
def ejecutar(
    deporte: str,
    *,
    carpeta: Path = CARPETA_SALIDA,
    pausa: float = PAUSA_POR_DEFECTO,
    timeout: float = TIMEOUT_POR_DEFECTO,
    reintentos: int = REINTENTOS_POR_DEFECTO,
    incluir_esports: bool = False,
    solo_ligas: bool = False,
    mercados: str = "principales",
    compacto: bool = False,
) -> Path:
    """Descarga un deporte entero y devuelve la ruta del JSON generado."""
    t0 = time.time()
    carpeta.mkdir(parents=True, exist_ok=True)
    sesion = requests.Session()

    print(f"Deporte: {deporte}")
    ligas, _ = ligas_del_deporte(sesion, deporte, incluir_esports=incluir_esports,
                                 timeout=timeout, reintentos=reintentos)
    print(f"Ligas descubiertas en group.json: {len(ligas)}"
          f" ({sum(1 for l in ligas if l.region_tk)} con país, "
          f"{sum(1 for l in ligas if not l.region_tk)} sin país)")

    if solo_ligas:
        for l in ligas:
            print(f"  {l.clave:34s} {l.nombre_completo:38s} "
                  f"eventos={l.event_count}  {l.url}")
        ruta = carpeta / f"kambi_{deporte}_ligas_{date.today().isoformat()}.json"
        _escribir(ruta, {
            "scrapeado": datetime.now().astimezone().isoformat(timespec="seconds"),
            "deporte": deporte,
            "n_ligas": len(ligas),
            "n_eventos": 0,
            "solo_ligas": True,
            "ligas": [l.a_json() for l in ligas],
        }, compacto)
        print(f"\nJSON (solo listado): {ruta}")
        return ruta

    print(f"Descargando {len(ligas)} ligas con {pausa:.2f}s de espera entre peticiones...")
    fallos = []
    for i, liga in enumerate(ligas, 1):
        eventos, info = descargar_liga(sesion, liga, timeout=timeout, reintentos=reintentos)
        liga.eventos = eventos
        liga.n_eventos = len(eventos)
        marca = "OK " if info.get("ok") else "ERR"
        extra = f" [{liga.nota}]" if liga.nota else ""
        print(f"  [{i:2d}/{len(ligas)}] {marca} {liga.clave:32s} "
              f"{liga.nombre_completo:34s} eventos={liga.n_eventos:3d}{extra}")
        if not info.get("ok"):
            fallos.append({"liga": liga.nombre_completo, "clave": liga.clave,
                           "url": liga.url, "error": info.get("error")})
        time.sleep(pausa)

    # --- mercados completos (opcional) -------------------------------------
    if mercados == "completos":
        ids = [e["id"] for l in ligas for e in l.eventos if e.get("id")]
        print(f"\nDescargando mercados completos de {len(ids)} eventos (lotes de 20)...")
        completos = descargar_mercados_completos(sesion, ids, timeout=timeout,
                                                 reintentos=reintentos)
        for l in ligas:
            for e in l.eventos:
                e["n_mercados_inline"] = len(e["mercados"])
                extra = completos.get(e["id"]) or []
                if extra:
                    e["mercados"] = extra
                    e["resumen"] = _resumen_mercados(extra)
                    e["mercados_completos"] = True
                else:
                    e["mercados_completos"] = False

    # --- contraste contra el deporte entero --------------------------------
    print("\nContraste contra listView/{deporte}.json ...")
    contraste = contraste_deporte(sesion, deporte, ligas, timeout=timeout,
                                  reintentos=reintentos)
    time.sleep(pausa)

    # --- auto-sanado: ligas que ya no están en group.json ------------------
    huerfanas = ligas_huerfanas(deporte, contraste)
    if huerfanas:
        print(f"Auto-sanado: {len(huerfanas)} liga(s) presentes en el deporte pero ausentes "
              f"de group.json; se descargan con su URL deducida de la ruta del evento.")
        recuperadas = 0
        for i, liga in enumerate(huerfanas, 1):
            eventos, info = descargar_liga(sesion, liga, timeout=timeout,
                                           reintentos=reintentos)
            liga.eventos, liga.n_eventos = eventos, len(eventos)
            marca = "OK " if info.get("ok") else "ERR"
            extra = f" [{liga.nota}]" if liga.nota else ""
            print(f"  [{i}/{len(huerfanas)}] {marca} {liga.clave:32s} "
                  f"{liga.nombre_completo:34s} eventos={liga.n_eventos:3d}{extra}")
            if info.get("ok") and liga.n_eventos:
                ligas.append(liga)
                recuperadas += liga.n_eventos
            time.sleep(pausa)
        if recuperadas:
            descargados = {e["id"] for l in ligas for e in l.eventos if e.get("id")}
            for h in contraste["sin_liga_descargada"]:
                h["recuperado"] = h["id"] in descargados
            contraste["cubiertos"] = contraste["n_eventos"] - sum(
                1 for h in contraste["sin_liga_descargada"] if not h.get("recuperado"))
            print(f"  Recuperados {recuperadas} evento(s) de ligas ausentes de group.json.")

    n_eventos = sum(l.n_eventos for l in ligas)
    ruta = carpeta / f"kambi_{deporte}_ligas_{date.today().isoformat()}.json"
    duracion = round(time.time() - t0, 1)

    documento = {
        "scrapeado": datetime.now().astimezone().isoformat(timespec="seconds"),
        "deporte": deporte,
        "deporte_nombre": ligas[0].nombre_deporte if ligas else None,
        "n_ligas": len(ligas),
        "n_eventos": n_eventos,
        "n_en_vivo": sum(1 for l in ligas for e in l.eventos if e["en_vivo"]),
        "duracion_s": duracion,
        "fallos": fallos,
        "contraste_deporte_completo": contraste,
        "ligas": [l.a_json() for l in ligas],
    }
    _escribir(ruta, documento, compacto)

    # --- resumen en consola -------------------------------------------------
    print("\n" + "=" * 78)
    print(f"Deporte          : {deporte} ({documento['deporte_nombre']})")
    print(f"Ligas descargadas: {len(ligas)}  (con eventos: "
          f"{sum(1 for l in ligas if l.n_eventos)}, sin eventos: "
          f"{sum(1 for l in ligas if not l.n_eventos)})")
    print(f"Eventos          : {n_eventos}  (en vivo: {documento['n_en_vivo']})")
    print(f"Fallos           : {len(fallos)}")
    for f in fallos:
        print(f"   - {f['liga']} ({f['clave']}): {f['error']}")
    if contraste.get("ok"):
        print(f"Contraste        : {contraste['cubiertos']}/{contraste['n_eventos']} "
              f"eventos del deporte cubiertos por las ligas descargadas")
        sin_liga = contraste["sin_liga_descargada"]
        n_esports = contraste.get("n_esports", 0)
        recuperados = [h for h in sin_liga if h.get("recuperado")]
        otros = [h for h in sin_liga
                 if not h.get("es_esports") and not h.get("recuperado")]
        if recuperados:
            print(f"   Recuperadas por auto-sanado (liga ausente de group.json): "
                  f"{len(recuperados)}")
            for h in recuperados[:10]:
                print(f"       + {h['nombre']}  [{h['clave_liga']}]")
        if n_esports:
            print(f"   Excluidos a propósito (ligas esports anidadas): {n_esports}")
            for tk, n in Counter(h.get("liga_tk") for h in sin_liga
                                 if h.get("es_esports")).most_common(5):
                print(f"       - {tk}: {n} eventos")
        if otros:
            print(f"   Sin liga descargada ({len(otros)}):")
            for h in otros[:15]:
                print(f"       - {h['nombre']}  [{h['clave_liga']}] "
                      f"(está en group.json: {h['liga_en_group_json']})")
    else:
        print(f"Contraste        : no disponible ({contraste.get('error')})")
    print(f"Duración         : {duracion}s")
    print(f"JSON             : {ruta}  ({ruta.stat().st_size / 1024:.1f} KB)")
    print("=" * 78)
    return ruta


def _escribir(ruta: Path, documento: dict, compacto: bool = False) -> None:
    with open(ruta, "w", encoding="utf-8") as fh:
        if compacto:
            json.dump(documento, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(documento, fh, ensure_ascii=False, indent=2)


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kambi_ligas.py",
        description="Descarga todas las ligas y partidos de un deporte "
                    "de la API Kambi/BetPlay a un JSON agregado.",
    )
    p.add_argument("deporte", nargs="?", default="basketball",
                   help="termKey del deporte (por defecto: basketball; "
                        "football, tennis, baseball, ice_hockey, ...)")
    p.add_argument("--deportes", nargs="+", metavar="DEPORTE",
                   help="varios deportes de una vez")
    p.add_argument("--lista", action="store_true",
                   help="lista los deportes disponibles y sale")
    p.add_argument("--salida", type=Path, default=CARPETA_SALIDA,
                   help=f"carpeta de salida (por defecto: {CARPETA_SALIDA})")
    p.add_argument("--pausa", type=float, default=PAUSA_POR_DEFECTO,
                   help=f"segundos de espera entre peticiones (por defecto {PAUSA_POR_DEFECTO})")
    p.add_argument("--timeout", type=float, default=TIMEOUT_POR_DEFECTO,
                   help=f"timeout por petición (por defecto {TIMEOUT_POR_DEFECTO})")
    p.add_argument("--reintentos", type=int, default=REINTENTOS_POR_DEFECTO,
                   help=f"reintentos por URL (por defecto {REINTENTOS_POR_DEFECTO})")
    p.add_argument("--mercados", choices=("principales", "completos"),
                   default="principales",
                   help="'completos' usa /betoffer/event/{id} (menú entero, más lento)")
    p.add_argument("--incluir-esports", action="store_true",
                   help="no excluir los nodos de esports dentro del deporte")
    p.add_argument("--solo-ligas", action="store_true",
                   help="solo lista las ligas descubiertas, sin descargar partidos")
    p.add_argument("--compacto", action="store_true",
                   help="JSON en una sola línea (sin sangría)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = construir_parser().parse_args(argv)

    try:
        if args.lista:
            with requests.Session() as s:
                for d in listar_deportes(s):
                    print(f"  {d['termKey']:22s} {d['nombre']:28s} "
                          f"eventos={str(d['eventos_declarados']):>6}  ligas={d['ligas']}")
            return 0

        deportes = args.deportes or ([args.deporte] if args.deporte else [])
        if not deportes:
            print("Indica al menos un deporte. Ejemplo: python kambi_ligas.py basketball")
            print("Para ver los disponibles: python kambi_ligas.py --lista")
            return 2

        for deporte in deportes:
            ejecutar(
                deporte,
                carpeta=args.salida,
                pausa=args.pausa,
                timeout=args.timeout,
                reintentos=args.reintentos,
                incluir_esports=args.incluir_esports,
                solo_ligas=args.solo_ligas,
                mercados=args.mercados,
                compacto=args.compacto,
            )
            if deporte != deportes[-1]:
                print()
        return 0

    except ErrorAPI as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
