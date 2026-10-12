# -*- coding: utf-8 -*-
"""
backtest.py — ¿Cómo nos habría ido en 2026 con la estrategia del descanso?

ESTRATEGIA BAJO PRUEBA (la misma que ejecuta la app en automático):

    En el descanso 2Q→3Q (1Q y 2Q finales), predecir el total con el modelo
    MCO de la liga y apostar OVER/UNDER si |predicción − línea| ≥ umbral.

LÍNEA (proxy del mercado)
-------------------------
La API-Sports no publica histórico de cuotas, así que hay que proxyar la
línea que la casa pondría en el descanso. Hay DOS modos:

  * `mitad` (POR DEFECTO, el honesto): línea = (1Q + 2Q) × 2. Es la
    proyección de ritmo que usa una casa real al descanso: ya conoce el
    primer tiempo, así que su línea se mueve con el partido. Vencerla
    exige que el MCO aporte algo que la proyección simple no sabe.

  * `media`: línea = media del total de la liga en días anteriores. Es un
    mercado que NO se entera del partido; sirve solo para medir cuánta
    información tiene el 1Q+2Q por sí solo (inflado: no es una casa real).

WALK-FORWARD (sin mirar el futuro)
----------------------------------
Para cada liga, los partidos se recorren ordenados por fecha. Para el partido
de hoy:

  1. Se entrena el modelo MCO solo con los partidos de días ANTERIORES
     (ventana expansiva: entrena con todo el pasado, nunca con el futuro ni
     con partidos del mismo día).
  2. Se predice el total del partido usando SU 1Q y SU 2Q (lo que se sabe en
     el descanso).
  3. Δ = predicción − línea proxy. Si |Δ| ≥ umbral → apuesta.
  4. Se gana si el total final cae del mismo lado de la línea que Δ.

Así el modelo nunca "ve" el partido que está evaluando: el overfitting quedó
fuera por construcción.

USO:
    python backtest.py                       # 2026, umbral 2.0, línea honesta
    python backtest.py --linea media         # proxy inflado (solo informativo)
    python backtest.py --umbral 3 --ligas nba euroleague
    python backtest.py --umbrales 0 2 3 5    # comparar varios umbrales
"""

from __future__ import annotations

import argparse
from datetime import date

import base_datos
import motor

MINIMO_ENTRENAMIENTO = 30     # partidos de días anteriores mínimos para entrenar
UMBRAL_POR_DEFECTO = 2.0      # Δ mínimo en puntos para apostar
CUOTA_PROXY = 1.85            # cuota típica usada para el ROI orientativo
LINEA_POR_DEFECTO = "mitad"   # "mitad" (honesto) | "media" (inflado, informativo)

# Aviso que se imprime al usar la línea "mitad": el edge medido procede de la
# reversión a la media entre mitades, y una casa real ya la pricea.
_AVISO_REVERSION = """\
AVISO: el edge medido aquí viene sobre todo de la REVERSIÓN A LA MEDIA entre
       mitades (ver diagnostico_backtest.py): con 1º tiempo bajo el 2º anota
       más el 76 % de las veces, y con 1º tiempo alto el 2º anota menos el
       71 %. El MCO la detecta, pero una casa real TAMBIÉN la conoce, así que
       su línea del descanso ya la incorpora. El winrate real contra BetPlay
       sería bastante menor que el de esta tabla."""


def _cargar_historico() -> dict[str, list[tuple]]:
    """
    {(liga): [(fecha, q1, q2, total), ...]} de TODAS las muestras, ordenado
    por fecha (y por id para desempatar el mismo día).

    Se carga entero una sola vez: después el walk-forward avanza en memoria
    (una consulta a la BD por liga, no por partido).
    """
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT liga, fecha, q1, q2, total FROM muestras
                   WHERE fecha IS NOT NULL
                   ORDER BY liga, fecha, id""")
            filas = cur.fetchall()
    finally:
        conn.close()

    por_liga: dict[str, list[tuple]] = {}
    for liga, fecha, q1, q2, total in filas:
        por_liga.setdefault(liga, []).append((fecha, q1, q2, total))
    return por_liga


def _backtest_liga(partidos: list[tuple], desde: date, hasta: date,
                   umbral: float, verbose: bool,
                   modo_linea: str = LINEA_POR_DEFECTO) -> dict | None:
    """
    Walk-forward de una liga. Devuelve el resumen, o None si no hay apuestas
    evaluables (historia insuficiente o sin partidos en el rango).

    `modo_linea` decide con qué se compara la predicción:
      * "mitad": (1Q+2Q)×2, la proyección de ritmo que usa una casa real en
        el descanso (la línea se mueve con el partido -> test honesto).
      * "media": media del total de la liga en días anteriores (mercado que
        no se entera del partido -> inflado, solo informativo).
    """
    evaluados = apuestas = 0
    ganadas = perdidas = empatadas = 0
    aciertos_naive = naive_decididos = 0

    modelo = None
    dias_desde_ultimo = 0          # días nuevos desde el último reentreno
    suma_entreno = 0.0             # suma de totales del bloque de entrenamiento
    n_entreno = 0                  # partidos ya incorporados al entrenamiento
    fecha_entreno = None           # fecha del último día ya incorporado

    for fecha, q1, q2, total in partidos:
        # --- incorporar al entrenamiento los días ya cerrados ---------------
        if fecha_entreno is None:
            fecha_entreno = fecha
        while fecha_entreno < fecha:
            # los partidos de fecha_entreno pasan a ser "pasado" (una vez)
            for f2, _q1b, _q2b, tot2 in partidos:
                if f2 == fecha_entreno:
                    suma_entreno += tot2
                    n_entreno += 1
            fecha_entreno = _siguiente_dia(fecha_entreno, partidos)
            dias_desde_ultimo += 1

        # --- ¿evaluar este partido? ----------------------------------------
        if not (desde <= fecha <= hasta):
            continue

        # --- línea proxy del mercado ---------------------------------------
        if modo_linea == "mitad":
            # proyección de ritmo del descanso (lo que ya sabe la casa)
            linea = (q1 + q2) * 2.0
        else:
            if n_entreno < MINIMO_ENTRENAMIENTO:
                continue              # aún no hay historia suficiente
            linea = suma_entreno / n_entreno
        if not linea:
            continue

        # reentrenar el MCO cada SALTO días nuevos (o al principio)
        if modelo is None or dias_desde_ultimo >= SALTO_REENTRENO:
            datos = [(a, b, c) for f, a, b, c in partidos if f < fecha]
            try:
                modelo = motor._ajustar(datos, "backtest", "walk-forward")
            except Exception:  # noqa: BLE001 (datos previos insuficientes)
                modelo = None
            dias_desde_ultimo = 0
            if modelo is not None and (modelo.errores or modelo.n == 0):
                modelo = None
        if modelo is None:
            continue

        evaluados += 1
        pred = modelo.predecir_dos(q1, q2)
        delta = pred - linea

        # referencia: ¿acertaría el naïve (1Q × 4) del mismo lado?
        naive = q1 * 4.0 - linea
        if naive != 0:
            naive_decididos += 1
            if (naive > 0) == (total > linea):
                aciertos_naive += 1

        if abs(delta) < umbral:
            continue                      # sin apuesta: diferencia pequeña
        apuestas += 1
        if total == linea:
            empatadas += 1                # push: no decide
        elif (delta > 0) == (total > linea):
            ganadas += 1
        else:
            perdidas += 1

        if verbose:
            veredicto = "OVER" if delta > 0 else "UNDER"
            acierto = ("EMPATE" if total == linea else
                       ("GANADA" if (delta > 0) == (total > linea)
                        else "PERDIDA"))
            print(f"   [{fecha}]  pred={pred:6.1f} línea={linea:6.1f} "
                  f"Δ={delta:+5.1f} → {veredicto:5s} total={total:5.1f}  {acierto}")

    if not evaluados:
        return None

    decididas = ganadas + perdidas
    winrate = (ganadas / decididas * 100.0) if decididas else 0.0
    naive_wr = (aciertos_naive / naive_decididos * 100.0) if naive_decididos else 0.0
    roi = ((winrate / 100.0) * (CUOTA_PROXY - 1.0)
           - (1.0 - winrate / 100.0)) * 100.0 if decididas else 0.0

    return {"evaluados": evaluados, "apuestas": apuestas,
            "ganadas": ganadas, "perdidas": perdidas, "empatadas": empatadas,
            "winrate": winrate, "naive_wr": naive_wr, "roi": roi}


def _siguiente_dia(fecha, partidos):
    """Siguiente fecha distinta presente en la liga (o la misma si no hay)."""
    for f, *_ in partidos:
        if f > fecha:
            return f
    return fecha


SALTO_REENTRENO = 10    # días de historia nuevos entre reentrenamientos


def backtest(desde: date, hasta: date, umbral: float = UMBRAL_POR_DEFECTO,
             ligas: list[str] | None = None,
             verbose: bool = False,
             modo_linea: str = LINEA_POR_DEFECTO) -> dict[str, dict]:
    """Ejecuta el walk-forward y devuelve el resultado por liga."""
    historico = _cargar_historico()
    resultados: dict[str, dict] = {}
    for liga, partidos in sorted(historico.items()):
        if ligas and liga not in ligas:
            continue
        r = _backtest_liga(partidos, desde, hasta, umbral, verbose and not ligas,
                           modo_linea)
        if r:
            resultados[liga] = r
    return resultados


def imprimir(resultados: dict[str, dict], desde: date, hasta: date,
             umbral: float, modo_linea: str = LINEA_POR_DEFECTO) -> None:
    print(f"Backtest WALK-FORWARD  ·  {desde} → {hasta}  ·  umbral Δ ≥ {umbral} pts")
    if modo_linea == "mitad":
        print("Línea proxy = (1Q+2Q)×2, la proyección de ritmo que usa una casa "
              "real en el descanso.")
        print("(La línea se mueve con el partido: vencerla exige que el MCO "
              "aporte de verdad.)")
        print(_AVISO_REVERSION)
    else:
        print("Línea proxy = media del total de la liga en días ANTERIORES "
              "(mercado que no se entera del partido: INFLADO, solo informativo).")
    print("El modelo de cada partido se entrenó SOLO con partidos de días "
          "anteriores.\n")

    tot = {"evaluados": 0, "apuestas": 0, "ganadas": 0, "perdidas": 0,
           "empatadas": 0}
    cab = (f"{'liga':30s} {'eval':>6s} {'apuestas':>8s} {'gan':>5s} "
           f"{'perd':>5s} {'emp':>4s} {'WINRATE':>9s} {'naive':>7s}")
    print(cab)
    print("-" * len(cab))
    for liga, r in sorted(resultados.items(), key=lambda kv: -kv[1]["winrate"]):
        for k in tot:
            tot[k] += r[k]
        print(f"{liga:30s} {r['evaluados']:6d} {r['apuestas']:8d} "
              f"{r['ganadas']:5d} {r['perdidas']:5d} {r['empatadas']:4d} "
              f"{r['winrate']:8.1f}% {r['naive_wr']:6.1f}%")

    decididas = tot["ganadas"] + tot["perdidas"]
    wr = (tot["ganadas"] / decididas * 100.0) if decididas else 0.0
    print("-" * len(cab))
    print(f"{'TOTAL':30s} {tot['evaluados']:6d} {tot['apuestas']:8d} "
          f"{tot['ganadas']:5d} {tot['perdidas']:5d} {tot['empatadas']:4d} "
          f"{wr:8.1f}%")
    if decididas:
        roi = (wr / 100.0 * (CUOTA_PROXY - 1.0) - (1.0 - wr / 100.0)) * 100.0
        print(f"\nWINRATE TOTAL: {wr:.1f}%  sobre {decididas} apuestas decididas "
              f"(+{tot['empatadas']} empatadas sin contar)")
        print(f"ROI orientativo a cuota {CUOTA_PROXY:.2f}: {roi:+.1f} %")


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest walk-forward de la "
                                            "estrategia del descanso 2Q→3Q.")
    p.add_argument("--desde", default="2026-01-01",
                   help="fecha inicial de evaluación (por defecto 2026-01-01)")
    p.add_argument("--hasta", default="2026-12-31",
                   help="fecha final de evaluación (por defecto 2026-12-31)")
    p.add_argument("--umbral", type=float, default=UMBRAL_POR_DEFECTO,
                   help=f"Δ mínimo en puntos para apostar (por defecto "
                        f"{UMBRAL_POR_DEFECTO})")
    p.add_argument("--umbrales", nargs="*", type=float, default=None,
                   help="compara varios umbrales (p. ej. --umbrales 0 2 3 5)")
    p.add_argument("--linea", choices=("mitad", "media"),
                   default=LINEA_POR_DEFECTO,
                   help="proxy de la línea: 'mitad' = (1Q+2Q)×2 (honesto, por "
                        "defecto); 'media' = media de la liga (inflado)")
    p.add_argument("--ligas", nargs="*", default=None,
                   help="solo estas ligas (por defecto todas)")
    p.add_argument("--detallado", action="store_true",
                   help="imprimir cada apuesta")
    args = p.parse_args()

    desde = date.fromisoformat(args.desde)
    hasta = date.fromisoformat(args.hasta)

    umbrales = args.umbrales or [args.umbral]
    todos: dict[float, dict[str, dict]] = {}
    for u in umbrales:
        todos[u] = backtest(desde, hasta, u, args.ligas, args.detallado,
                            args.linea)
        if not todos[u]:
            print("No hay partidos en ese rango de fechas.")
            return 1

    if len(umbrales) == 1:
        imprimir(todos[umbrales[0]], desde, hasta, umbrales[0], args.linea)
        return 0

    # comparación de umbrales: el objetivo es elegir cuánto Δ exigir
    print(f"Comparación de umbrales  ·  {desde} → {hasta}\n")
    cab = (f"{'umbral':>7s} {'apuestas':>8s} {'gan':>5s} {'perd':>5s} "
           f"{'WINRATE':>9s} {'ROI':>7s}")
    print(cab)
    print("-" * len(cab))
    for u in umbrales:
        por_liga = todos[u]
        tot = {k: sum(r[k] for r in por_liga.values())
               for k in ("apuestas", "ganadas", "perdidas")}
        dec = tot["ganadas"] + tot["perdidas"]
        wr = (tot["ganadas"] / dec * 100.0) if dec else 0.0
        roi = ((wr / 100.0) * (CUOTA_PROXY - 1.0)
               - (1.0 - wr / 100.0)) * 100.0 if dec else 0.0
        print(f"{u:7.1f} {tot['apuestas']:8d} {tot['ganadas']:5d} "
              f"{tot['perdidas']:5d} {wr:8.1f}% {roi:+6.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
