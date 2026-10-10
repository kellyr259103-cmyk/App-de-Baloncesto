# -*- coding: utf-8 -*-
"""Valida el JSON de baloncesto: estructura, mercados por cuarto y datos en vivo."""
import json
from collections import Counter
from pathlib import Path

carpeta = Path(__file__).resolve().parent
ruta = carpeta / "kambi_basketball_ligas_2026-10-10.json"
d = json.loads(ruta.read_text(encoding="utf-8"))
evs = [e for l in d["ligas"] for e in l["eventos"]]

print("=" * 86)
print("1) CABECERA Y COBERTURA")
print("=" * 86)
print(f"  scrapeado   : {d['scrapeado']}")
print(f"  ligas       : {d['n_ligas']}   fallos: {len(d['fallos'])}")
print(f"  eventos     : {d['n_eventos']}   en vivo: {d['n_en_vivo']}")
c = d["contraste_deporte_completo"]
print(f"  contraste   : {c['cubiertos']}/{c['n_eventos']}  "
      f"(esports excluidos: {c.get('n_esports', 0)}, "
      f"recuperados: {sum(1 for h in c['sin_liga_descargada'] if h.get('recuperado'))})")
print(f"  duración    : {d['duracion_s']}s   tamaño: {ruta.stat().st_size/1024:.0f} KB")
print(f"  fallos      : {d['fallos']}")

print("\n" + "=" * 86)
print("2) LIGAS (ordenadas por nº de eventos)")
print("=" * 86)
for l in sorted(d["ligas"], key=lambda x: -x["n_eventos"])[:14]:
    print(f"  {l['region'] or '-':14s} {l['liga'][:30]:30s} "
          f"eventos={l['n_eventos']:3d}  patron={l['patron_usado'] or l['patron']}"
          + (f"  ({l['nota']})" if l["nota"] else ""))
print(f"  ... y {max(0, d['n_ligas'] - 14)} ligas más")

print("\n" + "=" * 86)
print("3) MERCADOS: cuántos tipos trae cada evento")
print("=" * 86)
print(f"  eventos con menú completo        : "
      f"{sum(1 for e in evs if e.get('mercados_completos'))}/{len(evs)}")
print(f"  mercados inline (layout combinado) media: "
      f"{sum(e.get('n_mercados_inline', 0) for e in evs)/len(evs):.1f}")
print(f"  mercados completos media               : "
      f"{sum(len(e['mercados']) for e in evs)/len(evs):.1f}")
print(f"  outcomes totales                       : "
      f"{sum(len(m['outcomes']) for e in evs for m in e['mercados'])}")

tipos = Counter((m["tipo"], m["tipo_id"]) for e in evs for m in e["mercados"])
print("\n  tipos de mercado encontrados (nombre, id):")
for (nom, tid), n in tipos.most_common(10):
    print(f"     {str(nom):22s} id={str(tid):>4}  x{n}")

print("\n" + "=" * 86)
print("4) LÍNEAS DE TOTALES (lo que usa el modelo: 1Q, 2Q, total partido)")
print("=" * 86)
criterios = Counter(m["criterio"] for e in evs for m in e["mercados"]
                    if m["tipo_id"] == 6 or "over/under" in (m["tipo_en"] or "").lower())
print("  criterios de totales disponibles:")
for crit, n in criterios.most_common(20):
    print(f"     x{n:4d}  {crit}")

con_partido = sum(1 for e in evs if e["resumen"]["totales"])
print(f"\n  eventos con línea de TOTAL del partido : {con_partido}/{len(evs)}")
for clave in ("cuarto 1", "cuarto 2", "1er cuarto", "2º cuarto"):
    n = sum(1 for e in evs if any(clave in (m["criterio"] or "").lower()
                                  for m in e["mercados"] if m["tipo_id"] == 6))
    if n:
        print(f"  eventos con línea de total de {clave:10s}: {n}/{len(evs)}")

print("\n" + "=" * 86)
print("5) EVENTO DE EJEMPLO (en vivo, con totales por cuarto)")
print("=" * 86)
ejemplo = next((e for e in evs if e["en_vivo"] and e["resumen"]["totales"]), evs[0])
print(f"  {ejemplo['nombre']}  [{ejemplo['ruta']['liga']}]")
print(f"  estado={ejemplo['estado']}  inicio={ejemplo['inicio_utc']}")
print(f"  marcador={json.dumps(ejemplo['marcador'], ensure_ascii=False)}")
print(f"  reloj   ={json.dumps(ejemplo['reloj'], ensure_ascii=False)}")
print(f"  mercados ({len(ejemplo['mercados'])}):")
for m in ejemplo["mercados"][:10]:
    print(f"     - {m['criterio']!r}  tipo={m['tipo']}  suspendido={m['suspendido']}  "
          f"outcomes={len(m['outcomes'])}")
    for o in m["outcomes"][:2]:
        print(f"         {str(o['etiqueta']):16s} {o['tipo']:9s} cuota={o['cuota']} "
              f"linea={o['linea']} {o['estado']}")

print("\n" + "=" * 86)
print("6) LISTO PARA ALIMENTAR EL MODELO (1Q + 2Q reales vs líneas de la casa)")
print("=" * 86)


def parciales(e):
    info = (e.get("marcador") or {}).get("parciales") or ""
    salida = {}
    for trozo in info.split("|"):
        trozo = trozo.strip()
        if trozo.upper().startswith("Q") and ":" in trozo:
            clave, valores = trozo.split(":", 1)
            a, b = valores.split("-")
            salida[clave.strip().upper()] = int(a) + int(b)
    return salida


preparados = []
for e in evs:
    p = parciales(e)
    if "Q1" not in p or "Q2" not in p:
        continue
    tp = e["resumen"].get("total_partido")
    t1 = e["resumen"].get("total_1a_parte")
    if not tp and not t1:
        continue
    preparados.append((e, p, tp, t1))

print(f"  eventos con 1Q + 2Q jugados y línea utilizable: {len(preparados)}")
print(f"  {'partido':42s} {'1Q':>4} {'2Q':>4} {'descanso':>9} "
      f"{'linea partido':>14} {'linea 1ª parte':>15}")
for e, p, tp, t1 in preparados[:12]:
    print(f"  {e['nombre'][:42]:42s} {p['Q1']:4d} {p['Q2']:4d} "
          f"{p['Q1'] + p['Q2']:9d} "
          f"{(tp['linea'] if tp else '-'):>14} "
          f"{(t1['linea'] if t1 else '-'):>15}")

print("\n" + "=" * 86)
print("7) CUÁNTOS EVENTOS TIENEN CADA TIPO DE LÍNEA")
print("=" * 86)
for clave, etiqueta in (("total_partido", "Total del partido"),
                        ("total_1a_parte", "Total 1.ª parte (Q1+Q2)"),
                        ("total_cuarto1", "Total Cuarto 1"),
                        ("total_cuarto2", "Total Cuarto 2")):
    n = sum(1 for e in evs if e["resumen"].get(clave))
    nv = sum(1 for e in evs if e["en_vivo"] and e["resumen"].get(clave))
    print(f"  {etiqueta:30s}: {n:3d}/{len(evs)} eventos  (en vivo: {nv})")
print(f"  {'Ganador (moneyline)':30s}: "
      f"{sum(1 for e in evs if e['resumen'].get('ganador')):3d}/{len(evs)}")
print(f"  {'Hándicap':30s}: "
      f"{sum(1 for e in evs if e['resumen'].get('handicap')):3d}/{len(evs)}")

print("\nVALIDACION BALONCESTO OK")
