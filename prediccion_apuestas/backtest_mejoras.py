# -*- coding: utf-8 -*-
"""
backtest_mejoras.py — Torneo de modelos walk-forward, SIN mirar el futuro.

Lo que se puede medir honestamente con nuestros datos (24k partidos con
cuartos y NOMBRES de equipos): ¿mejora la predicción del total si al MCO
(Q1,Q2) se le añade INFORMACIÓN DE EQUIPO (ritmo esperado del cruce)?

Variantes (todas ajustadas SOLO con partidos de días anteriores):

    A) actual : total = b0 + b1·Q1 + b2·Q2
    B) equipo : A + b3·(ritmo_del_cruce − media_liga)      (4 parámetros)
    C) equipos: A + b3·ritmo_local + b4·ritmo_visitante    (5 parámetros)

`ritmo_equipo` = media del total final de los partidos ANTERIORES de ese
equipo, encogida hacia la media de la liga: (Σtot + k·media)/(n + k).

Métricas en la ventana de evaluación:
  * RMSE fuera de muestra (menos = mejor predictor);
  * winrate de la estrategia actual (A) contra la "línea AGUDA" (= la mejor
    predicción informada, B) a varios umbrales de |Δ|: mide el costo de
    ganarle a un oponente que SÍ conoce a los equipos (≈ lo que hace la casa).

Uso:
    python backtest_mejoras.py
    python backtest_mejoras.py --desde 2026-01-01 --minimo-previos 60
"""
from __future__ import annotations

import argparse
import math
from datetime import date

import base_datos

SALTO_REENTRENO = 10        # días de historia nuevos entre reentrenamientos
K_PACE = 5.0                # encogimiento del ritmo por equipo
UMBRALES = (0.5, 2.0, 3.0, 5.0)


def _ols(X: list[list[float]], y: list[float]) -> list[float] | None:
    """Mínimos cuadrados por ecuaciones normales (Gauss). None si singular."""
    n = len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(len(X))) for b in range(n)]
         for a in range(n)]
    v = [sum(X[i][a] * y[i] for i in range(len(X))) for a in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-9:
            return None
        A[col], A[piv] = A[piv], A[col]
        v[col], v[piv] = v[piv], v[col]
        for r in range(col + 1, n):
            f = A[r][col] / A[col][col]
            for c in range(col, n):
                A[r][c] -= f * A[col][c]
            v[r] -= f * v[col]
    b = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = v[r] - sum(A[r][c] * b[c] for c in range(r + 1, n))
        b[r] = s / A[r][r]
    return b


def _cargar() -> dict[str, list[tuple]]:
    """{liga: [(fecha, q1, q2, total, local, visitante), ...]}."""
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT liga, fecha, q1, q2, total, partido FROM muestras
                    WHERE fecha IS NOT NULL ORDER BY liga, fecha, id""")
            filas = cur.fetchall()
    finally:
        conn.close()
    por_liga: dict[str, list[tuple]] = {}
    for liga, fecha, q1, q2, total, partido in filas:
        partes = (partido or "").rsplit(" - ", 1)
        local = partes[0].strip() if len(partes) == 2 else ""
        visit = partes[1].strip() if len(partes) == 2 else ""
        por_liga.setdefault(liga, []).append(
            (fecha, float(q1), float(q2), float(total), local, visit))
    return por_liga


def _siguiente(fecha, partidos):
    for f, *_ in partidos:
        if f > fecha:
            return f
    return fecha


def backtest_liga(partidos, desde: date, hasta: date,
                  minimo_previos: int) -> dict | None:
    """Walk-forward de una liga: RMSE de A/B/C + winrate de A vs línea aguda."""
    suma_total = 0.0
    n_total = 0.0
    equipos: dict[str, float] = {}       # nombre -> ritmo encogido
    modeloA = modeloB = modeloC = None
    dias = 0
    fecha_ent = None
    n = nB = nC = 0
    eA = eB = eC = 0.0
    por_u = {u: [0, 0] for u in UMBRALES}

    for fecha, q1, q2, total, local, visit in partidos:
        if fecha_ent is None:
            fecha_ent = fecha
        while fecha_ent < fecha:                     # ese día pasa a "pasado"
            for f2, _a1, _a2, t2, _l, _v in partidos:
                if f2 == fecha_ent:
                    suma_total += t2
                    n_total += 1
            fecha_ent = _siguiente(fecha_ent, partidos)
            dias += 1

        evaluar = (desde <= fecha <= hasta) and bool(local) and bool(visit)
        reentrenar = modeloA is None or dias >= SALTO_REENTRENO
        if not (evaluar or reentrenar):
            continue

        if reentrenar:
            previos = [(a1, a2, t2, l2, v2)
                       for f2, a1, a2, t2, l2, v2 in partidos if f2 < fecha]
            if len(previos) < minimo_previos:
                continue
            media = suma_total / n_total if n_total else 0.0
            acum: dict[str, list] = {}
            for _a1, _a2, t2, l2, v2 in previos:
                for nombre in (l2, v2):
                    if nombre:
                        reg = acum.setdefault(nombre, [0.0, 0.0])
                        reg[0] += t2
                        reg[1] += 1
            equipos = {k: (s + K_PACE * media) / (c + K_PACE)
                       for k, (s, c) in acum.items()}
            y = [t2 for _a1, _a2, t2, _l, _v in previos]
            modeloA = _ols([[1.0, a1, a2] for a1, a2, _t, _l, _v in previos], y)
            modeloB = modeloC = None
            if modeloA is not None:
                XB, XC = [], []
                ok = True
                for a1, a2, _t, l2, v2 in previos:
                    pl, pv = equipos.get(l2), equipos.get(v2)
                    if pl is None or pv is None:
                        ok = False
                        break
                    XB.append([1.0, a1, a2, (pl + pv) / 2.0 - media])
                    XC.append([1.0, a1, a2, pl, pv])
                if ok:
                    modeloB = _ols(XB, y)
                    modeloC = _ols(XC, y)
            dias = 0

        if not evaluar or modeloA is None:
            continue

        pl, pv = equipos.get(local), equipos.get(visit)
        media = suma_total / n_total if n_total else 0.0
        predA = modeloA[0] + modeloA[1] * q1 + modeloA[2] * q2
        predB = predC = None
        if modeloB is not None and pl is not None and pv is not None and media:
            predB = (modeloB[0] + modeloB[1] * q1 + modeloB[2] * q2
                     + modeloB[3] * ((pl + pv) / 2.0 - media))
        if modeloC is not None and pl is not None and pv is not None:
            predC = (modeloC[0] + modeloC[1] * q1 + modeloC[2] * q2
                     + modeloC[3] * pl + modeloC[4] * pv)

        n += 1
        eA += (predA - total) ** 2
        if predB is not None:
            nB += 1
            eB += (predB - total) ** 2
        if predC is not None:
            nC += 1
            eC += (predC - total) ** 2

        if predB is not None and total != predB:
            delta = predA - predB
            gano = (total > predB) == (delta > 0)
            for u in UMBRALES:
                if abs(delta) >= u:
                    por_u[u][0] += 1
                    por_u[u][1] += gano

    if not n:
        return None
    return {"n": n, "eA": eA, "nB": nB, "eB": eB, "nC": nC, "eC": eC,
            "por_u": por_u}


def main() -> int:
    p = argparse.ArgumentParser(description="Torneo de modelos walk-forward.")
    p.add_argument("--desde", default="2026-01-01")
    p.add_argument("--hasta", default="2026-12-31")
    p.add_argument("--minimo-previos", type=int, default=60)
    args = p.parse_args()
    desde = date.fromisoformat(args.desde)
    hasta = date.fromisoformat(args.hasta)

    print(f"Torneo walk-forward  ·  eval {desde} → {hasta}  ·  "
          f"mín. previos {args.minimo_previos}\n")
    historico = _cargar()
    res: dict[str, dict] = {}
    for liga, partidos in sorted(historico.items()):
        r = backtest_liga(partidos, desde, hasta, args.minimo_previos)
        if r:
            res[liga] = r

    cab = (f"{'liga':30s} {'n':>5s} {'RMSE A':>7s} {'RMSE B':>7s} "
           f"{'RMSE C':>7s} {'mejora B':>9s}")
    print(cab)
    print("-" * len(cab))
    tot = {"n": 0, "eA": 0.0, "nB": 0, "eB": 0.0, "nC": 0, "eC": 0.0}
    for liga, r in sorted(res.items(), key=lambda kv: -kv[1]["n"]):
        for k in tot:
            tot[k] += r[k]
        ra = math.sqrt(r["eA"] / r["n"])
        rb = math.sqrt(r["eB"] / r["nB"]) if r["nB"] else float("nan")
        rc = math.sqrt(r["eC"] / r["nC"]) if r["nC"] else float("nan")
        print(f"{liga:30s} {r['n']:5d} {ra:7.2f} {rb:7.2f} {rc:7.2f} "
              f"{(ra - rb):+9.2f}")

    ra = math.sqrt(tot["eA"] / tot["n"])
    rb = math.sqrt(tot["eB"] / tot["nB"]) if tot["nB"] else float("nan")
    rc = math.sqrt(tot["eC"] / tot["nC"]) if tot["nC"] else float("nan")
    print("-" * len(cab))
    print(f"{'TOTAL':30s} {tot['n']:5d} {ra:7.2f} {rb:7.2f} {rc:7.2f} "
          f"{(ra - rb):+9.2f}")

    print("\n=== ESTRATEGIA ACTUAL (A) CONTRA LA LÍNEA AGUDA (B) ===")
    print("(oponente que conoce el ritmo de los equipos ≈ lo que hace la casa)")
    tot_u = {u: [0, 0] for u in UMBRALES}
    for r in res.values():
        for u in UMBRALES:
            tot_u[u][0] += r["por_u"][u][0]
            tot_u[u][1] += r["por_u"][u][1]
    for u in UMBRALES:
        nn, g = tot_u[u]
        if nn:
            print(f"  |Δ| ≥ {u:3.1f}: n={nn:5d}  winrate={g / nn * 100:5.1f}%")

    print("\n=== LECTURA ===")
    if rb < ra - 0.2:
        print(f"La información de equipo MEJORA la predicción "
              f"({ra:.2f} → {rb:.2f} pts de RMSE): es la vía a explorar.")
    else:
        print(f"La información de equipo apenas cambia la predicción "
              f"({ra:.2f} → {rb:.2f}). El Q1+Q2 ya la contiene casi toda.")
    print("El winrate contra la línea aguda mide el costo de jugar contra un")
    print("oponente informado: si ronda 50 %, no hay edge que apostar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
