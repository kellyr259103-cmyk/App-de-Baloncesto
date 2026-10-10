# -*- coding: utf-8 -*-
"""Prueba del cliente genérico: ligas, deporte entero y predicción por liga."""
import api_betplay
import motor

print("=" * 78)
print("1) DESCUBRIMIENTO DE LIGAS DE BALONCESTO")
print("=" * 78)
ligas = api_betplay.obtener_ligas("basketball")
print(f"   ligas encontradas: {len(ligas)}")
for l in ligas[:6]:
    print(f"   - {l.nombre:34s} clave={l.clave:26s} patron={l.patron:11s} "
          f"eventos_declarados={l.event_count}")
print(f"   ... y {len(ligas) - 6} más")
n_3 = sum(1 for l in ligas if l.region_tk)
print(f"   con región (3 niveles): {n_3}   sin región (2 niveles): {len(ligas) - n_3}")
print(f"   deportes disponibles igual a basketball: "
      f"{all(l.deporte == 'basketball' for l in ligas)}")

print("\n" + "=" * 78)
print("2) TODOS LOS PARTIDOS DEL DEPORTE EN UNA PETICION")
print("=" * 78)
todos = api_betplay.obtener_partidos_deporte("basketball")
print(f"   partidos: {len(todos)}")
print(f"   en vivo : {sum(1 for p in todos if p.es_vivo)}")
print(f"   con liga asignada: {sum(1 for p in todos if p.liga_nombre)}/{len(todos)}")
print(f"   ligas distintas con partidos: "
      f"{len({p.liga_nombre for p in todos if p.liga_nombre})}")
print("\n   ejemplos:")
for p in todos[:4]:
    print(f"   - [{p.liga_nombre:26s}] {p.nombre[:38]:38s} estado={p.state:11s} "
          f"1Q={str(p.q1):>4} 2Q={str(p.q2):>4} linea={p.linea}")

print("\n" + "=" * 78)
print("3) PING A UNA LIGA CONCRETA (compatibilidad)")
print("=" * 78)
bbl = next(l for l in ligas if l.clave == "germany/bbl")
p_bbl = api_betplay.obtener_partidos(liga=bbl)
print(f"   {bbl.nombre}: {len(p_bbl)} partidos, "
      f"{sum(1 for p in p_bbl if p.es_vivo)} en vivo")
p_defecto = api_betplay.obtener_partidos()
print(f"   obtener_partidos() sin argumentos (BBL por defecto): {len(p_defecto)} partidos")
assert [p.evento_id for p in p_bbl] == [p.evento_id for p in p_defecto], \
    "la compatibilidad de obtener_partidos() se rompió"
print("   compatibilidad OK")

print("\n" + "=" * 78)
print("4) PREDICCIONES SOBRE LOS PARTIDOS CON 1Q+2Q (todas las ligas)")
print("=" * 78)
modelo = motor.cargar_modelo(motor.EXCEL_POR_DEFECTO, "EUROLIGA")
print(f"   modelo EUROLIGA cargado (n={modelo.n})")

filas = []
for p in todos:
    if not p.listo_para_prediccion or not p.linea:
        continue
    res = motor.predecir(modelo, float(p.q1), float(p.q2), float(p.linea))
    if res.veredicto in ("OVER", "UNDER"):
        filas.append((abs(res.diferencia), p, res))

filas.sort(key=lambda f: -f[0])
print(f"   partidos con 1Q+2Q y línea utilizables: {len(filas)}")
print(f"   {'liga':24s} {'partido':36s} {'1Q':>4} {'2Q':>4} {'total':>6} "
      f"{'linea':>6} {'pred':>7} {'dif':>7} {'ver':>5} {'P(over)':>8}")
for dif, p, res in filas[:12]:
    print(f"   {p.liga_nombre[:24]:24s} {p.nombre[:36]:36s} {p.q1:4d} {p.q2:4d} "
          f"{p.total_parcial or 0:6d} {p.linea:6.1f} {res.prediccion:7.1f} "
          f"{res.diferencia:+7.1f} {res.veredicto:>5} {res.prob_over * 100:7.1f}%")

print("\n" + "=" * 78)
print("5) MODO 'TODAS LAS LIGAS': resumen")
print("=" * 78)
por_liga = {}
for p in todos:
    por_liga.setdefault(p.liga_nombre or "(sin liga)", []).append(p)
vivas = [(k, v) for k, v in por_liga.items() if any(p.es_vivo for p in v)]
print(f"   ligas con partidos hoy: {len(por_liga)}   con partidos en vivo: {len(vivas)}")
for k, v in sorted(vivas, key=lambda kv: -len(kv[1]))[:8]:
    n_v = sum(1 for p in v if p.es_vivo)
    n_listos = sum(1 for p in v if p.listo_para_prediccion and p.linea)
    print(f"   - {k:30s} partidos={len(v):3d}  en_vivo={n_v:3d}  "
          f"listos_para_modelo={n_listos:3d}")

print("\nPRUEBA DEL CLIENTE GENERICO OK")
