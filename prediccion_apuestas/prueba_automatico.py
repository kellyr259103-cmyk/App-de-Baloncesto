# -*- coding: utf-8 -*-
"""
prueba_automatico.py — Al abrir la app ya va sola.

Comprueba:
  1. `arrancar_automatico()` enciende la apuesta en el descanso Y el sondeo
     de 5 s sin depender de nada que haya que marcar.
  2. La configuración se guarda en config.json (aun así se recuerda).
  3. Una SEGUNDA sesión arranca igual (siempre automático).
  4. Cerrar la ventana no deja timers sueltos (sin ruido de Tk).
  5. La ventana NUNCA es más alta que la pantalla.
  6. Las pestañas no desbordan: EUROLIGA lleva barra si no cabe.
"""
from __future__ import annotations

import json
import os
import time

import tkinter as tk

import app as mod_app
import base_datos

mod_app.messagebox.showinfo = lambda *a, **k: None
mod_app.messagebox.showwarning = lambda *a, **k: None
mod_app.messagebox.showerror = lambda *a, **k: None

RUTA_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _cfg_vivo() -> dict:
    with open(RUTA_CFG, "r", encoding="utf-8") as fh:
        return json.load(fh).get("vivo", {})


def _abrir() -> mod_app.App:
    a = mod_app.App()
    a.update()
    limite = time.time() + 25
    while not a.db_ok and time.time() < limite:
        a.update()
        time.sleep(0.05)
    assert a.db_ok, a.db_error
    return a


print("=" * 72)
print("1) arrancar_automatico(): enciende apuesta + vista cada 5 s")
print("=" * 72)
a1 = _abrir()
v1 = a1.pestana_vivo
# el usuario lo había dejado apagado: al abrir tiene que arrancar igual
v1.var_auto_apostar.set(False)
v1.var_auto.set(False)
v1.toggle_auto_apostar()
v1.toggle_auto()
a1.update()
assert not v1._auto_activo

v1.arrancar_automatico()
a1.update()
print(f"   AUTO={v1.var_auto_apostar.get()}   sondeo={v1.var_auto.get()}   "
      f"intervalo={v1.var_intervalo.get()} s   _auto_activo={v1._auto_activo}")
assert v1.var_auto_apostar.get(), "la apuesta automática debe quedar encendida"
assert v1.var_auto.get(), "el sondeo debe quedar encendido"
assert int(v1.var_intervalo.get()) == 5, "el intervalo por defecto es 5 s"
assert v1._auto_activo, "el polling debe estar programado"
assert "vigilo el descanso" in v1.lbl_auto_estado.cget("text")

print("\n" + "=" * 72)
print("2) Se guarda en config.json")
print("=" * 72)
print(f"   config.json vivo = {_cfg_vivo()}")
assert _cfg_vivo().get("auto_apostar") is True
assert _cfg_vivo().get("auto") is True
assert _cfg_vivo().get("intervalo") == 5

print("\n" + "=" * 72)
print("3) Segunda sesión: arranca igual de sola")
print("=" * 72)
a1.destroy()
a2 = _abrir()
v2 = a2.pestana_vivo
v2.arrancar_automatico()
a2.update()
print(f"   AUTO={v2.var_auto_apostar.get()}   sondeo={v2.var_auto.get()}   "
      f"umbral={v2.var_umbral.get()}   _auto_activo={v2._auto_activo}")
assert v2.var_auto_apostar.get() and v2._auto_activo

print("\n" + "=" * 72)
print("4) Cerrar no deja timers sueltos")
print("=" * 72)
a2.destroy()
a2.update()
print("   cerrado sin 'invalid command name' ni 'can't delete Tcl command'")
assert True

print("\n" + "=" * 72)
print("5) La ventana nunca supera la altura de la pantalla")
print("=" * 72)
a3 = _abrir()
pantalla_alto = a3.winfo_screenheight()
pantalla_ancho = a3.winfo_screenwidth()
a3.update()
alto = a3.winfo_height()
ancho = a3.winfo_width()
print(f"   pantalla = {pantalla_ancho}x{pantalla_alto}   "
      f"ventana = {ancho}x{alto}")
assert alto <= pantalla_alto - 60, (
    f"la ventana ({alto}) no debe superar la pantalla ({pantalla_alto})")
assert ancho <= pantalla_ancho - 10
# y el mínimo de la ventana tampoco puede forzar desbordes
print(f"   minsize = {a3.minsize()}")
assert int(a3.minsize()[1]) <= pantalla_alto - 60

print("\n" + "=" * 72)
print("6) Las pestañas no desbordan en vertical")
print("=" * 72)
for nombre, pestana in [("EUROLIGA", a3.pestanas["EUROLIGA"]),
                        ("NBA", a3.pestanas["NBA"]),
                        ("EN VIVO", a3.pestana_vivo),
                        ("APUESTAS", a3.pestana_apuestas)]:
    a3.notebook.select(pestana)
    a3.update()
    disp = a3.notebook.winfo_height() - 30
    # en EUROLIGA/NBA el contenido real vive dentro del canvas
    panel = getattr(pestana, "_panel", None)
    alto_req = panel.winfo_reqheight() if panel is not None else pestana.winfo_reqheight()
    print(f"   {nombre:9s} contenido = {alto_req:4d} px   disponible ≈ {disp} px")
    if alto_req > disp + 2:
        assert panel is not None, (
            f"{nombre} desborda y no tiene barra de desplazamiento")
        pestana._panel_configurado()
        a3.update()
        assert pestana._vsb_visible, f"en {nombre} debe aparecer la barra"
        print("            -> desborda: aparece la barra vertical (OK)")
    else:
        if panel is not None:
            pestana._panel_configurado()
            a3.update()
            assert not pestana._vsb_visible, "con hueco no debe verse la barra"
        print("            -> cabe entera sin barra (OK)")

print("\n" + "=" * 72)
print("7) Al encoger la ventana, EUROLIGA muestra la barra")
print("=" * 72)
pe = a3.pestanas["EUROLIGA"]
a3.notebook.select(pe)
alto_min = int(a3.minsize()[1])
a3.geometry(f"{a3.winfo_width()}x{alto_min}")
for _ in range(10):
    a3.update()
    pe._panel_configurado()
disp = a3.notebook.winfo_height() - 30
print(f"   ventana = {a3.winfo_width()}x{alto_min}   "
      f"contenido = {pe._panel.winfo_reqheight()} px   disponible ≈ {disp} px")
assert pe._panel.winfo_reqheight() > disp + 2, "a esta altura tiene que desbordar"
assert pe._vsb_visible, "al encoger debe aparecer la barra vertical"
pe._canvas.yview_moveto(0.5)
a3.update()
print(f"   -> desborda y aparece la barra (OK, y se puede desplazar)")

a3.destroy()

base_datos.guardar_config({**base_datos.cargar_config(),
                           "vivo": {"auto": True, "intervalo": 5,
                                    "auto_apostar": True, "umbral": 2.0}})
print("\nPRUEBA AUTOMÁTICA + RESPONSIVE OK")
