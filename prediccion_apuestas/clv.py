# -*- coding: utf-8 -*-
"""
clv.py — CLV (Closing Line Value): ¿ganamos a la línea de cierre?

El test más honesto de si una apuesta tiene EDGE no es ganarla, sino si la
línea se movió a nuestro favor después de apostarla: si compramos 160.5 y la
casa cierra el descanso en 158, ganamos 2.5 puntos de precio (CLV+) aunque el
partido luego caiga del otro lado. CLV positivo sostenido ⟺ edge real.

Fuente: `cuotas_historial` (cada movimiento de línea que ve la app). Para cada
apuesta se busca la ÚLTIMA línea registrada del mismo evento DESPUÉS de la
apuesta (y antes de 40 min, dentro del descanso):

    CLV (puntos) = (cierre − apuesta) × (+1 si OVER, −1 si UNDER)
    CLV positivo = cerramos mejor que la apuesta.

Uso:
    python clv.py            # resumen general + por liga
    python clv.py --detalle  # apuesta a apuesta
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import base_datos
import paperbet


def _cierre(evento_id: int, desde):
    """Última línea registrada del evento después de la apuesta (≤ 40 min)."""
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT linea, cuota_over, cuota_under, momento
                     FROM cuotas_historial
                    WHERE evento_id = %s AND momento > %s
                      AND momento <= %s + interval '40 minutes'
                      AND linea IS NOT NULL
                    ORDER BY id DESC LIMIT 1""",
                (evento_id, desde, desde))
            fila = cur.fetchone()
    finally:
        conn.close()
    return fila


def main() -> int:
    p = argparse.ArgumentParser(description="CLV de las apuestas paperbet.")
    p.add_argument("--detalle", action="store_true")
    args = p.parse_args()

    apuestas = base_datos.listar_apuestas()
    if not apuestas:
        print("No hay apuestas todavía.")
        return 1

    filas = []
    sin_datos = 0
    for a in apuestas:
        cierre = _cierre(a.evento_id, a.fecha)
        if cierre is None:
            sin_datos += 1
            continue
        linea_cierre, cu_over, cu_under, _mom = cierre
        signo = 1.0 if a.seleccion == "OVER" else -1.0
        clv = (float(linea_cierre) - a.linea) * signo
        filas.append((a, float(linea_cierre), clv))

    if not filas:
        print(f"Aún no hay movimientos de línea registrados después de ninguna "
              f"apuesta ({sin_datos} apuestas sin datos de cierre).\n"
              f"El histórico de líneas empezó el 11-10-2026: vuelve a correrlo "
              f"en unos días.")
        return 0

    if args.detalle:
        print(f"{'id':>4} {'partido':42s} {'sel':6s} {'apuesta':>8s} "
              f"{'cierre':>7s} {'CLV':>6s}")
        for a, cierre, clv in filas:
            print(f"{a.id:>4} {a.partido[:42]:42s} {a.seleccion:6s} "
                  f"{a.linea:8.1f} {cierre:7.1f} {clv:+6.1f}")

    por_liga: dict[str, list[float]] = defaultdict(list)
    for a, _c, clv in filas:
        por_liga[a.liga_modelo or "?"].append(clv)

    print(f"\n=== CLV POR LIGA ({len(filas)} apuestas con datos de cierre) ===")
    cab = f"{'liga':30s} {'n':>4s} {'CLV medio':>9s} {'CLV+':>6s}"
    print(cab)
    print("-" * len(cab))
    total_pos = total_n = 0
    for liga, vals in sorted(por_liga.items()):
        pos = sum(1 for v in vals if v > 0)
        total_pos += pos
        total_n += len(vals)
        print(f"{liga:30s} {len(vals):4d} {sum(vals) / len(vals):+9.2f} "
              f"{pos / len(vals) * 100:5.0f}%")
    print("-" * len(cab))
    todos = [v for vals in por_liga.values() for v in vals]
    print(f"{'TOTAL':30s} {len(todos):4d} {sum(todos) / len(todos):+9.2f} "
          f"{total_pos / total_n * 100:5.0f}%")
    print(f"\n(apuestas sin movimiento de línea registrado: {sin_datos})")
    print("CLV medio positivo y estable = edge real contra el mercado;")
    print("≈0 o negativo = estamos comprando a precio justo o peor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
