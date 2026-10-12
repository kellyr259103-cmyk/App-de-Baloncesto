# -*- coding: utf-8 -*-
"""
motor_gradiente.py — Motor nuevo e INDEPENDIENTE: descenso de gradiente (Adam)
con modelo jerárquico por liga + probabilidades re-calibradas por GD.

Qué es (honestamente):
  * Para una recta pura por liga, el GD converge al mismo mínimo que OLS: no
    hay magia que sacar de ahí. Lo que este motor añade de verdad es:
      1) ENTRENAMIENTO CONJUNTO con encogimiento jerárquico: cada liga ajusta
         desviaciones (d0,d1,d2) sobre un modelo GLOBAL compartido, penalizadas
         por L2 → las ligas con pocos datos heredan fuerza del conjunto
         (pooling real, no "el modelo de otra liga").
      2) RECALIBRACIÓN de la probabilidad: en vez de Φ(Δ/RMSE) fijo, aprende
         P(lado) = sigmoid(a·z + b) minimizando log-loss (BCE) por GD, con
         z = |Δ|/σ de la liga.
  * Todo en numpy puro, Adam, sin dependencias del motor actual.

Contenido:
  A) Backtest walk-forward 2026 en las ligas PERDEDORAS (y dos de control):
     RMSE OLS vs GD jerárquico + ECE de Φ vs probabilidad calibrada.
  B) Replay de las apuestas de HOY: con el motor GD (entrenado SOLO con datos
     anteriores a hoy) + mismas reglas (Δ≥2, 1 por evento, EV≥0) → ¿qué
     hubiera pasado?

Uso:
    python motor_gradiente.py
"""
from __future__ import annotations

import re
from datetime import date

import numpy as np

import base_datos
import motor

LAM = 30.0          # fuerza del encogimiento jerárquico (n/(n+lam) de libertad)
LR = 0.05
EPOCHS = 3000
UMBRAL = 2.0
STAKE = 10000.0

# ligas perdedoras de hoy + 2 de control que ganaron
LIGAS_OBJETIVO = ("italy/lega_a", "austria/obl", "portugal/lpb", "germany/bbl",
                  "poland/plk", "switzerland/sb_league__w_",
                  "lithuania/lkl", "france/elite")

HOY = date(2026, 10, 11)
_RE_Q = re.compile(r"1Q=(\d+)\s+2Q=(\d+)")


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _ece_arrays(p, y, bins=(0.5, 0.6, 0.7, 0.8, 0.9, 1.01)) -> float:
    p = np.asarray(p, float); y = np.asarray(y, float)
    if not len(p):
        return 0.0
    ece = 0.0
    for lo, hi in zip(bins, bins[1:]):
        sel = (p >= lo) & (p < hi)
        if not sel.any():
            continue
        ece += (sel.sum() / len(p)) * abs(p[sel].mean() - y[sel].mean())
    return ece


class MotorGD:
    """Modelo jerárquico: TOTAL = (g0+d0L) + (g1+d1L)·q1 + (g2+d2L)·q2."""

    def __init__(self, lr=0.1, epochs=EPOCHS, lam=LAM):
        self.lr, self.epochs, self.lam = lr, epochs, lam
        self.ligas: dict[str, int] = {}
        self.g = np.zeros(3)
        self.d = None
        self.sigma_liga: dict[str, float] = {}
        self.sigma_global = 12.0
        self.a, self.b = 1.0, 0.0        # calibración
        # media/desviación de estandarización (se fijan en fit)
        self.mx1, self.sx1 = 45.0, 8.0
        self.mx2, self.sx2 = 45.0, 8.0
        self.my, self.sy = 165.0, 25.0

    # ------------------------------------------------------------- entrenar
    def fit(self, ligas, q1, q2, y, lineas=None):
        idx = np.array([self.ligas.setdefault(l, len(self.ligas))
                        for l in ligas], dtype=int)
        L = len(self.ligas)
        q1 = np.asarray(q1, float)
        q2 = np.asarray(q2, float)
        y = np.asarray(y, float)
        n = len(y)

        # --- ESTANDARIZACIÓN (imprescindible para que Adam converja: sin
        # esto, intercepción (~90) y features (~45) casi colineales tardan
        # miles de pasos en coordinarse y el modelo queda a medio entrenar).
        self.mx1, self.sx1 = float(q1.mean()), float(q1.std() or 1.0)
        self.mx2, self.sx2 = float(q2.mean()), float(q2.std() or 1.0)
        self.my, self.sy = float(y.mean()), float(y.std() or 1.0)
        x1 = (q1 - self.mx1) / self.sx1
        x2 = (q2 - self.mx2) / self.sx2
        ys = (y - self.my) / self.sy

        self.g = np.zeros(3)
        self.d = np.zeros((L, 3))

        # --- Adam para (g, d) --------------------------------------------
        mg, vg = np.zeros(3), np.zeros(3)
        md, vd = np.zeros((L, 3)), np.zeros((L, 3))
        b1, b2, eps = 0.9, 0.999, 1e-8
        uno = np.ones_like(x1)
        for t in range(1, self.epochs + 1):
            pred = (self.g[0] + self.d[idx, 0]
                    + (self.g[1] + self.d[idx, 1]) * x1
                    + (self.g[2] + self.d[idx, 2]) * x2)
            err = pred - ys
            gg = np.array([err.mean(), (err * x1).mean(), (err * x2).mean()])
            gd = np.empty((L, 3))
            for j, feat in enumerate((uno, x1, x2)):
                gd[:, j] = (np.bincount(idx, weights=err * feat, minlength=L)
                            / n + 2.0 * self.lam * self.d[:, j] / n)
            for p, gr, m, v in ((self.g, gg, mg, vg), (self.d, gd, md, vd)):
                m *= b1; m += (1 - b1) * gr
                v *= b2; v += (1 - b2) * gr * gr
                p -= self.lr * (m / (1 - b1 ** t)) / (
                    np.sqrt(v / (1 - b2 ** t)) + eps)

        # --- sigma por liga (residuo en unidades ORIGINALES) --------------
        pred_orig = self.my + self.sy * self.pred(idx, x1, x2)
        res = y - pred_orig
        self.sigma_global = float(res.std() or 12.0)
        for l, i in self.ligas.items():
            r = res[idx == i]
            self.sigma_liga[l] = (float(r.std()) if len(r) >= 20
                                  else self.sigma_global)

        # --- calibración de P(lado) por GD (log-loss) ---------------------
        if lineas is not None:
            linea = np.asarray(lineas, float)
            delta = pred_orig - linea
            ok = delta != 0
            z = np.abs(delta[ok]) / np.array(
                [self.sigma_liga.get(ligas[i], self.sigma_global)
                 for i in np.where(ok)[0]])
            yb = ((y > linea) == (delta > 0))[ok].astype(float)
            self._calibrar(z, yb)

    def _calibrar(self, z, yb, epochs=1500, lr=0.05):
        a, b = 1.7, 0.0                  # arranque ≈ Φ(z)
        ma = mb = va = vb = 0.0
        b1, b2, eps = 0.9, 0.999, 1e-8
        for t in range(1, epochs + 1):
            s = _sigmoid(a * z + b)
            ga = float(((s - yb) * z).mean())
            gb = float((s - yb).mean())
            ma = b1 * ma + (1 - b1) * ga; va = b2 * va + (1 - b2) * ga * ga
            mb = b1 * mb + (1 - b1) * gb; vb = b2 * vb + (1 - b2) * gb * gb
            a -= lr * (ma / (1 - b1 ** t)) / (np.sqrt(va / (1 - b2 ** t)) + eps)
            b -= lr * (mb / (1 - b1 ** t)) / (np.sqrt(vb / (1 - b2 ** t)) + eps)
        self.a, self.b = a, b

    # ------------------------------------------------------------ predecir
    def pred(self, idx, x1, x2):
        """Predicción en espacio ESTANDARIZADO (el que entrena Adam)."""
        return (self.g[0] + self.d[idx, 0]
                + (self.g[1] + self.d[idx, 1]) * x1
                + (self.g[2] + self.d[idx, 2]) * x2)

    def predecir(self, liga: str, q1: float, q2: float) -> float:
        i = self.ligas.get(liga, -1)
        d0, d1, d2 = (0.0, 0.0, 0.0) if i < 0 else tuple(self.d[i])
        x1 = (q1 - self.mx1) / self.sx1
        x2 = (q2 - self.mx2) / self.sx2
        return self.my + self.sy * ((self.g[0] + d0)
                                    + (self.g[1] + d1) * x1
                                    + (self.g[2] + d2) * x2)

    def prob_lado(self, liga: str, pred: float, linea: float) -> float:
        sig = self.sigma_liga.get(liga, self.sigma_global)
        z = abs(pred - linea) / sig if sig else 0.0
        return float(_sigmoid(self.a * z + self.b))


# --------------------------------------------------------------------------- #
def _cargar(hasta: date | None = None):
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT liga, fecha, q1, q2, total FROM muestras "
                        "WHERE fecha IS NOT NULL ORDER BY fecha, id")
            filas = cur.fetchall()
    finally:
        conn.close()
    out = []
    for liga, fecha, q1, q2, total in filas:
        if hasta and fecha >= hasta:
            continue
        out.append((liga, fecha, float(q1), float(q2), float(total)))
    return out


def parte_a() -> None:
    print("=" * 78)
    print("A) BACKTEST WALK-FORWARD 2026 — LIGAS PERDEDORAS (+2 de control)")
    print("=" * 78)
    datos = _cargar()
    meses = [date(2026, m, 1) for m in range(1, 11)]
    acum = {l: {"ols": [0.0, 0], "gd": [0.0, 0],
                "p_phi": [], "p_cal": [], "y": []} for l in LIGAS_OBJETIVO}
    a_last = b_last = None

    for k, borde in enumerate(meses):
        train = [d for d in datos if d[1] < borde]
        if len(train) < 500:
            continue
        fin = meses[k + 1] if k + 1 < len(meses) else date(2026, 12, 31)
        ev = [d for d in datos
              if borde <= d[1] < fin and d[0] in LIGAS_OBJETIVO]
        if not ev:
            continue
        eng = MotorGD()
        ligas_t = [d[0] for d in train]
        q1t = np.array([d[2] for d in train])
        q2t = np.array([d[3] for d in train])
        yt = np.array([d[4] for d in train])
        eng.fit(ligas_t, q1t, q2t, yt, lineas=2.0 * (q1t + q2t))
        a_last, b_last = eng.a, eng.b
        ols = {}
        for l in LIGAS_OBJETIVO:
            filas = [(d[2], d[3], d[4]) for d in train if d[0] == l]
            if len(filas) >= 60:
                ols[l] = motor._ajustar(filas, l, "walk-forward")
        for liga, _fecha, q1, q2, total in ev:
            r = acum[liga]
            pred_gd = eng.predecir(liga, q1, q2)
            r["gd"][0] += (pred_gd - total) ** 2
            r["gd"][1] += 1
            m = ols.get(liga)
            if m is not None:
                pred_ols = m.predecir_dos(q1, q2)
                r["ols"][0] += (pred_ols - total) ** 2
                r["ols"][1] += 1
            proxy = 2.0 * (q1 + q2)
            if total != proxy:
                sig = eng.sigma_liga.get(liga, eng.sigma_global)
                z = abs(pred_gd - proxy) / sig
                r["p_phi"].append(float(_sigmoid(1.702 * z)))
                r["p_cal"].append(eng.prob_lado(liga, pred_gd, proxy))
                r["y"].append(1.0 if ((total > proxy) == (pred_gd > proxy))
                              else 0.0)

    cab = (f"{'liga':30s} {'n':>5s} {'RMSE OLS':>9s} {'RMSE GD':>8s} "
           f"{'ECE Φ':>7s} {'ECE cal':>8s}")
    print(f"\n{cab}")
    print("-" * len(cab))
    for l in LIGAS_OBJETIVO:
        r = acum[l]
        if not r["gd"][1]:
            continue
        n = r["gd"][1]
        rmse_gd = (r["gd"][0] / n) ** 0.5
        rmse_ols = ((r["ols"][0] / r["ols"][1]) ** 0.5 if r["ols"][1]
                    else float("nan"))
        print(f"{l:30s} {n:5d} {rmse_ols:9.2f} {rmse_gd:8.2f} "
              f"{_ece_arrays(r['p_phi'], r['y']) * 100:6.1f}% "
              f"{_ece_arrays(r['p_cal'], r['y']) * 100:7.1f}%")
    if a_last is not None:
        print(f"\n(Calibración final del motor: P = sigmoid({a_last:.2f}·z "
              f"{b_last:+.2f})  ·  Φ equivale a sigmoid(1.70·z))")


def _cuota_lado(evento_id: int, fecha, lado: str, cuota_registrada: float):
    """Cuota del lado pedido: del histórico si existe; si no, la registrada."""
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT cuota_over, cuota_under FROM cuotas_historial
                    WHERE evento_id = %s AND momento >= %s - interval '2 minutes'
                      AND momento <= %s + interval '45 minutes'
                      AND cuota_over IS NOT NULL
                    ORDER BY id LIMIT 1""", (evento_id, fecha, fecha))
            fila = cur.fetchone()
    finally:
        conn.close()
    if fila:
        c = fila[0] if lado == "OVER" else fila[1]
        if c and float(c) > 1.01:
            return float(c)
    return float(cuota_registrada)


def _q(a, i: int) -> float:
    m = _RE_Q.search(a.nota or "")
    return float(m.group(1 + i)) if m else 0.0


def parte_b() -> None:
    print("\n" + "=" * 78)
    print("B) REPLAY DE LAS APUESTAS DE HOY con el motor GD")
    print("   (entrenado SOLO con partidos ANTERIORES a hoy; mismas reglas)")
    print("=" * 78)
    datos = _cargar(hasta=HOY)
    eng = MotorGD()
    ligas_t = [d[0] for d in datos]
    q1t = np.array([d[2] for d in datos])
    q2t = np.array([d[3] for d in datos])
    yt = np.array([d[4] for d in datos])
    print(f"Entrenando con {len(datos):,} partidos de {len(set(ligas_t))} "
          f"ligas anteriores a hoy…")
    eng.fit(ligas_t, q1t, q2t, yt, lineas=2.0 * (q1t + q2t))

    idx = np.array([eng.ligas[l] for l in ligas_t])
    x1t = (q1t - eng.mx1) / eng.sx1
    x2t = (q2t - eng.mx2) / eng.sx2
    pred_s = eng.my + eng.sy * eng.pred(idx, x1t, x2t)
    proxy = 2.0 * (q1t + q2t)
    sig = np.array([eng.sigma_liga[l] for l in ligas_t])
    ok = yt != proxy
    z = np.abs(pred_s - proxy)[ok] / sig[ok]
    yb = ((yt > proxy) == (pred_s > proxy))[ok].astype(float)
    p_phi = _sigmoid(1.702 * z)
    p_cal = _sigmoid(eng.a * z + eng.b)
    print(f"Calibración: a={eng.a:.2f} b={eng.b:+.2f}  ·  ECE en train: "
          f"Φ={_ece_arrays(p_phi, yb) * 100:.1f}pp → "
          f"calibrada={_ece_arrays(p_cal, yb) * 100:.1f}pp")

    apuestas = [a for a in base_datos.listar_apuestas()
                if a.fecha and a.fecha.date() == HOY]
    # Q1/Q2 por partido: las notas de apuestas YA RESUELTAS pueden haber
    # perdido el texto del bot; las muestras del recolector guardan los
    # cuartos de todos los partidos terminados (incluye hoy).
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT partido, q1, q2 FROM muestras WHERE fecha = %s",
                        (HOY,))
            qx = {p: (float(a1), float(a2)) for p, a1, a2 in cur.fetchall()}
    finally:
        conn.close()

    # Relleno: partidos que el recolector no guardó (se quedaron sin estado
    # final en el feed) → una petición a API-Sports por los cuartos de hoy.
    faltan = [a for a in apuestas if _q(a, 0) == 0 and a.partido not in qx]
    if faltan:
        try:
            import historico
            import resultados_api as res_mod
            d = historico._Cliente().get("games", date=HOY.isoformat())
            juegos = []
            for g in d.get("response") or []:
                sc = g.get("scores") or {}
                h = sc.get("home") or {}
                aw = sc.get("away") or {}
                qs = (h.get("quarter_1"), aw.get("quarter_1"),
                      h.get("quarter_2"), aw.get("quarter_2"))
                if any(v is None for v in qs):
                    continue
                te = g.get("teams") or {}
                juegos.append((((te.get("home") or {}).get("name") or ""),
                               ((te.get("away") or {}).get("name") or ""),
                               float(qs[0] + qs[1]), float(qs[2] + qs[3])))
            for a in faltan:
                partes = (a.partido or "").rsplit(" - ", 1)
                if len(partes) != 2:
                    continue
                nl, nv = res_mod._norm(partes[0]), res_mod._norm(partes[1])
                mejor, ms = None, 0.0
                for hl, hv, q1s, q2s in juegos:
                    s = min(res_mod._parecido(nl, res_mod._norm(hl)),
                            res_mod._parecido(nv, res_mod._norm(hv)))
                    if s >= 0.5 and s > ms:
                        ms, mejor = s, (q1s, q2s)
                if mejor:
                    qx[a.partido] = mejor
            print(f"   (relleno API-Sports: {len(faltan)} apuestas sin cuartos "
                  f"en muestras → recuperadas: "
                  f"{sum(1 for a in faltan if a.partido in qx)})")
        except Exception as exc:  # noqa: BLE001
            print("   aviso: no se pudo rellenar desde API-Sports:", exc)
    print(f"Apuestas de hoy: {len(apuestas)}  ·  cuartos disponibles: "
          f"{sum(1 for a in apuestas if _q(a, 0) > 0 or a.partido in qx)}"
          f"\n")

    cab = (f"{'id':>4} {'liga':24s} {'Δ_old':>6s} {'Δ_new':>6s} "
           f"{'lado→nuevo':>11s} {'cuota':>6s} {'final':>6s} {'real':>4s} "
           f"{'sim':>7s}  nota")
    print(cab)
    print("-" * len(cab))

    res = {"real": [0, 0, 0.0], "sim": [0, 0, 0.0]}
    perd = {"real": [0, 0, 0.0], "sim": [0, 0, 0.0]}
    L_PERDEDORAS = ("italy/lega_a", "austria/obl", "portugal/lpb",
                    "germany/bbl", "poland/plk", "switzerland/sb_league__w_",
                    "EUROLIGA")

    for a in sorted(apuestas, key=lambda x: x.id or 0):
        liga = a.liga_modelo or ""
        q1r, q2r = _q(a, 0), _q(a, 1)
        if q1r == 0.0 and q2r == 0.0:
            par = qx.get(a.partido)
            if par is None:
                print(f"{a.id:>4} {liga[:24]:24s}  sin Q1/Q2 (no puntuable)")
                continue
            q1r, q2r = par
        pred = eng.predecir(liga, q1r, q2r)
        d_new = pred - a.linea
        lado = "OVER" if d_new > 0 else "UNDER"
        cuota = (a.cuota if lado == a.seleccion
                 else _cuota_lado(a.evento_id, a.fecha, lado, a.cuota))
        p = eng.prob_lado(liga, pred, a.linea)
        ev = p * cuota - 1.0
        decide = abs(d_new) >= UMBRAL and ev >= 0.0
        final = a.total_final
        real = a.estado if a.estado in ("GANADA", "PERDIDA") else "—"
        sim = "—"
        if decide and final is not None:
            if abs(final - a.linea) < 1e-9:
                sim = "push"
            else:
                gano = (final > a.linea) == (lado == "OVER")
                sim = "GANA" if gano else "PIERDE"
        if real in ("GANADA", "PERDIDA"):
            res["real"][0 if real == "GANADA" else 1] += 1
            res["real"][2] += (a.beneficio or 0.0)
            if liga in L_PERDEDORAS:
                perd["real"][0 if real == "GANADA" else 1] += 1
                perd["real"][2] += (a.beneficio or 0.0)
        if sim in ("GANA", "PIERDE"):
            ben = STAKE * (cuota - 1) if sim == "GANA" else -STAKE
            res["sim"][0 if sim == "GANA" else 1] += 1
            res["sim"][2] += ben
            if liga in L_PERDEDORAS:
                perd["sim"][0 if sim == "GANA" else 1] += 1
                perd["sim"][2] += ben
        marca = ("" if decide else
                 "no apuesta: " + ("|Δ|<2" if abs(d_new) < UMBRAL
                                   else f"EV {ev * 100:+.1f}%"))
        d_old = a.diferencia if a.diferencia is not None else 0.0
        print(f"{a.id:>4} {liga[:24]:24s} {d_old:+6.1f} {d_new:+6.1f} "
              f"{a.seleccion + '→' + lado:>11s} {cuota:6.2f} "
              f"{'' if final is None else f'{final:6.0f}'} {real:>4s} "
              f"{sim:>7s}  {marca}")

    def _linea(t):
        g, p, b = t
        n = g + p
        return (f"n={n}  G={g}  P={p}  winrate="
                f"{g / n * 100 if n else 0:.1f}%  beneficio={b:+,.0f}")

    print("\n=== RESUMEN (decididas de hoy) ===")
    print(f"REAL     : {_linea(res['real'])}")
    print(f"MOTOR GD : {_linea(res['sim'])}")
    print("\n=== SUBCONJUNTO LIGAS PERDEDORAS (lega_a, obl, lpb, bbl, plk, "
          "sb_w, respaldo) ===")
    print(f"REAL     : {_linea(perd['real'])}")
    print(f"MOTOR GD : {_linea(perd['sim'])}")


def main() -> int:
    parte_a()
    parte_b()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
