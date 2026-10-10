# -*- coding: utf-8 -*-
"""Prueba de la GUI: ventana + ping real + flujo 1Q/2Q + apuesta guardada en PostgreSQL."""
import api_betplay
import app as mod_app
import base_datos
import paperbet

avisos = []
mod_app.messagebox.showinfo = lambda titulo, texto, **k: avisos.append(("info", titulo, texto))
mod_app.messagebox.showwarning = lambda titulo, texto, **k: avisos.append(("aviso", titulo, texto))
mod_app.messagebox.showerror = lambda titulo, texto, **k: avisos.append(("error", titulo, texto))

print("=" * 72)
print("1) ABRIR LA VENTANA")
print("=" * 72)
a = mod_app.App()
a.update()
# esperar a que termine la comprobación de PostgreSQL en segundo plano
import time
limite = time.time() + 25
while not a.db_ok and time.time() < limite:
    a.update()
    time.sleep(0.05)
print(f"   db_ok={a.db_ok}  {a.db_error if not a.db_ok else base_datos.descripcionConexion()}")
assert a.db_ok
print(f"   estado: {a.estado.cget('text')}")
print(f"   pestañas: {a.notebook.tabs()}")
assert len(a.notebook.tabs()) == 4
for liga, m in a.modelos.items():
    print(f"   modelo {liga}: n={m.n} pred_ejemplo_ok={m.n > 0}")

print("\n" + "=" * 72)
print("2) PING A LA API (misma ruta que el botón)")
print("=" * 72)
partidos = api_betplay.obtener_partidos_deporte("basketball")
a.pestana_vivo.var_liga.set(a.pestana_vivo.TODAS)
a.pestana_vivo._ping_terminado(partidos, None)
a.update()
filas = a.pestana_vivo.tree.get_children()
print(f"   filas en la tabla: {len(filas)}")
for iid in filas[:3]:
    print("    ", a.pestana_vivo.tree.item(iid, "values"))


def _buscar_candidato():
    """Partido con 1Q+2Q completos y mercado abierto (a esta hora puede no
    haber ninguno en vivo: el 2Q acaba cada pocos minutos)."""
    ps = api_betplay.obtener_partidos_deporte("basketball")
    return next((p for p in ps
                 if p.listo_para_prediccion and p.linea and p.mercado_abierto), None)


candidato = _buscar_candidato()
for _ in range(18):                       # hasta ~90 s esperando un descanso real
    if candidato:
        break
    time.sleep(5)
    candidato = _buscar_candidato()
if candidato is None:
    # sin candidato en vivo: se ejercita el flujo con un partido sintético
    # (mismos campos que uno real en el descanso del 2Q)
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
a.pestana_vivo._partido_sel = candidato
a.pestana_vivo.var_modelo.set("EUROLIGA")
a.pestana_vivo.calcular_flujo()
a.update()

print("\n" + "=" * 72)
print("3) FLUJO 1Q + 2Q")
print("=" * 72)
v = a.pestana_vivo
print(f"   partido   : {candidato.nombre}")
print(f"   1Q        : {v.lbl_q1.cget('text')}")
print(f"   2Q        : {v.lbl_q2.cget('text')}")
print(f"   línea     : {v.lbl_linea.cget('text')}")
print(f"   predicción: {v.lbl_pred.cget('text')}")
print(f"   veredicto : {v.lbl_veredicto.cget('text')}")
print(f"   prob OVER : {v.lbl_prob.cget('text')}")
print(f"   estado    : {v.lbl_flujo_estado.cget('text')}")
print(f"   botón APOSTAR habilitado: {str(v.btn_apostar.cget('state')) == 'normal'}")
assert v.lbl_veredicto.cget("text") in ("OVER", "UNDER")
assert str(v.btn_apostar.cget("state")) == "normal"

print("\n" + "=" * 72)
print("4) APUESTAR (paperbet) Y VERIFICAR EN POSTGRESQL")
print("=" * 72)
antes = base_datos.estadisticas_bd()["apuestas"]
v.var_stake.set("10000")
v.apostar()
a.update()
despues = base_datos.estadisticas_bd()["apuestas"]
print(f"   filas en la BD antes={antes}  después={despues}")
assert despues == antes + 1, "la apuesta no se guardó"
print(f"   diálogo mostrado: {avisos[-1][1] if avisos else '(ninguno)'}")
for av in avisos:
    if av[0] == "info":
        print("   ---- contenido del diálogo ----")
        for linea in av[2].splitlines():
            print("   ", linea)

print("\n" + "=" * 72)
print("5) PESTAÑA APUESTAS")
print("=" * 72)
a.pestana_apuestas.refrescar()
a.update()
for iid in a.pestana_apuestas.tree.get_children():
    print("   ", a.pestana_apuestas.tree.item(iid, "values"))
print("   resumen:", {k: lbl.cget("text") for k, lbl in a.pestana_apuestas.labels_resumen.items()})
assert a.pestana_apuestas.tree.get_children()

print("\n" + "=" * 72)
print("6) RESOLVER LA APUESTA CON UN PARTIDO TERMINADO SIMULADO")
print("=" * 72)
candidato.state = "FINISHED"
candidato.corriendo = False
candidato.izq_min, candidato.izq_seg = 0, 0
candidato.info_cuartos = "Q1: 20-29 | Q2: 19-24 | Q3: 15-16 | Q4: 12-10"
candidato.marcador_local, candidato.marcador_visitante = 86, 79   # total 165 > línea
print(f"   partido finalizado, total={candidato.total_final} (línea {candidato.linea})")
n = a.pestana_vivo._resolver_con([candidato])
a.pestana_apuestas.refrescar()
a.update()
print(f"   apuestas resueltas: {n}")
for iid in a.pestana_apuestas.tree.get_children():
    print("   ", a.pestana_apuestas.tree.item(iid, "values"))
pendientes = base_datos.apuestas_pendientes()
assert not pendientes, "quedan apuestas pendientes sin resolver"

print("\n" + "=" * 72)
print("7) LIMPIEZA (borrar la apuesta de prueba)")
print("=" * 72)
for ap in base_datos.listar_apuestas():
    base_datos.eliminar_apuesta(ap.id)
print(f"   filas restantes: {base_datos.estadisticas_bd()['apuestas']}")

a.destroy()
print("\nPRUEBA DE GUI OK")
