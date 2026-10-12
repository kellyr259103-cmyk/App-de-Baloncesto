# -*- coding: utf-8 -*-
"""
prueba_resultados.py — la memoria de resultados (`resultados.py`).

Reproduce el fallo que dejaba apuestas PENDIENTE para siempre: la casa
retira cada partido del feed en cuanto termina y no deja consultar uno
concreto (`event/{id}.json` -> 404), así que si la app no llega a leer el
marcador en esa ventana, no queda forma de resolver la apuesta.

Comprueba, sin tocar la red:
  1. La coincidencia de nombres (casa vs API-Sports) elige el partido
     correcto y deja fuera a los parecidos.
  2. El marcador que da el feed se guarda en PostgreSQL.
  3. EL BUG: con el feed VACÍO (partido ya retirado), la apuesta se
     resuelve igual con lo que se guardó antes.
  4. Un partido que todavía no tiene resultado NO se resuelve.
  5. Limpieza: solo las filas de ESTA prueba.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import api_betplay
import app as mod_app
import base_datos
import paperbet
import resultados

avisos = []
mod_app.messagebox.showinfo = lambda t, x, **k: avisos.append(("info", t, x))
mod_app.messagebox.showwarning = lambda t, x, **k: avisos.append(("aviso", t, x))
mod_app.messagebox.showerror = lambda t, x, **k: avisos.append(("error", t, x))

HOY = date(2026, 10, 11)
EVENTO = 900901          # ids inventados: no chocan con partidos reales
EVENTO_SIN_DATO = 900902

print("=" * 72)
print("1) COINCIDENCIA DE NOMBRES (casa vs API-Sports)")
print("=" * 72)
assert resultados._partes("Bilbao Basket - San Pablo Burgos") == \
    ("Bilbao Basket", "San Pablo Burgos")
print("   _partes OK")
assert resultados._normaliza("Šiauliai") == "siauliai"
assert resultados._normaliza("Unieuro Forlì") == "unieuro forli"
print("   _normaliza (tildes y letras raras) OK")

s = resultados._similitud("Carpegna Prosciutto Basket Pesaro", "Pesaro")
print(f"   'Carpegna Prosciutto Basket Pesaro' vs 'Pesaro' -> {s:.2f}")
assert s >= 0.8, s
s = resultados._similitud("Real Sebastiani Rieti", "Sebastiani Rieti")
print(f"   'Real Sebastiani Rieti' vs 'Sebastiani Rieti' -> {s:.2f}")
assert s >= 0.85, s
s = resultados._similitud("Bilbao Basket", "Real Madrid")
print(f"   'Bilbao Basket' vs 'Real Madrid' (no emparejar) -> {s:.2f}")
assert s < 0.4, s

tokens = resultados._tokens_liga("España / Liga ACB")
print(f"   liga 'España / Liga ACB' -> palabras {sorted(tokens)}")
assert resultados._liga_encaja(tokens, {"liga": "ACB", "pais": "Spain"})
assert not resultados._liga_encaja(tokens, {"liga": "Lega A", "pais": "Italy"})

candidatos = [
    {"id": 1, "local": "Bilbao", "visita": "San Pablo Burgos", "total": 183.0,
     "liga": "ACB", "pais": "Spain"},
    {"id": 2, "local": "Real Madrid", "visita": "Manresa", "total": 203.0,
     "liga": "ACB", "pais": "Spain"},
    {"id": 3, "local": "Murcia", "visita": "Tenerife", "total": 167.0,
     "liga": "ACB", "pais": "Spain"},
    {"id": 4, "local": "Wisla Krakow W", "visita": "Polonia Warszawa W",
     "total": 150.0, "liga": "Liga Femenina W", "pais": "Spain"},
]
ap_prueba = paperbet.Apuesta(evento_id=EVENTO,
                             partido="Bilbao Basket - San Pablo Burgos",
                             liga_apuesta="España / Liga ACB", linea=175.0,
                             fecha=datetime(2026, 10, 11, 12, 0))
g, s, motivo = resultados._buscar(ap_prueba, {HOY: candidatos})
print(f"   mejor candidato: {g and g['local']} - {g and g['visita']} "
      f"(total {g and g['total']}, {s:.2f}, {motivo})")
assert g is not None and g["id"] == 1, g
assert g["total"] == 183.0

# una liga que API-Sports NO cubre no debe dar un resultado inventado
ap_mar = paperbet.Apuesta(evento_id=EVENTO_SIN_DATO,
                          partido="TAS Casablanca - AS Sale",
                          liga_apuesta="Marruecos / Division 1",
                          fecha=datetime(2026, 10, 11, 12, 0))
g2, s2, m2 = resultados._buscar(ap_mar, {HOY: candidatos})
print(f"   partido de una liga sin cobertura -> {g2} ({s2:.2f}, {m2})")
assert g2 is None, "ha inventado un resultado"

print("\n" + "=" * 72)
print("2) GUARDAR EL MARCADAR QUE DA EL FEED")
print("=" * 72)
partido = api_betplay.Partido(
    evento_id=EVENTO, nombre="Bilbao Basket - San Pablo Burgos",
    local="Bilbao Basket", visitante="San Pablo Burgos",
    state="FINISHED", inicio="2026-10-11T18:00:00+00:00",
    liga_nombre="España / Liga ACB", liga_tk="liga_acb", region_tk="spain",
    periodo="final", period_id="RESULT", reloj_min=40, reloj_seg=0,
    corriendo=False, izq_min=0, izq_seg=0,
    info_cuartos="Q1: 40-45 | Q2: 44-54 | Q3: 45-42 | Q4: 54-45",
    marcador_local=100, marcador_visitante=83)
assert partido.finalizado and partido.total_final == 183
assert resultados._definitivo(partido)
print(f"   partido finalizado con total {partido.total_final}")
n = resultados.guardar_partidos([partido])
print(f"   guardados en PostgreSQL: {n}")
tot = resultados.totales([EVENTO, EVENTO_SIN_DATO])
print(f"   totales leídos: {tot}")
assert tot.get(EVENTO) == 183.0, tot
assert EVENTO_SIN_DATO not in tot

# una prórroga pendiente NO se guarda (el marcador aún no es el final)
en_prorroga = api_betplay.Partido(
    evento_id=900903, nombre="X - Y", local="X", visitante="Y",
    state="STARTED", inicio="2026-10-11T18:00:00+00:00",
    liga_nombre="Italia / Serie A2", liga_tk="serie_a2", region_tk="italy",
    periodo="4to cuarto", period_id="QUARTER4", reloj_min=40, reloj_seg=0,
    corriendo=False, izq_min=0, izq_seg=0,
    q1=40.0, q2=45.0, q3=45.0, q4=50.0, total_parcial=190.0,
    info_cuartos="Q1: 40-45 | Q2: 44-54 | Q3: 45-42 | Q4: 54-45",
    marcador_local=100, marcador_visitante=90)
assert en_prorroga.finalizado and not resultados._definitivo(en_prorroga), \
    "ha guardado un marcador con prórroga pendiente"
print("   fin del 4T con los cuartos sin cuadrar (prórroga pendiente): NO "
      "se guarda -> OK")

print("\n" + "=" * 72)
print("3) EL BUG: EL PARTIDO YA NO ESTÁ EN EL FEED Y AUN ASÍ SE RESUELVE")
print("=" * 72)
print("   abriendo la app y esperando a PostgreSQL…")
a = mod_app.App()
a.update()
limite = time.time() + 25
while not a.db_ok and time.time() < limite:
    a.update()
    time.sleep(0.05)
assert a.db_ok, a.db_error
v = a.pestana_vivo

previas = {x.id for x in base_datos.listar_apuestas()}
mia = paperbet.Apuesta(
    evento_id=EVENTO, partido="Bilbao Basket - San Pablo Burgos",
    liga_apuesta="España / Liga ACB", liga_modelo="spain/liga_acb",
    seleccion="OVER", linea=175.0, cuota=1.85, stake=10000.0,
    prediccion=180.0, diferencia=5.0, probabilidad=0.60,
    nota="prueba_resultados")
id_mia = base_datos.guardar_apuesta(mia)
otra = paperbet.Apuesta(
    evento_id=EVENTO_SIN_DATO, partido="Partido sin resultado",
    liga_apuesta="Italia / Serie A2", liga_modelo="italy/serie_a2",
    seleccion="OVER", linea=175.0, cuota=1.85, stake=10000.0,
    prediccion=180.0, diferencia=5.0, probabilidad=0.60,
    nota="prueba_resultados")
id_otra = base_datos.guardar_apuesta(otra)
print(f"   2 apuestas guardadas: #{id_mia} (con resultado) e "
      f"#{id_otra} (sin resultado)")

n = v._resolver_con([])          # <- feed VACÍO: la casa ya retiró el partido
a.pestana_apuestas.refrescar()
a.update()
print(f"   _resolver_con con feed vacío -> {n} resuelta(s)")

guardadas = {x.id: x for x in base_datos.listar_apuestas()}
resuelta = guardadas[id_mia]
print(f"   #{resuelta.id} {resuelta.partido}: {resuelta.estado}  "
      f"total_final={resuelta.total_final}  beneficio={resuelta.beneficio:+,.0f}")
print(f"   nota: {resuelta.nota}")
assert resuelta.estado != "PENDIENTE", "sin la memoria no se resuelve"
assert resuelta.total_final == 183.0, resuelta.total_final
esperada = "ANULADA" if resuelta.total_final == resuelta.linea else (
    "GANADA" if resuelta.seleccion == "OVER" else "PERDIDA")
assert resuelta.estado == esperada, (resuelta.estado, esperada)

sigue = guardadas[id_otra]
print(f"   #{sigue.id} {sigue.partido}: {sigue.estado} (no debe resolverse)")
assert sigue.estado == "PENDIENTE", "ha resuelto un partido sin resultado"

print("\n" + "=" * 72)
print("4) LIMPIEZA (solo las filas de ESTA prueba)")
print("=" * 72)
for i in (id_mia, id_otra):
    base_datos.eliminar_apuesta(i)
conn = base_datos.conectar()
try:
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM resultados "
                        "WHERE evento_id = ANY(%s)",
                        ([EVENTO, EVENTO_SIN_DATO, 900903],))
            print(f"   resultados de prueba borrados: {cur.rowcount}")
finally:
    conn.close()
quedan = [x for x in base_datos.listar_apuestas() if x.id not in previas]
print(f"   apuestas ajenas que quedan intactas: {len(quedan)}")
assert not quedan, "ha dejado apuestas de esta prueba"

a.destroy()
print("\nPRUEBA DE LA MEMORIA DE RESULTADOS OK")
