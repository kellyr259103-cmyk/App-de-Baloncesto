# -*- coding: utf-8 -*-
"""
auditar_ui.py — ¿Qué modelo enseña la interfaz para cada liga del feed?

Replica la resolución AUTO de la app (`App.modelo_para_partido`) contra el
feed real y la base de datos, y lista exactamente lo que vería el usuario:

    liga | modelo | n | aviso (VERDE = liga calibrada / ROJO = respaldo)

También marca incoherencias entre las tablas:
  * ligas con muestras pero sin modelo entrenado;
  * modelos entrenados que no aparecen en el feed de ahora.

Uso:
    python auditar_ui.py
"""
from __future__ import annotations

import api_betplay
import modelos
import motor


def _excel() -> dict:
    return {l: motor.cargar_modelo(motor.EXCEL_POR_DEFECTO, l)
            for l in ("EUROLIGA", "NBA")}


def resolver(clave: str, modelos_ligas: dict, excel: dict):
    """Idéntico a App.modelo_para_partido(p, 'AUTO')."""
    m = modelos_ligas.get(clave)
    if m is not None and m.n:
        return m, f"por liga ({clave})"
    base = "NBA" if "nba" in clave or "wnba" in clave else "EUROLIGA"
    return (excel.get(base) or motor.Modelo(liga=base, origen="")), \
        f"Excel/{base} (sin datos de {clave or 'la liga'})"


def main() -> int:
    modelos_ligas = modelos.cargar_modelos()
    excel = _excel()
    try:
        partidos = api_betplay.obtener_partidos_deporte("basketball")
    except api_betplay.ErrorAPI as exc:
        print("No se pudo leer el feed:", exc)
        return 1

    por_liga: dict[str, str] = {}
    for p in partidos:
        por_liga.setdefault(p.clave_liga or "?", p.nombre)

    muestras = modelos.contar_muestras()

    print(f"{len(partidos)} partidos del feed · {len(por_liga)} ligas · "
          f"{len(modelos_ligas)} modelos cargados\n")
    cab = (f"{'liga del feed':44s} {'modelo':14s} {'n':>6s}  aviso de la interfaz")
    print(cab)
    print("-" * len(cab))
    propias = respaldos = 0
    for clave in sorted(por_liga):
        m, etiqueta = resolver(clave, modelos_ligas, excel)
        propio = etiqueta.startswith("por liga")
        propias += propio
        respaldos += (not propio)
        aviso = ("VERDE ✔ liga calibrada" if propio else
                 f"ROJO ⚠ respaldo {m.liga}")
        print(f"{clave:44s} {m.liga:14s} {m.n:6d}  {aviso}")

    print("-" * len(cab))
    print(f"ligas con modelo PROPIO: {propias} · con RESPALDO: {respaldos} "
          f"(de {len(por_liga)})\n")

    con_muestras = {l for l, n in muestras.items() if n > 0}
    sin_modelo = sorted(con_muestras - set(modelos_ligas))
    con_modelo = set(modelos_ligas)
    if sin_modelo:
        print("INCOHERENCIA: ligas con muestras pero SIN modelo entrenado:")
        for l in sin_modelo:
            print(f"   {l}  ({muestras[l]} muestras)")
    else:
        print("✔ Sin incoherencias: toda liga con muestras tiene su modelo.")

    fuera = sorted(con_modelo - set(por_liga))
    if fuera:
        print(f"\n(Entrenadas pero no en el feed de ahora: {len(fuera)} "
              f"ligas — normal, el feed cambia por horas.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
