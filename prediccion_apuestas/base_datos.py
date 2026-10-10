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
                       probabilidad, estado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        a.evento_id, a.partido, a.liga_apuesta, a.liga_modelo,
                        a.mercado, a.seleccion, a.linea, a.cuota, a.stake,
                        a.prediccion, a.diferencia, a.probabilidad, a.estado,
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
