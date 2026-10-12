# -*- coding: utf-8 -*-
"""
base_datos.py — Persistencia de las apuestas paperbet en PostgreSQL.

Configuración en config.json (se crea solo con la primera ejecución):

    {
      "postgres": {
        "host": "localhost",
        "port": 5432,
        "dbname": "paperbet",
        "user": "postgres",
        "password": "postgres"
      }
    }

Tablas creadas automáticamente:
    apuestas  — cada apuesta simulada y su resultado
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2 import extras

import paperbet
from paperbet import Apuesta

CARPETA = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_CONFIG = os.path.join(CARPETA, "config.json")

CONFIG_POR_DEFECTO = {
    "postgres": {
        "host": "localhost",
        "port": 5432,
        "dbname": "paperbet",
        "user": "postgres",
        "password": "postgres",
    }
}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS apuestas (
    id               BIGSERIAL PRIMARY KEY,
    fecha            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    evento_id        BIGINT       NOT NULL,
    partido          TEXT         NOT NULL,
    liga_apuesta     TEXT         NOT NULL DEFAULT 'BBL',
    liga_modelo      TEXT         NOT NULL,
    mercado          TEXT         NOT NULL DEFAULT 'TOTAL',
    seleccion        TEXT         NOT NULL CHECK (seleccion IN ('OVER','UNDER')),
    linea            DOUBLE PRECISION NOT NULL,
    cuota            DOUBLE PRECISION NOT NULL,
    stake            DOUBLE PRECISION NOT NULL,
    prediccion       DOUBLE PRECISION,
    diferencia       DOUBLE PRECISION,
    probabilidad     DOUBLE PRECISION,
    estado           TEXT         NOT NULL DEFAULT 'PENDIENTE'
                     CHECK (estado IN ('PENDIENTE','GANADA','PERDIDA','ANULADA')),
    total_final      DOUBLE PRECISION,
    beneficio        DOUBLE PRECISION,
    nota             TEXT         DEFAULT '',
    fecha_resolucion TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS apuestas_estado_idx ON apuestas (estado);
CREATE INDEX IF NOT EXISTS apuestas_evento_idx ON apuestas (evento_id);

-- Muestras de entrenamiento: cada partido acabado con sus cuartos.
-- Las descarga el histórico oficial y las va recogiendo la pestaña EN VIVO.
CREATE TABLE IF NOT EXISTS muestras (
    id           BIGSERIAL PRIMARY KEY,
    liga         TEXT         NOT NULL,   -- termKey de BetPlay (euroleague, nba, ...)
    fecha        DATE,
    q1           DOUBLE PRECISION NOT NULL,
    q2           DOUBLE PRECISION NOT NULL,
    q3           DOUBLE PRECISION,
    q4           DOUBLE PRECISION,
    total        DOUBLE PRECISION NOT NULL,
    partido      TEXT         DEFAULT '',
    fuente       TEXT         NOT NULL DEFAULT '',
    evento_clave TEXT         UNIQUE      -- para no repetir la misma muestra
);
CREATE INDEX IF NOT EXISTS muestras_liga_idx ON muestras (liga);

-- Un modelo MCO por liga, con sus métricas (mismas fórmulas que el Excel).
CREATE TABLE IF NOT EXISTS modelos_liga (
    liga            TEXT PRIMARY KEY,
    n               INTEGER NOT NULL,
    b0              DOUBLE PRECISION NOT NULL,
    b1              DOUBLE PRECISION NOT NULL,
    b2              DOUBLE PRECISION NOT NULL,
    a0              DOUBLE PRECISION NOT NULL,
    a1              DOUBLE PRECISION NOT NULL,
    r2_dos          DOUBLE PRECISION NOT NULL,
    r2_uno          DOUBLE PRECISION NOT NULL,
    rmse_dos        DOUBLE PRECISION NOT NULL,
    rmse_uno        DOUBLE PRECISION NOT NULL,
    rmse_naive      DOUBLE PRECISION NOT NULL,
    rmse_test_dos   DOUBLE PRECISION NOT NULL,
    rmse_test_uno   DOUBLE PRECISION NOT NULL,
    media_total     DOUBLE PRECISION NOT NULL,
    media_1q        DOUBLE PRECISION NOT NULL,
    media_2q        DOUBLE PRECISION NOT NULL,
    fecha_min       DATE,
    fecha_max       DATE,
    fuente          TEXT         DEFAULT '',
    entrenado_en    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Autocalibración del descanso 2Q→3Q: duración medida en partidos reales.
CREATE TABLE IF NOT EXISTS descansos_liga (
    liga            TEXT PRIMARY KEY,
    minutos_medios  DOUBLE PRECISION NOT NULL,
    n_descansos     INTEGER NOT NULL,
    ultimo_minuto   DOUBLE PRECISION
);
"""

# Migración sobre bases ya creadas: marca de "total final verificado con el
# resultado oficial" (API-Sports) y el histórico de líneas/cuotas reales.
ESQUEMA_EXTRAS = """
ALTER TABLE apuestas ADD COLUMN IF NOT EXISTS
    verificado BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS apuestas_verificado_idx ON apuestas (verificado);

CREATE TABLE IF NOT EXISTS cuotas_historial (
    id            BIGSERIAL PRIMARY KEY,
    momento       TIMESTAMPTZ NOT NULL DEFAULT now(),
    evento_id     BIGINT NOT NULL,
    liga          TEXT NOT NULL DEFAULT '',
    partido       TEXT NOT NULL DEFAULT '',
    linea         DOUBLE PRECISION,
    cuota_over    DOUBLE PRECISION,
    cuota_under   DOUBLE PRECISION,
    period_id     TEXT DEFAULT '',
    reloj         TEXT DEFAULT '',
    finalizado    BOOLEAN NOT NULL DEFAULT FALSE,
    total_final   DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS cuotas_historial_evento_idx
    ON cuotas_historial (evento_id, id DESC);

-- Marca de "en el descanso 2Q→3Q" en cada movimiento de línea: permite medir
-- si la casa re-afina la línea DURANTE el descanso o la deja congelada.
ALTER TABLE cuotas_historial ADD COLUMN IF NOT EXISTS
    en_descanso BOOLEAN NOT NULL DEFAULT FALSE;

-- Registro de TODAS las señales del descanso (apostadas o no) para medir
-- edge de forma honesta. UNIQUE(evento_id): la PRIMERA evaluación válida del
-- descanso es la línea de entrada y nunca se sobrescribe.
CREATE TABLE IF NOT EXISTS señales (
    id              BIGSERIAL PRIMARY KEY,
    evento_id       BIGINT NOT NULL,
    liga            TEXT NOT NULL DEFAULT '',
    partido         TEXT NOT NULL DEFAULT '',
    momento         TIMESTAMPTZ NOT NULL DEFAULT now(),
    q1              DOUBLE PRECISION NOT NULL,
    q2              DOUBLE PRECISION NOT NULL,
    linea_entrada   DOUBLE PRECISION,
    cuota_over      DOUBLE PRECISION,
    cuota_under     DOUBLE PRECISION,
    pred_modelo     DOUBLE PRECISION,
    delta           DOUBLE PRECISION,
    retraso_seg     INTEGER,
    modelo_tipo     TEXT DEFAULT '',
    apuesta_id      BIGINT,
    linea_3q_5min   DOUBLE PRECISION,
    linea_cierre    DOUBLE PRECISION,
    total_final     DOUBLE PRECISION,
    resuelta_en     TIMESTAMPTZ,
    CONSTRAINT senales_evento_unico UNIQUE (evento_id)
);
CREATE INDEX IF NOT EXISTS senales_resueltas_idx ON señales (resuelta_en);
"""


class ErrorBaseDatos(Exception):
    """Error de conexión o de consulta a PostgreSQL."""


# --------------------------------------------------------------------------- #
#  Configuración                                                               #
# --------------------------------------------------------------------------- #
def cargar_config() -> dict:
    if not os.path.exists(ARCHIVO_CONFIG):
        guardar_config(CONFIG_POR_DEFECTO)
        return json.loads(json.dumps(CONFIG_POR_DEFECTO))
    try:
        with open(ARCHIVO_CONFIG, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(CONFIG_POR_DEFECTO))


def guardar_config(cfg: dict) -> None:
    with open(ARCHIVO_CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)


def descripcionConexion(cfg: Optional[dict] = None) -> str:
    cfg = cfg or cargar_config()
    p = cfg.get("postgres", {})
    return f"{p.get('user')}@{p.get('host')}:{p.get('port')}/{p.get('dbname')}"


# --------------------------------------------------------------------------- #
#  Conexión                                                                    #
# --------------------------------------------------------------------------- #
def conectar(cfg: Optional[dict] = None):
    cfg = cfg or cargar_config()
    p = cfg.get("postgres", {})
    try:
        conn = psycopg2.connect(
            host=p.get("host", "localhost"),
            port=int(p.get("port", 5432)),
            dbname=p.get("dbname", "paperbet"),
            user=p.get("user", "postgres"),
            password=p.get("password", ""),
            connect_timeout=8,
        )
    except psycopg2.OperationalError as exc:
        raise ErrorBaseDatos(
            f"No se pudo conectar a PostgreSQL ({descripcionConexion(cfg)}).\n"
            f"Detalle: {str(exc).strip()}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ErrorBaseDatos(f"Error de base de datos: {exc}") from exc
    return conn


def probar_conexion() -> tuple[bool, str]:
    """Devuelve (ok, mensaje). No lanza excepción."""
    try:
        conn = conectar()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = cur.fetchone()[0]
        conn.close()
        return True, f"PostgreSQL OK — {version.split(' on')[0]}"
    except ErrorBaseDatos as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"Error inesperado: {exc}"


def crear_esquema(cfg: Optional[dict] = None) -> None:
    conn = conectar(cfg)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(ESQUEMA)
                cur.execute(ESQUEMA_EXTRAS)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Conversión Apuesta <-> fila                                                 #
# --------------------------------------------------------------------------- #
def _fila_a_apuesta(tupla) -> Apuesta:
    (
        a_id, fecha, evento_id, partido, liga_apuesta, liga_modelo, mercado,
        seleccion, linea, cuota, stake, prediccion, diferencia, probabilidad,
        estado, total_final, beneficio, nota, fecha_resolucion,
    ) = tupla
    return Apuesta(
        id=a_id,
        fecha=fecha,
        evento_id=evento_id,
        partido=partido,
        liga_apuesta=liga_apuesta,
        liga_modelo=liga_modelo,
        mercado=mercado,
        seleccion=seleccion,
        linea=float(linea),
        cuota=float(cuota),
        stake=float(stake),
        prediccion=None if prediccion is None else float(prediccion),
        diferencia=None if diferencia is None else float(diferencia),
        probabilidad=None if probabilidad is None else float(probabilidad),
        estado=estado,
        total_final=None if total_final is None else float(total_final),
        beneficio=None if beneficio is None else float(beneficio),
        nota=nota or "",
        fecha_resolucion=fecha_resolucion,
    )


_SELECT = """
    SELECT id, fecha, evento_id, partido, liga_apuesta, liga_modelo, mercado,
           seleccion, linea, cuota, stake, prediccion, diferencia, probabilidad,
           estado, total_final, beneficio, nota, fecha_resolucion
      FROM apuestas
"""


# --------------------------------------------------------------------------- #
#  CRUD                                                                        #
# --------------------------------------------------------------------------- #
def guardar_apuesta(a: Apuesta) -> int:
    """Inserta la apuesta y devuelve su id."""
    if paperbet.validar(a):
        raise ErrorBaseDatos(paperbet.validar(a))
    conn = conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO apuestas
                      (evento_id, partido, liga_apuesta, liga_modelo, mercado,
                       seleccion, linea, cuota, stake, prediccion, diferencia,
                       probabilidad, estado, nota)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        a.evento_id, a.partido, a.liga_apuesta, a.liga_modelo,
                        a.mercado, a.seleccion, a.linea, a.cuota, a.stake,
                        a.prediccion, a.diferencia, a.probabilidad, a.estado,
                        a.nota or "",
                    ),
                )
                nuevo_id = cur.fetchone()[0]
    finally:
        conn.close()
    a.id = nuevo_id
    return nuevo_id


def listar_apuestas(estado: Optional[str] = None) -> list[Apuesta]:
    conn = conectar()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            if estado:
                cur.execute(_SELECT + " WHERE estado = %s ORDER BY fecha DESC, id DESC", (estado,))
            else:
                cur.execute(_SELECT + " ORDER BY fecha DESC, id DESC")
            filas = cur.fetchall()
    finally:
        conn.close()
    return [_fila_a_apuesta(tuple(f)) for f in filas]


def apuestas_pendientes() -> list[Apuesta]:
    return listar_apuestas(paperbet.ESTADO_PENDIENTE)


def actualizar_resultado(a: Apuesta) -> None:
    """Guarda el resultado de una apuesta ya resuelta."""
    if a.id is None:
        raise ErrorBaseDatos("La apuesta no está guardada (id nulo).")
    conn = conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE apuestas
                       SET estado = %s,
                           total_final = %s,
                           beneficio = %s,
                           nota = %s,
                           fecha_resolucion = %s
                     WHERE id = %s
                    """,
                    (
                        a.estado, a.total_final, a.beneficio, a.nota,
                        a.fecha_resolucion, a.id,
                    ),
                )
    finally:
        conn.close()


def eliminar_apuesta(apuesta_id: int) -> None:
    conn = conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM apuestas WHERE id = %s", (apuesta_id,))
    finally:
        conn.close()


def resumen() -> paperbet.Resumen:
    return paperbet.resumir(listar_apuestas())


def estadisticas_bd() -> dict:
    """Info extra de la base (nº filas, primera y última apuesta)."""
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), MIN(fecha), MAX(fecha), "
                "COALESCE(SUM(stake),0), COALESCE(SUM(beneficio),0) FROM apuestas"
            )
            n, f1, f2, stake, benef = cur.fetchone()
    finally:
        conn.close()
    return {
        "apuestas": int(n),
        "primera": f1,
        "ultima": f2,
        "stake_total": float(stake or 0),
        "beneficio": float(benef or 0),
    }


# --------------------------------------------------------------------------- #
#  Candado anti-duplicados, verificación y cuotas reales                      #
# --------------------------------------------------------------------------- #
def existe_apuesta(evento_id: int) -> bool:
    """¿Ya hay una apuesta guardada para este partido? (candado persistente)."""
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM apuestas WHERE evento_id = %s LIMIT 1",
                        (evento_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def apuestas_para_reconciliar(horas: float = 14.0) -> list[tuple[Apuesta, bool]]:
    """
    Apuestas recientes que aún pueden corregirse con el resultado oficial:

      * las PENDIENTE (por si el evento desapareció del feed sin cerrarse), y
      * las resueltas con `verificado = false` (total aún sin contrastar).

    Devuelve [(Apuesta, verificado), ...] de las últimas `horas` horas.
    """
    conn = conectar()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute(
                """SELECT id, fecha, evento_id, partido, liga_apuesta,
                          liga_modelo, mercado, seleccion, linea, cuota, stake,
                          prediccion, diferencia, probabilidad, estado,
                          total_final, beneficio, nota, fecha_resolucion,
                          verificado
                     FROM apuestas
                    WHERE fecha >= now() - (%s * interval '1 hour')
                      AND (estado = 'PENDIENTE' OR verificado = false)
                    ORDER BY id""", (horas,))
            filas = cur.fetchall()
    finally:
        conn.close()

    salida: list[tuple[Apuesta, bool]] = []
    for f in filas:
        a = _fila_a_apuesta(tuple(f)[:19])
        salida.append((a, bool(f["verificado"])))
    return salida


def marcar_verificado(apuesta_id: int, verificado: bool = True) -> None:
    conn = conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE apuestas SET verificado = %s WHERE id = %s",
                            (verificado, apuesta_id))
    finally:
        conn.close()


def ultimas_cuotas() -> dict[int, tuple]:
    """
    Último movimiento guardado por evento en `cuotas_historial`:
    {evento_id: (linea, cuota_over, cuota_under, period_id, finalizado, total_final)}.

    Sirve para no repetir filas al reiniciar la app (el caché en memoria se
    pierde) y guardar solo cuando la línea/cuotas cambian de verdad.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (evento_id)
                          evento_id, linea, cuota_over, cuota_under,
                          period_id, finalizado, total_final
                     FROM cuotas_historial
                    ORDER BY evento_id, id DESC""")
            return {r[0]: tuple(r[1:]) for r in cur.fetchall()}
    finally:
        conn.close()


def guardar_cuotas(filas: list[tuple]) -> int:
    """
    Inserta movimientos de línea/cuota en `cuotas_historial`.

    Cada fila: (evento_id, liga, partido, linea, cuota_over, cuota_under,
    period_id, reloj, finalizado, total_final, en_descanso).
    """
    if not filas:
        return 0
    conn = conectar()
    try:
        with conn:
            with conn.cursor() as cur:
                extras.execute_values(
                    cur,
                    """INSERT INTO cuotas_historial
                         (evento_id, liga, partido, linea, cuota_over,
                          cuota_under, period_id, reloj, finalizado,
                          total_final, en_descanso)
                       VALUES %s""",
                    filas, page_size=200)
        return len(filas)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Señales (registro de TODAS las evaluaciones del descanso, apostadas o no)  #
# --------------------------------------------------------------------------- #
def guardar_senal(p, res, retraso, modelo_tipo: str = "") -> bool:
    """
    Registra una señal del descanso 2Q→3Q (se apueste o no).

    ON CONFLICT (evento_id) DO NOTHING: la PRIMERA evaluación válida es la
    línea de entrada y NUNCA se sobrescribe. Silencioso por diseño: un fallo
    de registro no puede interrumpir al bot de apuestas (devuelve False).
    """
    try:
        conn = conectar()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO señales
                             (evento_id, liga, partido, q1, q2, linea_entrada,
                              cuota_over, cuota_under, pred_modelo, delta,
                              retraso_seg, modelo_tipo)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                   %s, %s)
                           ON CONFLICT (evento_id) DO NOTHING""",
                        (int(p.evento_id), p.clave_liga or "", p.nombre or "",
                         float(p.q1), float(p.q2),
                         None if p.linea is None else float(p.linea),
                         p.cuota_over, p.cuota_under,
                         getattr(res, "prediccion", None),
                         getattr(res, "diferencia", None),
                         None if retraso is None else int(retraso),
                         modelo_tipo or ""))
            return True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — jamás interrumpe al bot
        return False


def vincular_apuesta(evento_id: int, apuesta_id: int) -> None:
    """Enlaza la señal con la apuesta creada (si el bot la tomó). Silencioso."""
    try:
        conn = conectar()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE señales SET apuesta_id = %s
                            WHERE evento_id = %s AND apuesta_id IS NULL""",
                        (int(apuesta_id), int(evento_id)))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def _reloj_a_segundos(reloj) -> Optional[int]:
    try:
        m, s = str(reloj or "").split(":")
        return int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return None


def _linea_3q_5min(evento_id: int) -> Optional[float]:
    """
    Línea de cierre a los 5 minutos de juego del 3Q.

    Definición (fijada; no se cambia después): como `cuotas_historial` solo
    guarda CAMBIOS, la línea "vigente" a los +5:00 es la ÚLTIMA fila del
    evento con period_id='QUARTER3' y reloj ≤ inicio_del_3Q + 5:00. El inicio
    del 3Q se deriva del reloj acumulado de medio tiempo registrado
    (20:00 = FIBA / 24:00 = NBA; por defecto FIBA).

    Devuelve NULL cuando:
      * no hay ninguna fila de QUARTER3 dentro de (inicio, inicio + 5:00], o
      * hay una PAUSA DE CAPTURA EVIDENTE: la primera fila capturada del 3Q
        aparece con más de 90 s de juego ya transcurridos (se perdió la
        transición y no puede afirmarse la línea vigente a los 5 min).

    Limitación documentada: un corte de captura DESPUÉS de la primera fila
    del 3Q y ANTES de +5:00 no es distinguible de "la línea no cambió" con
    estos datos (no hay latido/heartbeat); en ese caso no se aproxima.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT reloj, linea FROM cuotas_historial
                    WHERE evento_id = %s AND period_id = 'QUARTER3'
                      AND linea IS NOT NULL AND reloj <> ''
                    ORDER BY id""", (evento_id,))
            filas = cur.fetchall()
            cur.execute(
                """SELECT reloj FROM cuotas_historial
                    WHERE evento_id = %s AND reloj <> '' AND (
                          en_descanso = true
                          OR (period_id = 'QUARTER2'
                              AND reloj IN ('20:00','24:00')))
                    ORDER BY id LIMIT 1""", (evento_id,))
            fila = cur.fetchone()
    finally:
        conn.close()
    if not filas:
        return None
    cuarto = 12 if (fila and fila[0] == "24:00") else 10
    inicio = 2 * cuarto * 60
    limite = inicio + 5 * 60
    primera = _reloj_a_segundos(filas[0][0])
    if primera is not None and primera > inicio + 90:
        return None                    # pausa de captura evidente
    elegida: Optional[float] = None
    for reloj, linea in filas:
        seg = _reloj_a_segundos(reloj)
        if seg is None:
            continue
        if inicio < seg <= limite:
            elegida = float(linea)     # la ÚLTIMA ≤ inicio+5:00
    return elegida


def _linea_cierre_descanso(evento_id: int) -> Optional[float]:
    """
    Última línea del descanso — SOLO REFERENCIA.

    Se espera CLV≈0 contra ella por construcción: la línea se queda CONGELADA
    durante el descanso (Kambi la re-mueve con el juego del 3Q, no con el
    reloj del descanso). El CLV real se mide contra `linea_3q_5min`.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT linea FROM cuotas_historial
                    WHERE evento_id = %s AND linea IS NOT NULL AND (
                          en_descanso = true
                          OR (period_id = 'QUARTER2'
                              AND reloj IN ('20:00','24:00')))
                    ORDER BY id DESC LIMIT 1""", (evento_id,))
            fila = cur.fetchone()
    finally:
        conn.close()
    return float(fila[0]) if fila else None


def _total_final_senal(partido: str, momento, apuesta_id) -> Optional[float]:
    """Total oficial: de la apuesta enlazada si ya lo tiene; si no, muestras."""
    conn = conectar()
    try:
        with conn.cursor() as cur:
            if apuesta_id is not None:
                cur.execute("SELECT total_final FROM apuestas WHERE id = %s",
                            (apuesta_id,))
                fila = cur.fetchone()
                if fila and fila[0] is not None:
                    return float(fila[0])
            fecha = momento.date() if hasattr(momento, "date") else None
            cur.execute(
                """SELECT total FROM muestras
                    WHERE partido = %s AND (%s IS NULL OR fecha = %s)
                    ORDER BY id DESC LIMIT 1""",
                (partido, fecha, fecha))
            fila = cur.fetchone()
    finally:
        conn.close()
    return float(fila[0]) if fila and fila[0] is not None else None


def cerrar_senales() -> int:
    """
    Completa las señales con cierre y resultado. IDEMPOTENTE (solo rellena
    columnas NULL; puede correr cuantas veces quiera). Nunca lanza.

      * linea_3q_5min: a los 5 min de juego del 3Q (ver _linea_3q_5min).
      * linea_cierre: última línea del descanso (solo referencia; ver nota).
      * total_final: apuesta enlazada o `muestras` por partido+fecha.
      * resuelta_en: cuando hay total_final.

    Devuelve cuántas filas se actualizaron.
    """
    try:
        conn = conectar()
    except Exception:  # noqa: BLE001
        return 0
    try:
        try:
            conn.autocommit = True   # un fallo puntual no aborta el resto
        except Exception:  # noqa: BLE001
            pass
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, evento_id, partido, momento,
                              linea_3q_5min, linea_cierre, total_final,
                              apuesta_id
                         FROM señales
                        WHERE resuelta_en IS NULL
                           OR linea_3q_5min IS NULL
                           OR linea_cierre IS NULL
                        ORDER BY id""")
                filas = cur.fetchall()
        except Exception:  # noqa: BLE001
            return 0

        cambios = 0
        for (sid, ev, partido, momento, l3, lc, total, ap_id) in filas:
            nuevo_3q = l3 if l3 is not None else _linea_3q_5min(ev)
            nueva_lc = lc if lc is not None else _linea_cierre_descanso(ev)
            nuevo_total = (total if total is not None
                           else _total_final_senal(partido, momento, ap_id))
            if nuevo_3q is None and nueva_lc is None and nuevo_total is None:
                continue
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """UPDATE señales
                                  SET linea_3q_5min =
                                        COALESCE(linea_3q_5min, %s),
                                      linea_cierre =
                                        COALESCE(linea_cierre, %s),
                                      total_final =
                                        COALESCE(total_final, %s),
                                      resuelta_en = CASE
                                        WHEN COALESCE(total_final, %s)
                                             IS NOT NULL
                                             AND resuelta_en IS NULL
                                        THEN now() ELSE resuelta_en END
                                WHERE id = %s""",
                            (nuevo_3q, nueva_lc, nuevo_total, nuevo_total,
                             sid))
                cambios += 1
            except Exception:  # noqa: BLE001
                continue
        return cambios
    finally:
        conn.close()
