# -*- coding: utf-8 -*-
"""
forense_perdidas.py — Autopsia de las apuestas PERDIDAS: ¿qué estamos
haciendo mal?

Cruza cada apuesta resuelta con: su contexto (1Q/2Q, recuperados de la nota,
de `muestras` o de API-Sports), el final oficial y la media de su liga, y
clasifica las pérdidas:

  * respaldo (modelo de otra liga) vs modelo propio;
  * duplicados (mismo partido apostado 2+ veces);
  * margen del resultado: bad-beat (≤2 pts), zona gris (3-9), paliza (≥10);
  * contexto de 1ª parte: ¿apostábamos a REVERSIÓN contra una 1ª parte
    extrema (|z|>1)?  y ¿qué hizo la 2ª parte vs lo que pedía la línea?
  * lado (OVER/UNDER).

Salida: tabla por pérdida + agregados + "qué habría pasado" sin respaldo y
sin duplicados.

Uso:
    python forense_perdidas.py
"""
from __future__ import annotations

import re
from collections import defaultdict

import numpy as np

import base_datos
import paperbet

_RE_Q = re.compile(r"1Q=(\d+)\s+2Q=(\d+)")
HOY = "2026-10-11"


def _cuartos() -> dict[str, tuple[float, float]]:
    """Q1/Q2 (suma de ambos equipos) por partido: muestras + API-Sports."""
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT partido, q1, q2 FROM muestras "
                        "WHERE fecha = %s", (HOY,))
            qx = {p: (float(a), float(b)) for p, a, b in cur.fetchall()}
    finally:
        conn.close()
    try:
        import historico
        import resultados as rm
        d = historico._Cliente().get("games", date=HOY)
        juegos = []
        for g in d.get("response") or []:
            sc = g.get("scores") or {}
            h, aw = sc.get("home") or {}, sc.get("away") or {}
            qs = (h.get("quarter_1"), aw.get("quarter_1"),
                  h.get("quarter_2"), aw.get("quarter_2"))
            if any(v is None for v in qs):
                continue
            te = g.get("teams") or {}
            juegos.append((((te.get("home") or {}).get("name") or ""),
                           ((te.get("away") or {}).get("name") or ""),
                           float(qs[0] + qs[1]), float(qs[2] + qs[3])))

        def _cubre(a: set, b: set) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / min(len(a), len(b))

        for a in base_datos.listar_apuestas():
            if a.partido in qx:
                continue
            partes = (a.partido or "").rsplit(" - ", 1)
            if len(partes) != 2:
                continue
            nl, nv = rm._norm(partes[0]), rm._norm(partes[1])
            mejor, ms = None, 0.0
            for hl, hv, q1s, q2s in juegos:
                s = min(_cubre(nl, rm._norm(hl)), _cubre(nv, rm._norm(hv)))
                if s >= 0.5 and s > ms:
                    ms, mejor = s, (q1s, q2s)
            if mejor:
                qx[a.partido] = mejor
    except Exception as exc:  # noqa: BLE001
        print("aviso: sin relleno API:", exc)
    return qx


def _medias_liga() -> dict[str, tuple[float, float]]:
    """(media, desv) de la 1ª parte (Q1+Q2) por liga, con todo el histórico."""
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT liga, q1, q2 FROM muestras")
            filas = cur.fetchall()
    finally:
        conn.close()
    por: dict[str, list] = defaultdict(list)
    for liga, q1, q2 in filas:
        por[liga].append(float(q1) + float(q2))
    return {l: (float(np.mean(v)), float(np.std(v) or 1.0))
            for l, v in por.items()}


def main() -> int:
    aps = [a for a in base_datos.listar_apuestas()]
    res = [a for a in aps if a.estado in ("GANADA", "PERDIDA")]
    qx = _cuartos()
    med = _medias_liga()

    def h1_de(a):
        q = _RE_Q.search(a.nota or "")
        if q:
            return float(q.group(1)) + float(q.group(2))
        p = qx.get(a.partido)
        return (p[0] + p[1]) if p else None

    def tipo(a):
        if a.liga_modelo == "EUROLIGA":
            return "RESPALDO"
        return "propio"

    # duplicados reales (misma pareja apostada 2+ veces)
    cuenta = defaultdict(int)
    for a in aps:
        cuenta[a.evento_id] += 1
    duplicado = {ev for ev, c in cuenta.items() if c > 1}

    print("=" * 112)
    print("AUTOPSIA — APUESTAS PERDIDAS")
    print("=" * 112)
    cab = (f"{'id':>4} {'liga':24s} {'tipo':8s} {'lado':6s} {'línea':>6s} "
           f"{'pred':>6s} {'final':>6s} {'marg':>6s} {'H1':>5s} {'z1H':>5s} "
           f"{'2H':>5s} {'2Himpl':>6s} {'desv':>6s}  dup")
    print(cab)
    print("-" * len(cab))

    perd = [a for a in res if a.estado == "PERDIDA"]
    gan = [a for a in res if a.estado == "GANADA"]
    h2_loss, h2_win = [], []
    z_loss, z_win = [], []
    for a in sorted(perd, key=lambda x: x.id or 0):
        h1 = h1_de(a)
        final = a.total_final or 0
        marg = (final - a.linea) * (1 if a.seleccion == "OVER" else -1)
        liga_ctx = a.liga_modelo if a.liga_modelo != "EUROLIGA" else a.liga_apuesta
        h1m, h1s = med.get(a.liga_modelo, (86.0, 9.0))
        z = (h1 - h1m) / h1s if h1 is not None else None
        h2 = final - h1 if h1 is not None else None
        h2_impl = a.linea - h1 if h1 is not None else None
        desv = (h2 - h2_impl) if h2 is not None else None
        if desv is not None:
            h2_loss.append(desv)
            z_loss.append(z)
        print(f"{a.id:>4} {liga_ctx[:24]:24s} {tipo(a):8s} {a.seleccion:6s} "
              f"{a.linea:6.1f} {(a.prediccion or 0):6.1f} {final:6.0f} "
              f"{marg:+6.1f} {'' if h1 is None else f'{h1:5.0f}'} "
              f"{'' if z is None else f'{z:+5.2f}'} "
              f"{'' if h2 is None else f'{h2:5.0f}'} "
              f"{'' if h2_impl is None else f'{h2_impl:6.0f}'} "
              f"{'' if desv is None else f'{desv:+6.1f}'}  "
              f"{'SÍ' if a.evento_id in duplicado else ''}")

    # medias contextuales de las apuestas ganadas (para comparar)
    for a in gan:
        h1 = h1_de(a)
        if h1 is None:
            continue
        final = a.total_final or 0
        h1m, h1s = med.get(a.liga_modelo, (86.0, 9.0))
        h2_win.append(final - h1 - (a.linea - h1))
        z_win.append((h1 - h1m) / h1s)

    print("-" * len(cab))

    # ---- agregados ------------------------------------------------------
    def _res(sel, nombre):
        g = sum(1 for a in sel if a.estado == "GANADA")
        p = sum(1 for a in sel if a.estado == "PERDIDA")
        ben = sum(a.beneficio or 0 for a in sel)
        n = g + p
        print(f"  {nombre:34s} n={n:3d}  G={g:2d} P={p:2d}  "
              f"winrate={g / n * 100 if n else 0:5.1f}%  "
              f"beneficio={ben:+10,.0f}")

    print("\n=== POR TIPO DE MODELO (resueltas) ===")
    _res([a for a in res if tipo(a) == "propio"], "modelo PROPIO")
    _res([a for a in res if tipo(a) == "RESPALDO"], "RESPALDO (otra liga)")

    print("\n=== SIN DUPLICADOS (un registro por partido) ===")
    vistos = set()
    unicas = []
    for a in res:
        if a.evento_id in vistos:
            continue
        vistos.add(a.evento_id)
        unicas.append(a)
    _res(unicas, "una por partido")

    print("\n=== POR |Δ| DE ENTRADA (resueltas) ===")
    for lo, hi, nom in ((0, 2, "|Δ|<2"), (2, 3, "2–3"),
                        (3, 5, "3–5"), (5, 99, ">5")):
        sel = [a for a in res if lo <= abs(a.diferencia or 0) < hi]
        _res(sel, nom)

    print("\n=== POR LADO ===")
    _res([a for a in res if a.seleccion == "OVER"], "OVER")
    _res([a for a in res if a.seleccion == "UNDER"], "UNDER")

    print("\n=== POR MARGEN DEL RESULTADO (|final − línea|) ===")
    for lo, hi, nom in ((0, 2.01, "clavado (≤2 pts) — mala suerte"),
                        (2.01, 10, "zona gris (2–10)"),
                        (10, 999, "paliza (≥10)")):
        sel = [a for a in res
               if lo <= abs((a.total_final or 0) - a.linea) < hi]
        _res(sel, nom)

    print("\n=== CONTEXTO 1ª PARTE (solo pérdidas con datos) ===")
    if h2_loss:
        hl = np.array(h2_loss)
        wl = np.array(h2_win) if h2_win else np.array([0.0])
        print(f"  pérdidas: 2ª parte real − 2ª parte implícita por la línea: "
              f"media {hl.mean():+.1f} pts  (n={len(hl)})")
        print(f"  ganadas : lo mismo: media {wl.mean():+.1f} pts (n={len(wl)})")
        zl = np.array([z for z in z_loss if z is not None])
        zw = np.array(z_win)
        print(f"  pérdidas: 1ª parte a {zl.mean():+.2f} desv. de su liga "
              f"(|z|>1 extremas: {int((abs(zl) > 1).sum())} de {len(zl)})")
        print(f"  ganadas : 1ª parte a {zw.mean():+.2f} desv. "
              f"(|z|>1: {int((abs(zw) > 1).sum())} de {len(zw)})")

    print("\n=== ESCENARIO 'LO QUE NO DEBIMOS HACER' ===")
    limpio = [a for a in res
              if tipo(a) == "propio" and a.evento_id not in duplicado
              and abs(a.diferencia or 0) >= 2.0]
    _res(limpio, "propio + sin duplicados + |Δ|≥2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
