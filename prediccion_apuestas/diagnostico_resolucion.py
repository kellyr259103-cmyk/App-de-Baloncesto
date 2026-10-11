# -*- coding: utf-8 -*-
"""
diagnostico_resolucion.py — ¿por qué queda alguna apuesta PENDIENTE?

Compara cada apuesta pendiente de PostgreSQL con las tres fuentes que tiene
la app para resolverla:

  1. el feed de la casa (partidos de hoy que aún no han salido)
  2. la memoria de resultados de `resultados.py` (marcadores ya guardados)
  3. API-Sports (lo que la casa ya retiró y la fuente oficial sí conserva)

Y dice, para cada una, en qué punto se queda:

  · ya no está en el feed pero HAY marcador guardado -> se resuelve sola
  · aparece en el feed y aún NO ha terminado         -> esperar
  · no está en el feed y NO hay marcador en ninguna  -> mirar API-Sports
  · API-Sports tampoco la identifica                 -> no recuperable

Uso:
    python diagnostico_resolucion.py
"""
from __future__ import annotations

import collections

import api_betplay
import base_datos
import resultados


def main() -> int:
    print("Leyendo el feed de la casa (1 petición)…")
    partidos = api_betplay.obtener_partidos_deporte("basketball")
    indice = {p.evento_id: p for p in partidos}

    por_estado = collections.Counter()
    for p in partidos:
        if p.finalizado:
            por_estado["finalizado"] += 1
        elif p.es_vivo:
            por_estado["en juego"] += 1
        else:
            por_estado["por jugar"] += 1
    print(f"   {len(partidos)} partidos en el feed: {dict(por_estado)}")
    print(f"   (la casa retira de la lista cada partido en cuanto termina: "
          f"por eso hace falta la memoria de `resultados.py`)")

    pend = base_datos.apuestas_pendientes()
    print("\n" + "=" * 78)
    print(f"APUESTAS PENDIENTES: {len(pend)}")
    print("=" * 78)
    if not pend:
        print("   ninguna. ✅")
        return 0

    guardados = resultados.detalle(a.evento_id for a in pend)
    print(f"   (en la memoria hay {resultados.contar()} resultados guardados)\n")

    motivos: collections.Counter = collections.Counter()
    ejemplos: dict[str, list[str]] = collections.defaultdict(list)
    for a in pend:
        p = indice.get(a.evento_id)
        if a.evento_id in guardados:
            total, fuente = guardados[a.evento_id]
            motivo = f"HAY marcador guardado (total {total:g}, {fuente}): se resuelve sola"
        elif p is None:
            motivo = "fuera del feed y SIN marcador -> toca API-Sports"
        elif not p.finalizado:
            motivo = "en el feed pero aún NO ha terminado: esperar"
        elif p.total_final is None:
            motivo = "finalizado pero SIN marcador en el feed"
        else:
            motivo = "se resolvería con este sondeo"
        motivos[motivo] += 1
        if len(ejemplos[motivo]) < 4:
            ejemplos[motivo].append(
                f"#{a.id} {a.partido[:40]} [{a.liga_apuesta}]")

    for motivo, n in motivos.most_common():
        print(f"   {n:3d}  {motivo}")
        for e in ejemplos[motivo]:
            print(f"         {e}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
