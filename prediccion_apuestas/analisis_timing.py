# -*- coding: utf-8 -*-
"""
analisis_timing.py — ¿Se afina la línea DURANTE el descanso? ¿Importa CUÁNDO
se apuesta?

Con `cuotas_historial` (marca `en_descanso`) mide, para cada partido:

  * apertura del descanso = primera línea registrada en descanso;
  * cierre del descanso   = última línea registrada en descanso;
  * deriva = cierre − apertura  (¿se movió aunque no se estaba jugando?).

Y para las apuestas nuevas (la nota guarda "+Xs" = retraso desde la primera
vista del descanso): CLV medio por tramo de retraso — si las apuestas tardías
valen menos, la casa está re-afinando la línea y conviene apretar
`auto.max_retraso_seg`.

Uso:
    python analisis_timing.py
"""
from __future__ import annotations

import re
from collections import defaultdict

import base_datos
import clv

_RE_RETRASO = re.compile(r"\+(\d+)s")


def main() -> int:
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT evento_id, partido FROM cuotas_historial
                            WHERE en_descanso = true
                            GROUP BY evento_id, partido""")
            eventos = cur.fetchall()
            detalle = []
            for ev, partido in eventos:
                cur.execute(
                    """SELECT linea, momento, en_descanso FROM cuotas_historial
                        WHERE evento_id = %s AND en_descanso = true
                          AND linea IS NOT NULL
                        ORDER BY id""", (ev,))
                filas = cur.fetchall()
                if not filas:
                    continue
                ap, ac = filas[0], filas[-1]
                deriva = (ac[0] - ap[0]) if len(filas) > 1 else 0.0
                segundos = (ac[1] - ap[1]).total_seconds() if len(filas) > 1 else 0.0
                detalle.append((ev, partido, ap[0], ac[0], deriva, segundos,
                                len(filas)))
    finally:
        conn.close()

    print("=== LÍNEA DURANTE EL DESCANSO 2Q→3Q ===")
    if not detalle:
        print("Aún no hay descansos registrados con la marca nueva "
              "(se llena en vivo, en cada descanso).")
    else:
        congeladas = sum(1 for d in detalle if not d[4])
        print(f"Descansos registrados: {len(detalle)}")
        print(f"  línea CONGELADA todo el descanso: {congeladas} "
              f"({congeladas / len(detalle) * 100:.0f} %)")
        movidas = [d for d in detalle if d[4]]
        if movidas:
            media = sum(abs(d[4]) for d in movidas) / len(movidas)
            print(f"  con movimiento: {len(movidas)} · deriva media "
                  f"{media:.2f} pts")
        print("\n  Detalle por partido (los de mayor movimiento):")
        for ev, partido, ap, ac, der, seg, n in sorted(
                detalle, key=lambda x: -abs(x[4]))[:12]:
            print(f"   {partido[:44]:44s} abre={ap:6.1f} cierra={ac:6.1f} "
                  f"deriva={der:+5.1f} pts en {seg:5.0f}s ({n} obs)")

    print("\n=== CLV POR RETRASO DE LA APUESTA ===")
    tramos: dict[str, list[float]] = defaultdict(list)
    sin_dato = 0
    for a in base_datos.listar_apuestas():
        cierre = clv._cierre(a.evento_id, a.fecha)
        if cierre is None:
            continue
        signo = 1.0 if a.seleccion == "OVER" else -1.0
        valor = (float(cierre[0]) - a.linea) * signo
        m = _RE_RETRASO.search(a.nota or "")
        if not m:
            sin_dato += 1
            continue
        seg = int(m.group(1))
        clave = "<30s" if seg < 30 else ("30-120s" if seg <= 120 else ">120s")
        tramos[clave].append(valor)

    if not tramos:
        print("Aún sin apuestas con retraso medido y cierre registrado.")
        if sin_dato:
            print(f"(apuestas anteriores a la instrumentación: {sin_dato})")
    else:
        cab = f"{'retraso':10s} {'n':>4s} {'CLV medio':>9s}"
        print(cab)
        print("-" * len(cab))
        for clave in ("<30s", "30-120s", ">120s"):
            vals = tramos.get(clave)
            if vals:
                print(f"{clave:10s} {len(vals):4d} {sum(vals) / len(vals):+9.2f}")

    print("\n(Lo que se busca: si '<30s' rinde mejor que '>120s', conviene")
    print(" apostar solo con la primera línea del descanso → "
          "auto.max_retraso_seg en config.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
