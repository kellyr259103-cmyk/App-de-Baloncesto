# -*- coding: utf-8 -*-
"""
descansos.py — Regla del descanso entre el 2Q y el 3Q, por liga.

El descanso NO dura lo mismo en todas las ligas (la NBA regula 15 min, la
FIBA 15 min, los amistosos sueltos suelen ser más laxos…), y para apostar
en ese hueco hace falta una regla **definida y configurable**, no un "más o
menos". Este módulo la centraliza:

  1. VENTANA (se detecta por ESTADO, no por reloj): el descanso empieza
     cuando el 2Q está completo y termina en cuanto arranca el 3Q. Mientras
     tanto, `1Q` y `2Q` son definitivos y la línea de la casa todavía no se
     ha reajustado al 3Q: es el momento de la estrategia.

  2. DURACIÓN POR LIGA: tabla `DURACIONES` (minutos) + sobreescripción por
     liga en `config.json` →  {"descansos": {"nba": 15, ...}}. Si la liga no
     aparece, se usa `DEFECTO_MINUTOS`.

  3. AUTOCALIBRACIÓN: la app observa los descansos reales (del fin del 2Q
     al arranque del 3Q) y los guarda en la tabla `descansos_liga`; con
     ellas se puede afinar la tabla y la app muestra los minutos medidos.

  4. SEGURO: si un partido lleva "en descanso" más minutos que la duración
     oficial + margen, se considera dato caducado y NO se apuesta (evita
     apostar con una línea vieja si la API dejó de actualizar el partido).

Uso típico (la app lo llama en cada ping):

    if descansos.en_ventana_descanso(p):
        ok, motivo = descansos.puede_apostar(p, p.clave_liga, marca_inicio)
        ...
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

# --------------------------------------------------------------------------- #
#  Regla                                                                       #
# --------------------------------------------------------------------------- #
DEFECTO_MINUTOS = 15     # regla FIBA/NBA para el descanso 2Q→3Q
MARGEN_MINUTOS = 2       # holgura antes de dar la ventana por caducada

# Duración del descanso (minutos) por liga. Todas las ligas de baloncesto
# del calendario regulado (NBA y competiciones FIBA) descansan 15 minutos
# entre el 2Q y el 3Q; se deja explícito por liga para poder ajustarlo sin
# tocar código, y para documentar qué liga está contemplada.
#
# Sobreescritura en config.json:
#     "descansos": {"club_friendly_matches": 12, "nba": 15}
DURACIONES: dict[str, int] = {
    # --- NBA / WNBA (reglamento NBA: 15 min) ---
    "nba": 15, "nba_cup": 15, "nba_preseason": 15, "wnba": 15,
    # --- Europa / FIBA (15 min) ---
    "euroleague": 15, "aba_league": 15, "bnxt_league": 15,
    "spain/liga_acb": 15, "spain/leb_oro": 15,
    "germany/bbl": 15, "france/elite": 15, "greece/gbl": 15,
    "italy/lega_a": 15, "italy/serie_a2": 15,
    "turkey/super_league": 15, "turkey/tbl": 15, "poland/plk": 15,
    "bulgaria/nbl": 15, "belgium/top_division_1": 15,
    # --- Resto del mundo (FIBA: 15 min) ---
    "argentina/lnb": 15, "chile/liga_nacional": 15,
    "portugal/campeonato_nacional_2": 15, "portugal/lfb__w_": 15,
    "portugal/taca_de_portugal": 15, "portugal/taca_de_portugal__w_": 15,
    "south_korea/kbl": 15, "japan/b1__league": 15,
    "australia/nbl": 15, "philippines/pba": 15,
    # --- Amistosos: sin reglamento estricto, se estima más corto ---
    "club_friendly_matches": 12,
}


# --------------------------------------------------------------------------- #
#  Duración por liga                                                           #
# --------------------------------------------------------------------------- #
def _config_descansos() -> dict:
    """Sobreescripciones del usuario desde config.json ("descansos")."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(ruta, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    brutos = cfg.get("descansos") or {}
    return {str(k): int(v) for k, v in brutos.items()
            if isinstance(v, (int, float)) and v > 0}


def duracion_descanso(clave_liga: str) -> int:
    """
    Minutos de descanso 2Q→3Q de una liga.

    Orden: config.json > tabla DURACIONES > DEFECTO_MINUTOS.
    """
    propia = _config_descansos().get(clave_liga)
    if propia:
        return propia
    return DURACIONES.get(clave_liga, DEFECTO_MINUTOS)


# --------------------------------------------------------------------------- #
#  Detección de la ventana (por ESTADO del partido, no por reloj)              #
# --------------------------------------------------------------------------- #
_YA_EN_3P_O_DESPUES = ("QUARTER3", "QUARTER4", "PERIOD3", "PERIOD4",
                       "OVERTIME", "OT")


def en_ventana_descanso(p) -> bool:
    """
    ¿El partido está en el hueco entre el fin del 2Q y el arranque del 3Q?

    Se apoya en las propiedades del propio `Partido` (que ya distinguen el
    descanso del 2Q de un cuarto en curso) y cierra la ventana en cuanto el
    3Q arranca: entonces la línea ya se ha movido y deja de ser comparable.

    Se cierra por DOS vías (por si la API tarda en publicar `Q3: x-y`):
      * ya hay puntos del 3Q (`p.q3` con valor), o
      * el reloj de la API ya marca 3er cuarto / 4T / prórroga.
    """
    if p is None or p.finalizado:
        return False
    if not p.q2_completo:          # el 2Q sigue en curso o sin empezar
        return False
    if p.q3 is not None:           # el 3Q ya ha empezado: ventana cerrada
        return False
    if (p.period_id or "").upper() in _YA_EN_3P_O_DESPUES:
        return False               # el reloj ya va en 3Q aunque aún no haya Q3
    return True


def minutos_desde(marca_inicio: Optional[float]) -> Optional[float]:
    """Minutos transcurridos desde que se abrió la ventana (None si no hay marca)."""
    if not marca_inicio:
        return None
    return (time.time() - marca_inicio) / 60.0


def ventana_caducada(clave_liga: str, marca_inicio: Optional[float]) -> bool:
    """
    ¿Se ha pasado ya de la duración oficial del descanso?

    Es el seguro anti-datos-viejos: si la API dejó de actualizar un partido,
    este bloque impide apostar con una línea que ya no refleja el partido.
    """
    transcurridos = minutos_desde(marca_inicio)
    if transcurridos is None:
        return False
    return transcurridos > duracion_descanso(clave_liga) + MARGEN_MINUTOS


def puede_apostar(p, clave_liga: str,
                  marca_inicio: Optional[float]) -> tuple[bool, str]:
    """
    ¿Se puede apostar AHORA en el descanso de este partido?

    Devuelve (ok, motivo) para poder enseñarlo en la interfaz.
    """
    if not en_ventana_descanso(p):
        return False, "no está en el descanso 2Q→3Q"
    if ventana_caducada(clave_liga, marca_inicio):
        return False, (f"descanso caducado (>{duracion_descanso(clave_liga)} min): "
                       "línea obsoleta")
    if p.linea is None:
        return False, "sin línea publicada"
    if not p.mercado_abierto:
        return False, "mercado suspendido"
    return True, "ventana abierta"


# --------------------------------------------------------------------------- #
#  Autocalibración: medir descansos reales                                     #
# --------------------------------------------------------------------------- #
def registrar_descanso_observado(clave_liga: str, minutos: float) -> None:
    """
    Guarda la duración medida de un descanso real (2Q fin → 3Q inicio).

    La app lo llama cuando ve el tránsito completo; con esos datos se puede
    afinar `DURACIONES` o la sobreescripción de config.json.
    """
    if not clave_liga or minutos <= 0 or minutos > 60:
        return                      # basura: tránsitos raros de la API
    import base_datos
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO descansos_liga
                          (liga, minutos_medios, n_descansos, ultimo_minuto)
                       VALUES (%s, %s, 1, %s)
                       ON CONFLICT (liga) DO UPDATE SET
                         minutos_medios = (descansos_liga.minutos_medios
                                           * descansos_liga.n_descansos
                                           + EXCLUDED.minutos_medios)
                                           / (descansos_liga.n_descansos + 1),
                         n_descansos = descansos_liga.n_descansos + 1,
                         ultimo_minuto = EXCLUDED.ultimo_minuto""",
                    (clave_liga, float(minutos), float(minutos)))
    finally:
        conn.close()


def descansos_medidos() -> dict[str, tuple[float, int]]:
    """{(liga): (minutos_medios, n)} de los descansos observados por la app."""
    import base_datos
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT liga, minutos_medios, n_descansos FROM descansos_liga")
            return {fila[0]: (float(fila[1]), int(fila[2])) for fila in cur.fetchall()}
    finally:
        conn.close()
