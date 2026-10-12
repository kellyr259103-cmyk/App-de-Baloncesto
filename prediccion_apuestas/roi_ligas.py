# -*- coding: utf-8 -*-
"""
roi_ligas.py — Winrate y ROI REALES por liga (con la cuota que se apostó).

Lee la tabla `apuestas` (paperbet) y resume, liga a liga, cómo va la
estrategia de verdad: operaciones, ganadas/perdidas/push, winrate, cuota
media, stake jugado, beneficio y ROI. Sirve para decidir qué ligas tienen
ventaja demostrable (`validadas`) y cuáles no.

Criterio sugerido de validación (por defecto): n ≥ 20 y ROI > 0.

Uso:
    python roi_ligas.py                 # tabla completa
    python roi_ligas.py --min-n 10      # tabla con ligas de al menos 10 ops
    python roi_ligas.py --csv roi.csv   # exporta
"""

from __future__ import annotations

import argparse

import base_datos
import paperbet


def resumir_por_liga() -> list[dict]:
    apuestas = base_datos.listar_apuestas()
    por_liga: dict[str, dict] = {}
    for a in apuestas:
        r = por_liga.setdefault(a.liga_modelo or "?", {
            "liga": a.liga_modelo or "?", "n": 0, "g": 0, "p": 0, "push": 0,
            "pend": 0, "cuotas": [], "stake_dec": 0.0, "benef": 0.0,
        })
        r["n"] += 1
        if a.estado == paperbet.ESTADO_PENDIENTE:
            r["pend"] += 1
        elif a.estado == paperbet.ESTADO_GANADA:
            r["g"] += 1
            r["stake_dec"] += a.stake
            r["benef"] += a.beneficio or 0.0
            r["cuotas"].append(a.cuota)
        elif a.estado == paperbet.ESTADO_PERDIDA:
            r["p"] += 1
            r["stake_dec"] += a.stake
            r["benef"] += a.beneficio or 0.0
            r["cuotas"].append(a.cuota)
        else:                                   # ANULADA (push)
            r["push"] += 1
            r["cuotas"].append(a.cuota)

    filas = []
    for r in por_liga.values():
        dec = r["g"] + r["p"]
        r["dec"] = dec
        r["winrate"] = (r["g"] / dec * 100.0) if dec else None
        r["cuota_media"] = (sum(r["cuotas"]) / len(r["cuotas"])) if r["cuotas"] else None
        r["breakeven"] = (100.0 / r["cuota_media"]) if r["cuota_media"] else None
        r["roi"] = (r["benef"] / r["stake_dec"] * 100.0) if r["stake_dec"] else None
        r["valida"] = bool(dec >= 20 and (r["roi"] or 0) > 0)
        filas.append(r)
    return sorted(filas, key=lambda x: -(x["roi"] if x["roi"] is not None else -999))


def imprimir(filas: list[dict], min_n: int = 0) -> None:
    filas = [f for f in filas if f["dec"] >= min_n]
    cab = (f"{'liga':30s} {'ops':>4s} {'G':>3s} {'P':>3s} {'push':>4s} {'pend':>4s} "
           f"{'winrate':>8s} {'cuota':>6s} {'breakeven':>9s} {'ROI':>8s} {'benef':>11s}  val")
    print(cab)
    print("-" * len(cab))
    tot = {"n": 0, "g": 0, "p": 0, "push": 0, "pend": 0,
           "stake_dec": 0.0, "benef": 0.0, "cuotas": []}
    for f in filas:
        tot["n"] += f["n"]; tot["g"] += f["g"]; tot["p"] += f["p"]
        tot["push"] += f["push"]; tot["pend"] += f["pend"]
        tot["stake_dec"] += f["stake_dec"]; tot["benef"] += f["benef"]
        tot["cuotas"].extend(f["cuotas"])
        wr = "—" if f["winrate"] is None else f"{f['winrate']:6.1f}%"
        cm = "—" if f["cuota_media"] is None else f"{f['cuota_media']:6.2f}"
        be = "—" if f["breakeven"] is None else f"{f['breakeven']:8.1f}%"
        ro = "—" if f["roi"] is None else f"{f['roi']:+7.1f}%"
        print(f"{f['liga']:30s} {f['n']:4d} {f['g']:3d} {f['p']:3d} "
              f"{f['push']:4d} {f['pend']:4d} {wr:>8s} {cm:>6s} {be:>9s} "
              f"{ro:>8s} {f['benef']:11,.0f}  {'✔' if f['valida'] else ''}")
    dec = tot["g"] + tot["p"]
    wr = (tot["g"] / dec * 100.0) if dec else 0.0
    roi = (tot["benef"] / tot["stake_dec"] * 100.0) if tot["stake_dec"] else 0.0
    cuota = (sum(tot["cuotas"]) / len(tot["cuotas"])) if tot["cuotas"] else 0.0
    print("-" * len(cab))
    print(f"{'TOTAL':30s} {tot['n']:4d} {tot['g']:3d} {tot['p']:3d} "
          f"{tot['push']:4d} {tot['pend']:4d} {wr:7.1f}% {cuota:6.2f} "
          f"{(100 / cuota if cuota else 0):8.1f}% {roi:+7.1f}% {tot['benef']:11,.0f}")
    print("\nval = válida sugerida (n≥20 y ROI>0). 'breakeven' = winrate "
          "necesario para no perder a esa cuota.")


def main() -> int:
    p = argparse.ArgumentParser(description="ROI/winrate reales por liga.")
    p.add_argument("--min-n", type=int, default=0,
                   help="solo ligas con al menos N decisiones (por defecto 0)")
    p.add_argument("--csv", default=None, help="exportar a CSV")
    args = p.parse_args()

    filas = resumir_por_liga()
    if not filas:
        print("No hay apuestas todavía.")
        return 1
    imprimir(filas, args.min_n)

    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()),
                               extrasaction="ignore")
            w.writeheader()
            for f in filas:
                w.writerow({k: ("" if v is None else
                                (f"{v:.4f}" if isinstance(v, float) else v))
                            for k, v in f.items() if k != "cuotas"})
        print(f"\nCSV: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
