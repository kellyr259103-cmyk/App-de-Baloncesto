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
"""
from __future__ import annotations

import sys

import api_betplay
import historico
import modelos

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


def main() -> int:
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
