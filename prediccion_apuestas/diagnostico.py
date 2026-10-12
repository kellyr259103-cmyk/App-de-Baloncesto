# -*- coding: utf-8 -*-
"""
diagnostico.py — ¿Por qué pierde la estrategia? Diagnóstico con datos REALES.

Usa las apuestas resueltas (con total oficial verificado) y compara, sobre
los MISMOS partidos:

    error del MODELO    = predicción − total final
    error de la CASA    = línea      − total final
    error del NAIVE     = 2·(1Q+2Q)  − total final   (la proyección simple)

Si la casa tiene menos error que el modelo, el Δ con el que se apuesta es
ruido alrededor de un estimador mejor → el juego es 50/50 menos el margen.
También descompone el resultado: modelo propio vs respaldo, duplicados, y
winrate por tamaño de |Δ|.

Uso:
    python diagnostico.py
"""
from __future__ import annotations

import re
from collections import Counter

import base_datos
import paperbet

_RE_Q = re.compile(r"1Q=(\d+)\s+2Q=(\d+)")


def main() -> int:
    apuestas = [a for a in base_datos.listar_apuestas()
                if a.estado != paperbet.ESTADO_PENDIENTE and a.total_final is not None]
    if not apuestas:
        print("No hay apuestas resueltas todavía.")
        return 1

    err_modelo, err_casa, err_naive, err_naive_n = [], [], [], 0
    g = p = 0
    buckets: dict[str, list[int]] = {}
    por_modelo: dict[str, list[float]] = {}
    duplicados: Counter = Counter()

    print(f"{'id':>4} {'liga':26s} {'sel':6s} {'línea':>6s} {'modelo':>7s} "
          f"{'final':>6s} {'e.mod':>6s} {'e.casa':>6s} {'e.naive':>7s}  res")
    print("-" * 100)
    for a in sorted(apuestas, key=lambda x: x.id or 0):
        final = float(a.total_final)
        e_mod = (a.prediccion or 0) - final
        e_casa = a.linea - final
        err_modelo.append(abs(e_mod))
        err_casa.append(abs(e_casa))
        q = _RE_Q.search(a.nota or "")
        e_naive = None
        if q:
            naive = 2.0 * (int(q.group(1)) + int(q.group(2)))
            e_naive = abs(naive - final)
            err_naive.append(e_naive)
            err_naive_n += 1

        decidida = a.estado in (paperbet.ESTADO_GANADA, paperbet.ESTADO_PERDIDA)
        if a.estado == paperbet.ESTADO_GANADA:
            g += 1
        elif a.estado == paperbet.ESTADO_PERDIDA:
            p += 1
        res = {"GANADA": "G", "PERDIDA": "P", "ANULADA": "—"}[a.estado]

        delta = a.diferencia or 0.0
        clave_b = ("2-3" if abs(delta) < 3 else
                   "3-5" if abs(delta) < 5 else ">5")
        if decidida:
            buckets.setdefault(clave_b, []).append(1 if a.estado == paperbet.ESTADO_GANADA else 0)

        tipo = "propio" if "RESPALDO" not in (a.nota or "") else "respaldo"
        por_modelo.setdefault(tipo, []).append(a.beneficio or 0.0)
        duplicados[a.evento_id] += 1

        print(f"{a.id:>4} {a.liga_modelo:26s} {a.seleccion:6s} {a.linea:6.1f} "
              f"{(a.prediccion or 0):7.1f} {final:6.0f} {e_mod:+6.1f} "
              f"{e_casa:+6.1f} "
              f"{'' if e_naive is None else f'{e_naive:+7.1f}'}  {res}")

    n = len(err_modelo)
    print("-" * 100)
    print(f"\nApuestas resueltas: {n}  ·  G={g}  P={p}  "
          f"winrate={g / (g + p) * 100:.1f}%" if g + p else "")

    print("\n=== ERROR MEDIO ABSOLUTO EN LOS MISMOS PARTIDOS (pts) ===")
    print(f"  modelo : {sum(err_modelo) / n:5.2f}")
    print(f"  casa   : {sum(err_casa) / n:5.2f}")
    if err_naive_n:
        print(f"  naive  : {sum(err_naive) / err_naive_n:5.2f}  "
              f"({err_naive_n} partidos)")

    print("\n=== WINRATE POR TAMAÑO DE |Δ| (las decididas) ===")
    for k in ("2-3", "3-5", ">5"):
        v = buckets.get(k)
        if v:
            print(f"  |Δ| {k:4s}: n={len(v):2d}  winrate={sum(v) / len(v) * 100:.0f}%")

    print("\n=== DESCOMPOSICIÓN DEL DINERO ===")
    for tipo, vals in por_modelo.items():
        print(f"  {tipo:8s}: n={len(vals):2d}  beneficio={sum(vals):+,.0f}")
    extra = [(ev, c) for ev, c in duplicados.items() if c > 1]
    if extra:
        extra_n = sum(c - 1 for _ev, c in extra)
        extra_benef = 0.0
        for a in apuestas:
            if duplicados[a.evento_id] > 1 and a.id not in []:
                pass
        benef_dup = sum(a.beneficio or 0.0 for a in apuestas
                        if duplicados[a.evento_id] > 1)
        print(f"  duplicados: {len(extra)} partidos apostados 2+ veces "
              f"({extra_n} apuestas extra) · su beneficio total={benef_dup:+,.0f}")
        for ev, c in extra:
            nombres = {a.partido for a in apuestas if a.evento_id == ev}
            print(f"     evento {ev} ×{c}: {list(nombres)[0][:50]}")

    total = sum(a.beneficio or 0.0 for a in apuestas)
    stake = sum(a.stake for a in apuestas if a.estado in
                (paperbet.ESTADO_GANADA, paperbet.ESTADO_PERDIDA))
    print(f"\n  TOTAL resueltas: beneficio={total:+,.0f}  "
          f"(sobre stake decidido {stake:,.0f}; ROI {total / stake * 100:+.1f}%)")

    print("\n=== LECTURA ===")
    media_m = sum(err_modelo) / n
    media_c = sum(err_casa) / n
    if media_c < media_m:
        print("La LÍNEA DE LA CASA predice el final mejor que el modelo sobre")
        print("estos mismos partidos: apostar contra ella con un Δ pequeño es")
        print("jugársela a ruido de estimación → ≈50% menos el margen ≈ perder.")
    else:
        print("El modelo predice mejor que la línea en esta muestra, pero el")
        print("margen de la cuota exige acertar bastante más del 50%: revisa el")
        print("tamaño de muestra (n pequeño) antes de concluir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
