# -*- coding: utf-8 -*-
"""Prueba de humo: API en vivo + flujo 1Q/2Q + lógica paperbet (sin tocar la BD)."""
import api_betplay
import motor
import paperbet
from paperbet import Apuesta

print("=" * 78)
print("1) PING A LA API DE BETPLAY")
print("=" * 78)
try:
    partidos = api_betplay.obtener_partidos()
except api_betplay.ErrorAPI as e:
    print("ERROR:", e)
    raise SystemExit(1)

print(f"   partidos devueltos: {len(partidos)}")
for p in partidos[:6]:
    print(f"   - {p.nombre[:44]:44s} state={p.state:11s} per={p.period_id:10s} "
          f"1Q={str(p.q1):>4} 2Q={str(p.q2):>4} linea={p.linea} "
          f"over={p.cuota_over} under={p.cuota_under}")

vivo = [p for p in partidos if p.es_vivo]
lista = [p for p in partidos if p.listo_para_prediccion]
print(f"\n   en vivo: {len(vivo)}   |   listos para predecir (1Q+2Q): {len(lista)}")

# --------------------------------------------------------------- predicción
modelo = motor.cargar_modelo(motor.EXCEL_POR_DEFECTO, "EUROLIGA")
print(f"\n   modelo EUROLIGA cargado: n={modelo.n}")
candidato = lista[0] if lista else (vivo[0] if vivo else (partidos[0] if partidos else None))
if candidato is not None and candidato.q1 is not None and candidato.linea:
    res = motor.predecir(modelo, float(candidato.q1),
                         float(candidato.q2) if candidato.q2_completo else None,
                         float(candidato.linea))
    print(f"   partido: {candidato.nombre}")
    print(f"   1Q={candidato.q1} 2Q={candidato.q2} (completo={candidato.q2_completo}) "
          f"linea={candidato.linea}")
    print(f"   predicción={res.prediccion:.3f}  veredicto={res.veredicto}  "
          f"P(over)={res.prob_over}")

    # ------------------------------------------------------------ paperbet
    print("\n" + "=" * 78)
    print("2) SIMULACIÓN PAPERBET (resolver con tres totales distintos)")
    print("=" * 78)
    for total, etiqueta in ((candidato.linea - 10, "por debajo de la línea"),
                            (candidato.linea, "exactamente en la línea (push)"),
                            (candidato.linea + 10, "por encima de la línea")):
        a = Apuesta(evento_id=candidato.evento_id, partido=candidato.nombre,
                    seleccion=res.veredicto, linea=float(candidato.linea),
                    cuota=1.91, stake=10000, prediccion=res.prediccion,
                    diferencia=res.diferencia, probabilidad=res.prob_over)
        paperbet.resolver(a, total)
        print(f"   total={total:7.1f} ({etiqueta:36s}) -> {a.estado:9s} "
              f"beneficio={a.beneficio:+9.2f}")
        assert paperbet.validar(a) is None

    r = paperbet.resumir([
        Apuesta(seleccion="OVER", linea=167, cuota=1.91, stake=10000),
        Apuesta(seleccion="OVER", linea=167, cuota=1.91, stake=10000, estado="GANADA",
                beneficio=9100),
        Apuesta(seleccion="UNDER", linea=167, cuota=1.91, stake=10000, estado="PERDIDA",
                beneficio=-10000),
    ])
    print(f"\n   resumen: {r.total} apuestas, {r.pendientes} pendientes, "
          f"aciertos={r.aciertos}%, ROI={r.roi}%, beneficio={r.beneficio:+.0f}")

print("\nPRUEBA DE HUMO OK")
