# -*- coding: utf-8 -*-
"""
descubrir_ligas.py — Encuentra el id de API-Sports de las ligas que faltan.

Descarga el listado COMPLETO de ligas de API-Sports (una sola petición) y
busca en él las claves de BetPlay que todavía no tienen traducción en
`historico.LIGAS_API_SPORTS`, para poder añadirlas y entrenar su modelo.

Muestra, para cada candidato, las temporadas que tienen partidos con estadísticas
— que es lo que decide si "usar la temporada anterior" da suficientes muestras.

Uso:
    python descubrir_ligas.py                 # las que faltan de verdad
    python descubrir_ligas.py lituania lkl    # buscar por texto libre
    python descubrir_ligas.py --auto          # todas las del feed sin id +
                                              # validación por equipos
"""
from __future__ import annotations

import sys

import api_betplay
import historico
import modelos
import resultados

# claves de BetPlay que hoy no se pueden entrenar
SIN_TRADUCCION = ("austria/obl", "latvian-estonian_league", "lithuania/lkl",
                  "nba_preseason", "new_zealand/tauihi_basketball_aotearoa__w_",
                  "united_kingdom/super_league_basketball")

# palabras con las que se puede llamar a cada una en API-Sports
SUGERENCIAS = {
    "austria/obl": ("austria", "bundesliga", "öbl", "obl"),
    "latvian-estonian_league": ("latvia", "estonia", "lebl", "baltic"),
    "lithuania/lkl": ("lithuania", "lkl"),
    "nba_preseason": ("nba",),
    "new_zealand/tauihi_basketball_aotearoa__w_": ("zealand", "tauihi", "aotearoa"),
    "united_kingdom/super_league_basketball": ("kingdom", "super league", "britain",
                                               "england"),
}


def temporadas_con_datos(liga: dict) -> list:
    """Temporadas de esa liga que traen partidos con estadísticas."""
    salen = []
    for s in liga.get("seasons") or []:
        cov = (((s.get("coverage") or {}).get("games") or {}).get("statistics") or {})
        if cov.get("teams"):
            salen.append(s.get("season"))
    salen.sort(key=historico._anio_temporada, reverse=True)
    return salen


# --------------------------------------------------------------------------- #
#  Validación por equipos (modo --auto)                                        #
# --------------------------------------------------------------------------- #
def _equipos_betplay(liga: "api_betplay.Liga") -> set[str]:
    """Nombres de equipos que publica la casa para esa liga (eventos de hoy)."""
    try:
        partidos = api_betplay.obtener_partidos(liga=liga, timeout=15)
    except Exception:  # noqa: BLE001
        return set()
    equipos = set()
    for p in partidos:
        for lado in (p.nombre or "").split(" - "):
            lado = lado.strip()
            if lado:
                equipos.add(lado)
    return equipos


def _clave_canonica(liga: "api_betplay.Liga") -> str:
    """Clave como la guarda la app: termKey suelto si la liga es terminal."""
    return liga.liga_tk if not liga.region_tk else liga.clave


def _equipos_api(id_api: int, temporadas: list) -> set[str]:
    """
    Equipos que API-Sports conoce para ese id de liga.

    El endpoint `teams` exige `season`: se prueban las temporadas CON DATOS
    de la liga (de la más reciente hacia atrás) hasta que devuelva algo.
    """
    for temporada in list(temporadas or [])[:3]:
        try:
            d = historico._Cliente().get("teams", league=id_api,
                                         season=temporada)
        except historico.ErrorHistorico:
            continue
        equipos = {str(t.get("name") or "") for t in d.get("response") or []}
        if equipos:
            return equipos
    return set()


def validar_por_equipos(liga: "api_betplay.Liga",
                        candidato: dict) -> tuple[float, int, int]:
    """
    (% de equipos de la casa reconocidos en API-Sports, n Kambi, n API).

    Evita el error de asignar una liga equivocada (p. ej. '1 Liga (F)'
    polaca NO era la Energa W): si los equipos no cuadran, no se propone.
    """
    kb = _equipos_betplay(liga)
    api = _equipos_api(candidato.get("id"), temporadas_con_datos(candidato))
    if not kb or not api:
        return 0.0, len(kb), len(api)
    aciertos = 0
    for k in kb:
        nk = resultados._norm(k)
        if any(resultados._parecido(nk, resultados._norm(a)) >= 0.5
               for a in api):
            aciertos += 1
    return aciertos / len(kb), len(kb), len(api)


def auto() -> int:
    """Ligas del feed sin traducción + mejor candidato validado por equipos."""
    print("Modo --auto: ligas del feed sin id de API-Sports + validación "
          "por equipos\n")
    try:
        ligas_feed = api_betplay.obtener_ligas(api_betplay.DEPORTE_POR_DEFECTO)
    except Exception as exc:  # noqa: BLE001
        print("No se pudo leer el feed de la casa:", exc)
        return 1
    ya = set(historico.LIGAS_API_SPORTS)
    faltan = [l for l in ligas_feed
              if _clave_canonica(l) not in ya
              and _clave_canonica(l) not in historico.NO_SON_LIGAS]
    if not faltan:
        print("✔ Todas las ligas del feed tienen traducción a API-Sports.")
        return 0
    print(f"Faltan {len(faltan)} ligas. Descargando el listado de API-Sports "
          f"(1 petición)…")
    try:
        d = historico._Cliente().get("leagues")
    except historico.ErrorHistorico as exc:
        print("ERROR:", exc)
        return 1
    ligas_api = d.get("response") or []
    print(f"   {len(ligas_api)} ligas en API-Sports\n")

    propuestas: list[str] = []
    for liga in faltan:
        clave = _clave_canonica(liga)
        print(f"► {clave}  [{liga.nombre}]")
        buscar = [t for t in ((liga.region or "") + " " + liga.liga)
                  .lower().replace("/", " ").split() if len(t) > 2]
        candidatos = []
        for l in ligas_api:
            texto = (f"{l.get('name')} "
                     f"{(l.get('country') or {}).get('name')}").lower()
            puntos = sum(1 for b in buscar if b in texto)
            if puntos:
                candidatos.append((puntos, l))
        candidatos.sort(key=lambda x: -x[0])
        encontrado = False
        for _puntos, l in candidatos[:4]:
            ratio, nk, na = validar_por_equipos(liga, l)
            # exigente a propósito: ≥60 % de equipos Y al menos 3 de la casa
            # (con 2 equipos, un 50 % es solo 1 acierto y puede ser casualidad)
            valida = ratio >= 0.6 and nk >= 3
            marca = ("✔ VALIDADA" if valida else
                     ("¿posible?" if ratio >= 0.3 else "✗"))
            print(f"     id {str(l.get('id')):5s} {l.get('name')} "
                  f"({(l.get('country') or {}).get('name')}) → equipos "
                  f"{ratio * 100:.0f}% ({nk} casa / {na} API)  {marca}")
            if valida and not encontrado:
                encontrado = True
                propuestas.append(f'    "{clave}": ({l.get("id")},),'
                                  f"   # {l.get('name')} / "
                                  f"{(l.get('country') or {}).get('name')}")
        if not encontrado:
            print("     sin candidato validado: seguirá con modelo de respaldo "
                  "y se entrenará con el recolector")
    if propuestas:
        print("\n" + "=" * 78)
        print("PROPUESTAS para añadir a historico.LIGAS_API_SPORTS:")
        print("=" * 78)
        for linea in propuestas:
            print(linea)
    return 0


def main() -> int:
    if "--auto" in sys.argv:
        return auto()
    buscados = [s.lower() for s in sys.argv[1:]]
    claves = SIN_TRADUCCION if not buscados else []

    print("Descargando el listado de ligas de API-Sports (1 petición)…")
    try:
        d = historico._Cliente().get("leagues")
    except historico.ErrorHistorico as exc:
        print("ERROR:", exc)
        return 1
    ligas = d.get("response") or []
    print(f"   {len(ligas)} ligas de baloncesto\n")

    ya_traducidas = set(historico.LIGAS_API_SPORTS)
    muestras = modelos.contar_muestras()

    if claves:
        print("=" * 78)
        print("LIGAS QUE FALTAN — candidatos ordenados por parecido")
        print("=" * 78)
        for clave in claves:
            print(f"\n  ► {clave}")
            if clave in ya_traducidas:
                print("     (ya tiene traducción, no hace falta)")
                continue
            print(f"     muestras propias en la BD: {muestras.get(clave, 0)}")
            claves_busqueda = SUGERENCIAS.get(clave, (clave.split("/")[-1],))
            encontradas = []
            for l in ligas:
                nombre = str(l.get("name") or "").lower()
                pais = str((l.get("country") or {}).get("name") or "").lower()
                if any(b in nombre or b in pais for b in claves_busqueda):
                    encontradas.append(l)
            if not encontradas:
                print("     ✗ NINGÚN candidato: hay que buscarlo a mano")
                continue
            for l in encontradas[:6]:
                temps = temporadas_con_datos(l)
                print(f"     id {str(l.get('id')):5s}  {l.get('name')} "
                      f"({(l.get('country') or {}).get('name')})")
                print(f"           temporadas con datos: "
                      f"{temps if temps else 'NINGUNA'}")
                if temps:
                    print(f"           última: {temps[0]} · penúltima: "
                          f"{temps[1] if len(temps) > 1 else '—'}")
        return 0

    # --- búsqueda libre ---
    print("=" * 78)
    print("BÚSQUEDA LIBRE")
    print("=" * 78)
    for l in ligas:
        texto = f"{l.get('name')} {(l.get('country') or {}).get('name')}".lower()
        if any(b in texto for b in buscados):
            temps = temporadas_con_datos(l)
            print(f"  id {str(l.get('id')):5s} {l.get('name')} "
                  f"({(l.get('country') or {}).get('name')}) -> "
                  f"{temps if temps else 'SIN DATOS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
