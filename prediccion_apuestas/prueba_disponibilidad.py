# -*- coding: utf-8 -*-
"""
prueba_disponibilidad.py — ¿Y si la casa NO tiene la apuesta disponible?

Audita los tres fallos que destapó `auditoria_auto.py` y comprueba que ya no
se cuelan apuestas en mercados que la casa no ofrece:

  1. Parsing: el lado UNDER también se comprueba (antes solo el OVER).
  2. Parsing: oferta entera suspendida -> ningún lado queda "abierto".
  3. AUTO: NO apuesta si el lado concreto está suspendido (aunque la cuota
     siga visible) y enseña el motivo en el panel.
  4. AUTO: NO apuesta si el valor esperado es negativo (puerta EV).
  5. AUTO: sí apuesta cuando todo está bien, y la nota lleva el EV.
"""
from __future__ import annotations

import time

import api_betplay
import app as mod_app
import base_datos

avisos = []
mod_app.messagebox.showinfo = lambda t, x, **k: avisos.append(("info", t, x))
mod_app.messagebox.showwarning = lambda t, x, **k: avisos.append(("aviso", t, x))
mod_app.messagebox.showerror = lambda t, x, **k: avisos.append(("error", t, x))


def bloque_total(linea=175000, over=1850, under=1850,
                 susp_oferta=False, est_over="OPEN", est_under="OPEN"):
    """Oferta de total cruda, como la devuelve la API (odds x1000)."""
    return {
        "event": {"id": 910001, "name": "A - B", "homeName": "A",
                  "awayName": "B", "state": "STARTED", "start": ""},
        "liveData": {
            "matchClock": {"period": "2do cuarto", "periodId": "QUARTER2",
                           "minute": 0, "second": 0, "running": False,
                           "minutesLeftInPeriod": 0, "secondsLeftInMinute": 0},
            "score": {"home": "50", "away": "45", "info": "Q1: 25-25 | Q2: 25-20"},
        },
        "betOffers": [{
            "betOfferType": {"id": api_betplay.ID_TOTAL},
            "criterion": {"label": "Total de puntos - Prórroga incluida"},
            "suspended": susp_oferta,
            "outcomes": [
                {"type": "OT_OVER", "odds": over, "line": linea, "status": est_over},
                {"type": "OT_UNDER", "odds": under, "line": linea, "status": est_under},
            ],
        }],
    }


def partido_en_descanso(evento_id, q1, q2, linea, liga_tk, liga_nombre,
                        cuota_over, cuota_under, over_abierta, under_abierta,
                        mercado_abierto=True):
    return api_betplay.Partido(
        evento_id=evento_id, nombre=f"Equipo{evento_id} - Rival{evento_id}",
        local=f"Equipo{evento_id}", visitante=f"Rival{evento_id}",
        state="STARTED", inicio="", liga_nombre=liga_nombre,
        liga_tk=liga_tk, region_tk="", periodo="2do cuarto",
        period_id="QUARTER2", reloj_min=0, reloj_seg=0,
        corriendo=False, izq_min=0, izq_seg=0,
        q1=q1, q2=q2, q3=None, total_parcial=q1 + q2,
        linea=linea, mercado_abierto=mercado_abierto,
        cuota_over=cuota_over, cuota_under=cuota_under,
        cuota_over_abierta=over_abierta, cuota_under_abierta=under_abierta)


print("=" * 72)
print("1) PARSING: CADA LADO SE COMPRUEBA POR SEPARADO")
print("=" * 72)
p = api_betplay._parsear_evento(bloque_total())
print(f"   todo OPEN      -> over_abierta={p.cuota_over_abierta}  "
      f"under_abierta={p.cuota_under_abierta}  mercado={p.mercado_abierto}")
assert p.cuota_over_abierta and p.cuota_under_abierta and p.mercado_abierto

p = api_betplay._parsear_evento(bloque_total(est_under="SUSPENDED"))
print(f"   Under SUSPEND. -> over_abierta={p.cuota_over_abierta}  "
      f"under_abierta={p.cuota_under_abierta}  "
      f"(la cuota under seguía visible: {p.cuota_under})")
assert p.cuota_over_abierta, "el Over sigue abierto"
assert not p.cuota_under_abierta, "el Under suspendido NO debe quedar abierto"
assert p.cuota_under is not None, "la cuota se conserva (histórico), pero no sirve"
assert not p.hay_cuota_under
assert p.hay_cuota_over

p = api_betplay._parsear_evento(bloque_total(est_over="SUSPENDED"))
print(f"   Over SUSPEND.  -> over_abierta={p.cuota_over_abierta}  "
      f"under_abierta={p.cuota_under_abierta}")
assert not p.cuota_over_abierta and p.cuota_under_abierta

p = api_betplay._parsear_evento(bloque_total(susp_oferta=True))
print(f"   oferta entera  -> mercado_abierto={p.mercado_abierto}  "
      f"over={p.cuota_over_abierta}  under={p.cuota_under_abierta}")
assert not p.mercado_abierto
assert not p.cuota_over_abierta and not p.cuota_under_abierta

print("\n" + "=" * 72)
print("2) ABRIR LA APP (PostgreSQL + modelos por liga)")
print("=" * 72)
a = mod_app.App()
a.update()
limite = time.time() + 25
while not a.db_ok and time.time() < limite:
    a.update(); time.sleep(0.05)
assert a.db_ok, a.db_error
a.cargar_modelos_ligas()
limite = time.time() + 25
while not a.modelos_ligas and time.time() < limite:
    a.update(); time.sleep(0.05)
assert a.modelos_ligas, "hacen falta modelos entrenados por liga"

v = a.pestana_vivo
v.var_auto_apostar.set(True)
v.var_stake.set("10000")
v.var_umbral.set(0.5)
v.var_ev_min.set(-20.0)          # muy laxo: solo para aislar el resto de gates
v._ventanas.clear()
v._apuestas_auto.clear()

liga = sorted(a.modelos_ligas)[0]
modelo = a.modelos_ligas[liga]
q1 = int(modelo.media_1q) if modelo.media_1q else 50
q2 = int(modelo.media_2q) if modelo.media_2q else 45
pred = modelo.predecir_dos(q1, q2)
# Δ = +4, dentro del rango que la app apostará por defecto (3–5); así las
# secciones siguientes llegan de verdad al mercado y al EV sin que el filtro
# de rango se los coma antes.
linea = round(pred - 4.0, 1)
print(f"   liga={liga} (n={modelo.n})  1Q={q1} 2Q={q2} pred={pred:.1f} "
      f"línea={linea}")

print("\n" + "=" * 72)
print("3) NO APUESTA SI EL LADO QUE QUEREMOS ESTÁ SUSPENDIDO")
print("=" * 72)
antes = base_datos.estadisticas_bd()["apuestas"]
p_susp = partido_en_descanso(910001, q1, q2, linea, liga, "Liga",
                             cuota_over=1.90, cuota_under=1.90,
                             over_abierta=False, under_abierta=True)
v._auto_apostar_en_descanso([p_susp])
a.update()
print(f"   motivo recogido: {dict(v._motivos_auto)}")
print(f"   texto del panel: {v.lbl_auto_motivos.cget('text')!r}")
assert "SUSPENDIDO" in " ".join(v._motivos_auto), v._motivos_auto
assert "SUSPENDIDO" in v.lbl_auto_motivos.cget("text")
assert base_datos.estadisticas_bd()["apuestas"] == antes, "no debe haber apostado"

print("\n" + "=" * 72)
print("4) NO APUESTA SI EL VALOR ESPERADO ES NEGATIVO")
print("=" * 72)
v._motivos_auto.clear()
v._apuestas_auto.clear()
# cuota tan mala que, con la probabilidad del modelo, es -EV
p_ev = partido_en_descanso(910002, q1, q2, linea, liga, "Liga",
                           cuota_over=1.01, cuota_under=1.01,
                           over_abierta=True, under_abierta=True)
v.var_ev_min.set(0.0)            # exige EV >= 0 %
v._auto_apostar_en_descanso([p_ev])
a.update()
print(f"   motivo recogido: {dict(v._motivos_auto)}")
assert any(m.startswith("EV ") for m in v._motivos_auto), v._motivos_auto
assert base_datos.estadisticas_bd()["apuestas"] == antes, "no debe haber apostado"

print("\n" + "=" * 72)
print("5) SÍ APUESTA CUANDO TODO ESTÁ BIEN (y la nota lleva el EV)")
print("=" * 72)
v._motivos_auto.clear()
v._apuestas_auto.clear()
v._ventanas.clear()
v.var_ev_min.set(-20.0)
p_ok = partido_en_descanso(910003, q1, q2, linea, liga, "Liga",
                           cuota_over=1.90, cuota_under=1.90,
                           over_abierta=True, under_abierta=True)
v._auto_apostar_en_descanso([p_ok])
a.update()
print(f"   apuestas en BD: {antes} -> {base_datos.estadisticas_bd()['apuestas']}")
assert 910003 in v._apuestas_auto, "debería haber apostado"
regs = [r for r in base_datos.listar_apuestas() if r.evento_id == 910003]
assert regs, "debe quedar registrada en PostgreSQL"
print(f"   nota: {regs[0].nota!r}")
assert "EV=" in regs[0].nota, "la nota debe llevar el valor esperado"
base_datos.eliminar_apuesta(regs[0].id)

print("\n" + "=" * 72)
print("6) LIGA SIN MODELO PROPIO: respaldo NBA (con su política)")
print("=" * 72)
# REGRESIÓN: el bot miraba `modelos_ligas[clave]` directamente y se negaba a
# apostar aunque la propia interfaz enseñara VEREDICTO con el modelo del
# Excel (p. ej. nba_preseason, que cae al de la NBA con n=1340).
#
# Y además ahora existe la política `config.json -> auto.solo_modelo_propio`
# (activada por defecto, porque esas apuestas de respaldo fueron 0/3). Se
# comprueba lo primero y lo segundo: que al abrirla el circuito original
# sigue apostando.
v._motivos_auto.clear()
v._apuestas_auto.clear()
v._ventanas.clear()
v.var_ev_min.set(-20.0)

p_fb = partido_en_descanso(910004, q1, q2, 150.0, "nba_prueba_fallback",
                           "Liga sin modelo propio",
                           cuota_over=1.90, cuota_under=1.90,
                           over_abierta=True, under_abierta=True)
mfb, etq = a.modelo_para_partido(p_fb, "AUTO")
print(f"   liga '{p_fb.clave_liga}' no está en modelos_ligas: "
      f"{p_fb.clave_liga not in a.modelos_ligas}")
assert p_fb.clave_liga not in a.modelos_ligas, "la liga debe ser nueva"
print(f"   modelo que usa: {mfb.liga} (n={mfb.n}) · {etq}")
assert mfb.n > 0, "el respaldo debe estar entrenado"
# Δ = +4, dentro del rango que la app apostará por defecto (3–5)
p_fb.linea = round(mfb.predecir_dos(float(q1), float(q2)) - 4.0, 1)

_config_real = base_datos.cargar_config

print("   a) política por defecto (solo modelo propio) -> NO apuesta")
v._auto_apostar_en_descanso([p_fb])
a.update()
print(f"   motivo recogido: {dict(v._motivos_auto)}")
assert 910004 not in v._apuestas_auto, "no debe apostar con modelo ajeno"
assert any("respaldo" in str(m) for m in v._motivos_auto), v._motivos_auto

print("   b) con `auto.solo_modelo_propio: false` -> SÍ apuesta")
base_datos.cargar_config = lambda: {**_config_real(),
                                    "auto": {"solo_modelo_propio": False}}
v._motivos_auto.clear()
v._apuestas_auto.clear()
v._ventanas.clear()
try:
    v._auto_apostar_en_descanso([p_fb])
finally:
    base_datos.cargar_config = _config_real      # nunca dejarlo parcheado
a.update()
print(f"   motivo recogido: {dict(v._motivos_auto)}")
assert 910004 in v._apuestas_auto, (
    f"debería haber apostado con el modelo de respaldo; motivos: "
    f"{dict(v._motivos_auto)}")
regs = [r for r in base_datos.listar_apuestas() if r.evento_id == 910004]
assert regs, "debe quedar registrada en PostgreSQL"
print(f"   liga_modelo guardada: {regs[0].liga_modelo!r}")
print(f"   nota: {regs[0].nota!r}")
assert "modelo" in regs[0].nota, "la nota debe avisar de qué modelo se usó"
assert regs[0].liga_modelo == mfb.liga, "debe guardar la liga del modelo usado"
base_datos.eliminar_apuesta(regs[0].id)

a.destroy()
print("\nPRUEBA DE DISPONIBILIDAD OK")
