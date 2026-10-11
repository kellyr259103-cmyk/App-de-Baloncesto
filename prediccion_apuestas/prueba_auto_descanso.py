# -*- coding: utf-8 -*-
"""
prueba_auto_descanso.py — Bucle COMPLETO de la apuesta automática en el descanso.

No toca la API: alimenta la pestaña EN VIVO con partidos sintéticos en el
estado exacto del descanso 2Q→3Q y comprueba que, con el AUTO activado:

  1. NO apuesta si |Δ| < umbral.
  2. NO apuesta si la ventana ya caducó (dato viejo).
  3. NO repite la apuesta del mismo partido.
  4. SÍ apuesta sola cuando hay modelo, |Δ| ≥ umbral y mercado abierto, y la
     apuesta queda en PostgreSQL con la nota del descanso.
  5. Al terminar el partido, la resuelve sola (GANADA/PERDIDA) con el total.
  6. Mide el descanso real y lo guarda en `descansos_liga`.
"""
from __future__ import annotations

import time

import api_betplay
import app as mod_app
import base_datos
import descansos

avisos = []
mod_app.messagebox.showinfo = lambda t, x, **k: avisos.append(("info", t, x))
mod_app.messagebox.showwarning = lambda t, x, **k: avisos.append(("aviso", t, x))
mod_app.messagebox.showerror = lambda t, x, **k: avisos.append(("error", t, x))


def partido_en_descanso(evento_id, q1=50, q2=45, linea=175.0,
                        mercado=True, liga_tk="euroleague",
                        liga_nombre="Euroliga", cuota=1.85):
    """Partido en el estado exacto del descanso 2Q→3Q (ventana abierta)."""
    return api_betplay.Partido(
        evento_id=evento_id, nombre=f"Equipo{evento_id} - Rival{evento_id}",
        local=f"Equipo{evento_id}", visitante=f"Rival{evento_id}",
        state="STARTED", inicio="", liga_nombre=liga_nombre,
        liga_tk=liga_tk, region_tk="", periodo="2do cuarto",
        period_id="QUARTER2", reloj_min=0, reloj_seg=0,
        corriendo=False, izq_min=0, izq_seg=0,
        q1=q1, q2=q2, q3=None, total_parcial=q1 + q2,
        linea=linea, mercado_abierto=mercado,
        cuota_over=cuota, cuota_under=cuota,
        cuota_over_abierta=True, cuota_under_abierta=True)


print("=" * 72)
print("1) ABRIR LA APP Y ESPERAR POSTGRESQL + MODELOS")
print("=" * 72)
a = mod_app.App()
a.update()
limite = time.time() + 25
while not a.db_ok and time.time() < limite:
    a.update()
    time.sleep(0.05)
assert a.db_ok, a.db_error
# los modelos por liga se cargan en main() (no en __init__): pedirlos a mano
a.cargar_modelos_ligas()
limite = time.time() + 25
while not a.modelos_ligas and time.time() < limite:
    a.update()
    time.sleep(0.05)
print(f"   db_ok={a.db_ok}   modelos por liga: {len(a.modelos_ligas)}")
assert a.modelos_ligas, "hacen falta modelos entrenados por liga"

v = a.pestana_vivo
v.var_auto_apostar.set(True)
v.var_umbral.set(3.0)
v.var_stake.set("10000")
# marcamos la ventana como si hubiéramos visto el descanso hace 1 minuto
v._ventanas.clear()
v._apuestas_auto.clear()
antes = base_datos.estadisticas_bd()["apuestas"]
# instantánea de lo que ya había: la app puede estar apostando a la vez y hay
# que dejar sus apuestas intactas al final
previas = {ap.id for ap in base_datos.listar_apuestas()}
print(f"   AUTO activado, umbral=3.0, stake=10000. apuestas en BD: {antes} "
      f"(de ellas, {len(previas)} ajenas)")

# --- elegir una liga que SÍ tenga modelo ---
liga_con_modelo = sorted(a.modelos_ligas)[0]
modelo = a.modelos_ligas[liga_con_modelo]
print(f"   liga de prueba: {liga_con_modelo} (n={modelo.n})")

# construir un descanso con Δ grande de forma CONTROLADA: elegimos q1,q2
# para que la predicción se aleje mucho de la línea
q1 = int(modelo.media_1q) if modelo.media_1q else 50
q2 = int(modelo.media_2q) if modelo.media_2q else 45
pred = modelo.predecir_dos(q1, q2)
linea_lejos = round(pred - 12.0, 1)     # Δ = +12 -> mucho mayor que el umbral
p_ok = partido_en_descanso(900001, q1=q1, q2=q2, linea=linea_lejos,
                           liga_tk=liga_con_modelo)
print(f"   descanso con Δ grande: 1Q={q1} 2Q={q2} pred={pred:.1f} "
      f"línea={linea_lejos} Δ={pred - linea_lejos:+.1f}")

print("\n" + "=" * 72)
print("2) NO APUESTA SI |Δ| < UMBRAL")
print("=" * 72)
p_poco = partido_en_descanso(900002, q1=q1, q2=q2,
                             linea=round(pred - 1.0, 1),   # Δ = +1 < 3
                             liga_tk=liga_con_modelo)
v._ventanas[p_poco.evento_id] = time.time()
v._auto_apostar_en_descanso([p_poco])
a.update()
print(f"   Δ=+1.0 (< umbral 3.0) -> apuestas en BD: "
      f"{base_datos.estadisticas_bd()['apuestas']}  "
      f"(estado: {v.lbl_auto_estado.cget('text')})")
assert base_datos.estadisticas_bd()["apuestas"] == antes, "apostó con Δ pequeño"

print("\n" + "=" * 72)
print("3) NO APUESTA SI LA VENTANA CADUCÓ (dato viejo)")
print("=" * 72)
p_viejo = partido_en_descanso(900003, q1=q1, q2=q2, linea=linea_lejos,
                              liga_tk=liga_con_modelo)
# la vimos en descanso hace 20 min (NBA/euroleague descansan 15 + margen 2)
v._ventanas[p_viejo.evento_id] = time.time() - 20 * 60
v._auto_apostar_en_descanso([p_viejo])
a.update()
print(f"   ventana de 20 min atrás -> apuestas en BD: "
      f"{base_datos.estadisticas_bd()['apuestas']}")
assert base_datos.estadisticas_bd()["apuestas"] == antes, "apostó con dato viejo"

print("\n" + "=" * 72)
print("4) NO APUESTA SI EL MERCADO ESTÁ SUSPENDIDO")
print("=" * 72)
p_suspendido = partido_en_descanso(900004, q1=q1, q2=q2, linea=linea_lejos,
                                   mercado=False, liga_tk=liga_con_modelo)
v._ventanas[p_suspendido.evento_id] = time.time()
v._auto_apostar_en_descanso([p_suspendido])
a.update()
print(f"   mercado suspendido -> apuestas en BD: "
      f"{base_datos.estadisticas_bd()['apuestas']}")
assert base_datos.estadisticas_bd()["apuestas"] == antes, "apostó con mercado cerrado"

print("\n" + "=" * 72)
print("5) SÍ APUESTA SOLA CON |Δ| GRANDE Y MERCADO ABIERTO")
print("=" * 72)
v._ventanas[p_ok.evento_id] = time.time()
v._auto_apostar_en_descanso([p_ok])
a.update()
despues = base_datos.estadisticas_bd()["apuestas"]
print(f"   apuestas en BD: {antes} -> {despues}")
print(f"   estado AUTO: {v.lbl_auto_estado.cget('text')}")
assert despues == antes + 1, "la apuesta automática no se guardó"

apuesta = next(ap for ap in base_datos.listar_apuestas()
               if ap.evento_id == p_ok.evento_id)
print(f"   guardada: #{apuesta.id} {apuesta.seleccion} @ {apuesta.linea} "
      f"cuota {apuesta.cuota} stake {apuesta.stake:,.0f}")
print(f"   predicción {apuesta.prediccion:.1f} Δ={apuesta.diferencia:+.1f} "
      f"modelo={apuesta.liga_modelo}")
print(f"   nota: {apuesta.nota}")
assert apuesta.estado == "PENDIENTE"
assert apuesta.seleccion == ("OVER" if pred > linea_lejos else "UNDER")
assert apuesta.liga_modelo == modelo.liga
assert "AUTO descanso2Q" in apuesta.nota
assert apuesta.diferencia and abs(apuesta.diferencia) >= 3.0

print("\n" + "=" * 72)
print("6) NO REPITE LA APUESTA DEL MISMO PARTIDO")
print("=" * 72)
v._ventanas[p_ok.evento_id] = time.time()      # ventana sigue abierta
v._auto_apostar_en_descanso([p_ok])
a.update()
print(f"   segundo ping con el mismo partido -> apuestas en BD: "
      f"{base_datos.estadisticas_bd()['apuestas']}")
assert base_datos.estadisticas_bd()["apuestas"] == despues, "repitió la apuesta"

print("\n" + "=" * 72)
print("7) AL ACABAR EL PARTIDO LA RESUELVE SOLA")
print("=" * 72)
total_final = p_ok.q1 + p_ok.q2 + 60          # 2Q+1Q + un 3Q/4Q cualquiera
p_ok.state = "FINISHED"
p_ok.corriendo = False
p_ok.izq_min, p_ok.izq_seg = 0, 0
p_ok.info_cuartos = (f"Q1: 0-{p_ok.q1} | Q2: 0-{p_ok.q2} | "
                     f"Q3: 0-30 | Q4: 0-30")
p_ok.marcador_local, p_ok.marcador_visitante = total_final - 80, 80
print(f"   partido finalizado: total={p_ok.total_final} (línea {p_ok.linea})")
n = v._resolver_con([p_ok])
a.update()
print(f"   apuestas resueltas: {n}")
apuesta = next(ap for ap in base_datos.listar_apuestas()
               if ap.evento_id == p_ok.evento_id)
esperado = ("GANADA" if (apuesta.seleccion == "OVER") ==
            (p_ok.total_final > apuesta.linea) else "PERDIDA")
print(f"   estado final: {apuesta.estado}  total_final={apuesta.total_final} "
      f"beneficio={apuesta.beneficio:+,.0f}")
print(f"   nota: {apuesta.nota}")
assert n >= 1
assert apuesta.estado == esperado, (apuesta.estado, esperado)

print("\n" + "=" * 72)
print("8) MIDE EL DESCANSO REAL Y LO GUARDA (autocalibración)")
print("=" * 72)
p_transito = partido_en_descanso(900005, q1=q1, q2=q2, linea=linea_lejos,
                                 liga_tk=liga_con_modelo)
v._transitos.clear()
v._vigilar_transito(p_transito, time.time() - 9 * 60)   # visto hace 9 min
print(f"   partido en descanso visto hace 9 min -> registrado en memoria: "
      f"{p_transito.evento_id in v._transitos}")
assert p_transito.evento_id in v._transitos
# ahora el 3Q arranca: debe medir y guardar en hilos
p_transito.q3 = 10
p_transito.period_id = "QUARTER3"
v._vigilar_transito(p_transito, time.time())
assert p_transito.evento_id not in v._transitos, "debía retirar la marca"
limite = time.time() + 10
while time.time() < limite:
    medidos = descansos.descansos_medidos()
    if liga_con_modelo in medidos:
        break
    time.sleep(0.3)
medidos = descansos.descansos_medidos()
print(f"   descansos medidos: {medidos.get(liga_con_modelo)}")
assert liga_con_modelo in medidos, "no guardó el descanso medido"
minutos, n_desc = medidos[liga_con_modelo]
assert 8.0 < minutos < 10.0, minutos

print("\n" + "=" * 72)
print("9) LIMPIEZA (solo las apuestas de ESTA prueba)")
print("=" * 72)
mias = [ap for ap in base_datos.listar_apuestas() if ap.id not in previas]
for ap in mias:
    base_datos.eliminar_apuesta(ap.id)
print(f"   borradas {len(mias)} · "
      f"quedan {base_datos.estadisticas_bd()['apuestas']} (las ajenas)")
conn = base_datos.conectar()
with conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM descansos_liga WHERE liga = %s",
                    (liga_con_modelo,))
conn.close()
print(f"   apuestas restantes: {base_datos.estadisticas_bd()['apuestas']}")

a.destroy()
print("\nPRUEBA DE APUESTA AUTOMÁTICA EN EL DESCANSO OK")
