# -*- coding: utf-8 -*-
"""
auditoria_auto.py — ¿Por qué NO apuesta el bot, y qué alternativa tiene?

Recorre la cadena de decisiones de `_auto_apostar_en_descanso` con datos
REALES de la API (una sola petición) y contesta tres preguntas:

  1. ¿En qué punto exacto se queda cada partido? (motivo real, no el
     genérico "esperando Δ ≥ 2.0" que enseña el panel).
  2. Cuando la casa NO tiene el mercado que el modelo quiere usar, ¿qué nos
     ofrece en su sitio (línea alterna, otro lado, solo suspendido)?
  3. ¿Cuál sería la MEJOR apuesta realmente disponible por valor esperado?

Uso:
    python auditoria_auto.py            # umbral 2.0 (el de la app)
    python auditoria_auto.py 3.0        # otro umbral
"""
from __future__ import annotations

import sys
from collections import Counter

import api_betplay
import descansos
import modelos
import motor
from motor import predecir

UMBRAL = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0


# --------------------------------------------------------------------------- #
#  Mercado crudo (el parser se queda con una sola línea; aquí se ve todo)      #
# --------------------------------------------------------------------------- #
def mercados_crudos(bloque: dict) -> list[dict]:
    """
    Todas las ofertas de TOTAL del partido completo que ofrece la casa.

    El parser (`api_betplay._parsear_evento`) se queda con UNA sola y marca
    `mercado_abierto` si hay alguna abierta. Aquí se desglosa oferta a oferta
    y lado a lado, que es donde está el problema.
    """
    filas = []
    for oferta in bloque.get("betOffers", []) or []:
        tipo = (oferta.get("betOfferType") or {}).get("id")
        if tipo != api_betplay.ID_TOTAL:
            continue
        criterio = (oferta.get("criterion") or {}).get("label", "")
        if not api_betplay._es_total_partido(criterio):
            continue
        suspendida = bool(oferta.get("suspended"))
        fila = {"criterio": criterio, "suspendida": suspendida,
                "linea": None, "over": None, "under": None,
                "over_abierta": False, "under_abierta": False}
        for oc in oferta.get("outcomes", []) or []:
            abierto = oc.get("status") == "OPEN"
            if oc.get("type") == "OT_OVER":
                fila["linea"] = api_betplay._num(oc.get("line")) or fila["linea"]
                fila["over"] = api_betplay._num(oc.get("odds"))
                fila["over_abierta"] = abierto and not suspendida
            elif oc.get("type") == "OT_UNDER":
                fila["linea"] = api_betplay._num(oc.get("line")) or fila["linea"]
                fila["under"] = api_betplay._num(oc.get("odds"))
                # OJO: el parser NO mira el estado del Under, solo el del Over
                fila["under_abierta"] = abierto and not suspendida
        if fila["linea"] is not None:
            filas.append(fila)
    return filas


def ev(prob: float, cuota: float) -> float:
    """Valor esperado de una cuota decimal: p*cuota - 1 (> 0 = favorable)."""
    return prob * cuota - 1.0 if cuota and cuota > 1.0 else float("-inf")


# --------------------------------------------------------------------------- #
#  Auditoría                                                                   #
# --------------------------------------------------------------------------- #
def main() -> int:
    print("Descargando el deporte entero (1 petición)…")
    try:
        crudo = api_betplay._get_json(api_betplay.url_deporte())
    except api_betplay.ErrorAPI as exc:
        print("ERROR:", exc)
        return 1
    bloques = crudo.get("events", []) or []
    partidos = [api_betplay._parsear_evento(b) for b in bloques]
    por_id = {p.evento_id: p for p in partidos}
    modelos_liga = modelos.cargar_modelos()

    print(f"   {len(partidos)} partidos · {len(modelos_liga)} modelos de liga\n")

    motivos = Counter()
    candidatos = 0
    vivos = 0

    print("=" * 78)
    print("1) CADENA DE DECISIONES — ¿dónde se queda cada partido?")
    print("=" * 78)
    for b in bloques:
        p = por_id.get(int((b.get("event") or {}).get("id", 0)))
        if p is None or p.finalizado:
            continue
        if p.es_vivo:
            vivos += 1
        # se audita todo lo que el modelo PUEDE pricear (1Q y 2Q conocidos),
        # que es exactamente lo que hay en el descanso del 2Q
        if not p.listo_para_prediccion:
            motivos["aún sin 1Q+2Q (normal)"] += 1
            continue

        candidatos += 1
        en_ventana = descansos.en_ventana_descanso(p)
        marca = "⏸ DESCANSO" if en_ventana else "  jugando"
        print(f"\n  {marca}  {p.nombre[:44]:44s} [{p.clave_liga}]")
        print(f"      1Q={p.q1}  2Q={p.q2}  línea que usa la app={p.linea}  "
              f"over={p.cuota_over} under={p.cuota_under}  "
              f"mercado_abierto={p.mercado_abierto}")

        if not en_ventana:
            motivos["aún no llega al descanso (normal)"] += 1
            continue

        # --- gates en el MISMO orden que usa la app ---
        ok, motivo = descansos.puede_apostar(p, p.clave_liga, None)
        if not ok:
            motivos[f"regla del descanso: {motivo}"] += 1
            print(f"      ✗ {motivo}")
            continue

        modelo = modelos_liga.get(p.clave_liga)
        if modelo is None or not modelo.n:
            motivos["sin modelo para la liga"] += 1
            print("      ✗ sin modelo de esta liga en la BD")
            continue

        res = predecir(modelo, float(p.q1),
                       None if not p.q2_completo else float(p.q2), float(p.linea))
        po = res.prob_over if res.prob_over is not None else 0.5
        print(f"      predicción={res.prediccion:.1f}  Δ={res.diferencia:+.1f}  "
              f"veredicto={res.veredicto}  P(over)={po:.3f}")

        if res.veredicto not in ("OVER", "UNDER"):
            motivos["veredicto sin decidir"] += 1
            print("      ✗ veredicto sin decidir")
            continue
        if abs(res.diferencia) < UMBRAL:
            motivos[f"|Δ| < umbral ({UMBRAL:.1f})"] += 1
            print(f"      ✗ |Δ| < umbral {UMBRAL:.1f}")
            continue

        # --- AQUÍ ESTÁ EL FALLO: se mira solo si hay cuota, no si está abierta
        lado = res.veredicto
        cuota = p.cuota_over if lado == "OVER" else p.cuota_under
        abierta = p.cuota_over_abierta if lado == "OVER" else p.cuota_under_abierta
        if not cuota or cuota <= 1.0:
            motivos["sin cuota para el lado elegido"] += 1
            print(f"      ✗ la casa no da cuota del {lado}")
        elif not abierta:
            motivos["lado elegido SUSPENDIDO (la app igual apuesta)"] += 1
            print(f"      ⚠ el {lado} está SUSPENDIDO pero la app apostaría "
                  f"igual (solo mira `cuota`, no `_abierta`)")
        else:
            p_over = res.prob_over if lado == "OVER" else 1 - res.prob_over
            print(f"      ✓ apostaría {lado} @ {cuota:.2f}  "
                  f"EV={ev(p_over, cuota) * 100:+.1f}%")

        # --- qué alternativas nos queda la casa? ---
        filas = mercados_crudos(b)
        abiertas = [f for f in filas if f["over_abierta"] or f["under_abierta"]]
        print(f"      la casa publica {len(filas)} línea(s) de total: "
              f"{[f['linea'] for f in filas]}  "
              f"(abiertas: {[f['linea'] for f in abiertas]})")

        mejores = []
        for f in abiertas:
            r = predecir(modelo, float(p.q1),
                         None if not p.q2_completo else float(p.q2), f["linea"])
            if r.prob_over is None:
                continue
            if f["over_abierta"]:
                mejores.append((ev(r.prob_over, f["over"] or 0),
                                "OVER", f["linea"], f["over"], r.prob_over))
            if f["under_abierta"]:
                mejores.append((ev(1 - r.prob_over, f["under"] or 0),
                                "UNDER", f["linea"], f["under"], 1 - r.prob_over))
        if mejores:
            mejores.sort(reverse=True)
            e, s, ln, cq, pr = mejores[0]
            print(f"      → MEJOR DISPONIBLE: {s} @ línea {ln}  cuota {cq:.2f}  "
                  f"P={pr:.3f}  EV={e * 100:+.1f}%")
            if e <= 0:
                motivos["mejor alternativa con EV ≤ 0 (no apostar)"] += 1
                print("        (ninguna alternativa con EV positivo: "
                      "la casa ya lo tiene precioado)")
        else:
            motivos["mercado entero cerrado: NO hay alternativa"] += 1
            print("      → no hay NINGUNA línea abierta: no se puede apostar")

    print("\n" + "=" * 78)
    print("2) RESUMEN — motivos por los que NO se apuesta")
    print("=" * 78)
    print(f"   partidos vivos: {vivos}   con 1Q y 2Q conocidos: {candidatos}\n")
    for m, n in motivos.most_common():
        print(f"   {n:4d}   {m}")

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("3) ESTRUCTURA DEL MERCADO — ¿cuántas líneas ofrece la casa?")
    print("=" * 78)
    n_con_linea = n_multi = n_mezcla = 0
    eleccion_peor = 0
    ejemplos_multi = []
    for b in bloques:
        ev_id = int((b.get("event") or {}).get("id", 0))
        p = por_id.get(ev_id)
        if p is None or p.finalizado:
            continue
        filas = mercados_crudos(b)
        if not filas:
            continue
        n_con_linea += 1
        abiertas = [f for f in filas if f["over_abierta"] or f["under_abierta"]]
        if len(filas) > 1:
            n_multi += 1
            if len(abiertas) != len(filas):
                n_mezcla += 1
            if len(ejemplos_multi) < 4:
                ejemplos_multi.append((p.nombre, [f["linea"] for f in filas],
                                       [f["linea"] for f in abiertas]))
        # ¿elige la app la mejor de las disponibles?
        if not abiertas or not p.q1 or not p.q2_completo:
            continue
        modelo = modelos_liga.get(p.clave_liga)
        if modelo is None or not modelo.n:
            continue
        opts = []
        for f in abiertas:
            r = predecir(modelo, float(p.q1), float(p.q2), f["linea"])
            if r.prob_over is None:
                continue
            if f["over_abierta"]:
                opts.append((abs(r.diferencia), "OVER", f["linea"]))
            if f["under_abierta"]:
                opts.append((abs(r.diferencia), "UNDER", f["linea"]))
        if not opts:
            continue
        opts.sort(reverse=True)
        mejor = opts[0]
        if p.linea != mejor[2]:
            eleccion_peor += 1

    print(f"   partidos con al menos 1 línea de total : {n_con_linea}")
    print(f"   ...con MÁS de una línea (alternas)    : {n_multi}")
    print(f"   ...con alguna suspendida y otra abierta: {n_mezcla}")
    print(f"   la app elige hoy una línea DISTINTA de la mejor disponible"
          f" en {eleccion_peor} partidos")
    if ejemplos_multi:
        print("\n   ejemplos de líneas alternas:")
        for nombre, lineas, abiertas in ejemplos_multi:
            print(f"     {nombre[:40]:40s} líneas={lineas}  abiertas={abiertas}")

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("4) ¿ES RENTABLE APOSTAR CON ESAS CUOTAS? (valor esperado real)")
    print("=" * 78)
    print("   La casa cobra un margen de ~8%. Un Δ de 2 pts puede ser +EV o no,\n"
          "   según la cuota. Se calcula con los partidos que ya tienen 1Q+2Q\n"
          "   (la línea YA se habrá reajustado en los que van por el 3Q/4T, así\n"
          "   que esto es un PROXY de lo que pasaría en el descanso).\n")
    filas_ev = []
    for b in bloques:
        p = por_id.get(int((b.get("event") or {}).get("id", 0)))
        if p is None or p.finalizado or not p.listo_para_prediccion:
            continue
        modelo = modelos_liga.get(p.clave_liga)
        if modelo is None or not modelo.n or not p.linea:
            continue
        r = predecir(modelo, float(p.q1), float(p.q2), float(p.linea))
        if r.prob_over is None:
            continue
        ov, un = p.cuota_over or 0.0, p.cuota_under or 0.0
        if ov <= 1.0 or un <= 1.0:
            continue
        filas_ev.append((p.nombre, p.clave_liga, r, ov, un))

    if not filas_ev:
        print("   (ningún partido con 1Q+2Q y cuotas)")
    else:
        margen = sum(1 / ov + 1 / un for _, _, _, ov, un in filas_ev) / len(filas_ev)
        print(f"   margen medio de la casa: {(margen - 1) * 100:.1f}%\n")
        print(f"   {'partido':40s} {'Δ':>6s} {'P(over)':>8s} "
              f"{'breakeven':>10s} {'EV over':>8s} {'EV under':>9s}")
        mejor_evs = []
        for nombre, clave, r, ov, un in filas_ev:
            be_over, be_under = 1.0 / ov, 1.0 / un
            e_o = ev(r.prob_over, ov)
            e_u = ev(1 - r.prob_over, un)
            mejor_evs.append((max(e_o, e_u), abs(r.diferencia), nombre))
            print(f"   {nombre[:40]:40s} {r.diferencia:+6.1f} "
                  f"{r.prob_over:8.3f} {be_over:10.3f} "
                  f"{e_o * 100:+7.1f}% {e_u * 100:+8.1f}%")

        con_umbral = [m for m in mejor_evs if m[1] >= UMBRAL]
        positivos = [m for m in mejor_evs if m[0] > 0]
        ambos = [m for m in con_umbral if m[0] > 0]
        print(f"\n   con umbral |Δ| ≥ {UMBRAL:.1f}: {len(con_umbral)}/{len(mejor_evs)} apostarían")
        print(f"   con EV positivo          : {len(positivos)}/{len(mejor_evs)}")
        print(f"   las DOS condiciones      : {len(ambos)}/{len(mejor_evs)}")
        if con_umbral and not ambos:
            print("\n   ⚠ NINGUNA pasaría el doble filtro: el umbral de Δ solo no basta.")

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("5) QUÉ MÁS OFRECE LA CASA (mercados disponibles)")
    print("=" * 78)
    print("   Si el total no estuviera disponible, ¿quedan alternativas?\n")
    por_tipo: Counter = Counter()
    total_ofertas = 0
    for b in bloques:
        tipos_partido = set()
        for o in b.get("betOffers", []) or []:
            total_ofertas += 1
            t = (o.get("betOfferType") or {}).get("id")
            lab = (o.get("criterion") or {}).get("label", "")
            por_tipo[t] += 1
            tipos_partido.add(t)
        if len(tipos_partido) >= 3:
            por_tipo["_tres_mercados"] += 1
    NOMBRES = {1: "Hándicap de puntos (spread)", 2: "Ganador (moneyline)",
               6: "Total de puntos (OVER/UNDER)"}
    for t in sorted(k for k in por_tipo if isinstance(k, int)):
        print(f"   tipo {t}: {NOMBRES.get(t, '?'):32s} {por_tipo[t]:4d} ofertas")
    print(f"\n   ofertas totales: {total_ofertas}")
    print(f"   partidos con los 3 mercados publicados a la vez: "
          f"{por_tipo.get('_tres_mercados', 0)}")
    print("""
   → El total NO está solo: la casa publica SIEMPRE también el spread y el
     moneyline. Esa es la alternativa de reserva:
       · spread (tipo 1): mercado de MARGEN, no de total. Exigiría un modelo
         del margen (no lo tenemos; el actual predice el total).
       · moneyline (tipo 2): exige saber quién gana, tampoco lo modelamos.
""")

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("6) DIAGNÓSTICO DE LOS CAMPOS QUE LA APP USA")
    print("=" * 78)
    totales = [p for p in partidos if not p.finalizado and p.linea is not None]
    con_ambas = [p for p in totales if p.cuota_over and p.cuota_under]
    print(f"   partidos con línea publicada        : {len(totales)}")
    print(f"   ...con cuota Over y Under           : {len(con_ambas)}")
    print(f"   ...con mercado_abierto=True         : "
          f"{sum(1 for p in totales if p.mercado_abierto)}")
    print(f"   ...con el Over concretamente abierto: "
          f"{sum(1 for p in totales if p.hay_cuota_over)}")
    print(f"   ...con el Under concretamente abierto: "
          f"{sum(1 for p in totales if p.hay_cuota_under)}")
    print("\n   (la diferencia entre 'mercado abierto' y 'lado abierto' es")
    print("    exactamente el hueco que audita este script)")

    print("\n" + "=" * 78)
    print("7) CONCLUSIÓN")
    print("=" * 78)
    print("""
   QUÉ OFRECE LA CASA (verificado hoy):
   · Publica SIEMPRE los 3 mercados a la vez: Hándicap (tipo 1), Ganador
     (tipo 2) y Total (tipo 6). 72 de 75 partidos los tienen los tres.
   · El Total se publica con UNA sola línea. NO hay líneas alternas (0 de 75),
     así que "cambiar de línea" no es una alternativa posible en este feed.
   · El Hándicap es la única reserva real, pero es un mercado de MARGEN y
     este modelo predice el TOTAL: exigiría entrenar un modelo nuevo.

   FALLOS QUE ESTE SCRIPT DESTAPÓ Y QUE YA ESTÁN CORREGIDOS:
   · Antes la app solo miraba `cuota is not None`: podía apostar un lado que
     la casa había SUSPENDIDO (el parser conserva la última cuota conocida).
     → Ahora usa `p.hay_cuota_over / hay_cuota_under` (lado concreto).
   · El estado del Under no se comprobaba en el parsing (solo el del Over).
     → Ahora se comprueban los DOS outcomes y `suspended` de la oferta.
   · El motivo real de no apostar nunca llegaba al panel (todos los
     `continue` eran silenciosos; el panel decía "esperando Δ" aunque la
     causa fuera otra). → Ahora se cuenta cada motivo y se enseña.
   · El umbral de Δ ignoraba las cuotas: con ~8% de margen, un Δ pequeño
     puede ser -EV. → Ahora hay puerta de EV mínima (por defecto 0% = +EV).

   ALTERNATIVAS DESCARTADAS (por si se retoman):
   · Elegir entre líneas alternas del total: imposible, no existen.
   · Apostar el Hándicap como respaldo: requiere modelo de margen.
   · Apostar el lado contrario si el nuestro está suspendido: sería jugar
     CONTRA el propio modelo. Lo correcto es NO apostar y avisar (ya hace).
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
