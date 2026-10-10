# -*- coding: utf-8 -*-
"""
verificar.py — Comprueba que Python reproduce EXACTAMENTE los resultados del Excel.

Compara las predicciones de este programa contra los valores que dejó calculados
Microsoft Excel en el propio archivo (celdas de la hoja PREDICCION).

Ejecutar:  python verificar.py
"""

from __future__ import annotations

import sys

import motor

RUTA = motor.EXCEL_POR_DEFECTO
TOL = 1e-6   # tolerancia en puntos (los decimales que muestra Excel)

# --- Valores cacheados por Excel en "Predicciones en vivo basque.xlsx" ------ #
CASOS = [
    # (liga, 1Q, 2Q, línea, descripción, valor esperado, clave)
    ("EUROLIGA", 50, 39, 167, "B8  PREDICCION TOTAL", 176.44717339025956, "pred"),
    ("EUROLIGA", 50, 39, 167, "B9  Pronostico solo con 1Q", 182.13526155457791, "solo"),
    ("EUROLIGA", 50, 39, 167, "B10 Referencia naive (1Q x 4)", 200.0, "naive"),
    ("EUROLIGA", 50, 39, 167, "B11 Pronostico menos linea", 9.4471733902595645, "dif"),
    ("EUROLIGA", 50, 39, 167, "B12 VEREDICTO", "OVER", "veredicto"),
    ("EUROLIGA", 50, 39, 167, "B14 R2 modelo 1Q + 2Q", 0.52024583098105215, "r2_dos"),
    ("EUROLIGA", 50, 39, 167, "B15 R2 modelo solo 1Q", 0.33185777256038174, "r2_uno"),
    ("EUROLIGA", 50, 39, 167, "B19 Partidos en el modelo", 402, "n"),
    ("EUROLIGA", 50, 39, 167, "B20 Promedio total de la liga", 171.99502487562188, "media"),

    ("NBA", 41, 59, 214, "F8  PREDICCION TOTAL", 211.73519317224776, "pred"),
    ("NBA", 41, 59, 214, "F9  Pronostico solo con 1Q", 208.02642920674128, "solo"),
    ("NBA", 41, 59, 214, "F10 Referencia naive (1Q x 4)", 164.0, "naive"),
    ("NBA", 41, 59, 214, "F11 Pronostico menos linea", -2.2648068277522384, "dif"),
    ("NBA", 41, 59, 214, "F12 VEREDICTO", "UNDER", "veredicto"),
    ("NBA", 41, 59, 214, "F14 R2 modelo 1Q + 2Q", 0.47838370442655498, "r2_dos"),
    ("NBA", 41, 59, 214, "F15 R2 modelo solo 1Q", 0.28661621261006898, "r2_uno"),
    ("NBA", 41, 59, 214, "F19 Partidos en el modelo", 1380, "n"),
    ("NBA", 41, 59, 214, "F20 Promedio total de la liga", 230.19347826086957, "media"),
]


def obtener(res, m, clave: str):
    if clave == "media":
        return m.media_total
    if clave in ("r2_dos", "r2_uno", "n"):
        return getattr(m, clave)
    mapa = {"pred": "prediccion", "solo": "solo_1q", "dif": "diferencia"}
    if clave == "veredicto":
        return res.veredicto
    return getattr(res, mapa.get(clave, clave))


def main() -> int:
    print(f"Archivo de datos: {RUTA}\n")
    try:
        modelos = {liga: motor.cargar_modelo(RUTA, liga) for liga in ("EUROLIGA", "NBA")}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR al leer el Excel: {exc}")
        return 2

    fallos = 0
    actual = None
    for liga, q1, q2, linea, desc, esperado, clave in CASOS:
        if (liga, q1, q2) != actual:
            actual = (liga, q1, q2)
            print(f"\n--- {liga} | 1Q={q1}, 2Q={q2}, línea={linea} ---")
        m = modelos[liga]
        res = motor.predecir(m, float(q1), None if q2 is None else float(q2), float(linea))
        obtenido = obtener(res, m, clave)

        if isinstance(esperado, str):
            ok = obtenido == esperado
            delta = "-"
        else:
            delta = abs(float(obtenido) - float(esperado))
            ok = delta <= TOL

        fallos += 0 if ok else 1
        marca = "OK " if ok else "FALLO"
        if isinstance(esperado, str):
            print(f"  [{marca}] {desc:<32} excel={esperado:<10} python={obtenido}")
        else:
            print(f"  [{marca}] {desc:<32} excel={esperado!r:<24} python={obtenido!r:<24} dif={delta:.2e}")

    # --- casos de contorno (misma lógica condicional que el Excel) ---------- #
    print("\n--- Casos de contorno (lógica SI de la hoja) ---")
    m = modelos["EUROLIGA"]
    r = motor.predecir(m, 50, None, 167)          # sin 2Q -> usa FORECAST (B9)
    ok = abs(r.prediccion - 182.13526155457791) <= TOL and r.modelo_usado == "solo 1Q"
    fallos += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FALLO'}] 2Q vacío -> modelo solo 1Q       pred={r.prediccion!r}")

    r = motor.predecir(m, None, 39, 167)           # sin 1Q -> "faltan datos"
    ok = r.veredicto == "faltan datos" and r.prediccion is None
    fallos += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FALLO'}] 1Q vacío -> 'faltan datos'      veredicto={r.veredicto!r}")

    r = motor.predecir(m, 50, 39, None)            # sin línea -> "faltan datos"
    ok = r.veredicto == "faltan datos" and r.prediccion is not None
    fallos += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FALLO'}] línea vacía -> 'faltan datos'   veredicto={r.veredicto!r} "
          f"(pred={r.prediccion:.4f})")

    pred_igual = motor.predecir(m, 50, 39, None).prediccion
    r = motor.predecir(m, 50, 39, pred_igual)      # diferencia = 0 -> OVER (>= 0)
    ok = r.veredicto == "OVER" and r.diferencia == 0.0
    fallos += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FALLO'}] diferencia = 0 -> OVER          veredicto={r.veredicto!r}")

    r = motor.predecir(m, 50, 39, pred_igual + 0.1)  # línea 0.1 por encima -> UNDER
    ok = r.veredicto == "UNDER"
    fallos += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FALLO'}] línea 0.1 > pred -> UNDER       veredicto={r.veredicto!r}")

    print("\n" + "=" * 78)
    if fallos:
        print(f"RESULTADO: {fallos} comprobacion(es) FALLIDA(S).")
        return 1
    print(f"RESULTADO: TODAS las {len(CASOS) + 5} comprobaciones OK — Python = Excel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
