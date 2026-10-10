# -*- coding: utf-8 -*-
"""
Prueba del descargador de histórico con la API de API-Sports.

Comprueba que la traducción BetPlay ↔ API-Sports funciona, que los cuartos
cuadran con el total (prórroga aparte) y que la cuota se respeta. Solo pide
3 ligas, así que gasta 4 peticiones de la cuota diaria.
"""
import historico

LIGAS_PRUEBA = ("euroleague", "spain/liga_acb", "chile/liga_nacional")

print("=" * 78)
print("1) CLIENTE Y CUOTA")
print("=" * 78)
c = historico._Cliente()
assert c.token, "falta el token de API-Sports en config.json"
print(f"   token cargado: {c.token[:8]}…{c.token[-4:]}")
inv = c.inventario()
print(f"   ligas con datos de partidos: {len(inv)}")
assert len(inv) > 100, "el inventario de ligas parece corto"
print(f"   cuota tras /leagues: restan {c.restantes_dia} hoy "
      f"y {c.restantes_total} en total")

print("\n" + "=" * 78)
print("2) TEMPORADAS QUE USARÁ CADA LIGA")
print("=" * 78)
for clave in LIGAS_PRUEBA:
    ids = historico.LIGAS_API_SPORTS[clave]
    etqs = [e for i in ids for e in inv.get(i, [])[:2]]
    print(f"   {clave:22s} ids={ids}  temporadas={etqs}")
    assert etqs, f"{clave} no tiene temporadas con datos"

print("\n" + "=" * 78)
print("3) DESCARGA (solo partidos acabados, con sus cuartos)")
print("=" * 78)
muestras = historico.descargartodo(LIGAS_PRUEBA, n_temporadas=1)
print(f"   muestras: {len(muestras)}")
for m in muestras[:3]:
    print(f"   {m.fecha}  [{m.liga}]  {m.partido[:40]:40s} "
          f"Q1={m.q1:.0f} Q2={m.q2:.0f} Q3={m.q3:.0f} Q4={m.q4:.0f} "
          f"total={m.total:.0f}  ({m.fuente})")
assert len(muestras) > 100, "demasiadas pocas muestras para 3 ligas"

# el total incluye prórroga, así que solo cuadra con Q1..Q4 sin OT
sin_prorroga = [m for m in muestras
                if m.total == m.q1 + m.q2 + m.q3 + m.q4]
prorrogas = [m for m in muestras if m not in sin_prorroga]
print(f"   sin prórroga: {len(sin_prorroga)}   con prórroga: {len(prorrogas)}")
assert len(sin_prorroga) > len(muestras) * 0.9, "demasiados totales descuadrados"

print("\n" + "=" * 78)
print("4) CLAVES ÚNICAS Y COHERENCIA CON BETPLAY")
print("=" * 78)
claves = {m.clave for m in muestras}
print(f"   claves de muestras: {len(claves)} (muestras: {len(muestras)})")
assert len(claves) == len(muestras), "hay muestras con la misma clave"
por_liga = historico.resumen_muestras(muestras)
for liga, n in por_liga.items():
    assert liga in historico.LIGAS_API_SPORTS, f"liga desconocida: {liga}"
    print(f"   {liga:22s} {n:5d} partidos")

# la clave de la app (Partido.clave_liga) debe ser la misma que la de la muestra
import api_betplay
ejemplo = api_betplay.Partido(evento_id=1, nombre="X - Y", local="X", visitante="Y",
                              state="NOT_STARTED", inicio="",
                              liga_nombre="España / Liga ACB",
                              liga_tk="liga_acb", region_tk="spain")
print(f"   Partido.clave_liga de un partido de la ACB: {ejemplo.clave_liga}")
assert ejemplo.clave_liga in por_liga, \
    "la clave del partido no coincide con la de las muestras"

print(f"\n   cuota final: {c.restantes_dia} hoy / {c.restantes_total} total "
      f"({c.peticiones} peticiones hechas)")
print("\nPRUEBA DEL HISTÓRICO (API-SPORTS) OK")
