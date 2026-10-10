# -*- coding: utf-8 -*-
"""
Prueba de la pestaña EN VIVO con selector de ligas.

Comprueba, con llamadas reales a la API:
  1) Descubrimiento automático de las ligas de baloncesto.
  2) Modo "TODAS LAS LIGAS" (una sola petición) con columna Liga + pred + Δ.
  3) Orden de la tabla por tamaño de diferencia y etiqueta de oportunidad.
  4) Selección de una liga concreta -> solo sus partidos.
  5) Flujo 1Q + 2Q sobre un partido y guarda de la apuesta en PostgreSQL.
No deja residuos: la apuesta que crea la borra al final.
"""
import time

import app as mod_app
import api_betplay
import base_datos
import paperbet

avisos = []
mod_app.messagebox.showinfo = lambda t, x, **k: avisos.append(("info", t, x))
mod_app.messagebox.showwarning = lambda t, x, **k: avisos.append(("aviso", t, x))
mod_app.messagebox.showerror = lambda t, x, **k: avisos.append(("error", t, x))


def bombardear(app, condicion, segundos=60):
    """Hace update() hasta que se cumpla `condicion` o pasen los segundos."""
    limite = time.time() + segundos
    while time.time() < limite:
        app.update()
        if condicion():
            return True
        time.sleep(0.05)
    return condicion()


print("=" * 78)
print("1) ABRIR LA VENTANA Y DESCUBRIR LAS LIGAS")
print("=" * 78)
a = mod_app.App()
a.update()
limite = time.time() + 25
while not a.db_ok and time.time() < limite:
    a.update()
    time.sleep(0.05)
assert a.db_ok, f"PostgreSQL no responde: {a.db_error}"

v = a.pestana_vivo
pings = []
_orig_fin = v._ping_terminado


def _fin(partidos, error):
    pings.append((len(partidos), error))
    _orig_fin(partidos, error)


v._ping_terminado = _fin
a.after(50, v.cargar_ligas)
ok = bombardear(a, lambda: len(v.ligas) > 0)
assert ok, "no se descubrieron las ligas"
print(f"   ligas descubiertas : {len(v.ligas)}")
print(f"   desplegable        : {len(v.combo_liga['values'])} opciones")
print(f"   aviso de arranque  : {v.lbl_oportunidad.cget('text')}")
assert v.combo_liga["values"][0] == v.TODAS
assert len(v.combo_liga["values"]) == len(v.ligas) + 1
muestra_ligas = [f"{l.nombre} [{l.clave}]" for l in v.ligas[:5]]
print("   primeras ligas     :", ", ".join(muestra_ligas))

print("\n" + "=" * 78)
print("2) MODO 'TODAS LAS LIGAS' (una sola petición, el deporte entero)")
print("=" * 78)
v.var_liga.set(v.TODAS)
a.after(50, v.hacer_ping)
ok = bombardear(a, lambda: len(pings) >= 1 and not v._ping_en_curso, 90)
assert ok, "el ping de todas las ligas no terminó"
print(f"   partidos           : {len(v.partidos)}")
print(f"   con liga asignada  : {sum(1 for p in v.partidos if p.liga_nombre)}"
      f"/{len(v.partidos)}")
print(f"   ligas distintas    : "
      f"{len({p.liga_nombre for p in v.partidos if p.liga_nombre})}")
print(f"   estado             : {v.lbl_ping_estado.cget('text')}")
filas = v.tree.get_children()
assert filas, "la tabla quedó vacía"
assert len(filas) == len(v.partidos), "la tabla no cuadra con los partidos"

valores = v.tree.item(filas[0], "values")
print(f"   1ª fila (mejor Δ)  : {valores}")
assert valores[0] not in ("", "—"), "la columna Liga viene vacía"
print(f"   fila resaltada     : {v.tree.item(filas[0], 'tags')}")
print(f"   oportunidad        : {v.lbl_oportunidad.cget('text')}")

# el orden debe ser: (0) predecibles en línea comparable por |Δ| desc,
# (1) predecibles ya en 4º cuarto, (2) el resto. Se usa el MISMO criterio
# que la interfaz (PestanaVivo.grupo_fila).
por_id = {p.evento_id: p for p in v.partidos}
grupos, difs = [], []
for iid in filas:
    p = por_id[int(iid)]
    res = v._prediccion_de(p)
    grupos.append(mod_app.PestanaVivo.grupo_fila(p, res))
    difs.append(None if res is None else abs(res.diferencia))

assert grupos == sorted(grupos), f"la tabla no está agrupada: {grupos}"
for g in (0, 1):
    dentro_g = [d for d, gg in zip(difs, grupos) if gg == g]
    assert dentro_g == sorted(dentro_g, reverse=True), \
        f"el grupo {g} no está ordenado por |Δ|: {dentro_g}"
print(f"   grupos 0/1/2       : {grupos.count(0)}/{grupos.count(1)}/{grupos.count(2)}")
print(f"   |Δ| del grupo 0    : "
      f"{[round(d, 1) for d, gg in zip(difs, grupos) if gg == 0][:6]} ...")
if grupos.count(0):
    assert all(por_id[int(iid)].q2_completo
               for iid, g in zip(filas, grupos) if g == 0), \
        "el grupo 0 debe exigir 1Q y 2Q finales"
    print("   grupo 0 exige 1Q+2Q finales: OK")
else:
    print("   (hoy no hay ningún partido con 1Q+2Q acabados)")

print("\n" + "=" * 78)
print("3) LIGA CONCRETA -> SOLO SUS PARTIDOS")
print("=" * 78)
elegida = next(l for l in v.ligas if l.clave == "spain/liga_acb")
v.var_liga.set(elegida.nombre)
espera = len(pings)
a.after(50, v.hacer_ping)
ok = bombardear(a, lambda: len(pings) > espera and not v._ping_en_curso, 60)
assert ok, f"falló el ping de {elegida.nombre}"
print(f"   liga seleccionada  : {elegida.nombre} ({elegida.patron})")
print(f"   partidos           : {len(v.partidos)}")
print(f"   estado             : {v.lbl_ping_estado.cget('text')}")
for p in v.partidos[:4]:
    print(f"     - {p.nombre[:52]:52s} liga={p.liga_nombre} "
          f"linea={p.linea} mercado={'ABIERTO' if p.mercado_abierto else 'CERRADO'}")
assert all(p.liga_nombre == elegida.nombre for p in v.partidos), \
    "se coló un partido de otra liga"
assert len(v.partidos) < 40, "la liga ACB no puede tener 40 partidos hoy"

print("\n" + "=" * 78)
print("4) FLUJO 1Q + 2Q SOBRE UN PARTIDO CUALQUIERA")
print("=" * 78)
v.var_liga.set(v.TODAS)
espera = len(pings)
a.after(50, v.hacer_ping)
bombardear(a, lambda: len(pings) > espera and not v._ping_en_curso, 90)

candidato = next((p for p in v.partidos
                  if p.listo_para_prediccion and p.q2_completo
                  and p.linea and p.mercado_abierto),
                 next((p for p in v.partidos
                       if p.listo_para_prediccion and p.linea and p.mercado_abierto),
                      None))
if candidato is None:
    # a esta hora puede no haber ningún partido con 1Q+2Q y mercado abierto:
    # se ejercita el flujo con un partido sintético (campos iguales a un real)
    print("   (sin partido con 1Q+2Q y mercado abierto: uso uno sintético)")
    candidato = api_betplay.Partido(
        evento_id=0, nombre="Local - Visitante", local="Local",
        visitante="Visitante", state="STARTED", inicio="",
        liga_nombre="Euroliga", liga_tk="euroleague", region_tk="",
        periodo="3er cuarto", period_id="QUARTER3",
        q1=45.0, q2=40.0, q3=0, q4=0, total_parcial=85.0,
        linea=175.0, mercado_abierto=True,
        cuota_over=1.85, cuota_under=1.85,
        cuota_over_abierta=True, cuota_under_abierta=True)
v._partido_sel = candidato
v.var_modelo.set("EUROLIGA")
v.calcular_flujo()
a.update()
print(f"   liga        : {candidato.liga_nombre}")
print(f"   partido     : {candidato.nombre}")
print(f"   1Q / 2Q     : {v.lbl_q1.cget('text')} / {v.lbl_q2.cget('text')}")
print(f"   línea       : {v.lbl_linea.cget('text')}")
print(f"   predicción  : {v.lbl_pred.cget('text')}")
print(f"   veredicto   : {v.lbl_veredicto.cget('text')}")
print(f"   estado      : {v.lbl_flujo_estado.cget('text')}")
print(f"   APOSTAR     : {v.btn_apostar.cget('state')}")
assert v.lbl_veredicto.cget("text") in ("OVER", "UNDER")
assert str(v.btn_apostar.cget("state")) == "normal"

print("\n" + "=" * 78)
print("5) APUESTA EN OTRA LIGA (se guarda con el nombre de su liga)")
print("=" * 78)
antes = base_datos.estadisticas_bd()["apuestas"]
v.var_stake.set("10000")
v.apostar()
a.update()
info = avisos[-1] if avisos else None
print(f"   diálogo     : {info}")
assert info and info[0] == "info", "la apuesta no se guardó"
nueva = max(base_datos.listar_apuestas(), key=lambda x: x.id)
print(f"   guardada    : id={nueva.id} liga={nueva.liga_apuesta!r} "
      f"seleccion={nueva.seleccion} linea={nueva.linea} cuota={nueva.cuota}")
assert nueva.liga_apuesta == candidato.liga_nombre, \
    "la apuesta no guarda la liga real del partido"
base_datos.eliminar_apuesta(nueva.id)
despues = base_datos.estadisticas_bd()["apuestas"]
print(f"   limpieza    : {antes} -> {despues} apuestas (sin residuos)")
assert antes == despues

print("\n" + "=" * 78)
print("6) COBERTURA DEL DEPORTE ENTERO")
print("=" * 78)
ligas = api_betplay.obtener_ligas("basketball")
todos = api_betplay.obtener_partidos_deporte("basketball")
con_liga = {p.liga_nombre for p in todos if p.liga_nombre}
ligas_con_partidos = {l.nombre for l in ligas} & con_liga
print(f"   ligas en group.json           : {len(ligas)}")
print(f"   ligas con partidos publicados : {len(ligas_con_partidos)}")
print(f"   partidos del deporte          : {len(todos)} "
      f"({sum(1 for p in todos if p.es_vivo)} en vivo)")
print(f"   ligas con partidos NO listadas: {sorted(con_liga - {l.nombre for l in ligas}) or 'ninguna'}")

a.destroy()
print("\n" + "=" * 78)
print("PRUEBA DE LIGAS OK")
print("=" * 78)
