# -*- coding: utf-8 -*-
"""
verificar_metodos.py — ¿Es la regresión lineal el mejor método para ESTA
información? Torneo walk-forward (solo con el pasado) sobre las 10 ligas con
más partidos de 2026:

  A) OLS [1, q1, q2]              ← el modelo actual (LINEST)
  B) OLS cuadrático [1,q1,q2,q1²,q2²,q1·q2]   (no-linealidad explícita)
  C) LightGBM [q1, q2]            (gradient boosting: captura lo que sea)
  D) LightGBM + ritmo de equipos  (la info extra que decíamos)

Métrica: RMSE fuera de muestra (menos = mejor). Nota: en D las features de
ritmo se calculan con los partidos anteriores a cada reentreno (sin mirar el
futuro del partido evaluado; el ritmo del equipo puede incluir partidos ya
pasados de la ventana de entrenamiento, aproximación estándar).

Uso:
    python verificar_metodos.py
"""
from __future__ import annotations

from datetime import date

import lightgbm as lgb
import numpy as np

from backtest_mejoras import _cargar, _siguiente, K_PACE

SALTO = 30          # reentreno cada 30 días (misma señal que 10, más rápido)
MIN_PREV = 120      # partidos previos mínimos para entrenar
LIGAS = ("nba", "japan/b1__league", "china/cba_preseason", "wnba",
         "germany/bbl", "argentina/lnb", "euroleague", "italy/serie_a2",
         "chile/liga_nacional", "japan/b2_league")


def _lgbm() -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(n_estimators=250, learning_rate=0.05,
                             num_leaves=15, min_child_samples=20,
                             subsample=0.9, colsample_bytree=1.0,
                             verbose=-1, random_state=7)


def _matriz(rows, ritmo, media):
    """Filas de features D (q1, q2, ritmo local, ritmo visitante, media)."""
    X = []
    for q1, q2, l2, v2 in rows:
        pl = ritmo.get(l2, media)
        pv = ritmo.get(v2, media)
        X.append([q1, q2, pl, pv, media])
    return np.asarray(X) if X else np.empty((0, 5))


def main() -> int:
    desde, hasta = date(2026, 1, 1), date(2026, 12, 31)
    hist = _cargar()
    tot = {"A": [0.0, 0], "B": [0.0, 0], "C": [0.0, 0], "D": [0.0, 0]}

    print(f"Torneo de MÉTODOS  ·  eval {desde} → {hasta}  ·  refit cada "
          f"{SALTO} días  ·  {len(LIGAS)} ligas grandes\n")
    print(f"{'liga':30s} {'n':>5s} {'A: OLS':>8s} {'B: OLS²':>8s} "
          f"{'C: LGBM':>8s} {'D: LGBM+eq':>10s}")
    print("-" * 76)

    for liga in LIGAS:
        partidos = hist.get(liga)
        if not partidos:
            continue
        # estado incremental
        suma_total = n_total = 0.0
        dias = 0
        fecha_ent = None
        ritmo: dict[str, float] = {}
        media = 0.0
        coefA = coefB = None
        gbmC: lgb.LGBMRegressor | None = None
        gbmD: lgb.LGBMRegressor | None = None
        ss = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}
        n = 0

        for fecha, q1, q2, total, local, visit in partidos:
            if fecha_ent is None:
                fecha_ent = fecha
            while fecha_ent < fecha:
                for f2, _a, _b, t2, _l, _v in partidos:
                    if f2 == fecha_ent:
                        suma_total += t2
                        n_total += 1
                fecha_ent = _siguiente(fecha_ent, partidos)
                dias += 1

            evaluar = (desde <= fecha <= hasta) and local and visit
            reentrenar = coefA is None or dias >= SALTO
            if not (evaluar or reentrenar):
                continue

            if reentrenar:
                previos = [(a1, a2, t2, l2, v2)
                           for f2, a1, a2, t2, l2, v2 in partidos if f2 < fecha]
                if len(previos) < MIN_PREV:
                    continue
                media = suma_total / n_total if n_total else 0.0
                acum: dict[str, list] = {}
                for _a1, _a2, t2, l2, v2 in previos:
                    for nombre in (l2, v2):
                        if nombre:
                            reg = acum.setdefault(nombre, [0.0, 0.0])
                            reg[0] += t2
                            reg[1] += 1
                ritmo = {k: (s + K_PACE * media) / (c + K_PACE)
                         for k, (s, c) in acum.items()}

                q1v = np.array([p[0] for p in previos])
                q2v = np.array([p[1] for p in previos])
                y = np.array([p[2] for p in previos])
                XA = np.column_stack([np.ones_like(q1v), q1v, q2v])
                XB = np.column_stack([XA, q1v ** 2, q2v ** 2, q1v * q2v])
                coefA = np.linalg.lstsq(XA, y, rcond=None)[0]
                coefB = np.linalg.lstsq(XB, y, rcond=None)[0]
                XC = np.column_stack([q1v, q2v])
                gbmC = _lgbm().fit(XC, y)
                XD = _matriz([(a1, a2, l2, v2) for a1, a2, _t, l2, v2
                              in previos], ritmo, media)
                gbmD = _lgbm().fit(XD, y) if len(XD) else None
                dias = 0

            if not evaluar or coefA is None:
                continue

            x1 = np.array([1.0, q1, q2])
            predA = float(x1 @ coefA)
            predB = float(np.concatenate([x1, [q1 * q1, q2 * q2, q1 * q2]])
                          @ coefB)
            predC = float(gbmC.predict(np.array([[q1, q2]]))[0])
            predD = None
            if gbmD is not None:
                predD = float(gbmD.predict(
                    _matriz([(q1, q2, local, visit)], ritmo, media))[0])

            n += 1
            ss["A"] += (predA - total) ** 2
            ss["B"] += (predB - total) ** 2
            ss["C"] += (predC - total) ** 2
            if predD is not None:
                ss["D"] += (predD - total) ** 2

        if n:
            for k in ("A", "B", "C", "D"):
                tot[k][0] += ss[k]
                tot[k][1] += n
            ra = (ss["A"] / n) ** 0.5
            rb = (ss["B"] / n) ** 0.5
            rc = (ss["C"] / n) ** 0.5
            rd = (ss["D"] / n) ** 0.5
            print(f"{liga:30s} {n:5d} {ra:8.2f} {rb:8.2f} {rc:8.2f} "
                  f"{rd:10.2f}")

    print("-" * 76)
    ra = (tot["A"][0] / tot["A"][1]) ** 0.5
    rb = (tot["B"][0] / tot["B"][1]) ** 0.5
    rc = (tot["C"][0] / tot["C"][1]) ** 0.5
    rd = (tot["D"][0] / tot["D"][1]) ** 0.5
    print(f"{'TOTAL':30s} {tot['A'][1]:5d} {ra:8.2f} {rb:8.2f} {rc:8.2f} "
          f"{rd:10.2f}")
    print("\nLectura: si B/C/D no bajan el RMSE de A de forma clara, el método")
    print("(lineal) NO es el cuello de botella: lo es la información disponible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
