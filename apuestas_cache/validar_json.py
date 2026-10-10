# -*- coding: utf-8 -*-
"""Valida el JSON generado por kambi_ligas.py contra el formato pedido."""
import json
import sys
from pathlib import Path

carpeta = Path(__file__).resolve().parent
ruta = carpeta / "kambi_basketball_ligas_2026-10-10.json"
d = json.loads(ruta.read_text(encoding="utf-8"))

print("=" * 78)
print("CABECERA DEL DOCUMENTO")
print("=" * 78)
for k in ("scrapeado", "deporte", "deporte_nombre", "n_ligas", "n_eventos",
          "n_en_vivo", "duracion_s"):
    print(f"  {k:28s} {d.get(k)}")
print(f"  {'fallos':28s} {d['fallos']}")
c = d["contraste_deporte_completo"]
print(f"  {'contraste':28s} ok={c['ok']} eventos={c['n_eventos']} "
      f"cubiertos={c['cubiertos']} sin_liga={len(c['sin_liga_descargada'])}")

print("\n" + "=" * 78)
print("CLAVES QUE DEBE TRAER CADA LIGA (plantilla)")
print("=" * 78)
esperadas = {"region", "region_termKey", "liga", "liga_termKey", "group_id",
             "patron", "url", "n_eventos", "eventos"}
ok = True
for l in d["ligas"]:
    faltan = esperadas - set(l)
    if faltan:
        ok = False
        print(f"  FALTA en {l['liga']}: {faltan}")
print("  todas las ligas tienen las claves de la plantilla:", ok)
print(f"  n_ligas del documento = {d['n_ligas']} = len(ligas) = {len(d['ligas'])}")
assert d["n_ligas"] == len(d["ligas"])

print("\n" + "=" * 78)
print("EJEMPLO: una liga con región y una liga terminal")
print("=" * 78)
ejemplo_3 = next(l for l in d["ligas"] if l["region"] and l["n_eventos"] >= 1)
ejemplo_2 = next(l for l in d["ligas"] if not l["region"] and l["n_eventos"] >= 1)
for l in (ejemplo_3, ejemplo_2):
    ev = l["eventos"][0]
    print(f"\n--- {l['region'] or '-'} / {l['liga']} "
          f"(patron={l['patron']} usado={l['patron_usado']}) ---")
    print("  evento:", json.dumps({k: ev[k] for k in
                                   ("id", "nombre", "local", "visitante", "inicio_utc",
                                    "estado", "en_vivo", "ruta")}, ensure_ascii=False))
    print("  marcador:", json.dumps(ev["marcador"], ensure_ascii=False))
    print("  resumen :", json.dumps(ev["resumen"], ensure_ascii=False)[:220])
    m = ev["mercados"][0] if ev["mercados"] else None
    if m:
        print("  mercado :", json.dumps({k: m[k] for k in
                                         ("betOfferId", "criterio", "tipo", "suspendido")},
                                        ensure_ascii=False))
        print("  outcomes:", json.dumps(m["outcomes"][:2], ensure_ascii=False))

print("\n" + "=" * 78)
print("CUADROS DE CONTROL")
print("=" * 78)
todos = [e for l in d["ligas"] for e in l["eventos"]]
print(f"  eventos totales          : {len(todos)}")
print(f"  con cuota en algún mercado: "
      f"{sum(1 for e in todos if any(o['cuota'] for m in e['mercados'] for o in m['outcomes']))}")
print(f"  con línea de totales      : "
      f"{sum(1 for e in todos if e['resumen']['totales'])}")
print(f"  con moneyline abierto     : {sum(1 for e in todos if e['resumen']['ganador'])}")
print(f"  con marcador (en vivo)    : {sum(1 for e in todos if e['marcador'])}")
print(f"  ids únicos                : {len({e['id'] for e in todos})}")
print(f"  ligas con 0 eventos       : {sum(1 for l in d['ligas'] if l['n_eventos'] == 0)}")
print(f"  mercados parseados        : {sum(len(e['mercados']) for e in todos)}")
print(f"  outcomes parseados        : "
      f"{sum(len(m['outcomes']) for e in todos for m in e['mercados'])}")
print(f"  tamaño del JSON           : {ruta.stat().st_size / 1024:.1f} KB")

print("\n" + "=" * 78)
print("COMPROBACIONES DE LA PLANTILLA")
print("=" * 78)
# 1. nba.json ~ 21 eventos
nba = next(l for l in d["ligas"] if l["liga_termKey"] == "nba")
print(f"  [1] basketball/nba.json  -> {nba['n_eventos']} eventos "
      f"(patrón {nba['patron_usado']})  {'OK' if nba['n_eventos'] >= 1 else 'REVISAR'}")
# 2. france/elite >= 1
elite = next(l for l in d["ligas"] if l["liga_termKey"] == "elite")
print(f"  [2] france/elite         -> {elite['n_eventos']} eventos  "
      f"{'OK' if elite['n_eventos'] >= 1 else 'REVISAR'}")
# 3. terminales con patrón 2 niveles
terminales = [l for l in d["ligas"] if l["patron"] == "2-niveles"]
print(f"  [3] ligas terminales (2 niveles): {len(terminales)} -> "
      f"{[l['liga_termKey'] for l in terminales]}")
# 4. ningún 404 registrado
print(f"  [4] fallos registrados   : {len(d['fallos'])} "
      f"{'OK' if not d['fallos'] else 'REVISAR'}")
# 5. cobertura
print(f"  [5] cobertura            : {c['cubiertos']}/{c['n_eventos']} "
      f"{'OK' if c['cubiertos'] == c['n_eventos'] else 'REVISAR'}")

print("\nVALIDACION JSON OK")
