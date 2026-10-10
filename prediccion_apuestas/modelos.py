# -*- coding: utf-8 -*-
"""
modelos.py — Un modelo MCO por liga, entrenado con partidos reales acabados.

Mismas fórmulas que el Excel (motor._ajustar):

    TOTAL = b0 + b1·1Q + b2·2Q        (R², RMSE y todo igual)

Pero aquí el modelo se ajusta con las `muestras` de cada liga y se guarda en
PostgreSQL (tablas `muestras` y `modelos_liga`), de forma que:

  * las ligas con archivo oficial (Euroliga, EuroCup, NBA, WNBA, NBL) salen
    entrenadas de una vez (ver historico.py);
  * el resto de ligas se entrenan SOLAS a medida que la pestaña EN VIVO va
    registrando partidos acabados con sus cuartos.

Se añade una mejora sobre el Excel: el RMSE que se guarda (`rmse_test_*`) se
calcula con **holdout** (80 % ajusta / 20 % valida) en vez de sobre la propia
muestra, que es como se comportará de verdad con partidos nuevos.

Uso:
    python modelos.py --resumen
    python modelos.py --entrenar --minimo 30
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Iterable, Optional

import psycopg2
from psycopg2 import extras

import base_datos
import motor
from historico import Muestra

MINIMO_MUESTRAS = 30        # mínimo para publicar un modelo de una liga
FRACCION_TEST = 0.2         # 20 % de las muestras se reserva para validar


class ErrorModelos(Exception):
    """Error entrenando o guardando modelos."""


# --------------------------------------------------------------------------- #
#  Muestras en PostgreSQL                                                      #
# --------------------------------------------------------------------------- #
def guardar_muestras(muestras: Iterable[Muestra]) -> int:
    """
    Guarda muestras en la tabla `muestras`. Devuelve las que eran NUEVAS
    (las repetidas no se cuentan: `evento_clave` es único).
    """
    filas = [(m.liga, m.fecha, m.q1, m.q2, m.q3, m.q4, m.total,
              m.partido, m.fuente, m.clave) for m in muestras]
    if not filas:
        return 0
    base_datos.crear_esquema()
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                # RETURNING + fetch=True: execute_values devuelve las filas que
                # se insertaron de verdad (rowcount solo daría la última página)
                insertadas = extras.execute_values(
                    cur,
                    """INSERT INTO muestras
                       (liga, fecha, q1, q2, q3, q4, total, partido, fuente, evento_clave)
                       VALUES %s ON CONFLICT (evento_clave) DO NOTHING
                       RETURNING evento_clave""",
                    filas, page_size=500, fetch=True)
                return len(insertadas or ())
    finally:
        conn.close()


def contar_muestras(liga: Optional[str] = None) -> dict[str, int]:
    """Muestras por liga (o total de una liga concreta)."""
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                if liga:
                    cur.execute("SELECT count(*) FROM muestras WHERE liga = %s;",
                                (liga,))
                    return {liga: cur.fetchone()[0]}
                cur.execute("SELECT liga, count(*) FROM muestras "
                            "GROUP BY liga ORDER BY count(*) DESC;")
                return dict(cur.fetchall())
    finally:
        conn.close()


def leer_muestras(liga: str) -> list[tuple[float, float, float]]:
    """Muestras de una liga en el formato que espera motor._ajustar."""
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT q1, q2, total FROM muestras WHERE liga = %s "
                            "ORDER BY fecha, id;", (liga,))
                return [(float(a), float(b), float(c)) for a, b, c in cur.fetchall()]
    finally:
        conn.close()


def primer_ultimo(liga: str) -> tuple[Optional[date], Optional[date]]:
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT min(fecha), max(fecha) FROM muestras "
                            "WHERE liga = %s;", (liga,))
                return cur.fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Entrenamiento                                                               #
# --------------------------------------------------------------------------- #
def _entrenar_liga(liga: str, datos: list[tuple[float, float, float]],
                   fuente: str, minimo: int = MINIMO_MUESTRAS) -> Optional[motor.Modelo]:
    """
    Ajusta el modelo MCO de una liga y calcula el RMSE con holdout.

    Con menos de `minimo` muestras no se publica el modelo (devuelve None): con
    pocas muestras el ajuste se memoriza el ruido y predice peor que el modelo
    general de Euroliga/NBA.
    """
    if len(datos) < minimo:
        return None

    # --- holdout: el 20 % más reciente valida (más honesto que aleatorio) ---
    datos = list(datos)
    corte = max(minimo, int(len(datos) * (1 - FRACCION_TEST)))
    ajuste, test = datos[:corte], datos[corte:]
    if not test:
        ajuste, test = datos, datos

    m = motor._ajustar(ajuste, liga, fuente)
    if m.n == 0 or m.errores:
        return None

    # RMSE del modelo en la muestra de validación (así se comportará de verdad)
    import math
    def _rmse(predecir) -> float:
        errores = [(y - predecir(x1, x2)) for x1, x2, y in test]
        return math.sqrt(sum(e * e for e in errores) / len(errores))

    m.rmse_test_dos = _rmse(lambda x1, x2: m.predecir_dos(x1, x2))
    m.rmse_test_uno = _rmse(lambda x1, _x2: m.predecir_uno(x1))
    m.rmse_test_naive = _rmse(lambda x1, _x2: x1 * 4.0)

    # R² y RMSE sobre TODA la muestra (para comparar ligas entre sí)
    completo = motor._ajustar(datos, liga, fuente)
    m.n = completo.n
    m.media_total, m.media_1q, m.media_2q = (completo.media_total,
                                             completo.media_1q, completo.media_2q)
    m.r2_dos, m.r2_uno = completo.r2_dos, completo.r2_uno
    m.rmse_dos, m.rmse_uno, m.rmse_naive = (completo.rmse_dos, completo.rmse_uno,
                                            completo.rmse_naive)
    m.fecha_min, m.fecha_max = completo.fecha_min, completo.fecha_max
    return m


def entrenar_todo(minimo: int = MINIMO_MUESTRAS,
                  ligas: Optional[list[str]] = None) -> dict[str, motor.Modelo]:
    """(Re)entrena el modelo de cada liga que tenga suficientes muestras."""
    disponibles = contar_muestras()
    entrenados: dict[str, motor.Modelo] = {}
    for liga, _n in disponibles.items():
        if ligas and liga not in ligas:
            continue
        datos = leer_muestras(liga)
        if len({(a, b) for a, b, _ in datos}) < 3:
            continue        # dos rectas no definen un plano
        fechas = primer_ultimo(liga)
        m = _entrenar_liga(liga, datos, "muestras propias", minimo)
        if m is None:
            continue
        m.fecha_min = _serial(fechas[0])
        m.fecha_max = _serial(fechas[1])
        _guardar_modelo(m)
        entrenados[liga] = m
    return entrenados


def _serial(f: Optional[date]) -> Optional[float]:
    if f is None:
        return None
    from datetime import datetime
    return (datetime(f.year, f.month, f.day) - datetime(1899, 12, 30)).days + 0.0


def _desde_serial(s: Optional[float]) -> Optional[date]:
    """Serial de Excel (como usa el motor) -> fecha."""
    if s is None:
        return None
    from datetime import datetime, timedelta
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(s))).date()
    except (TypeError, ValueError, OverflowError):
        return None


# --------------------------------------------------------------------------- #
#  Modelos en PostgreSQL                                                       #
# --------------------------------------------------------------------------- #
def _guardar_modelo(m: motor.Modelo) -> None:
    base_datos.crear_esquema()
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO modelos_liga
                       (liga, n, b0, b1, b2, a0, a1, r2_dos, r2_uno,
                        rmse_dos, rmse_uno, rmse_naive,
                        rmse_test_dos, rmse_test_uno,
                        media_total, media_1q, media_2q, fecha_min, fecha_max,
                        fuente)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (liga) DO UPDATE SET
                         n = EXCLUDED.n, b0 = EXCLUDED.b0, b1 = EXCLUDED.b1,
                         b2 = EXCLUDED.b2, a0 = EXCLUDED.a0, a1 = EXCLUDED.a1,
                         r2_dos = EXCLUDED.r2_dos, r2_uno = EXCLUDED.r2_uno,
                         rmse_dos = EXCLUDED.rmse_dos, rmse_uno = EXCLUDED.rmse_uno,
                         rmse_naive = EXCLUDED.rmse_naive,
                         rmse_test_dos = EXCLUDED.rmse_test_dos,
                         rmse_test_uno = EXCLUDED.rmse_test_uno,
                         media_total = EXCLUDED.media_total,
                         media_1q = EXCLUDED.media_1q, media_2q = EXCLUDED.media_2q,
                         fecha_min = EXCLUDED.fecha_min, fecha_max = EXCLUDED.fecha_max,
                         fuente = EXCLUDED.fuente, entrenado_en = now()""",
                    (m.liga, m.n, m.b0, m.b1, m.b2, m.a0, m.a1,
                     m.r2_dos, m.r2_uno, m.rmse_dos, m.rmse_uno, m.rmse_naive,
                     m.rmse_test_dos, m.rmse_test_uno, m.media_total,
                     m.media_1q, m.media_2q,
                     _desde_serial(m.fecha_min), _desde_serial(m.fecha_max),
                     m.origen))
    finally:
        conn.close()


def cargar_modelos() -> dict[str, motor.Modelo]:
    """Todos los modelos entrenados, por liga."""
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM modelos_liga ORDER BY liga;")
                filas = cur.fetchall()
    finally:
        conn.close()
    salida: dict[str, motor.Modelo] = {}
    for f in filas:
        m = motor.Modelo(liga=f["liga"], origen=f.get("fuente") or "muestras propias",
                         n=f["n"], b0=f["b0"], b1=f["b1"], b2=f["b2"],
                         a0=f["a0"], a1=f["a1"], r2_dos=f["r2_dos"],
                         r2_uno=f["r2_uno"], rmse_dos=f["rmse_dos"],
                         rmse_uno=f["rmse_uno"], rmse_naive=f["rmse_naive"],
                         rmse_test_dos=f["rmse_test_dos"],
                         rmse_test_uno=f["rmse_test_uno"],
                         rmse_test_naive=f["rmse_naive"],
                         media_total=f["media_total"], media_1q=f["media_1q"],
                         media_2q=f["media_2q"])
        m.fecha_min = _serial_desde(f.get("fecha_min"))
        m.fecha_max = _serial_desde(f.get("fecha_max"))
        salida[f["liga"]] = m
    return salida


def _serial_desde(f) -> Optional[float]:
    if f is None:
        return None
    from datetime import datetime
    if isinstance(f, datetime):
        d = f.date()
    else:
        d = f
    return (datetime(d.year, d.month, d.day) - datetime(1899, 12, 30)).days + 0.0


# --------------------------------------------------------------------------- #
#  Uso desde la aplicación                                                     #
# --------------------------------------------------------------------------- #
def modelo_para_liga(liga_tk: str,
                     modelos: Optional[dict[str, motor.Modelo]] = None) -> Optional[motor.Modelo]:
    """Modelo entrenado para una liga de BetPlay (por su termKey), si existe."""
    if not liga_tk:
        return None
    return (modelos or cargar_modelos()).get(liga_tk)


def borrar_todo() -> None:
    """Borra muestras y modelos (para pruebas). No toca la tabla `apuestas`."""
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE muestras, modelos_liga RESTART IDENTITY;")
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  CLI                                                                         #
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Modelos MCO por liga.")
    p.add_argument("--resumen", action="store_true", help="tabla de modelos y muestras")
    p.add_argument("--entrenar", action="store_true", help="(re)entrena todas las ligas")
    p.add_argument("--minimo", type=int, default=MINIMO_MUESTRAS,
                   help=f"muestras mínimas por liga (por defecto {MINIMO_MUESTRAS})")
    p.add_argument("--ligas", nargs="*", default=None, help="restringir a estas ligas")
    args = p.parse_args()

    if args.entrenar:
        entrenados = entrenar_todo(args.minimo, args.ligas)
        print(f"Modelos entrenados y guardados: {len(entrenados)}")

    if args.resumen or not args.entrenar:
        modelos = cargar_modelos()
        muestras = contar_muestras()
        print(f"{'liga':16s} {'muestras':>9} {'n modelo':>9} {'R2':>6} "
              f"{'RMSE test':>10} {'media':>7}  fechas")
        print("-" * 78)
        for liga in sorted(set(muestras) | set(modelos)):
            m = modelos.get(liga)
            n = muestras.get(liga, 0)
            if m:
                fechas = (f"{motor.fecha_excel_a_texto(m.fecha_min)} → "
                          f"{motor.fecha_excel_a_texto(m.fecha_max)}"
                          if m.fecha_min else "—")
                print(f"{liga:16s} {n:9d} {m.n:9d} {m.r2_dos:6.3f} "
                      f"{m.rmse_test_dos:10.1f} {m.media_total:7.1f}  {fechas}")
            else:
                print(f"{liga:16s} {n:9d} {'—':>9} {'—':>6} {'—':>10} {'—':>7}  "
                      f"(sin modelo: faltan muestras)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
