# -*- coding: utf-8 -*-
"""
historico.py — Descarga histórico de partidos ACABADOS con sus cuartos.

La API de BetPlay NO guarda histórico (un partido acabado desaparece y sus
endpoints dan 404, verificado el 10-10-2026). Las muestras de entrenamiento
salen de la API de API-Sports, que sí publica los cuatro cuartos de cada
partido jugado:

    https://v1.basketball.api-sports.io/games?league={id}&season={etiqueta}

  * Token en `config.json` → `api_sports.token` (o variable API_SPORTS_TOKEN).
  * Cuota: 300 peticiones al día / 5000 totales. Cada liga entera sale en
    UNA sola petición (sin paginación), así que dar todas las ligas cuesta
    ~30 peticiones.
  * El `season` no es siempre un año: a veces es número (2026) y a veces
    etiqueta de texto ("2025-2026"). Se pide la lista real desde `/leagues`
    y se toman las temporadas que declaran con datos de partidos.
  * `scores.home/away` trae `quarter_1..4`, `over_time` y `total` por equipo.
    Los cuartos de la muestra son la suma de AMBOS equipos, como en el Excel.

Liga de BetPlay ↔ liga de API-Sports
-------------------------------------
BetPlay identifica cada liga por su clave única `region/liga_tk` (o solo
`liga_tk` si el árbol es de dos niveles). El mapa LIGAS_API_SPORTS traduce
esa clave al id de API-Sports; las traducciones están verificadas mirando un
partido de cada liga el 10-10-2026 (mismo nombre de liga y país).

Las ligas de BetPlay que NO están en el mapa (Serie B italiana, Paulista,
Super Liga del Reino Unido, pretemporada NBA...) no tienen contrapartida en
API-Sports: sus muestras las recoge el recolector en vivo de la aplicación
a medida que se juegan.

Uso:
    python historico.py                      # todas las ligas del mapa
    python historico.py --ligas chile/liga_nacional spain/liga_acb
    python historico.py --temporadas 3 --guardar --entrenar
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable, Optional

import requests

BASE = "https://v1.basketball.api-sports.io"
PAUSA = 0.4          # espera entre peticiones, igual que el resto del proyecto
TIMEOUT = 30
CUOTA_MINIMA = 5     # si quedan menos peticiones que esto, se para con aviso

CARPETA = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_CONFIG = os.path.join(CARPETA, "config.json")

# clave de BetPlay  ->  id(s) de liga en API-Sports (verificados 10-10-2026)
LIGAS_API_SPORTS: dict[str, tuple[int, ...]] = {
    # --- sin región en BetPlay (árbol de dos niveles) ---
    "euroleague": (120,),                  # Euroleague / Europe
    "nba": (12,),                          # NBA / USA
    "nba_preseason": (12,),                # NBA Pretemporada → modelo NBA
    "nba_cup": (422,),                     # NBA Cup / USA
    "wnba": (13,),                         # NBA W (WNBA) / USA
    "aba_league": (198,),                  # ABA League / Europe
    "bnxt_league": (368,),                 # BNXT League / Europe
    "club_friendly_matches": (205,),       # Friendly International / World
    # Añadidas el 10-10-2026 para que TODAS las ligas del feed tengan modelo
    # (verificado con descubrir_ligas.py: id + temporadas con datos).
    "latvian-estonian_league": (204,),     # LEBL (Letonia+Estonia) / Europe
    # --- con región ---
    "argentina/lnb": (18,),                # Liga A / Argentina
    "austria/obl": (217,),                 # Superliga (antes ÖBL) / Austria
    "austria/obl__w_": (218,),             # Superliga Women / Austria
    "australia/nbl": (1,),                 # NBL / Australia
    "belgium/top_division_1": (374,),      # Pro Basketball League / Belgium
    "belgium/top_division__w_": (25,),     # Top Division Women / Belgium
    "bulgaria/nbl": (113,),                # NBL / Bulgaria
    # Añadidas el 11-10-2026: ligas del feed que iban a respaldo y ya tienen id
    # verificado con descubrir_ligas.py (las que no existen en API-Sports —
    # Marruecos, Golfo Arábigo, NLB suizo, 1 Liga femenina polaca y las que
    # salen SIN DATOS — siguen con el modelo de respaldo y se entrenarán solas
    # con el recolector).
    "chile/liga_nacional": (114,),         # LNB / Chile
    "china/cba_preseason": (31,),          # CBA / China (usa el modelo de la CBA)
    "cyprus/division_a": (115,),           # Division A / Cyprus
    "czech_republic/nbl": (32,),           # NBL / República Checa
    "czech_republic/1__liga": (228,),      # 1. Liga / República Checa
    "france/elite": (2,),                  # LNB / France
    "france/lfb__w_": (10,),               # LFB W / France
    "greece/a1__w_": (234,),               # A1 Women / Greece
    "germany/bbl": (40,),                  # BBL / Germany
    "germany/dbbl__w_": (41,),             # DBBL Women / Germany
    "germany/pro_a": (42,),                # Pro A (2ª) / Germany
    "greece/gbl": (45,),                   # Basket League / Greece
    "hungary/nb_i_a": (46,),               # NB I A / Hungary
    "hungary/nb_i__w_": (47,),             # NB I A W / Hungary
    "italy/a2__w_": (243, 244,),           # Serie A2 Women N/S / Italy
    "italy/lega_a": (52,),                 # Lega A / Italy
    "italy/a1__w_": (53,),                 # Serie A1 W / Italy
    "italy/serie_a2": (242,),              # Serie A2 / Italy
    "israel/super_league": (51,),          # Super League / Israel
    "japan/b1__league": (56,),             # B League / Japan
    "japan/b2_league": (407,),             # B2.League / Japan
    "lithuania/lkl": (60,),                # LKL / Lithuania
    "lithuania/lmkl__w_": (62,),           # Moteru Lyga W / Lithuania
    "mexico/lnbp": (63,),                  # LNBP / Mexico
    "new_zealand/tauihi_basketball_aotearoa__w_": (375,),   # Tauihi W / Nueva Zelanda
    "norway/blno": (68,),                  # BLNO / Norway
    "philippines/pba": (151, 152, 153,),   # Philippine/Governors/Commissioners Cup
    "poland/1_liga": (71,),                # 1 Liga (2ª) / Poland
    "poland/plk": (72,),                   # Energa Basket Liga / Poland
    "poland/1_liga": (71,),                # 1 Liga (2ª división) / Poland
    "portugal/campeonato_nacional_2": (261,),   # Proliga / Portugal
    "portugal/lpb": (74,),                 # LPB (1ª división) / Portugal
    "portugal/lfb__w_": (260,),            # LFB Women / Portugal
    "portugal/taca_de_portugal": (75,),    # Taca de Portugal / Portugal
    "portugal/taca_de_portugal__w_": (400,),    # Taca de Portugal Women
    "south_korea/kbl": (91,),              # KBL / South Korea
    "spain/leb_oro": (95,),                # Primera FEB (antes LEB Oro) / Spain
    "spain/leb_plata": (96,),              # Segunda FEB (antes LEB Plata) / Spain
    "spain/liga_acb": (117,),              # ACB / Spain
    "spain/liga_femenina__w_": (94,),      # Liga Femenina W / Spain
    "sweden/basketligan__w_": (97,),       # Basketligan W / Sweden
    "sweden/superettan": (99,),            # Superettan / Sweden
    "switzerland/sb_league__w_": (101,),   # SB League W / Switzerland
    "turkey/super_league": (104,),         # Basketbol Süper Ligi / Turkey
    "turkey/tbl": (102,),                  # TBL (2ª división) / Turkey
    "united_kingdom/super_league_basketball": (108,),   # SLB / Reino Unido
    "united_kingdom/women_super_league_basketball__w_": (109,),   # SLB Women
}

# Claves que hoy no tienen traducción a API-Sports (o la tienen pero esa liga
# todavía no publica estadísticas). Siguen siendo apostables: caen al modelo
# del Excel (NBA/EUROLIGA) hasta que encuentren su id con
# `python descubrir_ligas.py <texto>`.
#   germany/dbbl__w_ (41), germany/pro_a (42) e italy/a2__w_ (243,244) YA están
#   en el mapa: API-Sports aún no les da estadísticas, así que cuestan 0
#   peticiones y se entrenarán solas en cuanto las publiquen.

# Claves que publica la casa pero que NO son ligas: son mercados de series
# (p. ej. "¿quién gana la serie?"). No se pueden entrenar ni apostar como un
# partido, así que se ignoran a propósito en vez de buscarles un id.
NO_SON_LIGAS = ("wnba_series_markets",)

ESTADOS_FINALIZADO = ("FT", "AET")     # finished / finished after overtime


class ErrorHistorico(Exception):
    """No se pudo descargar el histórico."""


# --------------------------------------------------------------------------- #
#  Muestra                                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class Muestra:
    """Un partido acabado con sus cuartos: la unidad de entrenamiento."""
    liga: str                # clave de BetPlay: 'nba', 'spain/liga_acb', ...
    fecha: Optional[date]
    q1: float                # suma de AMBOS equipos en el 1er cuarto
    q2: float
    total: float             # total final (con prórroga)
    partido: str = ""
    q3: Optional[float] = None
    q4: Optional[float] = None
    fuente: str = ""
    evento_id: Optional[str] = None   # id del partido en la fuente

    @property
    def clave(self) -> str:
        """Identificador único para no repetir muestras."""
        if self.evento_id:
            return f"{self.liga}|{self.evento_id}"
        f = self.fecha.isoformat() if self.fecha else "s/f"
        return f"{self.liga}|{f}|{self.partido}"

    @property
    def tupla(self) -> tuple[float, float, float]:
        """Lo que espera motor._ajustar: (1Q, 2Q, total)."""
        return (self.q1, self.q2, self.total)


def _fecha(texto: str) -> Optional[date]:
    try:
        return date.fromisoformat((texto or "")[:10])
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  Cliente de API-Sports                                                       #
# --------------------------------------------------------------------------- #
def leer_token() -> str:
    """Token de config.json (`api_sports.token`) o de la variable de entorno."""
    tok = os.environ.get("API_SPORTS_TOKEN", "")
    if tok:
        return tok
    try:
        with open(ARCHIVO_CONFIG, "r", encoding="utf-8") as fh:
            return json.load(fh).get("api_sports", {}).get("token", "")
    except (OSError, json.JSONDecodeError):
        return ""


class _Cliente:
    """Cliente mínimo: token, cuota y pausa entre peticiones."""

    def __init__(self) -> None:
        self.token = leer_token()
        self.sesion = requests.Session()
        self.sesion.headers.update({"x-apisports-key": self.token})
        self.restantes_dia: Optional[int] = None
        self.restantes_total: Optional[int] = None
        self.peticiones = 0
        self._inventario: Optional[dict[int, list]] = None

    def get(self, ruta: str, **params) -> dict:
        if not self.token:
            raise ErrorHistorico(
                "Falta el token de API-Sports: añádelo a config.json "
                "(\"api_sports\": {\"token\": \"...\"}).")
        if self.restantes_dia is not None and self.restantes_dia <= CUOTA_MINIMA:
            raise ErrorHistorico(
                f"Cuota diaria de API-Sports agotada (quedan "
                f"{self.restantes_dia} peticiones).")
        r = self.sesion.get(f"{BASE}/{ruta}", params=params, timeout=TIMEOUT)
        self.peticiones += 1
        try:
            self.restantes_dia = int(r.headers.get("x-ratelimit-remaining") or 0)
            self.restantes_total = int(
                r.headers.get("x-ratelimit-requests-remaining") or 0)
        except ValueError:
            pass
        time.sleep(PAUSA)
        if r.status_code != 200:
            raise ErrorHistorico(
                f"API-Sports respondió HTTP {r.status_code} en /{ruta}.")
        d = r.json()
        errores = d.get("errors")
        if errores:
            raise ErrorHistorico(
                "API-Sports: " +
                json.dumps(errores, ensure_ascii=False)[:200])
        return d

    def inventario(self) -> dict[int, list]:
        """Temporadas CON datos de partidos por liga (una sola petición)."""
        if self._inventario is not None:
            return self._inventario
        d = self.get("leagues")
        inv: dict[int, list] = {}
        for l in d.get("response") or []:
            lid = l.get("id")
            con_datos = []
            for s in l.get("seasons") or []:
                cov = (((s.get("coverage") or {}).get("games") or {})
                       .get("statistics") or {})
                if cov.get("teams"):
                    con_datos.append(s.get("season"))
            con_datos.sort(key=_anio_temporada, reverse=True)   # más nueva 1ª
            if con_datos:
                inv[lid] = con_datos
        self._inventario = inv
        return inv


def _anio_temporada(etiqueta) -> int:
    """2026 -> 2026 ; '2025-2026' -> 2026 ; '2024' -> 2024."""
    if isinstance(etiqueta, int):
        return etiqueta
    t = str(etiqueta)
    if "-" in t:
        try:
            return int(t.split("-")[1])
        except ValueError:
            pass
    try:
        return int(t)
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
#  Descarga                                                                    #
# --------------------------------------------------------------------------- #
def _parsear_partidos(d: dict, clave: str, etiqueta) -> list[Muestra]:
    """Convierte la respuesta de /games en muestras (solo partidos acabados)."""
    salen: list[Muestra] = []
    for g in d.get("response") or []:
        estado = (g.get("status") or {}).get("short")
        if estado not in ESTADOS_FINALIZADO:
            continue                      # aun en juego o aplazado
        sc = g.get("scores") or {}
        h, a = sc.get("home") or {}, sc.get("away") or {}
        cuartos_h = [h.get(f"quarter_{i}") for i in (1, 2, 3, 4)]
        cuartos_a = [a.get(f"quarter_{i}") for i in (1, 2, 3, 4)]
        if any(v is None for v in cuartos_h + cuartos_a):
            continue                      # sin desglose completo
        cuartos = [float(x) + float(y) for x, y in zip(cuartos_h, cuartos_a)]
        th, ta = h.get("total"), a.get("total")
        if th is not None and ta is not None:
            total = float(th) + float(ta)
        else:
            total = (sum(cuartos)
                     + float(h.get("over_time") or 0)
                     + float(a.get("over_time") or 0))
        if total <= 0:
            continue
        eq = g.get("teams") or {}

        def nombre(e) -> str:
            return (e or {}).get("name") or "?"

        salen.append(Muestra(
            liga=clave,
            fecha=_fecha(g.get("date")),
            q1=cuartos[0], q2=cuartos[1], q3=cuartos[2], q4=cuartos[3],
            total=total,
            partido=f"{nombre(eq.get('home'))} - {nombre(eq.get('away'))}",
            fuente=f"apisports:{etiqueta}",
            evento_id=str(g.get("id")) if g.get("id") else None,
        ))
    return salen


def descargar_liga(clave: str, ids: Iterable[int],
                   cliente: Optional[_Cliente] = None,
                   n_temporadas: int = 2,
                   al_avanzar: Optional[Callable] = None,
                   minimo_muestras: int = 0) -> list[Muestra]:
    """
    Descarga una liga de BetPlay desde API-Sports.

    `clave` es la clave única de BetPlay ('nba', 'spain/liga_acb') y `ids` los
    ids de esa liga en API-Sports (varios cuando la liga se divide en copas,
    como la PBA). Por defecto las dos temporadas más recientes con datos.

    RESERVA DE TEMPORADAS: si con esas aún no hay `minimo_muestras`, sigue
    bajando TEMPORADAS ANTERIORES (la más nueva primero) hasta alcanzar el
    mínimo o agotar las disponibles. Así una liga recién creada —p. ej. la SLB
    del Reino Unido, que solo empezó en 2023— usa el histórico de cursos
    anteriores en vez de quedarse sin modelo.
    """
    cliente = cliente or _Cliente()
    inv = cliente.inventario()

    # (etiqueta, id) de TODAS las temporadas con datos, de más nueva a más vieja
    pares: list[tuple] = []
    for lid in ids:
        for etiqueta in inv.get(lid, []):
            pares.append((etiqueta, lid))
    pares.sort(key=lambda t: _anio_temporada(t[0]), reverse=True)

    if not pares:
        if al_avanzar:
            al_avanzar(clave, "ningún id con temporadas con datos en API-Sports")
        return []

    # primeras `n_temporadas` por id; el resto se guarda por si hacen falta
    limite = max(1, n_temporadas)
    vistos: dict[int, int] = {}
    primeras: list[tuple] = []
    resto: list[tuple] = []
    for etiqueta, lid in pares:
        vistos[lid] = vistos.get(lid, 0) + 1
        (primeras if vistos[lid] <= limite else resto).append((etiqueta, lid))

    salen: list[Muestra] = []
    plan = primeras if minimo_muestras <= 0 else primeras + resto
    for etiqueta, lid in plan:
        if minimo_muestras and len(salen) >= minimo_muestras:
            break               # ya hay historial suficiente: no gastar cuota
        d = cliente.get("games", league=lid, season=etiqueta)
        lote = _parsear_partidos(d, clave, etiqueta)
        salen.extend(lote)
        if al_avanzar:
            al_avanzar(f"{clave} ({etiqueta})", len(lote))
    return salen


def descargartodo(ligas: Optional[Iterable[str]] = None,
                  n_temporadas: int = 2,
                  al_avanzar: Optional[Callable] = None,
                  minimo_muestras: int = 0) -> list[Muestra]:
    """Descarga todas las ligas del mapa (o solo las pedidas)."""
    cliente = _Cliente()
    pedidas = list(ligas) if ligas else list(LIGAS_API_SPORTS)
    desconocidas = [c for c in pedidas if c not in LIGAS_API_SPORTS]
    if desconocidas:
        raise ErrorHistorico(
            "Ligas sin traducción a API-Sports: " + ", ".join(desconocidas) +
            ". Disponibles: " + ", ".join(sorted(LIGAS_API_SPORTS)))
    salen: list[Muestra] = []
    for clave in pedidas:
        try:
            salen.extend(descargar_liga(clave, LIGAS_API_SPORTS[clave], cliente,
                                        n_temporadas, al_avanzar,
                                        minimo_muestras))
        except ErrorHistorico as exc:
            if al_avanzar:
                al_avanzar(clave, f"ERROR: {exc}")
    if al_avanzar:
        al_avanzar(f"(cuota) peticiones={cliente.peticiones} "
                   f"restantes_día={cliente.restantes_dia} "
                   f"restantes_total={cliente.restantes_total}", None)
    return salen


def resumen_muestras(muestras: list[Muestra]) -> dict[str, int]:
    """Cuántas muestras hay por liga."""
    cuenta: dict[str, int] = {}
    for m in muestras:
        cuenta[m.liga] = cuenta.get(m.liga, 0) + 1
    return dict(sorted(cuenta.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- #
#  CLI                                                                         #
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(
        description="Descarga histórico de partidos acabados con sus cuartos "
                    "desde API-Sports.")
    p.add_argument("--ligas", nargs="*", default=None,
                   help="claves de BetPlay (nba, spain/liga_acb, ...)")
    p.add_argument("--temporadas", type=int, default=2,
                   help="temporadas más recientes por liga (por defecto 2)")
    p.add_argument("--cubrir", type=int, default=0,
                   help="si con las temporadas pedidas no llegan las muestras, "
                        "sigue bajando temporadas ANTERIORES hasta alcanzar "
                        "este mínimo (0 = no reservar)")
    p.add_argument("--guardar", action="store_true",
                   help="guarda las muestras en PostgreSQL")
    p.add_argument("--entrenar", action="store_true",
                   help="tras descargar, entrena los modelos por liga")
    p.add_argument("--minimo", type=int, default=None,
                   help="muestras mínimas por liga al entrenar")
    args = p.parse_args()

    def avance(que, n):
        # `n` es el número de partidos, o un mensaje cuando algo ha ido mal
        extra = "" if n is None else (f" → {n} partidos" if isinstance(n, int)
                                      else f" → {n}")
        print(f"   {que}{extra}", flush=True)

    print(f"Descargando de API-Sports (temporadas: {args.temporadas}"
          + (f", reserva hasta {args.cubrir} muestras" if args.cubrir else "")
          + ")…")
    muestras = descargartodo(args.ligas, args.temporadas, avance, args.cubrir)
    print(f"\nMuestras descargadas: {len(muestras)}")
    for liga, n in resumen_muestras(muestras).items():
        print(f"   {liga:38s} {n:5d}")

    if args.guardar or args.entrenar:
        import modelos
        guardadas = modelos.guardar_muestras(muestras)
        print(f"\nGuardadas en PostgreSQL (nuevas): {guardadas}")
    if args.entrenar:
        import modelos
        entrenados = modelos.entrenar_todo(args.minimo) if args.minimo \
            else modelos.entrenar_todo()
        print(f"Modelos entrenados: {len(entrenados)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
