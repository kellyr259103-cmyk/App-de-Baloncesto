# -*- coding: utf-8 -*-
"""
reconciliacion.py — Cierra/corrige apuestas con el resultado OFICIAL.

Red de seguridad sobre el feed de BetPlay (Kambi), que al final de un partido
puede dar totales imprecisos (prórrogas, reloj con retraso) o eliminar el
evento sin estado final claro:

  * PENDIENTE ya vencido (el evento desapareció del feed o lleva horas):
    se busca el resultado oficial en API-Sports y se liquida.
  * Resueltas con `verificado = false`: se contrasta el total con el oficial
    y, si no coincide, se re-liquida (y se deja anotado en la nota).

Uso:
    python reconciliacion.py                # últimas 48 h
    python reconciliacion.py --horas 6
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Callable, Optional

import base_datos
import paperbet
import resultados_api as resultados


def _edad_horas(apuesta) -> float:
    if apuesta.fecha is None:
        return 0.0
    fecha = apuesta.fecha
    if fecha.tzinfo is not None:
        fecha = fecha.astimezone().replace(tzinfo=None)
    return (datetime.now() - fecha).total_seconds() / 3600.0


def reconciliar(
    horas: float = 48.0,
    minimo_edad_horas: float = 2.0,
    sin_en_feed: Optional[set[int]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Recorre las apuestas recientes y cierra/corrige con el resultado oficial.

    `sin_en_feed`: ids de eventos del ping ACTUAL. Un pendiente que ya no
    está en el feed se intenta cerrar con solo 1 hora de antigüedad (el feed
    elimina los partidos terminados, así que su ausencia es una pista fuerte).

    Devuelve cuántas apuestas se cerraron o corrigieron.
    """
    cambios = 0
    for a, verificado in base_datos.apuestas_para_reconciliar(horas):
        pendiente = a.estado == paperbet.ESTADO_PENDIENTE
        edad = _edad_horas(a)
        if pendiente:
            fuera = bool(sin_en_feed) and a.evento_id not in sin_en_feed
            if edad < minimo_edad_horas and not (fuera and edad >= 1.0):
                continue
        elif verificado:
            continue

        local, visitante = resultados.partes_de_betplay(a.partido)
        if not visitante:
            continue
        try:
            total = resultados.resultado_oficial(local, visitante, a.fecha.date())
        except Exception as exc:  # noqa: BLE001 (API caída no rompe la app)
            if log:
                log(f"reconciliacion: error consultando API-Sports: {exc}")
            continue
        if total is None:
            continue

        if pendiente:
            paperbet.resolver(a, total)
        elif a.total_final is not None and abs(a.total_final - total) > 1e-9:
            paperbet.recalcular(a, total)
        else:
            base_datos.marcar_verificado(a.id)
            continue

        a.nota = (a.nota or "") + f" [oficial API-Sports: {total:g}]"
        base_datos.actualizar_resultado(a)
        base_datos.marcar_verificado(a.id)
        cambios += 1
        if log:
            log(f"reconciliacion: #{a.id} {a.partido} -> {a.estado} "
                f"total={total:g} benef={0.0 if a.beneficio is None else a.beneficio:+.0f}")
    return cambios


def main() -> int:
    p = argparse.ArgumentParser(description="Reconciliar apuestas con el "
                                            "resultado oficial de API-Sports.")
    p.add_argument("--horas", type=float, default=48.0,
                   help="ventana hacia atrás en horas (por defecto 48)")
    p.add_argument("--minimo-edad", type=float, default=2.0,
                   help="horas mínimas desde la apuesta para cerrar un "
                        "pendiente (por defecto 2)")
    args = p.parse_args()
    print(f"Reconciliando apuestas de las últimas {args.horas:g} h "
          f"con el resultado oficial…")
    n = reconciliar(args.horas, args.minimo_edad, log=print)
    print(f"Listo: {n} apuesta(s) cerrada(s)/corregida(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
