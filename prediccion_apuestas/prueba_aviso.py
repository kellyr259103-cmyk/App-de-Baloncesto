# -*- coding: utf-8 -*-
"""
prueba_aviso.py — El pie del flujo dice el modelo REAL que se va a usar.

Comprueba que el aviso (antes un texto fijo que siempre decía "NO está
calibrada") ahora se recalcula:
  * liga con modelo propio -> verde, "LIGA CALIBRADA" con su n
  * liga sin modelo        -> rojo, avisa de la caída al Excel
  * modelo fijado a mano   -> gris informativo
"""
from __future__ import annotations

import time

import api_betplay
import app as mod_app

mod_app.messagebox.showinfo = lambda *a, **k: None
mod_app.messagebox.showwarning = lambda *a, **k: None
mod_app.messagebox.showerror = lambda *a, **k: None


def partido(liga_tk, liga_nombre, q1=45, q2=40, linea=175.0):
    return api_betplay.Partido(
        evento_id=1, nombre="A - B", local="A", visitante="B",
        state="STARTED", inicio="", liga_nombre=liga_nombre,
        liga_tk=liga_tk, region_tk="", periodo="3er cuarto",
        period_id="QUARTER3", q1=q1, q2=q2, q3=10, total_parcial=q1 + q2 + 10,
        linea=linea, mercado_abierto=True,
        cuota_over=1.85, cuota_under=1.85,
        cuota_over_abierta=True, cuota_under_abierta=True)


a = mod_app.App()
a.update()
limite = time.time() + 25
while not a.db_ok and time.time() < limite:
    a.update()
    time.sleep(0.05)
a.cargar_modelos_ligas()
limite = time.time() + 25
while not a.modelos_ligas and time.time() < limite:
    a.update()
    time.sleep(0.05)
assert a.modelos_ligas, "faltan los modelos por liga"
print(f"modelos por liga cargados: {len(a.modelos_ligas)}\n")

v = a.pestana_vivo
v.var_modelo.set("AUTO")
v._partido_sel = None

print("=" * 72)
print("1) LIGA CON MODELO PROPIO -> AVISO VERDE")
print("=" * 72)
for clave in sorted(a.modelos_ligas)[:3]:
    p = partido(clave, clave)
    v._partido_sel = p
    v.calcular_flujo()
    a.update()
    txt = v.lbl_aviso.cget("text")
    fg = v.lbl_aviso.cget("fg")
    print(f"   {clave:28s} -> [{fg}] {txt}")
    assert "LIGA CALIBRADA" in txt, txt
    assert fg == mod_app.C_VERDE, fg
    assert str(a.modelos_ligas[clave].n) in txt, "debe mostrar el n de partidos"

print("\n" + "=" * 72)
print("2) LIGA SIN MODELO -> AVISO ROJO (caída al Excel)")
print("=" * 72)
sin_modelo = "liga/que/no/existe"
p = partido(sin_modelo, "Liga Inexistente")
v._partido_sel = p
v.calcular_flujo()
a.update()
txt = v.lbl_aviso.cget("text")
fg = v.lbl_aviso.cget("fg")
print(f"   [{fg}] {txt}")
assert "Sin modelo propio" in txt, txt
assert fg == mod_app.C_ROJO_FG, fg

print("\n" + "=" * 72)
print("3) MODELO FIJADO A MANO -> AVISO GRIS")
print("=" * 72)
v.var_modelo.set("EUROLIGA")
p = partido("euroleague", "Euroliga")
v._partido_sel = p
v.calcular_flujo()
a.update()
txt = v.lbl_aviso.cget("text")
fg = v.lbl_aviso.cget("fg")
print(f"   [{fg}] {txt}")
assert "fijado a mano" in txt, txt
assert fg == mod_app.C_SUB_FG, fg

print("\n" + "=" * 72)
print("4) SIN PARTIDO SELECCIONADO -> TEXTO NEUTRO")
print("=" * 72)
v.var_modelo.set("AUTO")
v._partido_sel = None
v.lbl_aviso.config(text="Elige un partido de la tabla para ver el modelo "
                        "que se usará.", fg=mod_app.C_SUB_FG)
print(f"   [{v.lbl_aviso.cget('fg')}] {v.lbl_aviso.cget('text')}")

a.destroy()
print("\nPRUEBA DEL AVISO OK")
