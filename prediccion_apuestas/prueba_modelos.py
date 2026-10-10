# -*- coding: utf-8 -*-
"""
Prueba del pipeline de modelos por liga.

Usa una liga ficticia ('liga_de_prueba') para no tocar las muestras reales:
descarga una temporada de Euroliga, la guarda como si fuera de esa liga,
entrena, guarda el modelo, lo recupera y comprueba que predice igual.
Al final borra SOLO sus filas.
"""
import base_datos
import historico
import modelos
import motor

LIGA = "liga_de_prueba"

print("=" * 78)
print("1) DESCARGAR MUESTRAS REALES (Euroliga vía API-Sports)")
print("=" * 78)
brutas = historico.descargartodo(["euroleague"], n_temporadas=1)
print(f"   partidos descargados: {len(brutas)}")
media_total = sum(m.total for m in brutas) / len(brutas)
media_q1 = sum(m.q1 for m in brutas) / len(brutas)
print(f"   media total={media_total:.1f}  media 1Q={media_q1:.1f}")
assert 140 < media_total < 190, "la media de totales de Euroliga no cuadra"
# el Q1 es la suma de AMBOS equipos: ~43 equivale a ~21,5 por equipo
assert 35 < media_q1 < 50, f"la media del 1Q no cuadra: {media_q1}"

print("\n" + "=" * 78)
print("2) ENTRENAR EL MODELO MCO (mismas fórmulas que el Excel)")
print("=" * 78)
datos = [(m.q1, m.q2, m.total) for m in brutas]
m = motor._ajustar(datos, LIGA, "prueba")
print(f"   n={m.n}  b0={m.b0:.2f}  b1={m.b1:.3f}  b2={m.b2:.3f}")
print(f"   R²(1Q+2Q)={m.r2_dos:.3f}   R²(1Q)={m.r2_uno:.3f}")
print(f"   RMSE en muestra: dos={m.rmse_dos:.1f}  uno={m.rmse_uno:.1f}  "
      f"naive(1Q*4)={m.rmse_naive:.1f}")
# con 402 partidos el modelo debe explicar bastante y mejorar al naive
assert m.r2_dos > 0.5, f"R² demasiado bajo: {m.r2_dos}"
assert m.rmse_dos < m.rmse_uno < m.rmse_naive, "el orden de RMSE no es el esperado"

print("\n" + "=" * 78)
print("3) HOLDOUT: el RMSE guardado se calcula sobre partidos NO usados")
print("=" * 78)
mh = modelos._entrenar_liga(LIGA, datos, "prueba")
print(f"   n ajuste={mh.n}   RMSE test dos={mh.rmse_test_dos:.1f}   "
      f"uno={mh.rmse_test_uno:.1f}")
# el conjunto de validación puede ser más fácil que el de ajuste, así que el
# RMSE de test no siempre es mayor; lo importante es que el modelo siga
# batiendo al naïve (1Q*4) y que no se dispare
assert mh.rmse_test_dos < mh.rmse_naive, \
    f"en partidos no vistos el modelo no bate al naïve ({mh.rmse_test_dos:.1f} " \
    f"vs {mh.rmse_naive:.1f})"
assert mh.rmse_test_dos < mh.rmse_naive * 0.75, \
    "el modelo apenas mejora al naïve en la validación"
relacion = mh.rmse_test_dos / max(mh.rmse_dos, 1e-9)
print(f"   test/ajuste = {relacion:.2f}   (naive de referencia="
      f"{mh.rmse_naive:.1f})")
assert 0.5 < relacion < 2.0, \
    f"el RMSE de test no se parece al de ajuste: {relacion:.2f}"

print("\n" + "=" * 78)
print("4) GUARDAR EN POSTGRESQL Y RECUPERAR")
print("=" * 78)
# limpieza previa por si se repite la prueba
conn = base_datos.conectar()
with conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM muestras WHERE liga = %s;", (LIGA,))
        cur.execute("DELETE FROM modelos_liga WHERE liga = %s;", (LIGA,))
conn.close()

muestras = [historico.Muestra(liga=LIGA, fecha=x.fecha, q1=x.q1, q2=x.q2,
                              q3=x.q3, q4=x.q4, total=x.total, partido=x.partido,
                              fuente="prueba") for x in brutas]
nuevas = modelos.guardar_muestras(muestras)
print(f"   muestras guardadas: {nuevas}")
assert nuevas == len(muestras)
# repetir no debe duplicar
assert modelos.guardar_muestras(muestras) == 0, "las muestras se duplicaron"

cuenta = modelos.contar_muestras(LIGA)
print(f"   en la tabla: {cuenta}")

entrenados = modelos.entrenar_todo(ligas=[LIGA])
print(f"   modelos entrenados: {list(entrenados)}")
assert LIGA in entrenados

recuperados = modelos.cargar_modelos()
m2 = recuperados[LIGA]
print(f"   recuperado: n={m2.n} b0={m2.b0:.4f} b1={m2.b1:.4f} b2={m2.b2:.4f} "
      f"R²={m2.r2_dos:.3f}")
assert m2.n == mh.n
assert abs(m2.b0 - mh.b0) < 1e-6 and abs(m2.b1 - mh.b1) < 1e-6
assert abs(m2.r2_dos - mh.r2_dos) < 1e-6

print("\n" + "=" * 78)
print("5) PREDICCIÓN CON EL MODELO DE LA LIGA")
print("=" * 78)
q1, q2 = 22.0, 21.0
r = motor.predecir(m2, q1, q2, 160.0)
esperado = m2.b0 + m2.b1 * q1 + m2.b2 * q2
print(f"   1Q={q1} 2Q={q2} línea=160  →  predicción={r.prediccion:.1f} "
      f"(esperado {esperado:.1f})  veredicto={r.veredicto} "
      f"diferencia={r.diferencia:+.1f}")
assert abs(r.prediccion - esperado) < 1e-9

print("\n" + "=" * 78)
print("6) UMBRAL: con pocas muestras NO se publica modelo")
print("=" * 78)
pocas = datos[:10]
assert modelos._entrenar_liga(LIGA, pocas, "prueba") is None
print(f"   con 10 muestras no se entrena (mínimo {modelos.MINIMO_MUESTRAS}): OK")
# justo en el umbral (con las descargadas hay bastantes)
justo = datos[:modelos.MINIMO_MUESTRAS]
if len(justo) == modelos.MINIMO_MUESTRAS:
    m_justo = modelos._entrenar_liga(LIGA, justo, "prueba")
    assert m_justo is not None
    print(f"   con justo {modelos.MINIMO_MUESTRAS} muestras: se entrena "
          f"(n={m_justo.n}): OK")
else:
    print(f"   (aviso: solo {len(justo)} muestras descargadas, umbral no probado)")

print("\n" + "=" * 78)
print("7) LIMPIEZA")
print("=" * 78)
conn = base_datos.conectar()
with conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM muestras WHERE liga = %s;", (LIGA,))
        cur.execute("DELETE FROM modelos_liga WHERE liga = %s;", (LIGA,))
conn.close()
print(f"   filas de la liga de prueba: {modelos.contar_muestras(LIGA)[LIGA]}")

print("\nPRUEBA DEL PIPELINE DE MODELOS OK")
