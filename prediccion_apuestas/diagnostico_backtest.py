# -*- coding: utf-8 -*-
"""
diagnostico_backtest.py — ¿POR QUÉ el modelo gana tanto contra (1Q+2Q)×2?

Comprueba si hay reversión a la media entre mitades: si el 1º tiempo es muy
anotador, ¿el 2º lo es menos? Eso explicaría que "apostar al lado que el MCO
marca contra el doble ciego del primer tiempo" acierte mucho.

Imprime, por tramos del total del PRIMER TIEMPO (1Q+2Q):
  n, media de (1º tiempo), media de (2º tiempo), y % de partidos donde el
  2º tiempo anotó MÁS que el 1º (que es lo que decide la apuesta).
"""
from __future__ import annotations

import base_datos


def main() -> int:
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT q1, q2, q3, q4 FROM muestras
                   WHERE q1 IS NOT NULL AND q2 IS NOT NULL
                     AND q3 IS NOT NULL AND q4 IS NOT NULL""")
            filas = cur.fetchall()
    finally:
        conn.close()

    datos = [(a, b, c, d) for a, b, c, d in filas]
    print(f"partidos con los 4 cuartos: {len(datos)}\n")

    mitades = sorted((a + b, c + d) for a, b, c, d in datos)
    n = len(mitades)
    tramos = 6
    print(f"{'tramo 1º tiempo':>22s} {'n':>6s} {'1º mitad':>9s} {'2º mitad':>9s} "
          f"{'dif 2º-1º':>10s} {'% 2º>1º':>8s}")
    print("-" * 72)
    for t in range(tramos):
        ini, fin = t * n // tramos, (t + 1) * n // tramos
        grupo = mitades[ini:fin]
        m1 = sum(x for x, _ in grupo) / len(grupo)
        m2 = sum(y for _, y in grupo) / len(grupo)
        mas = sum(1 for x, y in grupo if y > x)
        print(f"{m1:16.1f} pts {len(grupo):6d} {m1:9.1f} {m2:9.1f} "
              f"{m2 - m1:+10.1f} {mas / len(grupo) * 100:7.1f}%")

    print("\nSi el % de '2º > 1º' se aleja de 50 % en los extremos, hay")
    print("reversión a la media: el MCO la explota y eso es el 'edge' real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
