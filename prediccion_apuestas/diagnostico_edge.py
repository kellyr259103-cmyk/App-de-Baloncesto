# -*- coding: utf-8 -*-
"""
diagnostico_edge.py — ¿Tiene el modelo edge REAL sobre la línea de la casa?

SOLO LECTURA. No escribe en la base de datos, no toca config.json, ni reglas,
ni el bot en marcha.

Fuente de datos (en este orden):
  1) tabla `señales` — registro de TODAS las evaluaciones del descanso
     (apostadas o no). Se usa cuando hay ≥ 30 señales con total_final;
     `linea_entrada` es la línea y `pred_modelo` la predicción registrada en
     vivo (sin fuga por construcción).
  2) RESPALDO mientras `señales` no llegue a 30: el universo se arma con
     `cuotas_historial` (primera línea del descanso) y el modelo se re-entrena
     por liga SOLO con muestras anteriores a HOY (sin fuga). Se indica en la
     salida qué fuente se usó.

Hipótesis fijadas ANTES de ejecutar (no se modifican):
    H1: RMSE(línea) ≤ RMSE(modelo)
    H2: pendiente de (total_final−línea) ~ (pred−línea) = 0   [vs 1 = info total]
    H3: CLV medio = 0   (cierre principal: `linea_3q_5min`)

Reglas de muestra:
    * Pruebas 1 y 2 → sin n≥30 el veredicto es INCONCLUSA POR MUESTRA.
    * Prueba 3 (CLV) → sin n≥100 el veredicto es INCONCLUSA POR MUESTRA.
    * Con n<30 los IC bootstrap no se muestran como fiables
      ("IC no fiable (n pequeño)").

Bootstrap: 5000 remuestreos, semilla 20261011.

Uso:
    python diagnostico_edge.py
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date

import numpy as np

import base_datos
import motor

HOY = date(2026, 10, 11)
RNG = np.random.default_rng(20261011)
N_BOOT = 5000
_Z = 1.959963984540054
_RE_RET = re.compile(r"\+(\d+)s")
N_MIN_P12 = 30     # mínimo para concluir en pruebas 1 y 2
N_MIN_CLV = 100    # mínimo para concluir en CLV
MIN_MUESTRAS_MODELO = 30
BREAKEVEN = 55.6   # % que exige una cuota ~1.8 (referencia pedida, no umbral)


def _q(sql, args=None):
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def _ic_boot(valores: np.ndarray) -> tuple[float, float, float]:
    """(media, ic_inf, ic_sup) por bootstrap percentil. n<2 → sin botas."""
    v = np.asarray(valores, float)
    n = len(v)
    est = float(np.mean(v)) if n else 0.0
    if n < 2:
        return est, est, est
    ests = np.empty(N_BOOT)
    for i in range(N_BOOT):
        ests[i] = v[RNG.integers(0, n, n)].mean()
    return est, float(np.percentile(ests, 2.5)), float(np.percentile(ests, 97.5))


def _fmt_ic(n: int, est: float, lo: float, hi: float,
            etiqueta: str = "IC95%", dec: int = 2) -> str:
    """Formatea estimación + IC, o el aviso de no fiable si n<30."""
    if n < N_MIN_P12:
        return f"{est:+.{dec}f}  {etiqueta} no fiable (n pequeño)"
    return f"{est:+.{dec}f}  {etiqueta} [{lo:+.{dec}f}, {hi:+.{dec}f}]"


def _wilson(k: int, n: int) -> tuple[float, float, float]:
    if not n:
        return 0.0, 0.0, 0.0
    p = k / n
    c = (p + _Z * _Z / (2 * n)) / (1 + _Z * _Z / n)
    h = (_Z * math.sqrt(p * (1 - p) / n + _Z * _Z / (4 * n * n))
         / (1 + _Z * _Z / n))
    return p, max(0.0, c - h), min(1.0, c + h)


def _norm(texto: str) -> set:
    import unicodedata
    t = unicodedata.normalize("NFKD", texto or "")
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    for ch in "().,-'’/*_":
        t = t.replace(ch, " ")
    relleno = {"basket", "basketball", "bc", "bk", "kk", "sc", "ac", "as",
               "cb", "club", "team", "w", "f", "fem", "u21", "u23"}
    return {p for p in t.split() if p and p not in relleno}


def _cobertura(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# --------------------------------------------------------------------------- #
#  PASO 0 — Inspección
# --------------------------------------------------------------------------- #
def paso0() -> None:
    print("=" * 78)
    print("PASO 0 — INSPECCIÓN DEL ESQUEMA Y DEL UNIVERSO")
    print("=" * 78)
    for tabla in ("cuotas_historial", "apuestas", "muestras", "señales"):
        try:
            filas = _q("""SELECT column_name, data_type
                            FROM information_schema.columns
                           WHERE table_name = %s
                           ORDER BY ordinal_position""", (tabla,))
        except Exception as exc:  # noqa: BLE001
            print(f"\n[{tabla}] no legible: {exc}")
            continue
        print(f"\n[{tabla}]  columnas: "
              + ", ".join(f"{c}({t})" for c, t in filas))

    n_c, f_c0, f_c1 = _q("SELECT count(*), min(momento), max(momento) "
                         "FROM cuotas_historial")[0]
    n_ev = _q("SELECT count(DISTINCT evento_id) FROM cuotas_historial")[0][0]
    n_desc_ev = _q("SELECT count(DISTINCT evento_id) FROM cuotas_historial "
                   "WHERE en_descanso = true")[0][0]
    n_heur_ev = _q("""SELECT count(DISTINCT evento_id) FROM cuotas_historial
                       WHERE period_id = 'QUARTER2'
                         AND reloj IN ('20:00','24:00')""")[0][0]
    print(f"\n[cuotas_historial] filas={n_c} · eventos={n_ev} · "
          f"desde {f_c0} hasta {f_c1}")
    print(f"   eventos con línea EN DESCANSO: marca en_descanso={n_desc_ev} · "
          f"por reloj de medio tiempo={n_heur_ev}")

    n_a = _q("SELECT count(*), min(fecha), max(fecha) FROM apuestas")[0]
    est = _q("SELECT estado, count(*) FROM apuestas GROUP BY estado")
    print(f"\n[apuestas] filas={n_a[0]} · {n_a[1]} → {n_a[2]} · por estado: "
          f"{dict(est)}")

    n_m = _q("SELECT count(*), min(fecha), max(fecha) FROM muestras")[0]
    n_hoy = _q("SELECT count(*) FROM muestras WHERE fecha = %s",
               (HOY,))[0][0]
    print(f"[muestras] filas={n_m[0]} · {n_m[1]} → {n_m[2]} · de hoy={n_hoy}")

    n_s = _q("SELECT count(*) FROM señales")[0][0]
    n_s_ok = _q("SELECT count(*) FROM señales "
                "WHERE total_final IS NOT NULL")[0][0]
    print(f"[señales] filas={n_s} · con total_final={n_s_ok} · "
          f"(la app en marcha debe reiniciarse con el código nuevo para "
          f"empezar a registrar)")


# --------------------------------------------------------------------------- #
#  Universo (respaldo por cuotas_historial)
# --------------------------------------------------------------------------- #
def universo() -> list[dict]:
    """Partidos con (a) línea de descanso, (b) final oficial, (c) Q1 y Q2."""
    filas = _q("""
        SELECT evento_id, liga, partido, linea, cuota_over, cuota_under,
               momento, en_descanso, period_id, reloj, id
          FROM cuotas_historial
         WHERE linea IS NOT NULL
           AND (en_descanso = true
                OR (period_id = 'QUARTER2' AND reloj IN ('20:00','24:00')))
         ORDER BY evento_id, id""")
    entradas: dict[int, dict] = {}
    for ev, liga, partido, linea, co, cu, mom, flag, per, rel, rid in filas:
        if ev in entradas:
            continue
        entradas[ev] = {"evento_id": ev, "liga": liga, "partido": partido,
                        "momento": mom, "linea": float(linea),
                        "cuota_over": co, "cuota_under": cu,
                        "flag": bool(flag)}

    qx: dict[str, tuple] = {}
    for p, q1, q2, tot in _q("SELECT partido, q1, q2, total FROM muestras "
                             "WHERE fecha = %s", (HOY,)):
        qx[p] = (float(q1), float(q2), float(tot))
    faltan = [e for e in entradas.values() if e["partido"] not in qx]
    if faltan:
        try:
            import historico
            d = historico._Cliente().get("games", date=HOY.isoformat())
            juegos = []
            for g in d.get("response") or []:
                sc = g.get("scores") or {}
                h, aw = sc.get("home") or {}, sc.get("away") or {}
                qs = (h.get("quarter_1"), aw.get("quarter_1"),
                      h.get("quarter_2"), aw.get("quarter_2"),
                      h.get("total"), aw.get("total"))
                if any(v is None for v in qs):
                    continue
                te = g.get("teams") or {}
                juegos.append((((te.get("home") or {}).get("name") or ""),
                               ((te.get("away") or {}).get("name") or ""),
                               float(qs[0] + qs[1]), float(qs[2] + qs[3]),
                               float(qs[4] + qs[5])))
            for e in faltan:
                partes = (e["partido"] or "").rsplit(" - ", 1)
                if len(partes) != 2:
                    continue
                nl, nv = _norm(partes[0]), _norm(partes[1])
                mejor, ms = None, 0.0
                for hl, hv, q1s, q2s, tot in juegos:
                    s = min(_cobertura(nl, _norm(hl)), _cobertura(nv, _norm(hv)))
                    if s >= 0.5 and s > ms:
                        ms, mejor = s, (q1s, q2s, tot)
                if mejor:
                    qx[e["partido"]] = mejor
            print(f"[universo] relleno API-Sports: {len(faltan)} eventos sin "
                  f"muestras → recuperados "
                  f"{sum(1 for e in faltan if e['partido'] in qx)}")
        except Exception as exc:  # noqa: BLE001
            print("[universo] aviso: relleno API falló:", exc)

    universo_ok = []
    sin_datos = 0
    for e in entradas.values():
        datos = qx.get(e["partido"])
        if datos is None:
            sin_datos += 1
            continue
        e["q1"], e["q2"], e["final"] = datos
        universo_ok.append(e)
    print(f"[universo] eventos con línea de descanso: {len(entradas)} · "
          f"con final+cuartos: {len(universo_ok)} · descartados sin datos: "
          f"{sin_datos}")
    if len(universo_ok) < 100:
        print("[universo] AVISO: menos de 100 partidos — muestra pequeña "
              "(se continúa igual, como se pidió).")
    return universo_ok


def modelos_previos() -> dict:
    """Modelo OLS por liga entrenado SOLO con muestras anteriores a hoy."""
    filas = _q("SELECT liga, q1, q2, total FROM muestras WHERE fecha < %s",
               (HOY,))
    por: dict[str, list] = defaultdict(list)
    for liga, q1, q2, tot in filas:
        por[liga].append((float(q1), float(q2), float(tot)))
    salida = {}
    for liga, datos in por.items():
        if len(datos) < MIN_MUESTRAS_MODELO:
            continue
        m = motor._ajustar(datos, liga, "diagnostico_edge (solo pasado)")
        if m and m.n and not m.errores:
            salida[liga] = m
    return salida


def universo_senales() -> list[dict]:
    """Todas las señales de `señales` con total_final (fuente principal)."""
    filas = _q("""SELECT evento_id, liga, partido, momento, q1, q2,
                         linea_entrada, pred_modelo, delta, retraso_seg,
                         modelo_tipo, linea_3q_5min, linea_cierre,
                         total_final
                    FROM señales
                   WHERE total_final IS NOT NULL
                     AND linea_entrada IS NOT NULL
                     AND pred_modelo IS NOT NULL
                   ORDER BY momento""")
    return [{"evento_id": f[0], "liga": f[1], "partido": f[2], "momento": f[3],
             "q1": float(f[4]), "q2": float(f[5]), "linea": float(f[6]),
             "pred": float(f[7]),
             "delta": (float(f[7]) - float(f[6])) if f[8] is None
             else float(f[8]),
             "retraso": f[9], "modelo_tipo": f[10], "linea_3q": f[11],
             "linea_cierre": f[12], "final": float(f[13])}
            for f in filas]


def pares_12() -> tuple[list[tuple], str, str]:
    """[(línea, final, pred)] para las pruebas 1-2 + fuente + nota."""
    uni_sen = universo_senales()
    if len(uni_sen) >= N_MIN_P12:
        pares = [(e["linea"], e["final"], e["pred"]) for e in uni_sen]
        return pares, "señales", ""
    uni = universo()
    modelos = modelos_previos()
    pares = []
    sin_modelo = 0
    for e in uni:
        m = modelos.get(e["liga"])
        if m is None:
            sin_modelo += 1
            continue
        pred = m.predecir_dos(e["q1"], e["q2"])
        pares.append((e["linea"], e["final"], pred))
    nota = (f"RESPALDO cuotas_historial: `señales` tiene "
            f"{len(uni_sen)} filas utilizables (<{N_MIN_P12}). "
            f"Juegos sin modelo de liga excluidos: {sin_modelo}.")
    return pares, "cuotas_historial (respaldo)", nota


# --------------------------------------------------------------------------- #
#  PRUEBA 1
# --------------------------------------------------------------------------- #
def prueba1(pares: list[tuple], fuente: str, nota: str) -> None:
    print("\n" + "=" * 78)
    print("PRUEBA 1 — ¿Quién predice mejor el total: modelo o línea?")
    print("=" * 78)
    print(f"Fuente: {fuente}" + (f"  ·  {nota}" if nota else ""))
    n = len(pares)
    print(f"Juegos de la prueba: {n}")
    if n < 5:
        print("Muestra insuficiente para la prueba 1.")
        return

    e_line = np.array([final - linea for linea, final, _p in pares])
    e_mod = np.array([final - pred for _l, final, pred in pares])

    def rmse(x):
        return float(np.sqrt(np.mean(x * x)))

    def mae(x):
        return float(np.mean(np.abs(x)))

    difs = np.empty(N_BOOT) if n >= 2 else np.array([0.0])
    if n >= 2:
        for i in range(N_BOOT):
            idx = RNG.integers(0, n, n)
            difs[i] = rmse(e_mod[idx]) - rmse(e_line[idx])
    dl, du = float(np.percentile(difs, 2.5)), float(np.percentile(difs, 97.5))
    diff = rmse(e_mod) - rmse(e_line)
    sesgo_l, l1, l2 = _ic_boot(e_line)
    sesgo_m, m1, m2 = _ic_boot(e_mod)

    X = np.column_stack([np.ones(n), [p[0] for p in pares],
                         [p[2] - p[0] for p in pares]])
    y = np.array([p[1] for p in pares])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = float(resid @ resid) / (n - 3) if n > 3 else float("nan")
    try:
        se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    except np.linalg.LinAlgError:
        se = np.full(3, float("nan"))

    print(f"\n{'métrica':34s} {'valor':>10s}")
    print(f"{'RMSE línea':34s} {rmse(e_line):10.2f}")
    print(f"{'RMSE modelo':34s} {rmse(e_mod):10.2f}")
    print(f"{'MAE línea':34s} {mae(e_line):10.2f}")
    print(f"{'MAE modelo':34s} {mae(e_mod):10.2f}")
    print(f"\nDiferencia RMSE (modelo − línea): "
          + _fmt_ic(n, diff, dl, du))
    print("Sesgo medio total − línea : " + _fmt_ic(n, sesgo_l, l1, l2))
    print("Sesgo medio total − modelo: " + _fmt_ic(n, sesgo_m, m1, m2))
    print(f"\nRegresión total ~ a + b·línea + c·(pred−línea):")
    print(f"   a={beta[0]:+.2f} (se {se[0]:.2f})  b={beta[1]:+.3f} "
          f"(se {se[1]:.3f})  c={beta[2]:+.3f} (se {se[2]:.3f})  [n={n}]")
    if n < N_MIN_P12:
        v1 = (f"INCONCLUSA POR MUESTRA (se requieren n≥{N_MIN_P12} para "
              f"concluir; n={n})")
    elif dl > 0:
        v1 = "CONFIRMADA (la línea tiene menos RMSE; el IC excluye 0)"
    elif du < 0:
        v1 = "RECHAZADA (el modelo tiene menos RMSE; el IC excluye 0)"
    else:
        v1 = "INCONCLUSA (el IC del diferencial incluye 0)"
    print(f"H1 (RMSE línea ≤ modelo): {v1}")


# --------------------------------------------------------------------------- #
#  PRUEBA 2
# --------------------------------------------------------------------------- #
def _slope(delta, y):
    n = len(delta)
    X = np.column_stack([np.ones(n), delta])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid ** 2).sum()) / ss_tot if ss_tot else 0.0
    if n < 2:
        return beta[1], beta[1], beta[1], r2, n
    ests = np.empty(N_BOOT)
    for i in range(N_BOOT):
        idx = RNG.integers(0, n, n)
        b, *_ = np.linalg.lstsq(X[idx], y[idx], rcond=None)
        ests[i] = b[1]
    return beta[1], float(np.percentile(ests, 2.5)), \
        float(np.percentile(ests, 97.5)), r2, n


def _veredicto_h2(b, b1, b2, n) -> str:
    if n < N_MIN_P12:
        return (f"INCONCLUSA POR MUESTRA (se requieren n≥{N_MIN_P12}; n={n})")
    if b1 <= 0 <= b2:
        return ("CONFIRMADA: el IC incluye 0 → sin información lineal "
                "medible en Δ")
    if b2 < 0:
        return "RECHAZADA: pendiente NEGATIVA (IC completo por debajo de 0)"
    if b1 > 0 and b2 < 1:
        return ("RECHAZADA: información PARCIAL (IC dentro de (0,1))")
    if b2 >= 1 and b1 > 0:
        return ("RECHAZADA: el IC alcanza/excede 1 → compatible con "
                "información total")
    return "INCONCLUSA (IC mixto)"


def prueba2(pares: list[tuple], fuente: str, nota: str) -> None:
    print("\n" + "=" * 78)
    print("PRUEBA 2 — ¿El Δ (=pred−línea) predice el resultado (final−línea)?")
    print("=" * 78)
    print(f"Fuente: {fuente}" + (f"  ·  {nota}" if nota else ""))
    datos = [(pred - linea, final - linea) for linea, final, pred in pares]
    if len(datos) < 5:
        print("Muestra insuficiente.")
        return
    for nombre, sel in (("TODAS las señales", datos),
                        ("|Δ| ≥ 2", [d for d in datos if abs(d[0]) >= 2])):
        if len(sel) < 5:
            print(f"\n{nombre}: n={len(sel)} — muestra insuficiente.")
            continue
        delta = np.array([d[0] for d in sel])
        y = np.array([d[1] for d in sel])
        b, b1, b2, r2, n = _slope(delta, y)
        linea_ic = _fmt_ic(n, b, b1, b2)
        print(f"\n{nombre}: n={n}  pendiente={linea_ic}  R²={r2:.4f}")
        print(f"H2 ({nombre}): {_veredicto_h2(b, b1, b2, n)}")

    sel = [d for d in datos if abs(d[0]) >= 2]
    if len(sel) >= 5:
        g = p = push = 0
        for d, y in sel:
            if abs(y) < 1e-9:
                push += 1
                continue
            g += ((y > 0) == (d > 0))
            p += ((y > 0) != (d > 0))
        pr, w1, w2 = _wilson(g, g + p)
        n = g + p
        if n < N_MIN_P12:
            ic_txt = "IC (Wilson) no fiable (n pequeño)"
        else:
            ic_txt = f"IC95% [{w1 * 100:.1f}%, {w2 * 100:.1f}%]"
        print(f"\nAciertos del lado (|Δ|≥2, sin pushes): {g}/{n} = "
              f"{pr * 100:.1f}%  {ic_txt}  ·  pushes={push} · n total="
              f"{len(sel)}")
        if n < N_MIN_P12:
            cmp_txt = "muestra insuficiente para comparar con la referencia"
        elif w1 * 100 > BREAKEVEN:
            cmp_txt = "IC por encima de la referencia"
        elif w2 * 100 < BREAKEVEN:
            cmp_txt = "IC por debajo de la referencia"
        else:
            cmp_txt = "el IC cruza la referencia"
        print(f"Referencia pedida a cuota ~1.8 (breakeven): {BREAKEVEN:.1f}% "
              f"→ {cmp_txt}")


# --------------------------------------------------------------------------- #
#  PRUEBA 3 — CLV
# --------------------------------------------------------------------------- #
def _clv_tramos(filas: list[dict]) -> None:
    tramos = {"<30s": [], "30-120s": [], ">120s": [], "sin dato": []}
    for f in filas:
        r = f["retraso"]
        if r is None:
            tramos["sin dato"].append(f["clv"])
        elif r < 30:
            tramos["<30s"].append(f["clv"])
        elif r <= 120:
            tramos["30-120s"].append(f["clv"])
        else:
            tramos[">120s"].append(f["clv"])
    for k, vals in tramos.items():
        if not vals:
            print(f"  {k:9s} n=0")
        elif len(vals) < N_MIN_P12:
            print(f"  {k:9s} n={len(vals)} — IC no fiable (n pequeño): "
                  f"sin conclusión")
        else:
            e, a, b = _ic_boot(np.array(vals))
            n = len(vals)
            if n < N_MIN_CLV:
                ver = f"INCONCLUSA POR MUESTRA (se requieren n≥{N_MIN_CLV})"
            elif a <= 0 <= b:
                ver = "no se puede rechazar CLV=0"
            else:
                ver = "CLV distinto de 0 (IC excluye 0)"
            print(f"  {k:9s} n={n}  CLV medio {e:+.2f}  "
                  + _fmt_ic(n, e, a, b, etiqueta="IC95%") + f"  → {ver}")


def prueba3() -> None:
    print("\n" + "=" * 78)
    print("PRUEBA 3 — CLV (cierre principal: linea_3q_5min)")
    print("=" * 78)
    print("Cierre principal = línea registrada a los 5 min de juego del 3Q "
          "(ventana 4–6 min; sin aproximar si no hay fila). "
          "CLV a favor de nuestro lado: lado = signo de Δ "
          "(OVER si Δ>0).")

    filas = _q("""SELECT delta, linea_entrada, linea_3q_5min, linea_cierre,
                         retraso_seg
                    FROM señales
                   WHERE linea_3q_5min IS NOT NULL
                     AND linea_entrada IS NOT NULL
                     AND delta IS NOT NULL""")
    principal = []
    for delta, le, l3, lc, ret in filas:
        le, l3 = float(le), float(l3)
        clv = (l3 - le) if float(delta) >= 0 else (le - l3)
        principal.append({"clv": clv, "retraso": ret})
    n = len(principal)
    print(f"\nFuente: señales (linea_3q_5min) · n={n}")
    if n == 0:
        print("Sin señales con cierre 3q_5min todavía → se usa el RESPALDO "
              "(apuestas + cuotas_historial) solo como referencia:")
        principal = _clv_respaldo()
        n = len(principal)
        if n == 0:
            print("H3: INCONCLUSA POR MUESTRA (no hay cierres medibles).")
            return
    clvs = np.array([f["clv"] for f in principal])
    est, a, b = _ic_boot(clvs)
    print("CLV medio: " + _fmt_ic(n, est, a, b))
    if n < N_MIN_CLV:
        v3 = (f"INCONCLUSA POR MUESTRA (se requieren n≥{N_MIN_CLV}; n={n})")
    elif a <= 0 <= b:
        v3 = "CONFIRMADA (no se puede rechazar CLV=0)"
    else:
        v3 = "RECHAZADA (el IC excluye 0)"
    print(f"H3 (CLV medio = 0): {v3}")
    print("\nPor tramo de retraso:")
    _clv_tramos(principal)

    # Referencia: CLV contra la ÚLTIMA línea del descanso (se espera ≈0
    # porque la línea se congela; por eso NO es el cierre principal).
    ref = _q("""SELECT delta, linea_entrada, linea_cierre
                  FROM señales
                 WHERE linea_cierre IS NOT NULL
                   AND linea_entrada IS NOT NULL
                   AND delta IS NOT NULL""")
    if ref:
        vals = []
        for delta, le, lc in ref:
            le, lc = float(le), float(lc)
            vals.append((lc - le) if float(delta) >= 0 else (le - lc))
        e2, a2, b2 = _ic_boot(np.array(vals))
        print(f"\nReferencia (esperado ≈0 por congelación del descanso): "
              f"CLV vs linea_cierre: " + _fmt_ic(len(vals), e2, a2, b2))


def _clv_respaldo() -> list[dict]:
    """CLV de respaldo con apuestas + cuotas_historial (método anterior)."""
    print("Definición de respaldo: entrada = apuestas.linea; cierre = última "
          "fila marcada en descanso, o la última línea antes de la primera "
          "fila de 3Q/4T/prórroga; sin cierre → se excluye.")
    aps = _q("""SELECT id, evento_id, fecha, seleccion, linea, nota
                  FROM apuestas ORDER BY id""")
    filas = []
    sin_cierre = 0
    for aid, ev, fecha, sel, linea, nota in aps:
        rows = _q("""SELECT id, momento, linea, en_descanso, period_id
                       FROM cuotas_historial WHERE evento_id = %s
                      ORDER BY id""", (ev,))
        q3_ids = [r[0] for r in rows
                  if (r[4] or "") in ("QUARTER3", "QUARTER4", "OVERTIME",
                                      "PERIOD3", "PERIOD4")]
        q3_first = min(q3_ids) if q3_ids else None
        cands = [r for r in rows if r[2] is not None and r[1] >= fecha]
        cierre = None
        marcadas = [r for r in cands if r[3]]
        if marcadas:
            cierre = float(marcadas[-1][2])
        else:
            antes_q3 = [r for r in cands
                        if q3_first is not None and r[0] < q3_first]
            if antes_q3:
                cierre = float(antes_q3[-1][2])
        if cierre is None:
            sin_cierre += 1
            continue
        clv = (cierre - linea) if sel == "OVER" else (linea - cierre)
        m = _RE_RET.search(nota or "")
        filas.append({"clv": clv, "retraso": int(m.group(1)) if m else None})
    print(f"[respaldo] apuestas={len(aps)} · con cierre medible={len(filas)} "
          f"· excluidas sin cierre={sin_cierre}")
    return filas


# --------------------------------------------------------------------------- #
#  PRUEBA 4 (informativa) — sesgo por tipo de modelo
# --------------------------------------------------------------------------- #
def prueba4() -> None:
    print("\n" + "=" * 78)
    print("PRUEBA 4 (informativa) — Sesgo (total_final − linea_entrada) por "
          "tipo de modelo")
    print("=" * 78)
    filas = _q("""SELECT modelo_tipo, total_final, linea_entrada
                    FROM señales
                   WHERE total_final IS NOT NULL
                     AND linea_entrada IS NOT NULL""")
    grupos: dict[str, list] = {"propio": [], "respaldo": []}
    for tipo, tot, le in filas:
        g = "respaldo" if (tipo or "").lower().startswith("respald") else "propio"
        grupos[g].append(float(tot) - float(le))
    total_sen = sum(len(v) for v in grupos.values())
    if total_sen < N_MIN_P12:
        print(f"Fuente señales: n={total_sen} (<{N_MIN_P12}) → RESPALDO desde "
              f"apuestas (tipo: respaldo = liga_modelo EUROLIGA):")
        filas2 = _q("""SELECT liga_modelo, total_final, linea
                         FROM apuestas WHERE total_final IS NOT NULL""")
        grupos = {"propio": [], "respaldo": []}
        for liga, tot, le in filas2:
            g = "respaldo" if liga == "EUROLIGA" else "propio"
            grupos[g].append(float(tot) - float(le))
    for g in ("propio", "respaldo"):
        v = grupos.get(g, [])
        n = len(v)
        if not n:
            print(f"  {g:9s} n=0")
            continue
        e, a, b = _ic_boot(np.array(v))
        print(f"  {g:9s} " + _fmt_ic(n, e, a, b))
    print("(Informativa: no se contrasta ninguna hipótesis nueva.)")


# --------------------------------------------------------------------------- #
#  Estado de la infraestructura de señales
# --------------------------------------------------------------------------- #
def estado_senales() -> None:
    print("\n" + "=" * 78)
    print("REGISTRO DE SEÑALES — estado de la infraestructura")
    print("=" * 78)
    print("Tabla `señales` creada en base_datos.py (ESQUEMA_EXTRAS) junto con")
    print("guardar_senal(), vincular_apuesta() y cerrar_senales(). La conexión")
    print("en app.py está PENDIENTE de aprobación (diff mostrado aparte).")
    print("Mientras la app en marcha no se reinicie con el código nuevo, la")
    print("tabla seguirá vacía y las pruebas usarán el respaldo de")
    print("cuotas_historial (se indica en cada prueba).")


def main() -> int:
    paso0()
    pares, fuente, nota = pares_12()
    prueba1(pares, fuente, nota)
    prueba2(pares, fuente, nota)
    prueba3()
    prueba4()
    estado_senales()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
