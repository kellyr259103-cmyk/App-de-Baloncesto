# -*- coding: utf-8 -*-
"""
calibracion.py — ¿Son reales las probabilidades del modelo? (reliability)

El modelo traduce su ventaja en puntos a una probabilidad con Φ(Δ/RMSE).
Ese Φ suele ser DEMASIADO CONFIADO (la tasa real de acierto queda por debajo
de la prometida; con la cuota real eso se come cualquier ventaja). Este script
lo mide y busca el factor `k` que mejor calibra:

    P'(over) = Φ(Δ / (k · RMSE))

con k > 1 = "enfriar" las probabilidades (más prudentes). Se elige el k que
minimiza el Brier score sobre el walk-forward (modelo entrenado SOLO con
partidos de días anteriores, línea proxy (1Q+2Q)×2 como en backtest.py).

Uso:
    python calibracion.py                       # informe (2026 completo)
    python calibracion.py --desde 2026-01-01 --hasta 2026-12-31
    python calibracion.py --aplicar             # guarda el k en config.json
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date

import backtest
import base_datos
import motor


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def recolectar(desde: date, hasta: date) -> list[tuple[str, float, float]]:
    """
    Walk-forward: [(liga, z+ , y+), ...] por partido evaluado.

    El modelo siempre apuesta el LADO con ventaja, así que se calibra esa
    probabilidad: `z+ = |Δ|/RMSE` y `y+ = 1` si el lado elegido ganó contra
    la línea. P(lado) = Φ(z+) ∈ [0.5, 1].
    """
    historico = backtest._cargar_historico()
    puntos: list[tuple[str, float, float]] = []
    for liga, partidos in sorted(historico.items()):
        modelo = None
        dias = 0
        fecha_entreno = None
        for fecha, q1, q2, total in partidos:
            if fecha_entreno is None:
                fecha_entreno = fecha
            while fecha_entreno < fecha:
                fecha_entreno = backtest._siguiente_dia(fecha_entreno, partidos)
                dias += 1
            if not (desde <= fecha <= hasta):
                continue
            linea = (q1 + q2) * 2.0
            if modelo is None or dias >= backtest.SALTO_REENTRENO:
                datos = [(a, b, c) for f, a, b, c in partidos if f < fecha]
                try:
                    modelo = motor._ajustar(datos, "backtest", "walk-forward")
                except Exception:  # noqa: BLE001 (datos previos insuficientes)
                    modelo = None
                dias = 0
                if modelo is not None and (modelo.errores or modelo.n == 0):
                    modelo = None
            if modelo is None:
                continue
            sigma = modelo.rmse_test_dos or modelo.rmse_dos
            if not sigma:
                continue
            if total == linea:
                continue                    # push: no informa de nada
            pred = modelo.predecir_dos(q1, q2)
            delta = pred - linea
            lado_arriba = delta >= 0          # OVER si Δ ≥ 0
            gano = (total > linea) if lado_arriba else (total < linea)
            puntos.append((liga, abs(delta) / sigma, 1.0 if gano else 0.0))
    return puntos


def _brier(puntos: list[tuple[str, float, float]], k: float) -> float:
    if not puntos:
        return 0.0
    return sum((_phi(z / k) - y) ** 2 for _, z, y in puntos) / len(puntos)


def _ece(puntos: list[tuple[str, float, float]], k: float,
         bins: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9, 1.01)) -> float:
    """Error de calibración esperado (media |predicho − real| por bin)."""
    total = len(puntos)
    if not total:
        return 0.0
    ece = 0.0
    for a, b in zip(bins, bins[1:]):
        sel = [(z, y) for _, z, y in puntos if a <= _phi(z / k) < b]
        if not sel:
            continue
        p_med = sum(_phi(z / k) for z, _ in sel) / len(sel)
        y_med = sum(y for _, y in sel) / len(sel)
        ece += (len(sel) / total) * abs(p_med - y_med)
    return ece


def _mejor_k(puntos, minimo=0.7, maximo=3.5, paso=0.05) -> float:
    mejor, mejor_b = 1.0, _brier(puntos, 1.0)
    k = minimo
    while k <= maximo + 1e-9:
        b = _brier(puntos, k)
        if b < mejor_b:
            mejor, mejor_b = round(k, 2), b
        k += paso
    return mejor


def fiabilidad(puntos, k: float,
               bins=(0.5, 0.6, 0.7, 0.8, 0.9, 1.01)) -> list[tuple]:
    """[(rango, n, P media, frecuencia real), ...] para el informe."""
    filas = []
    for a, b in zip(bins, bins[1:]):
        sel = [(z, y) for _, z, y in puntos if a <= _phi(z / k) < b]
        if not sel:
            continue
        filas.append((f"{a:.2f}-{b:.2f}", len(sel),
                      sum(_phi(z / k) for z, _ in sel) / len(sel),
                      sum(y for _, y in sel) / len(sel)))
    return filas


def main() -> int:
    p = argparse.ArgumentParser(description="Calibración de las probabilidades "
                                            "del modelo (walk-forward).")
    p.add_argument("--desde", default="2026-01-01")
    p.add_argument("--hasta", default="2026-12-31")
    p.add_argument("--aplicar", action="store_true",
                   help="guarda el factor en config.json "
                        "(calibracion.factor_rmse)")
    args = p.parse_args()

    desde = date.fromisoformat(args.desde)
    hasta = date.fromisoformat(args.hasta)

    print(f"Walk-forward {desde} → {hasta}  ·  línea proxy (1Q+2Q)×2")
    print("Calculando puntos de calibración (modelo entrenado solo con el "
          "pasado)…\n")
    puntos = recolectar(desde, hasta)
    if not puntos:
        print("No hay partidos evaluables en ese rango.")
        return 1
    print(f"Partidos evaluados: {len(puntos)}\n")

    k_global = _mejor_k(puntos)
    b1, bk = _brier(puntos, 1.0), _brier(puntos, k_global)
    e1, ek = _ece(puntos, 1.0), _ece(puntos, k_global)

    print("=" * 78)
    print("GLOBAL")
    print("=" * 78)
    print(f"  factor k=1.00 (crudo): Brier {b1:.4f} · ECE {e1 * 100:.1f} pp")
    print(f"  factor k={k_global:.2f} (elegido): Brier {bk:.4f} · "
          f"ECE {ek * 100:.1f} pp")
    print("\n  Fiabilidad con el k elegido (bin → P media vs frecuencia real):")
    for rango, n, pm, fr in fiabilidad(puntos, k_global):
        aviso = "  ← demasiado optimista" if pm - fr > 0.04 else ""
        print(f"    {rango}: n={n:5d}  P={pm * 100:5.1f}%  real={fr * 100:5.1f}%"
              f"{aviso}")

    print("\n" + "=" * 78)
    print("POR LIGA (k propio; n mínimo 40 para sugerirlo)")
    print("=" * 78)
    por_liga: dict[str, list] = {}
    for liga, z, y in puntos:
        por_liga.setdefault(liga, []).append((liga, z, y))
    cab = f"{'liga':30s} {'n':>5s} {'Brier1':>7s} {'k*':>5s} {'ECE1':>6s} {'ECE*':>6s}"
    print(cab)
    print("-" * len(cab))
    for liga in sorted(por_liga, key=lambda l: -len(por_liga[l])):
        datos = por_liga[liga]
        k = _mejor_k(datos) if len(datos) >= 40 else k_global
        print(f"{liga:30s} {len(datos):5d} {_brier(datos, 1.0):7.4f} "
              f"{k:5.2f} {_ece(datos, 1.0) * 100:5.1f} "
              f"{_ece(datos, k) * 100:5.1f}")

    if args.aplicar:
        cfg = base_datos.cargar_config()
        cfg["calibracion"] = {"factor_rmse": k_global,
                              "medido_en": date.today().isoformat(),
                              "n": len(puntos)}
        base_datos.guardar_config(cfg)
        print(f"\nAplicado: config.json → calibracion.factor_rmse = {k_global:.2f}")
        print("(la app lo lee al arrancar; reiníciala si está abierta)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
