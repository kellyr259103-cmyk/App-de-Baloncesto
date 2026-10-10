# -*- coding: utf-8 -*-
"""Analiza los JSON de basketball/football/tennis: cobertura, fallbacks y eventos."""
import json
from collections import Counter
from pathlib import Path

carpeta = Path(__file__).resolve().parent
archivos = sorted(carpeta.glob("kambi_*_ligas_*.json"))

print("=" * 84)
print("RESUMEN POR DEPORTE")
print("=" * 84)
for f in archivos:
    d = json.loads(f.read_text(encoding="utf-8"))
    c = d["contraste_deporte_completo"]
    huerf = c.get("sin_liga_descargada", [])
    print(f"\n--- {d['deporte']} ---")
    print(f"  archivo            : {f.name} ({f.stat().st_size/1024:.0f} KB)")
    print(f"  ligas              : {d['n_ligas']}   fallos: {len(d['fallos'])}")
    print(f"  eventos            : {d['n_eventos']}   en vivo: {d['n_en_vivo']}")
    print(f"  duración           : {d['duracion_s']}s")
    print(f"  contraste          : {c['cubiertos']}/{c['n_eventos']} "
          f"(sin liga: {len(huerf)})")
    if huerf:
        esp = [h for h in huerf if h.get("es_esports")]
        otros = [h for h in huerf if not h.get("es_esports")]
        print(f"    deportes de los no cubiertos: "
              f"{dict(Counter(h['deporte'] for h in huerf))}")
        print(f"    esports (excluidos a propósito): {len(esp)} -> "
              f"{Counter(h.get('liga_tk') for h in esp).most_common(3)}")
        print(f"    no-cubiertos NO esports        : {len(otros)}")
        for h in otros[:5]:
            print(f"       - {h['nombre']} [{h['clave_liga']}] "
                  f"(en group.json: {h['liga_en_group_json']})")

print("\n" + "=" * 84)
print("FALLBACKS 404 (patron_usado != patron)")
print("=" * 84)
n_fb = 0
for f in archivos:
    d = json.loads(f.read_text(encoding="utf-8"))
    for l in d["ligas"]:
        if l["patron_usado"] and l["patron_usado"] != l["patron"]:
            n_fb += 1
            print(f"  {d['deporte']:10s} {l['region'] or '-':16s} {l['liga']:30s} "
                  f"{l['patron']} -> {l['patron_usado']}  ({l['nota']})")
print(f"  total de fallbacks: {n_fb}")

print("\n" + "=" * 84)
print("LIGAS CON 0 EVENTOS (respuesta válida, no error)")
print("=" * 84)
for f in archivos:
    d = json.loads(f.read_text(encoding="utf-8"))
    vacias = [l for l in d["ligas"] if l["n_eventos"] == 0]
    print(f"  {d['deporte']:10s}: {len(vacias)}/{d['n_ligas']} vacías -> "
          f"{[l['liga_termKey'] for l in vacias][:8]}")

print("\n" + "=" * 84)
print("CUADROS DE CONTROL GLOBALES")
print("=" * 84)
tot_ev = tot_li = tot_ml = tot_tot = tot_hs = 0
for f in archivos:
    d = json.loads(f.read_text(encoding="utf-8"))
    evs = [e for l in d["ligas"] for e in l["eventos"]]
    ml = sum(1 for e in evs if e["resumen"]["ganador"])
    tt = sum(1 for e in evs if e["resumen"]["totales"])
    hs = sum(1 for e in evs if e["resumen"]["handicap"])
    tot_ev += len(evs); tot_li += d["n_ligas"]; tot_ml += ml; tot_tot += tt; tot_hs += hs
    print(f"  {d['deporte']:10s} ligas={d['n_ligas']:3d} eventos={len(evs):5d} "
          f"moneyline={ml:5d} totales={tt:5d} handicap={hs:5d}")
print(f"  {'TOTAL':10s} ligas={tot_li:3d} eventos={tot_ev:5d} "
      f"moneyline={tot_ml:5d} totales={tot_tot:5d} handicap={tot_hs:5d}")

print("\nANALISIS OK")
