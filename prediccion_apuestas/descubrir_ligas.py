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


def ligas_sin_traduccion() -> list[tuple[str, int]]:
    """Ligas que publica la casa AHORA y no están en `LIGAS_API_SPORTS`.

    Se lee el feed en vivo en lugar de llevar una lista fija: el feed cambia
    a lo largo del día (entran y salen ligas según la hora), así que lo que
    hay que buscar se calcula en cada ejecución.
    """
    crudo = api_betplay._get_json(api_betplay.url_deporte())
    salen: dict[str, int] = {}
    for b in crudo.get("events", []) or []:
        p = api_betplay._parsear_evento(b)
        if p.clave_liga in salen or p.clave_liga in historico.LIGAS_API_SPORTS:
            continue
        if p.clave_liga in historico.NO_SON_LIGAS:
            continue
        salen[p.clave_liga] = p.liga_nombre or p.clave_liga
    return sorted(salen.items())

# palabras con las que se puede llamar a cada una en API-Sports.
# Se recalculan según lo que falte hoy; consultar con
# `python cobertura_ligas.py` (sección C) cuáles son.
SUGERENCIAS = {
    "club_tournaments/arabian_gulf_club_championship": ("asia", "wasl", "gulf", "arab"),
    "italy/serie_b": ("italy", "b nazionale"),
    "morocco/division_1": ("moroc", "maroc"),
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
    claves: list[tuple[str, int]] = []
    if not buscados:
        print("Leyendo el feed de la casa para ver qué falta (1 petición)…")
        try:
            claves = ligas_sin_traduccion()
        except api_betplay.ErrorAPI as exc:
            print("ERROR al leer el feed:", exc)
            return 1
        print(f"   {len(claves)} liga(s) del feed sin traducción\n")

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
        for clave, nombre in claves:
            print(f"\n  ► {nombre}  [{clave}]")
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
            for l in encontradas[:10]:
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
