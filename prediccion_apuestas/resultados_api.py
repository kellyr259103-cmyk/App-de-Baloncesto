# -*- coding: utf-8 -*-
"""
resultados.py — Resultado OFICIAL de un partido desde API-Sports.

Sirve para cerrar/corregir apuestas cuando el feed de BetPlay (Kambi) no es
fiable al final: deja `running=True` en el pitido, a veces da un total
impreciso en las transiciones (prórrogas, reloj con retraso) y elimina el
evento minutos después sin dejar un estado final claro.

Uso:
    from datetime import date
    total = resultados.resultado_oficial("BK Liepaja", "Rigas Juniori",
                                         date(2026, 10, 11))

Funcionamiento:
    * Descarga `games?date=YYYY-MM-DD` (con caché por fecha: 1 petición por
      día y proceso; también mira el día anterior y el siguiente por husos).
    * Solo considera partidos TERMINADOS (status "Game Finished" o
      "After Over Time") con marcador completo.
    * Empareja equipos normalizando nombres (sin tildes/mayúsculas, sin
      palabras de relleno) y compara por solapamiento de palabras; exige ≥50 %
      de parecido en ADOS los equipos.
"""

from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from typing import Optional

import historico

# palabras que no aportan a la identidad del equipo
_RELLENO = {
    "basket", "basketball", "baloncesto", "bc", "bk", "kk", "sc", "ac", "as",
    "cb", "club", "team", "b.c", "b", "c", "the", "de", "del",
    "women", "woman", "w", "f", "fem", "femenino", "femenina",
}


def _norm(texto: str) -> set[str]:
    """Nombre de equipo -> conjunto de palabras significativas."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = t.encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    for ch in "().,-'’/*_":
        t = t.replace(ch, " ")
    palabras = [p for p in t.split() if p and p not in _RELLENO]
    return set(palabras)


def _parecido(a: set[str], b: set[str]) -> float:
    """
    Similitud de Jaccard (|∩| / |∪|), 0..1.

    Jaccard PENALIZA los tokens de más: 'Saint-Quentin' vs 'Saint Quentin U21'
    da 2/3, mientras el partido senior (sin U21) da 1.0 → nunca se cuela el
    partido juvenil por delante del de mayores.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_cache: dict[str, list[dict]] = {}


def _juegos_del_dia(fecha: date) -> list[dict]:
    """Partidos TERMINADOS de esa fecha (con caché en memoria)."""
    clave = fecha.isoformat()
    if clave in _cache:
        return _cache[clave]
    juegos: list[dict] = []
    try:
        d = historico._Cliente().get("games", date=clave)
    except historico.ErrorHistorico:
        _cache[clave] = juegos
        return juegos
    for g in d.get("response") or []:
        st = (((g.get("status") or {}).get("long") or "") + " " +
              ((g.get("status") or {}).get("short") or "")).lower()
        if "finish" not in st and "after" not in st:
            continue                    # en juego / suspendido / por jugar
        sc = g.get("scores") or {}
        h = (sc.get("home") or {}).get("total")
        a = (sc.get("away") or {}).get("total")
        if h is None or a is None:
            continue
        eq = g.get("teams") or {}
        juegos.append({
            "local": ((eq.get("home") or {}).get("name") or ""),
            "visitante": ((eq.get("away") or {}).get("name") or ""),
            "total": float(h) + float(a),
        })
    _cache[clave] = juegos
    return juegos


def resultado_oficial(local: str, visitante: str, fecha: date,
                      minimo: float = 0.5) -> Optional[float]:
    """
    Total final oficial del partido, o None si no se encuentra.

    Busca en el día indicado y en los vecinos (±1 día por husos horarios) y
    se queda con el mejor emparejamiento de equipos (≥ `minimo` en ambos).
    """
    a_local, a_vis = _norm(local), _norm(visitante)
    if not a_local or not a_vis:
        return None
    mejor_total: Optional[float] = None
    mejor_score = 0.0
    for dia in (fecha, fecha - timedelta(days=1), fecha + timedelta(days=1)):
        for j in _juegos_del_dia(dia):
            s1 = _parecido(a_local, _norm(j["local"]))
            s2 = _parecido(a_vis, _norm(j["visitante"]))
            score = min(s1, s2)
            if score >= minimo and score > mejor_score:
                mejor_score = score
                mejor_total = j["total"]
    return mejor_total


def partes_de_betplay(partido: str) -> tuple[str, str]:
    """'Local - Visitante' del feed -> (local, visitante)."""
    if " - " in partido:
        local, visit = partido.rsplit(" - ", 1)
        return local.strip(), visit.strip()
    return partido, ""
