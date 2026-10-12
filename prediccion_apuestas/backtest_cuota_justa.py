# -*- coding: utf-8 -*-
"""
backtest_cuota_justa.py — Backtest de la estrategia del descanso con ROI en
DINERO usando la CUOTA JUSTA interna (1/P), para cuando no hay histórico de
cuotas reales de la casa.

ESTRATEGIA (la misma del bot):
    En el descanso 2Q→3Q (1Q y 2Q finales) predicir el total con el modelo
    MCO de la liga y apostar OVER/UNDER si |predicción − línea| ≥ umbral.

CUOTA JUSTA (1/P)
    P(lado) sale del propio modelo: P(over) = Φ(Δ / RMSE) como en la app
    (motor.predecir), y la cuota justa del lado elegido es 1/P. La tabla
    enseña la MEDIA de esas cuotas justas por liga y el ROI calculado como
    winrate × cuota_justa_media − 1.

LÍNEA (proxy del mercado)
    Igual que backtest.py: (1Q + 2Q) × 2, la proyección de ritmo que ya
    conoce una casa real en el descanso (test honesto).

WALK-FORWARD
    El modelo de cada partido se entrena SOLO con partidos de días
    anteriores (sin mirar el futuro), reentrenando cada SALTO días.

Uso:
    python backtest_cuota_justa.py                      # septiembre 2026
    python backtest_cuota_justa.py --desde 2026-09-01 --hasta 2026-09-30
    python backtest_cuota_justa.py --umbral 2 --stake 1000
"""

from __future__ import annotations

import argparse
from datetime import date

import backtest
import motor

STAKE_POR_DEFECTO = 1000.0


def backtest_liga(partidos: list[tuple], desde: date, hasta: date,
                  umbral: float) -> list[dict]:
    """
    Walk-forward de una liga guardando CADA apuesta con su cuota justa.

    Devuelve [{fecha, lado, cuota, marca, delta, pred, linea, total}, ...]
    donde marca = +1 ganada, −1 perdida, 0 push (total = línea).
    """
    filas: list[dict] = []
    modelo = None
    dias_desde_reentreno = 0
    fecha_entreno = None

    for fecha, q1, q2, total in partidos:
        if fecha_entreno is None:
            fecha_entreno = fecha
        while fecha_entreno < fecha:
            fecha_entreno = backtest._siguiente_dia(fecha_entreno, partidos)
            dias_desde_reentreno += 1

        if not (desde <= fecha <= hasta):
            continue

        linea = (q1 + q2) * 2.0
        if modelo is None or dias_desde_reentreno >= backtest.SALTO_REENTRENO:
            datos = [(a, b, c) for f, a, b, c in partidos if f < fecha]
            try:
                modelo = motor._ajustar(datos, "backtest", "walk-forward")
            except Exception:  # noqa: BLE001 (datos previos insuficientes)
                modelo = None
            dias_desde_reentreno = 0
            if modelo is not None and (modelo.errores or modelo.n == 0):
                modelo = None
        if modelo is None:
            continue

        pred = modelo.predecir_dos(q1, q2)
        delta = pred - linea
        if abs(delta) < umbral:
            continue

        res = motor.predecir(modelo, q1, q2, linea)
        if res.prob_over is None:
            continue
        prob_lado = res.prob_over if delta > 0 else 1.0 - res.prob_over
        if not 0.0 < prob_lado < 1.0:
            continue
        cuota = 1.0 / prob_lado

        if total == linea:
            marca = 0
        elif (delta > 0) == (total > linea):
            marca = 1
        else:
            marca = -1

        filas.append({"fecha": fecha, "lado": "OVER" if delta > 0 else "UNDER",
                      "cuota": cuota, "marca": marca, "delta": delta,
                      "pred": pred, "linea": linea, "total": total})
    return filas


def resumen(filas: list[dict], stake: float) -> dict | None:
    """Métricas de una liga: ops, winrate, cuota justa media, ROI y dinero."""
    if not filas:
        return None
    ganadas = sum(1 for f in filas if f["marca"] > 0)
    perdidas = sum(1 for f in filas if f["marca"] < 0)
    push = sum(1 for f in filas if f["marca"] == 0)
    decididas = ganadas + perdidas
    winrate = ganadas / decididas if decididas else 0.0
    cuota_media = sum(f["cuota"] for f in filas) / len(filas)
    dinero = stake * (ganadas * (cuota_media - 1.0) - perdidas)
    roi = dinero / (len(filas) * stake) if filas else 0.0
    return {"ops": len(filas), "ganadas": ganadas, "perdidas": perdidas,
            "push": push, "winrate": winrate, "cuota_media": cuota_media,
            "roi": roi, "dinero": dinero}


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest por mes con cuota justa "
                                            "interna (1/P) y dinero por stake.")
    p.add_argument("--desde", default="2026-09-01",
                   help="fecha inicial de evaluación (por defecto 2026-09-01)")
    p.add_argument("--hasta", default="2026-09-30",
                   help="fecha final de evaluación (por defecto 2026-09-30)")
    p.add_argument("--umbral", type=float, default=2.0,
                   help="Δ mínimo en puntos para apostar (por defecto 2.0)")
    p.add_argument("--stake", type=float, default=STAKE_POR_DEFECTO,
                   help="dinero por operación en COP (por defecto 1000)")
    args = p.parse_args()

    desde = date.fromisoformat(args.desde)
    hasta = date.fromisoformat(args.hasta)

    historico = backtest._cargar_historico()
    resultados: dict[str, dict] = {}
    for liga, partidos in sorted(historico.items()):
        filas = backtest_liga(partidos, desde, hasta, args.umbral)
        r = resumen(filas, args.stake)
        if r:
            resultados[liga] = r

    if not resultados:
        print("No hay apuestas evaluables en ese rango.")
        return 1

    print(f"Backtest WALK-FORWARD  ·  {desde} → {hasta}  ·  "
          f"umbral Δ ≥ {args.umbral} pts  ·  stake {args.stake:,.0f} COP")
    print("Línea proxy = (1Q+2Q)×2  ·  cuota = CUOTA JUSTA interna (1/P, "
          "P = Φ(Δ/RMSE) del modelo)")
    print("ROI = [ganadas × (cuota justa media − 1) − perdidas] ÷ operaciones "
          "(la cuota real de una casa sería menor)\n")

    cab = (f"{'liga':30s} {'ops':>4s} {'gan':>4s} {'perd':>5s} {'push':>4s} "
           f"{'WINRATE':>8s} {'cuota justa':>11s} {'ROI':>8s} {'dinero':>12s}")
    print(cab)
    print("-" * len(cab))
    tot = {"ops": 0, "ganadas": 0, "perdidas": 0, "push": 0, "dinero": 0.0}
    suma_cuotas = 0.0
    for liga, r in sorted(resultados.items(), key=lambda kv: -kv[1]["roi"]):
        tot["ops"] += r["ops"]
        tot["ganadas"] += r["ganadas"]
        tot["perdidas"] += r["perdidas"]
        tot["push"] += r["push"]
        tot["dinero"] += r["dinero"]
        suma_cuotas += r["cuota_media"] * r["ops"]
        print(f"{liga:30s} {r['ops']:4d} {r['ganadas']:4d} {r['perdidas']:5d} "
              f"{r['push']:4d} {r['winrate'] * 100:7.1f}% "
              f"{r['cuota_media']:11.3f} {r['roi'] * 100:+7.1f}% "
              f"{r['dinero']:12,.0f}")

    decididas = tot["ganadas"] + tot["perdidas"]
    winrate = tot["ganadas"] / decididas if decididas else 0.0
    cuota_media = suma_cuotas / tot["ops"] if tot["ops"] else 0.0
    roi = tot["dinero"] / (tot["ops"] * args.stake) if tot["ops"] else 0.0
    print("-" * len(cab))
    print(f"{'TOTAL':30s} {tot['ops']:4d} {tot['ganadas']:4d} "
          f"{tot['perdidas']:5d} {tot['push']:4d} {winrate * 100:7.1f}% "
          f"{cuota_media:11.3f} {roi * 100:+7.1f}% {tot['dinero']:12,.0f}")
    print(f"\nstake {args.stake:,.0f} COP por operación · "
          f"decididas {decididas} (los push van a 0) · "
          f"beneficio total {tot['dinero']:+,.0f} COP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
