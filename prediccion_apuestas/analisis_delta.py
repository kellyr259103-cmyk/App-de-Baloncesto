# -*- coding: utf-8 -*-
"""
analisis_delta.py — ¿cómo se comporta el winrate según el tamaño de Δ?

La pregunta típica es "si solo apostamos con Δ mayor de X, ¿ganamos más?".
Se responde desde DOS fuentes, porque dicen cosas distintas:

  · `apuestas`   → lo que el bot ha apostado de VERDAD contra la casa. Poco n,
                   pero es la única verdad que no se puede discutir.
  · `backtest`   → walk-forward sobre el histórico (python backtest.py
                   --umbrales …). Mucho n, pero con la línea proxy
                   (1Q+2Q)×2 y cuota fija 1.85: sirve para ver la FORMA de la
                   curva, no el beneficio esperado.

Ambas se impriman juntas a propósito: el error clásico es quedarse con la
segunda porque tiene números grandes.

Uso:
    python analisis_delta.py
    python analisis_delta.py --stake 5000
"""
from __future__ import annotations

import argparse
import math

import base_datos

# bandas por defecto: cubren los cinco escenarios típicos
BANDAS = ((0.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 8.0), (8.0, None))


def stake_por_defecto() -> float:
    try:
        return float(base_datos.cargar_config().get("stake", 10000))
    except Exception:                     # noqa: BLE001 (si no hay config)
        return 10000.0


def _leer_apuestas() -> list[dict]:
    """Apuestas reales del bot, con su Δ y cómo acabaron."""
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT evento_id, partido, liga_apuesta, seleccion, linea,
                       cuota, stake, prediccion, diferencia, estado,
                       total_final
                  FROM apuestas
                 WHERE estado IN ('GANADA', 'PERDIDA', 'ANULADA')
                 ORDER BY diferencia""")
            filas = []
            for (ev, partido, liga, sel, linea, cuota, stake, pred, dif,
                 estado, total) in cur.fetchall():
                if not dif or not cuota:
                    continue
                filas.append(dict(ev=ev, partido=partido, liga=liga,
                                  sel=sel or "", linea=linea, cuota=float(cuota),
                                  stake=float(stake or 10000),
                                  pred=pred, dif=float(dif), estado=estado,
                                  total=total))
            return filas
    finally:
        conn.close()


def _puntuar(filas: list[dict]) -> dict:
    gan = per = anu = 0
    benef = 0.0
    for f in filas:
        st = f["stake"]
        if f["estado"] == "GANADA":
            gan += 1
            benef += st * (f["cuota"] - 1.0)
        elif f["estado"] == "PERDIDA":
            per += 1
            benef -= st
        else:
            anu += 1
    dec = gan + per
    wr = 100.0 * gan / dec if dec else 0.0
    # intervalo 95 %: con poco n el winrate baila, y hay que decirlo
    ic = (1.96 * math.sqrt((wr / 100.0) * (1.0 - wr / 100.0) / dec) * 100.0
          if dec else 0.0)
    ap = sum(f["stake"] for f in filas if f["estado"] != "ANULADA")
    cuota = (sum(f["cuota"] for f in filas if f["estado"] != "ANULADA") / dec
             if dec else 0.0)
    return dict(n=len(filas), dec=dec, gan=gan, per=per, anu=anu, wr=wr, ic=ic,
                benef=benef, roi=(100.0 * benef / ap) if ap else 0.0,
                cuota=cuota, equilibrio=(100.0 / cuota) if cuota > 1 else 0.0)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stake", type=float, default=None,
                   help="stake por apuesta (por defecto el de config.json)")
    args = p.parse_args()
    stake = args.stake or stake_por_defecto()

    filas = _leer_apuestas()
    print("=" * 104)
    print("WINRATE SEGÚN EL TAMAÑO DE Δ  ·  apuestas REALES del bot")
    print("=" * 104)
    if not filas:
        print("   Aún no hay apuestas decididas.")
        return 0

    r = _puntuar(filas)
    print(f"   apuestas decididas: {r['n']}  ·  stake {stake:,.0f}  ·  "
          f"cuota media {r['cuota']:.2f}")
    print(f"   winrate global: {r['wr']:.1f}%  ·  punto de equilibrio a esa "
          f"cuota: {r['equilibrio']:.1f}%")
    print(f"   beneficio: {r['benef']:+,.0f}   ROI: {r['roi']:+.1f}%")

    print("\n" + "-" * 104)
    print("POR BANDAS DE |Δ|")
    print("-" * 104)
    cab = (f"{'banda':>14s} {'n':>4s} {'gan':>4s} {'per':>4s} {'anu':>4s} "
           f"{'WINRATE':>14s} {'cuota':>6s} {'mín.p/empate':>13s} "
           f"{'ROI':>8s} {'beneficio':>12s}")
    print(cab)
    print("-" * len(cab))
    for inf, sup in BANDAS:
        sub = [f for f in filas
               if abs(f["dif"]) > inf and (sup is None or abs(f["dif"]) < sup)]
        if not sub:
            rot = (f">{inf:g}" if sup is None else f"{inf:g}–{sup:g}")
            print(f"{rot:>14s}    — sin apuestas en esta banda")
            continue
        rb = _puntuar(sub)
        rot = (f">{inf:g}" if sup is None else f"{inf:g}–{sup:g}")
        print(f"{rot:>14s} {rb['n']:4d} {rb['gan']:4d} {rb['per']:4d} "
              f"{rb['anu']:4d} {rb['wr']:8.1f}% ±{rb['ic']:4.1f} "
              f"{rb['cuota']:6.2f} {rb['equilibrio']:12.1f}% "
              f"{rb['roi']:+7.1f}% {rb['benef']:+12,.0f}")

    # --- acumulado: |Δ| > X, que es como se configura la app ---
    print("\n" + "-" * 104)
    print("ACUMULADO  ·  así lo configura la app: \"apuesta si |Δ| > X\"")
    print("-" * 104)
    for umbral in (1.0, 2.0, 3.0, 4.0, 5.0):
        sub = [f for f in filas if abs(f["dif"]) > umbral]
        if not sub:
            print(f"   |Δ| > {umbral:g}          sin apuestas")
            continue
        ra = _puntuar(sub)
        veredicto = "por encima" if ra["wr"] > ra["equilibrio"] else "POR DEBAJO"
        print(f"   |Δ| > {umbral:g}   n={ra['n']:3d}  winrate={ra['wr']:5.1f}% "
              f"±{ra['ic']:4.1f}  cuota={ra['cuota']:.2f}  "
              f"(equilibrio {ra['equilibrio']:.1f}%)  {veredicto}  "
              f"benef={ra['benef']:+10,.0f}")

    print("\n" + "-" * 104)
    print("LECTURA")
    print("-" * 104)

    # ¿la curva SUBE o BAJA al exigir más Δ? Se comprueba con los datos, no
    # se supone: aquí ha salido al revés que en el backtest, y decir lo
    # contrario sería mentir con la propia tabla encima.
    acum = [(u, _puntuar([f for f in filas if abs(f["dif"]) > u]))
            for u in (1.0, 2.0, 3.0, 4.0, 5.0)]
    acum = [(u, ra) for u, ra in acum if ra["dec"] >= 10]
    if len(acum) >= 2:
        (u0, r0), (u1, r1) = acum[0], acum[-1]
        if r1["wr"] > r0["wr"] + 5:
            print(f"   · Exigir más Δ SUBE el winrate: {r0['wr']:.1f}% con "
                  f"|Δ|>{u0:g} → {r1['wr']:.1f}% con |Δ|>{u1:g}.")
        elif r1["wr"] < r0["wr"] - 5:
            print(f"   · ATENCIÓN: exigir más Δ NO sube el winrate aquí, lo "
                  f"BAJA: {r0['wr']:.1f}% con |Δ|>{u0:g} → "
                  f"{r1['wr']:.1f}% con |Δ|>{u1:g}.")
        else:
            print(f"   · Exigir más Δ apenas mueve el winrate: "
                  f"{r0['wr']:.1f}% → {r1['wr']:.1f}%.")

    arriba = []
    for inf, sup in BANDAS:
        sub = [f for f in filas
               if abs(f["dif"]) > inf
               and (sup is None or abs(f["dif"]) < sup)]
        rb = _puntuar(sub)
        if rb["dec"] >= 5 and rb["wr"] > rb["equilibrio"]:
            arriba.append((inf, sup, rb))
    if arriba:
        for inf, sup, rb in arriba:
            rot = (f">{inf:g}" if sup is None else f"{inf:g}–{sup:g}")
            print(f"   · La banda {rot} está POR ENCIMA de su equilibrio "
                  f"({rb['wr']:.1f}% > {rb['equilibrio']:.1f}%) con n={rb['n']}"
                  f" — con esa n puede ser suerte.")
    else:
        print("   · Ninguna banda con n≥5 llega al punto de equilibrio de su "
              "cuota: todas pierden dinero.")

    print(f"   · Muestra total: {r['n']} apuestas. El ± de cada banda es de")
    print("     15–40 puntos, así que una banda con n<10 no decide nada.")
    print("   · Para la FORMA de la curva con miles de partidos:")
    print("       python backtest.py --umbrales 1 2 3 4")
    print("       python backtest.py --umbral 3 --umbral-max 5")
    print("     Ese backtest da winrates del 70-80 % porque compara contra la")
    print("     línea PROXY (1Q+2Q)×2, no contra la línea real de la casa:")
    print("     mide la reversión a la media entre mitades, que la propia")
    print("     casa ya pricea. ESA BRECHA entre las dos columnas es el")
    print("     problema de fondo, no el valor del umbral.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
