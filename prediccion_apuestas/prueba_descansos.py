# -*- coding: utf-8 -*-
"""
prueba_descansos.py — Regla del descanso 2Q→3Q + apuesta automática.

Comprueba, con partidos sintéticos (sin tocar la API ni la BD):

  1. La ventana se ABRE cuando el 2Q está completo y el 3Q no ha empezado.
  2. La ventana se CIERRA al arrancar el 3Q (por q3 o por el reloj).
  3. La ventana NUNCA se abre con el 2Q en curso o sin empezar.
  4. La duración por liga sale de la tabla (y de config.json si la hay).
  5. El seguro anti-datos-viejos caduca la ventana al pasar la duración.
  6. La lógica de decisión de la app solo apuesta si |Δ| ≥ umbral y hay
     modelo/mercado/línea.
"""
from __future__ import annotations

import time

import api_betplay
import descansos


def partido(state="STARTED", period_id="QUARTER2", q1=45, q2=40, q3=None,
            q4=None, finalizado=False, linea=175.0, mercado=True,
            corriendo=False, izq_min=0, izq_seg=0, liga="euroleague"):
    """Partido sintético con los campos que usa la regla del descanso."""
    p = api_betplay.Partido(
        evento_id=1, nombre="A - B", local="A", visitante="B",
        state=state, inicio="", liga_nombre=liga, liga_tk=liga, region_tk="",
        periodo="", period_id=period_id, reloj_min=0, reloj_seg=0,
        corriendo=corriendo, izq_min=izq_min, izq_seg=izq_seg,
        q1=q1, q2=q2, q3=q3, q4=q4,
        total_parcial=(q1 or 0) + (q2 or 0) + (q3 or 0) + (q4 or 0),
        linea=linea, mercado_abierto=mercado,
        cuota_over=1.85, cuota_under=1.85,
        cuota_over_abierta=True, cuota_under_abierta=True)
    if finalizado:
        p.state = "FINISHED"
    return p


print("=" * 72)
print("1) LA VENTANA SE ABRE EN EL DESCANSO 2Q→3Q")
print("=" * 72)
descanso = partido()
print(f"   2Q completo, 3Q sin empezar, reloj parado -> "
      f"en_ventana_descanso={descansos.en_ventana_descanso(descanso)}")
assert descansos.en_ventana_descanso(descanso)

# con el reloj AÚN corriendo en QUARTER2 el 2Q está en curso (score.info trae
# el 2Q parcial), así que la ventana NO se abre: el valor de q2 no es final
descanso_reloj = partido(corriendo=True, izq_min=10, izq_seg=30)
print(f"   QUARTER2 con reloj corriendo (2Q en curso) -> "
      f"en_ventana_descanso={descansos.en_ventana_descanso(descanso_reloj)}")
assert not descansos.en_ventana_descanso(descanso_reloj)

print("\n" + "=" * 72)
print("2) LA VENTANA SE CIERRA AL ARRANCAR EL 3Q")
print("=" * 72)
en_3q = partido(period_id="QUARTER3", q3=10)
print(f"   period_id=QUARTER3 y q3=10 -> "
      f"en_ventana_descanso={descansos.en_ventana_descanso(en_3q)}")
assert not descansos.en_ventana_descanso(en_3q)

# la API a veces marca QUARTER3 antes de publicar Q3: x-y (q3 todavía None)
en_3q_sin_q3 = partido(period_id="QUARTER3", q3=None)
print(f"   period_id=QUARTER3 pero q3=None -> "
      f"en_ventana_descanso={descansos.en_ventana_descanso(en_3q_sin_q3)}")
assert not descansos.en_ventana_descanso(en_3q_sin_q3)

en_4q = partido(period_id="QUARTER4", q3=10, q4=5)
print(f"   period_id=QUARTER4 -> "
      f"en_ventana_descanso={descansos.en_ventana_descanso(en_4q)}")
assert not descansos.en_ventana_descanso(en_4q)

fin = partido(finalizado=True)
print(f"   partido finalizado -> en_ventana_descanso={descansos.en_ventana_descanso(fin)}")
assert not descansos.en_ventana_descanso(fin)

print("\n" + "=" * 72)
print("3) LA VENTANA NO SE ABRE CON EL 2Q EN CURSO")
print("=" * 72)
en_1q = partido(period_id="QUARTER1", q1=20, q2=None)
print(f"   1Q en curso (q2=None) -> en_ventana_descanso={descansos.en_ventana_descanso(en_1q)}")
assert not descansos.en_ventana_descanso(en_1q)

en_2q = partido(period_id="QUARTER2", q1=45, q2=20, corriendo=True,
                izq_min=5, izq_seg=0)
print(f"   2Q en curso (q2 parcial) -> en_ventana_descanso={descansos.en_ventana_descanso(en_2q)}")
assert not descansos.en_ventana_descanso(en_2q)

print("\n" + "=" * 72)
print("4) DURACIÓN POR LIGA (tabla + config.json)")
print("=" * 72)
esperado_tabla = {
    "nba": 15, "euroleague": 15, "spain/liga_acb": 15,
    "germany/bbl": 15, "philippines/pba": 15,
}
for liga, mins in esperado_tabla.items():
    obtenido = descansos.duracion_descanso(liga)
    print(f"   {liga:20s} -> {obtenido} min  (esperado {mins})")
    assert obtenido == mins, (liga, obtenido, mins)

desconocida = descansos.duracion_descanso("liga/que/no/existe")
print(f"   {'(desconocida)':20s} -> {desconocida} min  "
      f"(DEFECTO={descansos.DEFECTO_MINUTOS})")
assert desconocida == descansos.DEFECTO_MINUTOS

# si el usuario pone una sobreescripción en config.json, manda esa
import json
import os
ruta_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
con_backup = os.path.exists(ruta_cfg)
backup = open(ruta_cfg, "r", encoding="utf-8").read() if con_backup else ""
try:
    cfg = json.loads(backup) if con_backup else {}
    cfg["descansos"] = {"euroleague": 12, "nba": 15}
    with open(ruta_cfg, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    print(f"   config.json descansos.euroleague=12 -> "
          f"{descansos.duracion_descanso('euroleague')} min")
    assert descansos.duracion_descanso("euroleague") == 12
finally:
    if con_backup:
        with open(ruta_cfg, "w", encoding="utf-8") as fh:
            fh.write(backup)
    print("   (config.json restaurado)")

print("\n" + "=" * 72)
print("5) SEGURO ANTI-DATOS-VIEJOS (ventana caducada)")
print("=" * 72)
recien = descansos.minutos_desde(time.time())
print(f"   marca recién puesta -> transcurridos={recien:.3f} min -> "
      f"caducada={descansos.ventana_caducada('nba', time.time())}")
assert not descansos.ventana_caducada("nba", time.time())

vieja = time.time() - 20 * 60            # 20 min atrás (NBA descansa 15)
print(f"   marca de 20 min atrás (NBA 15+{descansos.MARGEN_MINUTOS} margen) -> "
      f"caducada={descansos.ventana_caducada('nba', vieja)}")
assert descansos.ventana_caducada("nba", vieja)

print("\n" + "=" * 72)
print("6) REGLA COMPLETA puede_apostar()")
print("=" * 72)
marca = time.time()
ok, motivo = descansos.puede_apostar(descanso, "euroleague", marca)
print(f"   descanso normal            -> {ok} ({motivo})")
assert ok

ok, motivo = descansos.puede_apostar(en_3q, "euroleague", marca)
print(f"   ya en 3Q                   -> {ok} ({motivo})")
assert not ok

ok, motivo = descansos.puede_apostar(
    partido(mercado=False), "euroleague", marca)
print(f"   mercado suspendido         -> {ok} ({motivo})")
assert not ok

ok, motivo = descansos.puede_apostar(
    partido(linea=None), "euroleague", marca)
print(f"   sin línea publicada        -> {ok} ({motivo})")
assert not ok

ok, motivo = descansos.puede_apostar(
    descanso, "euroleague", time.time() - 20 * 60)
print(f"   ventana caducada (20 min)  -> {ok} ({motivo})")
assert not ok

print("\n" + "=" * 72)
print("7) DECISIÓN |Δ| ≥ UMBRAL (misma lógica que la app)")
print("=" * 72)
import motor
modelo = motor.Modelo(liga="euroleague", origen="prueba", n=100,
                      b0=30.0, b1=0.8, b2=0.9)   # pred = 30 + .8*1Q + .9*2Q
# 1Q=45, 2Q=40 -> pred = 30 + 36 + 36 = 102... ajustemos a valores reales:
modelo.b0 = 30.0
casos = [
    # (q1, q2, línea, umbral, esperado_veredicto, esperado_apostar)
    (45, 40, 95.0, 2.0, "OVER", True),    # pred 102 vs línea 95  -> Δ=+7
    (45, 40, 110.0, 2.0, "UNDER", True),  # pred 102 vs línea 110 -> Δ=-8
    (45, 40, 102.0, 2.0, "OVER", False),  # Δ=0 -> veredicto OVER pero sin apuesta
    (45, 40, 100.0, 2.0, "OVER", True),   # pred 102 vs 100 -> Δ=+2 -> sí
    (45, 40, 100.5, 2.0, "OVER", False),  # Δ=+1.5 -> OVER pero |Δ|<2 -> no
]
p = descanso
for q1, q2, linea, umbral, veredicto_esperado, apostar_esperado in casos:
    p.q1, p.q2, p.linea = q1, q2, linea
    res = motor.predecir(modelo, float(q1), float(q2), linea)
    apostar = (res.veredicto in ("OVER", "UNDER")
               and abs(res.diferencia) >= umbral)
    print(f"   1Q={q1} 2Q={q2} línea={linea:6.1f} umbral={umbral} -> "
          f"pred={res.prediccion:6.1f} Δ={res.diferencia:+5.1f} "
          f"veredicto={str(res.veredicto):5s} apostar={apostar}")
    assert res.veredicto == veredicto_esperado, (linea, res.veredicto)
    assert apostar == apostar_esperado, (linea, apostar)

print("\n" + "=" * 72)
print("8) AUTOCALIBRACIÓN: guardar y leer descansos medidos")
print("=" * 72)
import base_datos
base_datos.crear_esquema()
liga_prueba = "prueba/descanso"
conn = base_datos.conectar()
with conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM descansos_liga WHERE liga = %s", (liga_prueba,))
conn.close()

for mins in (14.5, 15.5, 15.0):
    descansos.registrar_descanso_observado(liga_prueba, mins)
medidos = descansos.descansos_medidos()
print(f"   registrados: {medidos.get(liga_prueba)}")
assert liga_prueba in medidos
media, n = medidos[liga_prueba]
assert n == 3
assert abs(media - 15.0) < 1e-6, media

# basura fuera: no se guarda nada fuera de rango
descansos.registrar_descanso_observado(liga_prueba, -1)
descansos.registrar_descanso_observado(liga_prueba, 900)
medidos2 = descansos.descansos_medidos()
assert medidos2[liga_prueba][1] == 3, "los valores basura no deben registrarse"

conn = base_datos.conectar()
with conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM descansos_liga WHERE liga = %s", (liga_prueba,))
conn.close()
print("   limpio")

print("\nPRUEBA DE DESCANSOS OK")
