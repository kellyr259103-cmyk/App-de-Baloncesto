# -*- coding: utf-8 -*-
"""
cobertura_ligas.py — ¿Qué ligas pueden apostarse y cuáles no?

Contesta a "necesito que todas las ligas tengan motor" con tres listas:

  A. con modelo propio          -> el bot apuesta ya
  B. con traducción a API-Sports pero sin modelo -> basta reentrenar / bajar más
  C. sin traducción            -> hay que encontrar su id en API-Sports

Y para cada una dice cuántas TEMPORADAS con datos ofrece la API (para saber si
"usar la temporada anterior" resuelve el problema de muestras).
"""
from __future__ import annotations

import sys

import api_betplay
import historico
import modelos

# qué mínimo de muestras se considera "se puede apostar"
UMBRAL_MUESTRAS = modelos.MINIMO_MUESTRAS


def main() -> int:
    print("Leyendo el feed de la casa (1 petición)…")
    try:
        crudo = api_betplay._get_json(api_betplay.url_deporte())
    except api_betplay.ErrorAPI as exc:
        print("ERROR:", exc)
        return 1
    bloques = crudo.get("events", []) or []

    # --- todas las ligas que publica la casa, no solo las que están vivas ---
    vivas: dict[str, str] = {}
    for b in bloques:
        p = api_betplay._parsear_evento(b)
        if p.clave_liga not in vivas:
            vivas[p.clave_liga] = p.liga_nombre or p.clave_liga

    muestras = modelos.contar_muestras()
    entrenados = modelos.cargar_modelos()
    mapeadas = historico.LIGAS_API_SPORTS

    print(f"   {len(bloques)} partidos publicados · {len(vivas)} ligas distintas")
    print(f"   {len(entrenados)} modelos entrenados · {len(muestras)} ligas con muestras")
    print(f"   {len(mapeadas)} ligas traducidas a API-Sports\n")

    # --- inventario de temporadas de API-Sports (1 petición) ---
    print("Consultando a API-Sports qué temporadas tiene por liga (1 petición)…")
    inv: dict[int, list] = {}
    try:
        inv = historico._Cliente().inventario()
    except historico.ErrorHistorico as exc:
        print(f"   (no se pudo consultar: {exc})")

    a, b, sin_cobertura, c, no_liga = [], [], [], [], []
    for clave in sorted(vivas):
        if clave in historico.NO_SON_LIGAS:
            no_liga.append(clave)
            continue
        n_muestras = muestras.get(clave, 0)
        if clave in entrenados:
            a.append((clave, vivas[clave], entrenados[clave]))
            continue
        if clave in mapeadas:
            temps = [inv.get(i, []) for i in mapeadas[clave]]
            if any(temps):
                # traducida y CON datos: basta con bajarla y reentrenar
                b.append((clave, vivas[clave], n_muestras, temps))
            else:
                # traducida pero API-Sports aún no publica sus estadísticas:
                # no se puede entrenar (cuesta 0 peticiones) y va con el respaldo
                sin_cobertura.append((clave, vivas[clave], n_muestras))
            continue
        c.append((clave, vivas[clave], n_muestras))

    print("\n" + "=" * 78)
    print(f"A) CON MODELO PROPIO — el bot apuesta ya ({len(a)})")
    print("=" * 78)
    for clave, nombre, m in sorted(a, key=lambda x: -x[2].n):
        print(f"   {clave:38s} n={m.n:5d}  R²={m.r2_dos:5.2f}  "
              f"RMSE test={m.rmse_test_dos:5.1f}  [{nombre[:22]}]")

    print("\n" + "=" * 78)
    print(f"B) TRADUCIDAS Y CON DATOS PERO SIN MODELO — falta bajar/entrenar ({len(b)})")
    print("=" * 78)
    if not b:
        print("   ninguna: todas las traducidas ya tienen modelo")
    for clave, nombre, n, temps in b:
        ids = mapeadas[clave]
        print(f"   {clave:38s} muestras={n:4d} (min {UMBRAL_MUESTRAS})  [{nombre[:22]}]")
        for lid, etiquetas in zip(ids, temps):
            if etiquetas:
                print(f"       id {lid}: {len(etiquetas)} temporadas con datos")
                print(f"           -> {etiquetas[:4]}")
            else:
                print(f"       id {lid}: SIN temporadas con datos")

    print("\n" + "=" * 78)
    print(f"B') SIN COBERTURA EN API-SPORTS — mapeadas pero sin estadísticas ({len(sin_cobertura)})")
    print("=" * 78)
    if sin_cobertura:
        for clave, nombre, n in sin_cobertura:
            ids = ", ".join(str(i) for i in mapeadas[clave])
            print(f"   {clave:38s} id {ids:12s} muestras={n:4d}  [{nombre[:22]}]")
        print("\n   no se pueden entrenar hasta que API-Sports publique sus")
        print("   estadísticas; el bot las apuesta igual con el modelo del Excel")
    else:
        print("   ninguna: todas las traducidas tienen estadísticas")

    print("\n" + "=" * 78)
    print(f"C) SIN TRADUCCIÓN A API-SPORTS — aún sin modelo ({len(c)})")
    print("=" * 78)
    if c:
        for clave, nombre, n in c:
            print(f"   {clave:38s} muestras={n:4d}  [{nombre[:28]}]")
        print("\n   (buscar su id con: python descubrir_ligas.py <texto>)")
    else:
        print("   ninguna: todas las ligas del feed tienen traducción")

    # las que no tienen modelo propio siguen siendo apostables si el de
    # respaldo está entrenado — igual que hace la app en `modelo_para_partido`
    _clave_excel = {"NBA": "nba", "EUROLIGA": "euroleague"}
    c_resueltas = []
    for clave, nombre, _n in c + sin_cobertura:
        base = "NBA" if ("nba" in clave or "wnba" in clave) else "EUROLIGA"
        k = _clave_excel.get(base, base)      # la app usa el del Excel
        if k in entrenados and entrenados[k].n:
            c_resueltas.append((clave, nombre, base, entrenados[k].n))
    cubiertas = {r[0] for r in c_resueltas}

    # --- las que no son liga, ni las que van con el modelo de otra ---
    print("\n" + "=" * 78)
    print(f"D) RESUELTAS CON EL MODELO DE OTRA LIGA — el bot ya apuesta ({len(c_resueltas)})")
    print("=" * 78)
    for clave, nombre, base, n in c_resueltas:
        print(f"   {clave:38s} -> modelo {base} (n={n})  [{nombre[:24]}]")
    if not c_resueltas:
        print("   ninguna")

    print("\n" + "=" * 78)
    print(f"E) NO SON LIGAS — mercados de series, se ignoran ({len(no_liga)})")
    print("=" * 78)
    for clave in no_liga:
        print(f"   {clave}")
    if not no_liga:
        print("   ninguna")

    print("\n" + "=" * 78)
    print("DIAGNÓSTICO")
    print("=" * 78)
    print(f"   A:  {len(a):3d} ligas con SU modelo")
    print(f"   D:  {len(c_resueltas):3d} con el de otra liga (respaldo)")
    print(f"   B:  {len(b):3d} traducidas con datos → bajar y entrenar")
    print(f"   B': {len(sin_cobertura):3d} sin estadísticas en API-Sports → respaldo")
    print(f"   C:  {len(c):3d} sin traducción → añadir su id")
    print(f"   E:  {len(no_liga):3d} no son ligas")
    total_apostables = len(a) + len(c_resueltas)
    print(f"\n   ligas apostables por el bot: {total_apostables} de "
          f"{len(vivas) - len(no_liga)}")

    if inv and b:
        print("\n   Temporadas disponibles en API-Sports para las de B:")
        for clave, _nombre, _n, temps in b:
            for lid, etiquetas in zip(mapeadas[clave], temps):
                print(f"     {clave:34s} id {lid:4d}: "
                      f"{etiquetas if etiquetas else 'SIN DATOS'}")

    # --- modo test: exit 1 si alguna liga del feed se queda sin motor ---
    if "--comprobar" in sys.argv:
        rotas = [f"{clave} (sin modelo ni respaldo)"
                 for clave, _nombre, _n in c if clave not in cubiertas]
        sin_entrenar = [f"{clave} ({n} muestras, traducida con datos)"
                        for clave, _n, n, _t in b]
        if rotas or sin_entrenar:
            print("\nCOMPROBACIÓN: FALLA — ligas del feed sin motor:")
            for r in sin_entrenar:
                print(f"   {r}")
            for r in sorted(set(rotas)):
                print(f"   {r}")
            print("\n   → bajar/entrenar: python historico.py --ligas <clave> "
                  "--cubrir 120 --guardar --entrenar")
            return 1
        print(f"\nCOMPROBACIÓN: OK — las {total_apostables} ligas del feed "
              f"tienen motor ({len(a)} propios + {len(c_resueltas)} de respaldo)")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
