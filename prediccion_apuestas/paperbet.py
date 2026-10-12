# -*- coding: utf-8 -*-
"""
paperbet.py — Simulación de apuestas (paper betting) sobre el mercado de TOTAL.

Reglas:
  * Se registra la apuesta en el momento con: partido, selección (OVER/UNDER),
    línea del bookmaker, cuota, stake, predicción del modelo y probabilidad.
  * Se resuelve cuando el partido termina, comparando el TOTAL FINAL (incluida
    la prórroga, igual que el mercado "Total de puntos - Prórroga incluida").
  * Beneficio:
        GANADA  -> stake * (cuota - 1)
        PERDIDA -> -stake
        ANULADA ->  0   (empate exacto sobre la línea: push, se devuelve el stake)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_GANADA = "GANADA"
ESTADO_PERDIDA = "PERDIDA"
ESTADO_ANULADA = "ANULADA"

ESTADOS = (ESTADO_PENDIENTE, ESTADO_GANADA, ESTADO_PERDIDA, ESTADO_ANULADA)


@dataclass
class Apuesta:
    # --- identificación ---
    id: Optional[int] = None
    fecha: Optional[datetime] = None
    evento_id: int = 0
    partido: str = ""
    liga_apuesta: str = "BBL"           # liga que se apuesta (la de la API)
    liga_modelo: str = "EUROLIGA"       # modelo usado para la predicción
    # --- mercado ---
    mercado: str = "TOTAL"              # TOTAL (Más/Menos)
    seleccion: str = "OVER"             # OVER | UNDER
    linea: float = 0.0
    cuota: float = 0.0
    stake: float = 0.0
    # --- modelo ---
    prediccion: Optional[float] = None
    diferencia: Optional[float] = None  # predicción - línea (el "edge" en puntos)
    probabilidad: Optional[float] = None
    # --- resultado ---
    estado: str = ESTADO_PENDIENTE
    total_final: Optional[float] = None
    beneficio: Optional[float] = None
    nota: str = ""
    fecha_resolucion: Optional[datetime] = None

    # ------------------------------------------------------------ derivados
    @property
    def es_over(self) -> bool:
        return self.seleccion.upper() == "OVER"

    @property
    def apostado(self) -> float:
        return self.stake

    @property
    def retorno(self) -> Optional[float]:
        if self.beneficio is None:
            return None
        return self.stake + self.beneficio


def _unir_nota(resultado: str, prev: str) -> str:
    """
    Une el texto del resultado con la nota ANTERIOR sin pisarla.

    La nota original del bot ("AUTO descanso2Q…: 1Q=.. 2Q=..") es valiosa
    (el replay y las auditorías la necesitan); antes se perdía al liquidar.
    Repetible: si ya había un resultado delante, lo sustituye (no acumula).
    """
    prev = prev or ""
    if prev.startswith(("Gana ", "Pierde ", "Total exactamente")):
        prev = prev.split(" · ", 1)[1] if " · " in prev else ""
    return f"{resultado} · {prev}" if prev else resultado


def recalcular(
    apuesta: Apuesta,
    total_final: float,
    ahora: Optional[datetime] = None,
) -> Apuesta:
    """
    Fija (o CORRIGE) el resultado de la apuesta con un total final.

    A diferencia de `resolver`, no exige que esté PENDIENTE: se usa para
    re-liquidar con el resultado OFICIAL cuando el total del feed era
    impreciso (prórrogas, datos con retraso...). Recalcula estado y dinero.

    - OVER gana si total_final > línea
    - UNDER gana si total_final < línea
    - Si total_final == línea -> ANULADA (push) y se devuelve el stake
    """
    apuesta.total_final = float(total_final)
    apuesta.fecha_resolucion = ahora or datetime.now()

    if abs(float(total_final) - float(apuesta.linea)) < 1e-9:
        apuesta.estado = ESTADO_ANULADA
        apuesta.beneficio = 0.0
        apuesta.nota = _unir_nota(
            "Total exactamente en la línea: apuesta anulada (push).",
            apuesta.nota)
        return apuesta

    gana_over = float(total_final) > float(apuesta.linea)
    gana = gana_over if apuesta.es_over else (not gana_over)

    if gana:
        apuesta.estado = ESTADO_GANADA
        apuesta.beneficio = round(apuesta.stake * (apuesta.cuota - 1.0), 2)
        apuesta.nota = _unir_nota(
            f"Gana {apuesta.seleccion}: total {total_final:g} vs línea "
            f"{apuesta.linea:g}.", apuesta.nota)
    else:
        apuesta.estado = ESTADO_PERDIDA
        apuesta.beneficio = round(-apuesta.stake, 2)
        apuesta.nota = _unir_nota(
            f"Pierde {apuesta.seleccion}: total {total_final:g} vs línea "
            f"{apuesta.linea:g}.", apuesta.nota)
    return apuesta


def resolver(
    apuesta: Apuesta,
    total_final: float,
    ahora: Optional[datetime] = None,
) -> Apuesta:
    """
    Resuelve la apuesta con el total final del partido (si sigue pendiente).

    - OVER gana si total_final > línea
    - UNDER gana si total_final < línea
    - Si total_final == línea -> ANULADA (push) y se devuelve el stake
    """
    if apuesta.estado != ESTADO_PENDIENTE:
        return apuesta
    return recalcular(apuesta, total_final, ahora)
    return apuesta


def anular(apuesta: Apuesta, motivo: str, ahora: Optional[datetime] = None) -> Apuesta:
    """Anula una apuesta (partido cancelado, cuota errónea, etc.)."""
    apuesta.estado = ESTADO_ANULADA
    apuesta.beneficio = 0.0
    apuesta.nota = motivo
    apuesta.fecha_resolucion = ahora or datetime.now()
    return apuesta


# --------------------------------------------------------------------------- #
#  Resumen / estadísticas del bankroll de papel                               #
# --------------------------------------------------------------------------- #
@dataclass
class Resumen:
    total: int = 0
    pendientes: int = 0
    ganadas: int = 0
    perdidas: int = 0
    anuladas: int = 0
    stake_total: float = 0.0
    beneficio: float = 0.0
    aciertos: Optional[float] = None   # % sobre ganadas + perdidas
    roi: Optional[float] = None        # % sobre lo apostado en partidas decididas

    @property
    def decididas(self) -> int:
        return self.ganadas + self.perdidas

    @property
    def bankroll(self) -> float:
        return self.beneficio


def resumir(apuestas: list[Apuesta]) -> Resumen:
    r = Resumen(total=len(apuestas))
    stake_decidido = 0.0
    for a in apuestas:
        if a.estado == ESTADO_PENDIENTE:
            r.pendientes += 1
        elif a.estado == ESTADO_GANADA:
            r.ganadas += 1
            stake_decidido += a.stake
            r.beneficio += a.beneficio or 0.0
        elif a.estado == ESTADO_PERDIDA:
            r.perdidas += 1
            stake_decidido += a.stake
            r.beneficio += a.beneficio or 0.0
        elif a.estado == ESTADO_ANULADA:
            r.anuladas += 1
            r.beneficio += a.beneficio or 0.0
    r.stake_total = stake_decidido
    if r.decididas:
        r.aciertos = r.ganadas / r.decididas * 100.0
    if stake_decidido:
        r.roi = r.beneficio / stake_decidido * 100.0
    return r


def validar(apuesta: Apuesta) -> Optional[str]:
    """Devuelve un mensaje de error o None si la apuesta es válida."""
    if apuesta.seleccion not in ("OVER", "UNDER"):
        return "La selección debe ser OVER o UNDER."
    if not apuesta.linea or apuesta.linea <= 0:
        return "Falta la línea del bookmaker."
    if not apuesta.cuota or apuesta.cuota <= 1.0:
        return "La cuota debe ser mayor que 1."
    if apuesta.stake <= 0:
        return "El stake debe ser mayor que 0."
    if apuesta.prediccion is None:
        return "Falta la predicción del modelo."
    return None
