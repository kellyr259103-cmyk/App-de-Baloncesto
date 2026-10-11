# -*- coding: utf-8 -*-
"""
resultados.py — memoria de los resultados finales.

Por qué hace falta
------------------
El feed de la casa (`/listView/basketball.json`) es la **cartelera del día**:
en cuanto un partido termina, sale de la lista y ya no se puede consultar.
No existe tampoco un endpoint para leer un partido concreto
(`event/{id}.json` responde 404, verificado). Así que si la app no llega a
leer el marcador en esa ventana breve —o está cerrada en ese momento—, la
apuesta se queda PENDIENTE para siempre.

Qué se hace aquí
----------------
1. `guardar_partidos`   — en cuanto el feed enseña un partido acabado con su
                          marcador, el total se guarda en PostgreSQL.
2. `totales`            — se leen de vuelta para resolver apuestas aunque el
                          partido ya no aparezca en ningún feed.
3. `recuperar_de_api`   — para las apuestas que se quedaron sin registrar,
                          busca el resultado en API-Sports (la fuente oficial)
                          por fecha, acotando la candidatura a la liga de la
                          apuesta para que la coincidencia de nombres no sea
                          una ruleta.

Uso:
    python resultados.py                  # informe de lo pendiente
    python resultados.py --aplicar        # resuelve las que pueda
"""
from __future__ import annotations

import argparse
import difflib
import re
import unicodedata
from datetime import date
from typing import Iterable, Optional

from psycopg2 import extras

import base_datos
import historico
import paperbet
from paperbet import Apuesta

# Se acepta una coincidencia si supera el umbral Y deja al segundo mejor una
# holgura clara: así nunca se resuelve con el partido "casi parecido".
UMBRAL_RESTRINGIDO = 0.35    # buscando dentro de la liga de la apuesta
UMBRAL_LIBRE = 0.50          # buscando entre todos los partidos del día
MARGEN = 0.08                # distancia mínima sobre el segundo mejor

_NO_LETRAS = re.compile(r"[^a-z0-9 ]+")

_tabla_creada = False


# --------------------------------------------------------------------------- #
#  Guardar / leer los totales vistos en el feed                                #
# --------------------------------------------------------------------------- #
def _asegurar_tabla() -> None:
    """Crea las tablas una vez por proceso (no en cada sondeo de 5 s)."""
    global _tabla_creada
    if _tabla_creada:
        return
    base_datos.crear_esquema()
    _tabla_creada = True


def _definitivo(p) -> bool:
    """
    ¿Se puede dar el marcador de este partido por definitivo?

    No vale con que el reloj marque el final: si el partido se va a prórroga,
    al terminar el 4º cuarto el marcador aún NO es el final y guardarlo ahora
    dejaría una apuesta resuelta con un total a la baja.

      · la casa pone el partido como terminado  -> seguro.
      · si el estado sigue en STARTED, solo se guarda si los cuatro cuartos
        cuadran con el marcador (prórroga pendiente NO cuadra).
    """
    if p.total_final is None:
        return False
    if p.state not in ("NOT_STARTED", "STARTED"):
        return True
    if not p.finalizado:
        return False
    q = (p.q1, p.q2, p.q3, p.q4)
    if None in q or p.total_parcial is None:
        return False
    suma = float(q[0]) + float(q[1]) + float(q[2]) + float(q[3])
    return abs(suma - float(p.total_parcial)) < 1e-6


def guardar_partidos(partidos: Iterable) -> int:
    """
    Guarda el total final de los partidos acabados del feed.

    Devuelve cuántos partidos han quedado registrados. No toca las apuestas,
    solo recuerda el marcador: si la casa enseña otro más tarde (prórroga),
    manda la última lectura.
    """
    filas = []
    for p in partidos:
        try:
            if not _definitivo(p):
                continue
        except Exception:                      # noqa: BLE001
            continue
        filas.append((p.evento_id, p.nombre or "",
                      getattr(p, "clave_liga", "") or "",
                      float(p.total_final), "feed"))
    if not filas:
        return 0
    _asegurar_tabla()
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                guardados = extras.execute_values(
                    cur,
                    """INSERT INTO resultados
                          (evento_id, partido, liga, total, fuente)
                       VALUES %s ON CONFLICT (evento_id) DO UPDATE
                          SET total = EXCLUDED.total,
                              capturado = now()
                       RETURNING evento_id""",
                    filas, page_size=200, fetch=True)
                return len(guardados or ())
    finally:
        conn.close()


def guardar_totales(filas: list[tuple[int, str, str, float]],
                    fuente: str = "apisports") -> int:
    """Guarda totales recuperados fuera del feed (mismo formato que arriba)."""
    filas = [tuple(f) + (fuente,) for f in filas]
    if not filas:
        return 0
    _asegurar_tabla()
    conn = base_datos.conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                guardados = extras.execute_values(
                    cur,
                    """INSERT INTO resultados
                          (evento_id, partido, liga, total, fuente)
                       VALUES %s ON CONFLICT (evento_id) DO UPDATE
                          SET total = EXCLUDED.total,
                              fuente = EXCLUDED.fuente,
                              capturado = now()
                       RETURNING evento_id""",
                    filas, page_size=200, fetch=True)
                return len(guardados or ())
    finally:
        conn.close()


def detalle(evento_ids: Iterable[int]) -> dict[int, tuple[float, str]]:
    """
    `(total, fuente)` conocidos para esos partidos (0 peticiones).

    `fuente` dice de dónde salió el marcador: `feed` (lo dio la casa) o
    `apisports` (lo recuperó `recuperar_de_api` porque el partido ya había
    salido del feed).
    """
    ids = [int(i) for i in evento_ids if i]
    if not ids:
        return {}
    _asegurar_tabla()
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT evento_id, total, fuente FROM resultados "
                        "WHERE evento_id = ANY(%s);", (ids,))
            return {int(e): (float(t), f) for e, t, f in cur.fetchall()}
    finally:
        conn.close()


def totales(evento_ids: Iterable[int]) -> dict[int, float]:
    """Totales finales conocidos para esos partidos (0 peticiones)."""
    return {k: v[0] for k, v in detalle(evento_ids).items()}


def contar() -> int:
    """Partidos con resultado guardado (para el informe)."""
    _asegurar_tabla()
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM resultados;")
            return int(cur.fetchone()[0])
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Recuperación de apuestas huérfanas desde API-Sports                         #
# --------------------------------------------------------------------------- #
def _normaliza(texto: str) -> str:
    """'Carpegna Prosciutto Basket Pesaro' -> 'carpegna prosciutto basket pesaro'"""
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("-", " ").replace("(", " ").replace(")", " ")
    return " ".join(_NO_LETRAS.sub(" ", s).split())


def _partes(nombre: str) -> tuple[str, str]:
    """'Bilbao Basket - San Pablo Burgos' -> ('Bilbao Basket', 'San Pablo Burgos')"""
    for sep in (" - ", " – ", " — "):
        if sep in (nombre or ""):
            h, v = (nombre or "").split(sep, 1)
            return h.strip(), v.strip()
    return (nombre or "").strip(), ""


def _similitud(a: str, b: str) -> float:
    """
    Parecido entre dos nombres de equipo.

    No basta con `difflib`: la casa escribe 'Carpegna Prosciutto Basket
    Pesaro' y API-Sports 'Pesaro'. Si uno contiene al otro (por tokens o como
    texto) es casi seguro el mismo equipo, y eso se puntúa alto.
    """
    na, nb = _normaliza(a), _normaliza(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb:
        r = max(r, len(ta & tb) / len(ta | tb))
        if ta <= tb or tb <= ta:               # un nombre es subconjunto
            r = max(r, 0.66)
        r = max(r, _cobertura_tokens(ta, tb))  # 'basketclubs' ~ 'basket'
    if na in nb or nb in na:                   # uno contiene al otro
        r = max(r, 0.85)
    return r


def _cobertura_tokens(ta: set[str], tb: set[str]) -> float:
    """
    ¿Cuántas palabras de `ta` tienen pareja en `tb`? (por igualdad, prefijo o
    parecido alto). Cierra el hueco de 'Basketclubs Vienna' con 'Vienna Basket'.
    """
    largas = {t for t in ta if len(t) >= 4}
    if not largas:
        return 0.0
    usadas, aciertos = set(), 0
    for t in largas:
        mejor, mejor_s = None, 0.0
        for u in tb:
            if u in usadas or len(u) < 4:
                continue
            if t.startswith(u) or u.startswith(t):
                s = min(len(t), len(u)) / max(len(t), len(u)) + 0.15
            else:
                s = difflib.SequenceMatcher(None, t, u).ratio()
            if s > mejor_s:
                mejor, mejor_s = u, s
        if mejor is not None and mejor_s >= 0.7:
            usadas.add(mejor)
            aciertos += 1
    return aciertos / len(largas)


SUELO_PAREJA = 0.25   # ninguna de las dos mitades puede quedar por debajo


def _pareja(apuesta: Apuesta, g: dict) -> float:
    """
    Puntuación de un partido candidato para una apuesta.

    Se puntúa la MEDIA de los dos equipos (no el mínimo: un solo nombre raro
    como 'Giorgio Tesi Group Pistoia' hundía la pareja entera), exigiendo que
    ninguna de las dos mitades baje de `SUELO_PAREJA`.
    """
    h, v = _partes(apuesta.partido)
    directo = (_similitud(h, g["local"]), _similitud(v, g["visita"]))
    cruzado = (_similitud(h, g["visita"]), _similitud(v, g["local"]))
    opciones = (directo if sum(directo) >= sum(cruzado) else cruzado)
    if min(opciones) < SUELO_PAREJA:
        return 0.0
    return sum(opciones) / 2


def _tokens_liga(liga_apuesta: str) -> set[str]:
    """Palabras con las que la apuesta nombra a su liga ('España / Liga ACB' -> acb...)."""
    ultima = (liga_apuesta or "").split("/")[-1]
    return {t for t in _normaliza(ultima).split() if len(t) >= 3}


def _liga_encaja(tokens: set[str], juego: dict) -> bool:
    """¿Puede ese partido de API-Sports ser de la liga de la apuesta?"""
    if not tokens:
        return True
    palabras = (set(_normaliza(str(juego.get("liga") or "")).split())
                | set(_normaliza(str(juego.get("pais") or "")).split()))
    return any(t in palabras for t in tokens)


def _finalizados_de(fecha: date) -> list[dict]:
    """Partidos acabados con total de API-Sports para esa fecha (1 petición)."""
    cli = historico._Cliente()
    d = cli.get("games", date=fecha.isoformat())
    salen = []
    for g in d.get("response") or []:
        est = str((g.get("status") or {}).get("short") or "")
        if est not in historico.ESTADOS_FINALIZADO and not est.startswith("FT"):
            continue
        sc = g.get("scores") or {}
        h, a = sc.get("home") or {}, sc.get("away") or {}
        th, ta = h.get("total"), a.get("total")
        if th is None or ta is None:
            continue
        eq = g.get("teams") or {}
        lg = g.get("league") or {}
        pais = lg.get("country")
        if isinstance(pais, dict):
            pais = pais.get("name")
        salen.append({
            "id": g.get("id"),
            "local": ((eq.get("home") or {}).get("name") or "?"),
            "visita": ((eq.get("away") or {}).get("name") or "?"),
            "total": float(th) + float(ta),
            "liga": str(lg.get("name") or ""),
            "pais": str(pais or ""),
        })
    return salen


def _mejor_candidato(apuesta: Apuesta, candidatos: list[dict]) -> tuple[float, Optional[dict], float]:
    """(puntuación, mejor partido, puntuación del segundo)."""
    puntos = [(_pareja(apuesta, g), g) for g in candidatos]
    puntos.sort(key=lambda x: -x[0])
    if not puntos:
        return 0.0, None, 0.0
    segundo = puntos[1][0] if len(puntos) > 1 else 0.0
    return puntos[0][0], puntos[0][1], segundo


def _buscar(apuesta: Apuesta, por_fecha: dict[date, list[dict]]
            ) -> tuple[Optional[dict], float, str]:
    """Partido de API-Sports que corresponde a esta apuesta (o None)."""
    candidatos = por_fecha.get(apuesta.fecha.date() if apuesta.fecha else None) or []
    if not candidatos:
        return None, 0.0, "sin partidos en API-Sports para esa fecha"

    tokens = _tokens_liga(apuesta.liga_apuesta)
    restringidos = [g for g in candidatos if _liga_encaja(tokens, g)]

    if restringidos:
        s, g, sg = _mejor_candidato(apuesta, restringidos)
        if g is not None and s >= UMBRAL_RESTRINGIDO and s - sg >= MARGEN:
            return g, s, "dentro de su liga"
    # o no había liga acotada o la de dentro no convence: se mira el día entero
    s, g, sg = _mejor_candidato(apuesta, candidatos)
    if g is not None and s >= UMBRAL_LIBRE and s - sg >= MARGEN:
        return g, s, "partido del día"
    return None, s, "sin coincidencia fiable"


def recuperar_de_api(apuestas: Iterable[Apuesta],
                     ) -> dict[int, tuple[float, str]]:
    """
    Busca en API-Sports el total final de apuestas cuyo partido ya salió del
    feed. Devuelve `{evento_id: (total, partido de API-Sports)}` — es decir,
    solo las que se pueden identificar SIN lugar a dudas. 1 petición por fecha.
    """
    pend = [a for a in apuestas if a.evento_id and a.fecha]
    if not pend:
        return {}
    fechas = sorted({a.fecha.date() for a in pend})
    por_fecha: dict[date, list[dict]] = {}
    for f in fechas:
        try:
            por_fecha[f] = _finalizados_de(f)
        except historico.ErrorHistorico:
            por_fecha[f] = []
    hallados: dict[int, tuple[float, str]] = {}
    for a in pend:
        g, _s, _motivo = _buscar(a, por_fecha)
        if g is not None:
            hallados[int(a.evento_id)] = (float(g["total"]),
                                          f"{g['local']} - {g['visita']}")
    return hallados


def resolver_con_api(apuestas: Iterable[Apuesta],
                     ) -> tuple[int, list[Apuesta]]:
    """
    Recupera y RESUELVE las apuestas huérfanas. Devuelve (resueltas, sigue_pendiente).
    """
    pend = [a for a in apuestas if a.estado == paperbet.ESTADO_PENDIENTE]
    hallados = recuperar_de_api(pend)
    if not hallados:
        return 0, pend
    guardar_totales([(eid, nombre, "apisports", total)
                     for eid, (total, nombre) in hallados.items()])
    resueltas, siguen = 0, []
    for a in pend:
        dato = hallados.get(int(a.evento_id))
        if dato is None:
            siguen.append(a)
            continue
        total, nombre = dato
        if a.total_final is None or a.estado == paperbet.ESTADO_PENDIENTE:
            paperbet.resolver(a, total)
            a.nota = (f"{a.nota}  · resultado recuperado de API-Sports: "
                      f"{nombre} (total {total:g})")
            base_datos.actualizar_resultado(a)
            resueltas += 1
    return resueltas, siguen


# --------------------------------------------------------------------------- #
#  CLI                                                                         #
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aplicar", action="store_true",
                    help="resuelve las apuestas que se puedan recuperar")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="muestra la puntuación de cada candidato")
    args = ap.parse_args()

    pendientes = base_datos.apuestas_pendientes()
    print(f"apuestas pendientes : {len(pendientes)}")
    print(f"resultados guardados: {contar()}")
    if not pendientes:
        return 0

    fechas = sorted({a.fecha.date() for a in pendientes if a.fecha})
    print("\nDescargando partidos de API-Sports (1 petición por fecha)…")
    por_fecha: dict[date, list[dict]] = {}
    for f in fechas:
        try:
            por_fecha[f] = _finalizados_de(f)
        except historico.ErrorHistorico as exc:
            print(f"   ✗ {f}: {exc}")
            por_fecha[f] = []

    vistos: set[int] = set()
    identificados = 0
    for a in pendientes:
        if a.evento_id in vistos:
            continue
        vistos.add(a.evento_id)
        g, s, motivo = _buscar(a, por_fecha)
        marca = "✔" if g else "✘"
        extra = f" -> total {g['total']:g}   [{g['local']} - {g['visita']}]" if g else ""
        print(f"   {marca} {a.partido[:44]:46s}{extra}   ({s:.2f}, {motivo})")
        identificados += bool(g)
        if args.verbose and g is not None:
            tokens = _tokens_liga(a.liga_apuesta)
            cands = [x for x in por_fecha.get(a.fecha.date(), [])
                     if _liga_encaja(tokens, x)]
            cands = sorted(cands, key=lambda x: -_pareja(a, x))[:4]
            for c in cands:
                print(f"        {_pareja(a, c):.2f}  {c['liga']:22s} "
                      f"{c['local']} - {c['visita']}")
    print(f"\n{len(vistos)} partidos distintos, {identificados} identificados")

    if args.aplicar:
        n, siguen = resolver_con_api(pendientes)
        print(f"\nRESUELTAS: {n} · siguen pendientes: {len(siguen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
