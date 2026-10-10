# -*- coding: utf-8 -*-
"""Prueba de la capa PostgreSQL: esquema, insertar, resolver, resumen y limpiar."""
import base_datos
import paperbet
from paperbet import Apuesta

print("=" * 72)
print("1) CONEXIÓN Y ESQUEMA")
print("=" * 72)
ok, msg = base_datos.probar_conexion()
print(f"   {msg}")
assert ok, "no se pudo conectar"
base_datos.crear_esquema()
print(f"   esquema creado en {base_datos.descripcionConexion()}")

print("\n" + "=" * 72)
print("2) INSERTAR APUESTA DE PRUEBA")
print("=" * 72)
a = Apuesta(
    evento_id=99999999,
    partido="PRUEBA - Local vs Visitante",
    liga_apuesta="BBL",
    liga_modelo="EUROLIGA",
    seleccion="OVER",
    linea=164.5,
    cuota=1.91,
    stake=10000,
    prediccion=179.504,
    diferencia=15.004,
    probabilidad=0.874,
)
assert paperbet.validar(a) is None
nuevo_id = base_datos.guardar_apuesta(a)
print(f"   guardada con id={nuevo_id}  ({a.seleccion} @ {a.linea} cuota {a.cuota})")

pend = base_datos.apuestas_pendientes()
assert any(x.id == nuevo_id for x in pend), "no aparece como pendiente"
print(f"   pendientes en la BD: {len(pend)}")

print("\n" + "=" * 72)
print("3) RESOLVER (total final 175 -> OVER gana)")
print("=" * 72)
a.estado = paperbet.ESTADO_PENDIENTE
paperbet.resolver(a, 175)
print(f"   estado={a.estado}  beneficio={a.beneficio:+,.0f}  nota={a.nota}")
assert a.estado == paperbet.ESTADO_GANADA
assert abs(a.beneficio - 9100) < 1e-6
base_datos.actualizar_resultado(a)

traida = next(x for x in base_datos.listar_apuestas() if x.id == nuevo_id)
print(f"   releída de la BD: estado={traida.estado} total_final={traida.total_final} "
      f"beneficio={traida.beneficio:+,.0f}")
assert traida.estado == paperbet.ESTADO_GANADA
assert traida.total_final == 175

print("\n" + "=" * 72)
print("4) RESUMEN")
print("=" * 72)
r = base_datos.resumen()
print(f"   apuestas={r.total} pendientes={r.pendientes} ganadas={r.ganadas} "
      f"perdidas={r.perdidas} anuladas={r.anuladas}")
print(f"   aciertos={r.aciertos}  ROI={r.roi}  beneficio={r.beneficio:+,.0f}")
print(f"   info extra: {base_datos.estadisticas_bd()}")

print("\n" + "=" * 72)
print("5) LIMPIEZA (borrar la apuesta de prueba)")
print("=" * 72)
base_datos.eliminar_apuesta(nuevo_id)
quedan = [x for x in base_datos.listar_apuestas() if x.id == nuevo_id]
assert not quedan, "no se borró"
print(f"   borrada. filas totales ahora: {base_datos.estadisticas_bd()['apuestas']}")

print("\nPRUEBA DE BASE DE DATOS OK")
