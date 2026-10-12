# -*- coding: utf-8 -*-
"""
app.py — Aplicación de escritorio (tkinter).

Partes:
  1. Pestañas EUROLIGA / NBA  -> réplica de la hoja PREDICCION del Excel
  2. Pestaña EN VIVO          -> PING a la API de BetPlay, flujo 1Q+2Q y predicción
  3. Pestaña APUESTAS         -> paperbet: simula la apuesta y la guarda en PostgreSQL

Ejecutar:  python app.py      (o doble clic en iniciar.bat)
"""

from __future__ import annotations

import os
import sys
import threading
import time
import tkinter as tk
from collections import Counter
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

import api_betplay
import base_datos
import descansos
import historico
import motor
import paperbet
from motor import Modelo, parsear, predecir, fecha_excel_a_texto
from paperbet import Apuesta

# ---------------------------------------------------------------------------
#  Paleta de colores copiada de los estilos del Excel
# ---------------------------------------------------------------------------
C_TITULO_BG = "#C00000"
C_TITULO_FG = "#FFFFFF"
C_SUB_FG = "#808080"
C_HEAD_BG = "#D9D9D9"
C_INPUT_BG = "#FFF2CC"
C_PRED_BG = "#E2EFDA"
C_VERDE = "#006100"
C_VERDICTO_OK = "#C6E0CE"
C_ROJO_BG = "#FFC7CE"
C_ROJO_FG = "#C00000"
C_AZUL = "#1F4E79"
C_AZUL_CLARO = "#DDEBF7"

F_TITULO = ("Calibri", 15, "bold")
F_SUB = ("Calibri", 9, "italic")
F_HEAD = ("Calibri", 11, "bold")
F_LABEL = ("Calibri", 11, "bold")
F_TEXTO = ("Calibri", 11)
F_INPUT = ("Calibri", 11, "bold")
F_PRED = ("Calibri", 13, "bold")
F_VEREDICTO = ("Calibri", 14, "bold")
F_PEQUE = ("Calibri", 9)

RUTA_DEFECTO = motor.EXCEL_POR_DEFECTO
STAKE_POR_DEFECTO = 10000
# Piso de seguridad: un Δ pequeño convierte la apuesta en ruido (el margen de
# la casa ~8-10 % exige ≥55 % de aciertos a cuota ~1.8). El backtest de
# septiembre con Δ=0.5 lo confirmó: menos winrate con más volumen.
UMBRAL_MINIMO = 2.0                 # Δ mínimo que la app permite usar
EV_MINIMA = 0.0                     # EV mínimo que la app permite usar (%)
INTERVALO_POR_DEFECTO = 5          # segundos entre pings automáticos
UMBRAL_POR_DEFECTO = 2.0           # Δ mínimo (pts) para apostar solo en el descanso
# Valor esperado mínimo. 0.0 = que la apuesta sea +EV con la cuota que ofrece
# la casa (con su margen del ~8%, un Δ pequeño puede ser -EV igualmente).
EV_MINIMA_POR_DEFECTO = 0.0

# Historial permanente de las decisiones del bot automático. Responde de forma
# definitiva a "¿y por qué no ha apostado?": la interfaz solo puede enseñar el
# motivo mientras el partido sigue en el descanso, y ese motivo se pierde al
# pasar al 3Q. (*.log ya está en .gitignore)
CARPETA_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")
ARCHIVO_LOG = os.path.join(CARPETA_LOG, "auto_apuestas.log")


def log_auto(texto: str) -> None:
    """Anota una decisión del bot con su hora. Nunca rompe la app."""
    try:
        os.makedirs(CARPETA_LOG, exist_ok=True)
        if os.path.exists(ARCHIVO_LOG) and os.path.getsize(ARCHIVO_LOG) > 400_000:
            os.remove(ARCHIVO_LOG)         # empieza de cero en vez de crecer
        marca = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(ARCHIVO_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{marca}  {texto}\n")
    except OSError:
        pass


def f1(v) -> str:
    return "" if v is None else f"{v:.1f}"


def f3(v) -> str:
    return "" if v is None else f"{v:.3f}"


def exacto(v) -> str:
    return "" if v is None else repr(v)


def ahora_texto() -> str:
    return datetime.now().strftime("%H:%M:%S")


def inicio_texto(iso: str) -> str:
    """'start' de Kambi (ISO, UTC) → fecha y hora LOCAL 'dd/mm HH:MM' (o '')."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return ""


# ===========================================================================
#  Pestaña de predicción manual (EUROLIGA / NBA) — réplica del Excel
# ===========================================================================
class Pestana(ttk.Frame):
    FILAS_ENTRADA = [
        ("q1", "1Q jugado (ambos equipos)", "Suma de ambos equipos en el 1er cuarto"),
        ("q2", "2Q jugado (ambos equipos)", "Dejalo vacio si aun no hay 2Q"),
        ("linea", "Linea del bookmaker (total)", "Total que ofrece la casa de apuestas"),
    ]

    def __init__(self, master, liga: str, app: "App"):
        super().__init__(master, padding=12)
        self.liga = liga
        self.app = app
        self.modelo: Modelo | None = None

        self.vars = {"q1": tk.StringVar(), "q2": tk.StringVar(), "linea": tk.StringVar()}
        for v in self.vars.values():
            v.trace_add("write", lambda *_: self.recalcular())

        self._construir()

    def _construir(self) -> None:
        # El contenido va dentro de un canvas con barra vertical. Así, en
        # monitores bajos la pestaña nunca desborda la pantalla (se desplaza)
        # y en monitores altos la barra ni se ve y el hueco sobrante queda
        # vacío, sin estirar las filas.
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self._vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._vsb_visible = False
        self._panel = tk.Frame(self._canvas)
        self._ventana_canvas = self._canvas.create_window(
            (0, 0), window=self._panel, anchor="nw")
        self._panel.bind("<Configure>", self._panel_configurado)
        self._canvas.bind("<Configure>", self._canvas_configurado)
        self._canvas.bind("<Enter>", self._rueda_on)
        self._canvas.bind("<Leave>", self._rueda_off)
        self._canvas.pack(side="left", fill="both", expand=True)

        p = self._panel
        p.lbl_titulo = tk.Label(
            p, text="EUROLIGA 2025-26" if self.liga == "EUROLIGA" else "NBA 2025-26",
            bg=C_TITULO_BG, fg=C_TITULO_FG, font=F_TITULO, anchor="w", padx=10,
        )
        p.lbl_titulo.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self.lbl_titulo = p.lbl_titulo

        self.lbl_sub = tk.Label(p, text="", font=F_SUB, fg=C_SUB_FG, anchor="w")
        self.lbl_sub.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))

        for col, txt in ((0, "DATO"), (1, "VALOR"), (2, "COMENTARIO")):
            tk.Label(p, text=txt, bg=C_HEAD_BG, font=F_HEAD, anchor="center", pady=3).grid(
                row=2, column=col, sticky="ew", padx=(0, 6)
            )

        self.entradas: dict[str, tk.Entry] = {}
        r = 3
        for clave, etiqueta, comentario in self.FILAS_ENTRADA:
            tk.Label(p, text=etiqueta, font=F_LABEL, anchor="w").grid(
                row=r, column=0, sticky="ew", padx=(0, 6), pady=2)
            e = tk.Entry(p, textvariable=self.vars[clave], font=F_INPUT, justify="center",
                         bg=C_INPUT_BG, relief="solid", bd=1)
            e.grid(row=r, column=1, sticky="ew", padx=(0, 6), pady=2)
            e.bind("<Return>", lambda _e: self.recalcular())
            self.entradas[clave] = e
            tk.Label(p, text=comentario, font=F_TEXTO, fg=C_SUB_FG, anchor="w").grid(
                row=r, column=2, sticky="ew", pady=2)
            r += 1

        r += 1
        self._fila_valor(r, "PREDICCION TOTAL", "Usa 1Q+2Q si hay 2Q; si no, solo 1Q", "pred")
        r += 1
        self.lbl_exacto = tk.Label(p, text="", font=F_PEQUE, fg=C_SUB_FG, anchor="w")
        self.lbl_exacto.grid(row=r, column=0, columnspan=3, sticky="ew", padx=(0, 6))
        r += 1
        self._fila_valor(r, "Pronostico solo con 1Q", "Modelo lineal calibrado con la liga entera", "solo")
        r += 1
        self._fila_valor(r, "Referencia naive (1Q x 4)", "Multiplicar por 4 es lo peor", "naive")
        r += 1
        self._fila_valor(r, "Pronostico menos linea", "Positivo = por encima de la linea", "dif")
        r += 1

        tk.Label(p, text="VEREDICTO over / under", font=F_LABEL, anchor="w").grid(
            row=r, column=0, sticky="ew", padx=(0, 6), pady=2)
        self.lbl_veredicto = tk.Label(p, text="faltan datos", font=F_VEREDICTO, anchor="center",
                                      bg=C_VERDICTO_OK, fg=C_VERDE, pady=4)
        self.lbl_veredicto.grid(row=r, column=1, sticky="ew", padx=(0, 6), pady=2)
        tk.Label(p, text="Compara el pronostico con la linea", font=F_TEXTO,
                 fg=C_SUB_FG, anchor="w").grid(row=r, column=2, sticky="ew", pady=2)
        r += 1

        r += 1
        self.metricas: dict[str, tk.Label] = {}
        filas_metricas = [
            ("r2_dos", "R2 modelo 1Q + 2Q", "Con el 2Q se explica la varianza"),
            ("r2_uno", "R2 modelo solo 1Q", "Solo con el 1Q se explica la varianza"),
            ("rmse_dos", "Error test (RMSE) 1Q + 2Q", "Validacion futura (fijada en el Excel)"),
            ("rmse_uno", "Error test (RMSE) solo 1Q", "Validacion futura (fijada en el Excel)"),
            ("rmse_naive", "Error test (RMSE) 1Q x 4", "Naive 1Q x 4: referencia a batir"),
            ("n", "Partidos en el modelo", "Partidos reales de la temporada 2025-26"),
            ("media", "Promedio total de la liga", "Total medio de los partidos"),
            ("basta", "Con el 1Q basta?", "El error baja con el 2Q"),
        ]
        for clave, etiqueta, comentario in filas_metricas:
            tk.Label(p, text=etiqueta, font=F_LABEL, anchor="w").grid(
                row=r, column=0, sticky="ew", padx=(0, 6), pady=2)
            lbl = tk.Label(p, text="", font=F_TEXTO, anchor="center", width=22)
            lbl.grid(row=r, column=1, sticky="ew", padx=(0, 6), pady=2)
            self.metricas[clave] = lbl
            c = tk.Label(p, text=comentario, font=F_TEXTO, fg=C_SUB_FG, anchor="w")
            c.grid(row=r, column=2, sticky="ew", pady=2)
            if clave == "basta":
                self.lbl_basta_coment = c
            r += 1

        r += 1
        tk.Label(p, text="EXTRA (no esta en el Excel)", font=("Calibri", 10, "bold"),
                 fg=C_SUB_FG, anchor="w").grid(row=r, column=0, columnspan=3, sticky="ew")
        r += 1
        tk.Label(p, text="Probabilidad de OVER", font=F_LABEL, anchor="w").grid(
            row=r, column=0, sticky="ew", padx=(0, 6), pady=2)
        self.lbl_prob = tk.Label(p, text="", font=F_TEXTO, anchor="center", width=22)
        self.lbl_prob.grid(row=r, column=1, sticky="ew", padx=(0, 6), pady=2)
        tk.Label(p, text="P = Φ((prediccion - linea) / RMSE)", font=F_TEXTO,
                 fg=C_SUB_FG, anchor="w").grid(row=r, column=2, sticky="ew", pady=2)

        r += 2
        caja = tk.Frame(p)
        caja.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))
        tk.Button(caja, text="Calcular (Enter)", font=F_LABEL, width=18,
                  command=self.recalcular).pack(side="left", padx=(0, 6))
        tk.Button(caja, text="Limpiar", font=F_TEXTO, width=10,
                  command=self.limpiar).pack(side="left", padx=(0, 6))
        tk.Button(caja, text="Ejemplo del Excel", font=F_TEXTO, width=16,
                  command=self.ejemplo).pack(side="left")

        p.columnconfigure(0, weight=3)
        p.columnconfigure(1, weight=1)
        p.columnconfigure(2, weight=4)

    # ------------------------------------------------- desplazamiento (bajo)
    def _panel_configurado(self, _evento=None) -> None:
        """La barra solo aparece si el contenido es más alto que la ventana."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        cabe = self._panel.winfo_reqheight() <= self._canvas.winfo_height() + 2
        if cabe and self._vsb_visible:
            self._vsb.pack_forget()
            self._vsb_visible = False
        elif not cabe and not self._vsb_visible:
            self._vsb.pack(side="right", fill="y")
            self._vsb_visible = True

    def _canvas_configurado(self, event) -> None:
        # el panel ocupa todo el ancho disponible: nunca barra horizontal
        self._canvas.itemconfigure(self._ventana_canvas, width=event.width)

    def _rueda_on(self, _evento=None) -> None:
        self._canvas.bind_all("<MouseWheel>", self._rueda)

    def _rueda_off(self, _evento=None) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _rueda(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _fila_valor(self, row: int, etiqueta: str, comentario: str, clave: str) -> None:
        p = self._panel
        tk.Label(p, text=etiqueta, font=F_LABEL, anchor="w").grid(
            row=row, column=0, sticky="ew", padx=(0, 6), pady=2)
        es_pred = clave == "pred"
        estilo = {"font": F_PRED if es_pred else F_TEXTO}
        if es_pred:
            estilo.update(bg=C_PRED_BG, fg=C_VERDE, pady=3)
        lbl = tk.Label(p, text="", anchor="center", **estilo)
        lbl.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=2)
        setattr(self, f"out_{clave}", lbl)
        tk.Label(p, text=comentario, font=F_TEXTO, fg=C_SUB_FG, anchor="w").grid(
            row=row, column=2, sticky="ew", pady=2)

    def limpiar(self) -> None:
        for v in self.vars.values():
            v.set("")

    def ejemplo(self) -> None:
        if self.liga == "EUROLIGA":
            self.vars["q1"].set("50"); self.vars["q2"].set("39"); self.vars["linea"].set("167")
        else:
            self.vars["q1"].set("41"); self.vars["q2"].set("59"); self.vars["linea"].set("214")

    def fijar_modelo(self, modelo: Modelo) -> None:
        self.modelo = modelo
        self.lbl_titulo.config(text="EUROLIGA 2025-26" if modelo.liga == "EUROLIGA" else "NBA 2025-26")
        texto = modelo.titulo
        if modelo.n and modelo.fecha_min and modelo.fecha_max:
            texto += (f"  |  {fecha_excel_a_texto(modelo.fecha_min)} - "
                      f"{fecha_excel_a_texto(modelo.fecha_max)}")
        self.lbl_sub.config(text=texto, fg=C_SUB_FG if not modelo.errores else C_ROJO_FG)
        if modelo.errores:
            self.lbl_sub.config(text="  |  ".join(modelo.errores), fg=C_ROJO_FG)
        self._cargar_metricas()
        self.recalcular()

    def _cargar_metricas(self) -> None:
        m = self.modelo
        if m is None:
            return
        self.metricas["r2_dos"].config(text=f3(m.r2_dos))
        self.metricas["r2_uno"].config(text=f3(m.r2_uno))
        self.metricas["rmse_dos"].config(text=f1(m.rmse_test_dos))
        self.metricas["rmse_uno"].config(text=f1(m.rmse_test_uno))
        self.metricas["rmse_naive"].config(text=f1(m.rmse_test_naive))
        self.metricas["n"].config(text=str(m.n))
        self.metricas["media"].config(text=f3(m.media_total))
        self.metricas["basta"].config(text="NO: con el 2Q baja el error" if m.n else "")
        if m.n:
            self.lbl_basta_coment.config(text=f"El error baja un {m.mejora_error:.1f}% con el 2Q")

    def recalcular(self) -> None:
        if self.modelo is None:
            return
        res = predecir(self.modelo,
                       parsear(self.vars["q1"].get()),
                       parsear(self.vars["q2"].get()),
                       parsear(self.vars["linea"].get()))
        self.out_pred.config(text=f1(res.prediccion))
        self.out_solo.config(text=f1(res.solo_1q))
        self.out_naive.config(text=f1(res.naive))
        self.out_dif.config(text=f1(res.diferencia))
        self.lbl_exacto.config(text=f"valor exacto de PREDICCION TOTAL:  {exacto(res.prediccion)}")

        v = res.veredicto
        if v == "OVER":
            bg, fg = C_VERDICTO_OK, C_VERDE
        elif v == "UNDER":
            bg, fg = C_ROJO_BG, C_ROJO_FG
        else:
            bg, fg = C_HEAD_BG, "#000000"
        self.lbl_veredicto.config(text=v, bg=bg, fg=fg)
        self.lbl_prob.config(text="" if res.prob_over is None else f"{res.prob_over * 100:.1f} %")


# ===========================================================================
#  Pestaña EN VIVO — ping + flujo 1Q+2Q + predicción
# ===========================================================================
class PestanaVivo(ttk.Frame):
    TODAS = "⚽ TODAS LAS LIGAS"

    COLUMNAS = ("inicio", "liga", "partido", "estado", "periodo", "reloj", "q1",
                "q2", "total", "linea", "pred", "dif", "over", "under", "mercado")

    TITULOS = {
        "inicio": "Inicio", "liga": "Liga", "partido": "Partido", "estado": "Estado",
        "periodo": "Periodo", "reloj": "Reloj", "q1": "1Q", "q2": "2Q",
        "total": "Total", "linea": "Linea", "pred": "Pred.", "dif": "Δ",
        "over": "Over", "under": "Under", "mercado": "Mercado",
    }
    ANCHOS = {
        "inicio": 88, "liga": 150, "partido": 215, "estado": 82, "periodo": 88,
        "reloj": 60, "q1": 40, "q2": 40, "total": 48, "linea": 55, "pred": 55,
        "dif": 55, "over": 48, "under": 48, "mercado": 80,
    }

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=10)
        self.app = app
        self.deporte = api_betplay.DEPORTE_POR_DEFECTO
        self.partidos: list[api_betplay.Partido] = []
        self.ligas: list[api_betplay.Liga] = []
        self._recolectando = False
        self._ping_en_curso = False
        self._auto_activo = False
        self._job_auto = None
        self._partido_sel: api_betplay.Partido | None = None
        # --- apuesta automática en el descanso 2Q→3Q ---
        # Se recuerda entre sesiones: si la dejaste encendida, al abrir la app
        # ya está encendida (y arranca sola el sondeo, porque sin ping no hay
        # descanso que detectar). Primera ejecución: AUTO encendido.
        vivo = base_datos.cargar_config().get("vivo", {}) or {}
        self.var_auto_apostar = tk.BooleanVar(
            value=bool(vivo.get("auto_apostar", True)))
        self.var_umbral = tk.DoubleVar(
            value=float(vivo.get("umbral", UMBRAL_POR_DEFECTO)))
        self.var_ev_min = tk.DoubleVar(
            value=float(vivo.get("ev_min", EV_MINIMA_POR_DEFECTO)))
        self.var_auto = tk.BooleanVar(
            value=bool(vivo.get("auto", self.var_auto_apostar.get())))
        self.var_intervalo = tk.IntVar(
            value=int(vivo.get("intervalo", INTERVALO_POR_DEFECTO)))
        # filtro de la tabla: mostrar SOLO los partidos en el descanso 2Q→3Q
        self.var_solo_descanso = tk.BooleanVar(
            value=bool(vivo.get("filtro_descanso", False)))
        self._ventanas: dict[int, float] = {}      # evento_id -> marca al abrir
        self._apuestas_auto: set[int] = set()      # evento_id ya apostados
        # señales ya registradas en `señales` (una por evento; evita una
        # conexión por ping). Se marca tras el primer intento.
        self._senales_registradas: set[int] = set()
        self._transitos: dict[int, tuple[str, float]] = {}  # para medir descansos
        self._motivos_auto: Counter = Counter()    # por qué NO se apuesta
        self._log_ya: set[tuple[int, str]] = set()  # no repetir en el log
        # --- integridad de datos ---
        # histórico de líneas/cuotas reales en `cuotas_historial`
        self._cuotas_ultimas: dict[int, tuple] | None = None
        # reconciliación periódica con el resultado oficial (API-Sports)
        self._reconciliando = False
        self._ultima_reconciliacion = 0.0
        # partidos del último ping (para saber qué eventos ya no están)
        self._eventos_feed: set[int] = set()
        self._construir()
        # guardar al cambiar cualquier control (van ya montados)
        for _v in (self.var_auto, self.var_intervalo, self.var_auto_apostar,
                   self.var_umbral, self.var_ev_min, self.var_solo_descanso):
            _v.trace_add("write", lambda *_a: self._guardar_vivo())

    # ------------------------------------------------------------------ UI
    def _construir(self) -> None:
        # --- barra superior: liga + ping + automático ---
        # En pantallas estrechas se parte en dos filas; en anchas va todo
        # seguido en una (responsive, sin ocupar altura de más).
        self._top = tk.Frame(self)
        self._top.pack(fill="x", pady=(0, 6))
        self._top_a = tk.Frame(self._top)
        self._top_b = tk.Frame(self._top)

        lbl_liga = tk.Label(self._top_a, text="Liga:", font=F_LABEL)
        self.var_liga = tk.StringVar(value=self.TODAS)
        self.combo_liga = ttk.Combobox(self._top_a, textvariable=self.var_liga, width=32,
                                       state="readonly", values=(self.TODAS,))
        self.combo_liga.bind("<<ComboboxSelected>>", lambda _e: self.hacer_ping())

        self.btn_ping = tk.Button(self._top_a, text="⚡ PING AHORA", font=F_LABEL, width=16,
                                  bg=C_AZUL, fg="white", command=self.hacer_ping)

        self.chk_auto = tk.Checkbutton(self._top_a, text="Automatico cada",
                                       variable=self.var_auto,
                                       font=F_TEXTO, command=self.toggle_auto)
        spin = tk.Spinbox(self._top_a, from_=2, to=60, textvariable=self.var_intervalo,
                          width=4, font=F_TEXTO)
        lbl_seg = tk.Label(self._top_a, text="seg", font=F_TEXTO)

        # filtro: ver SOLO los partidos en el descanso 2Q→3Q (listos para
        # apostar: 1Q y 2Q finales, ventana abierta). El número entre
        # paréntesis es cuántos hay ahora mismo en el descanso.
        self.chk_solo_descanso = tk.Checkbutton(
            self._top_a, text="⏸ Solo descanso 2Q→3Q",
            variable=self.var_solo_descanso, font=F_TEXTO,
            command=self._poblar_tabla)

        self.lbl_ultima = tk.Label(self._top_b, text="aún sin consultar",
                                   font=F_PEQUE, fg=C_SUB_FG)
        self.lbl_ping_estado = tk.Label(self._top_b, text="", font=F_PEQUE, fg=C_SUB_FG)

        self._controles = [lbl_liga, self.combo_liga, self.btn_ping,
                           self.chk_auto, spin, lbl_seg, self.chk_solo_descanso]
        self._estado = [self.lbl_ultima, self.lbl_ping_estado]
        self._top_modo = None
        self._top.bind("<Configure>", self._repack_top)
        self._repack_top()

        # --- oportunidad destacada (solo en modo "todas las ligas") ---
        self.lbl_oportunidad = tk.Label(
            self, text="Cargando lista de ligas…", font=F_LABEL, anchor="w",
            bg=C_AZUL_CLARO, fg=C_AZUL, padx=8, pady=4)
        self.lbl_oportunidad.pack(fill="x", pady=(0, 6))

        # --- tabla de partidos ---
        marco_tabla = tk.Frame(self)
        marco_tabla.pack(fill="both", expand=True, pady=(0, 6))

        self.tree = ttk.Treeview(marco_tabla, columns=self.COLUMNAS, show="headings", height=7)
        for col in self.COLUMNAS:
            self.tree.heading(col, text=self.TITULOS[col])
            self.tree.column(col, width=self.ANCHOS[col],
                             anchor="w" if col in ("liga", "partido") else "center")
        vsb = ttk.Scrollbar(marco_tabla, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._al_seleccionar)

        estilo = ttk.Style()
        try:
            estilo.configure("Treeview", rowheight=22, font=("Calibri", 10))
            estilo.configure("Treeview.Heading", font=("Calibri", 10, "bold"))
        except tk.TclError:
            pass
        self.tree.tag_configure("oportunidad", background=C_AZUL_CLARO)

        # --- flujo de predicción ---
        flujo = tk.LabelFrame(self, text=" FLUJO: activar predicción con 1Q + 2Q ",
                              font=F_LABEL, padx=8, pady=6)
        flujo.pack(fill="x", pady=(0, 6))
        flujo.columnconfigure(1, weight=1)
        flujo.columnconfigure(3, weight=1)

        self.lbl_partido_sel = tk.Label(flujo, text="Selecciona un partido de la tabla",
                                        font=F_LABEL, anchor="w")
        self.lbl_partido_sel.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        tk.Label(flujo, text="Modelo:", font=F_LABEL).grid(row=1, column=0, sticky="w")
        self.var_modelo = tk.StringVar(value="AUTO")
        self.combo_modelo = ttk.Combobox(flujo, textvariable=self.var_modelo,
                                         values=("AUTO", "EUROLIGA", "NBA"),
                                         width=22, state="readonly")
        self.combo_modelo.grid(row=1, column=1, sticky="w", padx=(4, 12))
        self.combo_modelo.bind("<<ComboboxSelected>>",
                               lambda _e: self.calcular_flujo())

        self.lbl_modelo_info = tk.Label(flujo, text="", font=F_PEQUE, fg=C_SUB_FG,
                                        anchor="w")
        self.lbl_modelo_info.grid(row=2, column=0, columnspan=4, sticky="w",
                                  pady=(0, 4))

        tk.Label(flujo, text="1Q:", font=F_LABEL).grid(row=1, column=2, sticky="e")
        self.lbl_q1 = tk.Label(flujo, text="—", font=F_INPUT, width=8, bg=C_INPUT_BG,
                               relief="solid", bd=1)
        self.lbl_q1.grid(row=1, column=3, sticky="w", padx=(4, 12))

        tk.Label(flujo, text="2Q:", font=F_LABEL).grid(row=2, column=0, sticky="w")
        self.lbl_q2 = tk.Label(flujo, text="—", font=F_INPUT, width=8, bg=C_INPUT_BG,
                               relief="solid", bd=1)
        self.lbl_q2.grid(row=2, column=1, sticky="w", padx=(4, 12), pady=2)

        tk.Label(flujo, text="Linea:", font=F_LABEL).grid(row=2, column=2, sticky="e")
        self.lbl_linea = tk.Label(flujo, text="—", font=F_INPUT, width=8, bg=C_INPUT_BG,
                                  relief="solid", bd=1)
        self.lbl_linea.grid(row=2, column=3, sticky="w", padx=(4, 12), pady=2)

        tk.Label(flujo, text="Prediccion:", font=F_LABEL).grid(row=3, column=0, sticky="w")
        self.lbl_pred = tk.Label(flujo, text="—", font=F_PRED, width=10, bg=C_PRED_BG,
                                 fg=C_VERDE, relief="solid", bd=1)
        self.lbl_pred.grid(row=3, column=1, sticky="w", padx=(4, 12), pady=2)

        tk.Label(flujo, text="Menos linea:", font=F_LABEL).grid(row=3, column=2, sticky="e")
        self.lbl_dif = tk.Label(flujo, text="—", font=F_INPUT, width=8)
        self.lbl_dif.grid(row=3, column=3, sticky="w", padx=(4, 12), pady=2)

        tk.Label(flujo, text="VEREDICTO:", font=F_LABEL).grid(row=4, column=0, sticky="w")
        self.lbl_veredicto = tk.Label(flujo, text="—", font=F_VEREDICTO, width=10,
                                      bg=C_HEAD_BG, relief="solid", bd=1)
        self.lbl_veredicto.grid(row=4, column=1, sticky="w", padx=(4, 12), pady=2)

        tk.Label(flujo, text="Prob. OVER:", font=F_LABEL).grid(row=4, column=2, sticky="e")
        self.lbl_prob = tk.Label(flujo, text="—", font=F_INPUT, width=8)
        self.lbl_prob.grid(row=4, column=3, sticky="w", padx=(4, 12), pady=2)

        self.lbl_aviso = tk.Label(
            flujo,
            text="Elige un partido de la tabla para ver el modelo que se usará.",
            font=F_PEQUE, fg=C_SUB_FG, anchor="w")
        self.lbl_aviso.grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- botones de apuesta ---
        botones = tk.Frame(self)
        botones.pack(fill="x", pady=(0, 4))

        tk.Label(botones, text="Stake:", font=F_LABEL).pack(side="left")
        self.var_stake = tk.StringVar(value=str(STAKE_POR_DEFECTO))
        tk.Entry(botones, textvariable=self.var_stake, font=F_INPUT, width=10,
                 justify="center", bg=C_INPUT_BG).pack(side="left", padx=(4, 12))

        self.btn_apostar = tk.Button(botones, text="💰 APOSTAR (paperbet)", font=F_LABEL,
                                     bg=C_VERDICTO_OK, fg=C_VERDE, width=22,
                                     state="disabled", command=self.apostar)
        self.btn_apostar.pack(side="left", padx=(0, 8))

        tk.Button(botones, text="✔ Resolver pendientes", font=F_TEXTO, width=20,
                  command=self.resolver_pendientes).pack(side="left", padx=(0, 8))

        self.lbl_flujo_estado = tk.Label(botones, text="", font=F_PEQUE, fg=C_SUB_FG)
        self.lbl_flujo_estado.pack(side="left")

        # --- apuesta AUTOMÁTICA en el descanso 2Q→3Q ---
        auto = tk.Frame(self)
        auto.pack(fill="x", pady=(0, 4))

        self.chk_auto_apostar = tk.Checkbutton(
            auto, text="AUTO: apostar en el descanso (2Q→3Q) con Δ ≥",
            variable=self.var_auto_apostar, font=F_LABEL,
            command=self.toggle_auto_apostar)
        self.chk_auto_apostar.pack(side="left")

        tk.Spinbox(auto, from_=0.5, to=20.0, increment=0.5, width=5,
                   textvariable=self.var_umbral, font=F_TEXTO).pack(
            side="left", padx=(2, 4))
        tk.Label(auto, text="pts   y EV ≥", font=F_TEXTO).pack(side="left")
        tk.Spinbox(auto, from_=-20.0, to=60.0, increment=0.5, width=6,
                   textvariable=self.var_ev_min, font=F_TEXTO).pack(
            side="left", padx=(2, 4))
        tk.Label(auto, text="%", font=F_TEXTO).pack(side="left", padx=(0, 10))

        self.lbl_auto_estado = tk.Label(
            auto, text="AUTO apagado", font=F_PEQUE, fg=C_SUB_FG)
        self.lbl_auto_estado.pack(side="left")

        # el motivo real por el que no se apuesta (antes era invisible)
        self.lbl_auto_motivos = tk.Label(
            self, text="", font=F_PEQUE, fg=C_SUB_FG, anchor="w", justify="left")
        self.lbl_auto_motivos.pack(fill="x", pady=(0, 2))

    def _repack_top(self, _evento=None) -> None:
        """Una sola fila si cabe todo; si no, se reparte en dos (responsive)."""
        ancho = self._top.winfo_width() or self._top.winfo_reqwidth()
        a = sum(w.winfo_reqwidth() + 10 for w in self._controles)
        b = sum(w.winfo_reqwidth() + 10 for w in self._estado)
        modo = "una" if a + b <= ancho else "dos"
        if modo == self._top_modo:
            return          # ya está así: evita bucle de repintados
        self._top_modo = modo

        self._top_a.pack_forget()
        self._top_b.pack_forget()
        for w in self._controles + self._estado:
            w.pack_forget()

        if modo == "una":
            self._top_a.pack(side="left", fill="x")
            self._top_b.pack(side="left")
        else:
            self._top_a.pack(fill="x")
            self._top_b.pack(fill="x")
        for w in self._controles:
            w.pack(side="left", padx=(0, 6))
        for w in self._estado:
            w.pack(side="left", padx=(0, 12))

    # ------------------------------------------------------------- acciones
    def hacer_ping(self) -> None:
        if self._ping_en_curso:
            return
        self._ping_en_curso = True
        self.btn_ping.config(state="disabled", text="⏳ consultando...")
        self.lbl_ping_estado.config(text="")

        liga = self._liga_sel()

        def consulta():
            if liga is None:
                # TODAS las ligas: una sola petición para el deporte entero
                return api_betplay.obtener_partidos_deporte(self.deporte)
            return api_betplay.obtener_partidos(liga=liga)

        def al_terminar(res):
            if isinstance(res, api_betplay.ErrorAPI):
                self._ping_terminado([], str(res))
            elif isinstance(res, Exception):
                self._ping_terminado([], f"Error inesperado: {res}")
            else:
                self._ping_terminado(res, None)

        self.app.en_hilo(consulta, al_terminar)

    def _liga_sel(self) -> "api_betplay.Liga | None":
        """Liga elegida en el desplegable, o None si es 'todas las ligas'."""
        if self.var_liga.get() == self.TODAS:
            return None
        nombre = self.var_liga.get()
        for l in self.ligas:
            if l.nombre == nombre:
                return l
        return None

    def cargar_ligas(self) -> None:
        """Descubre las ligas del deporte y rellena el desplegable."""
        def al_terminar(res):
            if isinstance(res, Exception):
                self.lbl_oportunidad.config(
                    text=f"No se pudo leer la lista de ligas: {res}", fg=C_ROJO_FG)
                return
            self.ligas = res
            nombres = [self.TODAS] + [l.nombre for l in res]
            self.combo_liga.config(values=nombres)
            self.lbl_oportunidad.config(
                text=f"{len(res)} ligas de baloncesto disponibles. "
                     f"'Todas las ligas' barre el deporte entero en una sola petición.",
                fg=C_AZUL)

        self.app.en_hilo(
            lambda: api_betplay.obtener_ligas(self.deporte), al_terminar)

    def actualizar_modelos_liga(self) -> None:
        """Refresca el desplegable del modelo con las ligas ya entrenadas."""
        nombres = ("AUTO", "EUROLIGA", "NBA") + tuple(
            sorted(self.app.modelos_ligas))
        self.combo_modelo.config(values=nombres)
        if self.var_modelo.get() not in nombres:
            self.var_modelo.set("AUTO")

    def _modelo_de(self, p):
        """(modelo, etiqueta) que corresponde a un partido."""
        return self.app.modelo_para_partido(p, self.var_modelo.get())

    def _prediccion_de(self, p) -> "motor.Resultado | None":
        """Predicción con el modelo de la liga del partido, o None."""
        modelo, _etiqueta = self._modelo_de(p)
        if modelo is None or not modelo.n or p.q1 is None or not p.linea:
            return None
        try:
            res = motor.predecir(modelo, float(p.q1),
                                 float(p.q2) if p.q2_completo else None,
                                 float(p.linea))
        except Exception:  # noqa: BLE001
            return None
        return res if res.veredicto in ("OVER", "UNDER") else None

    def _ping_terminado(self, partidos, error) -> None:
        self._ping_en_curso = False
        self.btn_ping.config(state="normal", text="⚡ PING AHORA")
        if error:
            self.lbl_ping_estado.config(text=error, fg=C_ROJO_FG)
            return
        self.partidos = partidos
        self.lbl_ultima.config(text=f"última consulta: {ahora_texto()}")
        vivos = sum(1 for p in partidos if p.es_vivo)
        liga_txt = "todas las ligas" if self._liga_sel() is None else self.var_liga.get()
        self.lbl_ping_estado.config(
            text=f"{len(partidos)} partidos · {vivos} en vivo · {liga_txt}", fg=C_SUB_FG)
        self._poblar_tabla()
        # histórico de líneas/cuotas reales (base para backtests honestos)
        self._eventos_feed = {p.evento_id for p in partidos}
        self._registrar_cuotas(partidos)
        # guardar los partidos acabados con sus cuartos (muestras de entrenamiento)
        self.recolectar_en_hilo(partidos)
        # resolver automáticamente las apuestas pendientes de partidos terminados
        resueltas = self._resolver_automaticas(partidos)
        if resueltas:
            self.lbl_ping_estado.config(
                text=f"{len(partidos)} partidos · {vivos} en vivo · "
                     f"{resueltas} apuesta(s) resuelta(s)", fg=C_VERDE)
            self.app.refrescar_apuestas()
        # apuesta automática en el descanso 2Q→3Q (si está activada)
        self._auto_apostar_en_descanso(partidos)
        # reconciliación oficial cada ~10 min: cierra las colgadas (evento
        # desaparecido del feed) y corrige totales con el resultado real.
        if time.time() - self._ultima_reconciliacion > 600:
            self._ultima_reconciliacion = time.time()
            self._reconciliar_en_hilo()
        # reseleccionar el partido que estábamos mirando
        if self._partido_sel is not None:
            for p in partidos:
                if p.evento_id == self._partido_sel.evento_id:
                    self._partido_sel = p
                    break
            self.calcular_flujo()

    @staticmethod
    def grupo_fila(p, res) -> int:
        """
        Grupo de ordenación de una fila:

        0 -> señal COMPLETA (1Q y 2Q finales) y con línea todavía comparable
             con la predicción: es el grupo del que salen las oportunidades
        1 -> señal parcial (solo el 1Q está acabado) o ya en 4º cuarto, donde
             la línea de la casa se ha reajustado: se listan, pero no mandan
        2 -> sin predicción todavía
        """
        if res is None:
            return 2
        if p.en_ultimo_periodo or not p.q2_completo:
            return 1
        return 0

    def _poblar_tabla(self) -> None:
        seleccion = self.tree.selection()
        self.tree.delete(*self.tree.get_children())

        solo = bool(self.var_solo_descanso.get())
        en_break = sum(1 for p in self.partidos if p.en_descanso_2q)
        try:
            self.chk_solo_descanso.config(
                text=f"⏸ Solo descanso 2Q→3Q ({en_break})")
        except tk.TclError:
            pass

        filas = []
        for p in self.partidos:
            if solo and not p.en_descanso_2q:
                continue        # filtro: solo los que están en el descanso

            res = self._prediccion_de(p)

            if p.finalizado:
                estado = "Terminado"
            elif p.es_pretemporada:
                estado = "Por jugar"
            elif p.es_vivo:
                estado = "EN VIVO"
            else:
                estado = "Terminado"

            reloj = f"{p.reloj_min}:{p.reloj_seg:02d}" if p.es_vivo else ""
            if p.en_descanso_2q:
                marca = self._ventanas.get(p.evento_id)
                restantes = (descansos.duracion_descanso(p.clave_liga)
                             - (descansos.minutos_desde(marca) or 0.0))
                try:
                    listo, _motivo = descansos.puede_apostar(p, p.clave_liga,
                                                             marca)
                except Exception:  # noqa: BLE001
                    listo = False
                periodo = (("DESCANSO 2Q" if restantes <= 0
                            else f"DESCANSO 2Q ({restantes:.0f}′)")
                           + (" · LISTO" if listo else ""))
            else:
                periodo = p.periodo or "—"
            if not p.mercado_abierto:
                mercado = "SUSPENDIDO"
            elif not p.cuota_over_abierta and not p.cuota_under_abierta:
                mercado = "CERRADO"
            else:
                mercado = "ABIERTO"

            valores = (
                inicio_texto(p.inicio) or "—",
                p.liga_nombre or "—",
                p.nombre, estado, periodo, reloj,
                p.q1 if p.q1 is not None else "—",
                p.q2 if p.q2 is not None else "—",
                p.total_parcial if p.total_parcial is not None else "—",
                f1(p.linea) if p.linea else "—",
                f1(res.prediccion) if res else "—",
                f"{res.diferencia:+.1f}" if res else "—",
                f1(p.cuota_over) if p.cuota_over else "—",
                f1(p.cuota_under) if p.cuota_under else "—",
                mercado,
            )
            # orden: (0) predecibles con línea todavía comparable, por |Δ|
            # descendente; (1) los que ya van en 4º cuarto; (2) el resto
            grupo = self.grupo_fila(p, res)
            clave_orden = (grupo, -abs(res.diferencia) if res else 0.0)
            tag = "oportunidad" if (grupo == 0 and abs(res.diferencia) >= 5) else "normal"
            filas.append((clave_orden, str(p.evento_id), valores, tag, res, p))

        filas.sort(key=lambda f: f[0])
        for _orden, iid, valores, tag, _res, _p in filas:
            self.tree.insert("", "end", iid=iid, values=valores, tags=(tag,))

        if seleccion:
            try:
                self.tree.selection_set(seleccion[0])
            except tk.TclError:
                pass

        self._mostrar_mejor_oportunidad(filas)

    def recolectar_en_hilo(self, partidos) -> None:
        """
        Guarda en segundo plano los partidos acabados con sus cuartos, y si ha
        llegado material nuevo reentrena los modelos de las ligas afectadas.

        Así las ligas sin histórico oficial (ACB, BBL, Portugal, Chile...)
        van entrenando su modelo solas mientras la app está abierta.
        """
        if self._recolectando or not self.app.db_ok:
            return
        self._recolectando = True

        def tarea():
            import modelos as mod_modelos
            nuevas = self.app.recolectar_resultados(partidos)
            if not nuevas:
                return 0
            mod_modelos.entrenar_todo()
            return nuevas

        def al_terminar(res):
            self._recolectando = False
            if isinstance(res, Exception) or not res:
                return
            import modelos as mod_modelos
            self.app.modelos_ligas = mod_modelos.cargar_modelos()
            self.actualizar_modelos_liga()
            self.lbl_ping_estado.config(
                text=(self.lbl_ping_estado.cget("text") or "") +
                     f" · +{res} muestras", fg=C_VERDE)

        self.app.en_hilo(tarea, al_terminar)

    def _mostrar_mejor_oportunidad(self, filas) -> None:
        """Etiqueta destacada con el partido de mayor diferencia."""
        if not filas:
            self.lbl_oportunidad.config(text="Sin partidos.", fg=C_SUB_FG)
            return
        con_datos = [f for f in filas if f[4] is not None]
        if not con_datos:
            vivos = sum(1 for p in self.partidos if p.es_vivo)
            self.lbl_oportunidad.config(
                text=f"{len(self.partidos)} partidos · {vivos} en vivo. "
                     f"Ninguno tiene todavía 1Q + 2Q completos con línea.",
                fg=C_SUB_FG)
            return
        comparables = [f for f in con_datos if f[0][0] == 0]
        pie = ""
        if not comparables:
            # no hay ninguna señal completa: se enseña la mejor parcial,
            # pero advirtiendo de por qué no es comparable
            comparables = con_datos
            pie = (" (solo con 1Q: el 2Q aún no ha terminado)"
                   if not comparables[0][5].q2_completo else
                   " (4º cuarto: la línea ya se ha reajustado)")
        _orden, _iid, _v, _tag, res, p = comparables[0]
        self.lbl_oportunidad.config(
            text=(f"Mayor diferencia: {p.liga_nombre} · {p.nombre} — "
                  f"1Q {p.q1} + 2Q {p.q2}, predicción {f1(res.prediccion)} vs "
                  f"línea {f1(p.linea)} → {res.veredicto} {res.diferencia:+.1f} pts "
                  f"(P(over) {res.prob_over * 100:.0f}%){pie}"),
            fg=C_VERDE if res.veredicto == "OVER" else C_ROJO_FG)

    def _al_seleccionar(self, _evento=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        try:
            evento_id = int(sel[0])
        except ValueError:
            return
        for p in self.partidos:
            if p.evento_id == evento_id:
                self._partido_sel = p
                break
        self.calcular_flujo()

    def toggle_auto(self) -> None:
        # Cancelar antes cualquier timer: si no, llamar dos veces deja dos
        # bucles de sondeo corriendo en paralelo.
        if self._job_auto:
            try:
                self.after_cancel(self._job_auto)
            except Exception:   # noqa: BLE001
                pass
            self._job_auto = None
        if self.var_auto.get() and not getattr(self.app, "_cerrando", False):
            self._auto_activo = True
            self._programar_auto()
        else:
            self._auto_activo = False

    def _programar_auto(self) -> None:
        if not self._auto_activo or getattr(self.app, "_cerrando", False):
            return
        self.hacer_ping()
        try:
            seg = max(2, int(self.var_intervalo.get()))
        except (ValueError, tk.TclError):
            seg = INTERVALO_POR_DEFECTO
        self._job_auto = self.after(seg * 1000, self._programar_auto)

    # -------------------------------------------------- apuesta automática
    def _guardar_vivo(self) -> None:
        """Recuerda la configuración de la pestaña EN VIVO en config.json."""
        try:
            intervalo = max(2, int(self.var_intervalo.get()))
        except (ValueError, tk.TclError):
            intervalo = INTERVALO_POR_DEFECTO
        try:
            umbral = max(UMBRAL_MINIMO, float(self.var_umbral.get()))
        except (ValueError, tk.TclError):
            umbral = UMBRAL_POR_DEFECTO
        try:
            ev_min = max(EV_MINIMA, float(self.var_ev_min.get()))
        except (ValueError, tk.TclError):
            ev_min = EV_MINIMA_POR_DEFECTO
        try:
            cfg = base_datos.cargar_config()
            cfg["vivo"] = {
                "auto": bool(self.var_auto.get()),
                "intervalo": intervalo,
                "auto_apostar": bool(self.var_auto_apostar.get()),
                "umbral": umbral,
                "ev_min": ev_min,
                "filtro_descanso": bool(self.var_solo_descanso.get()),
            }
            base_datos.guardar_config(cfg)
        except Exception:   # noqa: BLE001  (guardar la preferencia no puede romper la app)
            pass

    def arrancar_automatico(self) -> None:
        """
        Arranca sola al abrir la app: apuesta en el descanso + vista cada 5 s.

        No depende de nada que tengas que marcar: apenas se abre la aplicación
        ya está mirando los descansos de todas las ligas y refrescando la tabla
        cada 5 segundos. Solo puedes apagarlo tú a mano.
        """
        self.var_intervalo.set(5)
        self.var_auto_apostar.set(True)
        # pisos de seguridad: Δ y EV por debajo de esto son apuestas de ruido
        # (el margen de la casa ~8-10 % se come cualquier ventaja mínima).
        if self.var_umbral.get() < UMBRAL_MINIMO:
            self.var_umbral.set(UMBRAL_MINIMO)
        if self.var_ev_min.get() < EV_MINIMA:
            self.var_ev_min.set(EV_MINIMA)
        self.toggle_auto_apostar()      # pone el texto, el aviso y el sondeo

    def toggle_auto_apostar(self) -> None:
        """Enciende/apaga la apuesta automática en el descanso 2Q→3Q."""
        if self.var_auto_apostar.get():
            # sin sondeo no hay descanso que detectar: se enciende también.
            # Se mira _auto_activo, no solo la casilla: si la casilla ya
            # venía marcada de la config pero el bucle no corre, hay que
            # arrancarlo igualmente.
            if not self.var_auto.get() or not self._auto_activo:
                self.var_auto.set(True)
                self.toggle_auto()
            self.lbl_auto_estado.config(
                text="AUTO: vigilo el descanso de todas las ligas…", fg=C_AZUL)
            self.lbl_oportunidad.config(
                text="AUTO activo: se apuesta SOLO en el descanso 2Q→3Q "
                     "(1Q y 2Q finales) cuando |Δ| ≥ el umbral y el mercado "
                     "está abierto. La apuesta se guarda sola en APUESTAS y "
                     "se resuelve al terminar el partido.",
                fg=C_AZUL)
        else:
            self._ventanas.clear()
            self.lbl_auto_estado.config(text="AUTO apagado", fg=C_SUB_FG)

    def _auto_apostar_en_descanso(self, partidos) -> None:
        """
        Regla automática, llamada tras cada ping:

          1. Detecta los partidos en el hueco 2Q→3Q (descansos.py).
          2. Abre la ventana del partido (con su duración por liga) la
             primera vez que lo ve en descanso.
          3. Si hay modelo de la liga, |Δ| ≥ umbral, mercado abierto y la
             ventana no ha caducado → apuesta sola (paperbet) y la registra
             en el panel de APUESTAS.
          4. Mide la duración real del descanso para autocalibrar la tabla.

        Nunca repite la apuesta de un mismo partido (control por evento_id).

        AUDITORÍA (ver `auditoria_auto.py`): aquí se comprueban TRES cosas que
        antes se ignoraban, porque podían hacer apostar donde la casa no lo
        tenía disponible:
          1. el lado concreto que se quiere jugar (`hay_cuota_over/under`),
             no solo la bandera global `mercado_abierto`;
          2. la cuota se compara con la probabilidad del modelo → EV ≥ mínimo
             (con el ~8% de margen de la casa, un Δ pequeño puede ser -EV);
          3. el motivo real de NO apostar se cuenta y se enseña en el panel
             (antes todos los `continue` eran silenciosos).
        """
        if not self.var_auto_apostar.get():
            return
        try:
            umbral = max(UMBRAL_MINIMO, float(self.var_umbral.get()))
        except (ValueError, tk.TclError):
            umbral = UMBRAL_POR_DEFECTO
        try:
            ev_min = max(EV_MINIMA, float(self.var_ev_min.get())) / 100.0   # el control va en %
        except (ValueError, tk.TclError):
            ev_min = EV_MINIMA_POR_DEFECTO / 100.0
        # Política (config.json -> "auto"): las ligas con modelo de RESPALDO
        # apostaron 0/3 hoy (modelo de otra liga); por defecto no se usan y
        # `ligas_validadas` (si tiene elementos) restringe a la lista blanca.
        try:
            cfg_auto = base_datos.cargar_config().get("auto", {}) or {}
        except Exception:   # noqa: BLE001 (leer la política no rompe el bot)
            cfg_auto = {}
        solo_propio = bool(cfg_auto.get("solo_modelo_propio", True))
        ligas_validadas = cfg_auto.get("ligas_validadas") or []
        # Si >0: solo se apuesta dentro de los primeros N segundos del descanso
        # (capturar la línea "congelada" antes de que la casa la re-afine).
        try:
            max_retraso = int(cfg_auto.get("max_retraso_seg", 0) or 0)
        except (TypeError, ValueError):
            max_retraso = 0

        en_descanso = 0
        apostadas = 0
        motivos: Counter = Counter()
        ahora = time.time()

        def rechazar(p, motivo: str) -> None:
            """Cuenta el motivo y lo deja escrito en el log del bot.

            El log es CLAVE: la interfaz solo puede enseñar el motivo mientras
            el partido sigue en el descanso, y al pasar al 3Q se pierde. Aquí
            queda para siempre, y sin repetir la misma línea 20 veces por los
            pings del descanso.
            """
            motivos[motivo] += 1
            k = (p.evento_id, motivo)
            if k not in self._log_ya:
                self._log_ya.add(k)
                log_auto(f"[{p.clave_liga}] {p.nombre[:44]} · NO apuesta: "
                         f"{motivo}")

        for p in partidos:
            if p.finalizado:
                continue
            # --- medir descansos reales para la autocalibración ---
            self._vigilar_transito(p, ahora)

            if not descansos.en_ventana_descanso(p):
                self._ventanas.pop(p.evento_id, None)
                continue
            en_descanso += 1
            if p.evento_id not in self._ventanas:
                self._ventanas[p.evento_id] = ahora
                log_auto(f"[{p.clave_liga}] {p.nombre[:44]} · entra en descanso "
                         f"2Q→3Q · 1Q={p.q1} 2Q={p.q2} línea={p.linea} "
                         f"O={p.cuota_over} U={p.cuota_under} "
                         f"mercado={'abierto' if p.mercado_abierto else 'cerrado'}")
            marca = self._ventanas[p.evento_id]

            # --- modelo y predicción (se calculan ANTES de los filtros de
            # mercado/caducidad para poder REGISTRAR LA SEÑAL aunque la casa
            # tenga el mercado suspendido o falte la línea; la decisión de
            # apostar NO cambia: esos filtros se siguen aplicando después). --
            # MISMO modelo que muestra la interfaz (`_modelo_de`): el de la
            # propia liga o, si esa liga aún no tiene suficientes partidos,
            # el del Excel (NBA/EUROLIGA). Antes se miraba solo
            # `modelos_ligas[clave]` y el bot se negaba a apostar lo que la
            # propia pantalla estaba enseñando como VEREDICTO (p. ej.
            # nba_preseason, que cae al modelo de la NBA con n=1380).
            modelo, etiqueta = self.app.modelo_para_partido(p, "AUTO")
            if modelo is None or not modelo.n:
                rechazar(p, "sin modelo usable (ni de la liga ni del Excel)")
                continue
            es_fallback = "sin datos de" in etiqueta

            try:
                res = predecir(modelo, float(p.q1),
                               None if not p.q2_completo else float(p.q2),
                               float(p.linea))
            except Exception as exc:        # noqa: BLE001
                rechazar(p, f"el modelo no pudo predecir ({exc})")
                continue

            # Retraso desde la primera vez que vimos el descanso (o desde el
            # reinicio de la app).
            retraso = int(max(0.0, ahora - marca))

            # --- REGISTRO DE SEÑAL (todas las del descanso con modelo,
            # apostadas o no, incluidas las SIN dirección clara y las que
            # están sin línea o con mercado suspendido). El set en memoria
            # evita una conexión por ping; se marca tras el primer intento,
            # tenga éxito o no. Silencioso por diseño: un fallo de registro
            # JAMÁS interrumpe al bot. ---
            if p.evento_id not in self._senales_registradas:
                self._senales_registradas.add(p.evento_id)
                try:
                    base_datos.guardar_senal(
                        p, res, retraso,
                        "respaldo" if es_fallback else "propio")
                except Exception:   # noqa: BLE001
                    pass

            # --- filtros de la lógica de apuesta (mismo orden de siempre) ---
            ok, motivo = descansos.puede_apostar(p, p.clave_liga, marca)
            if not ok:
                rechazar(p, motivo)
                continue
            if max_retraso and retraso > max_retraso:
                rechazar(p, f"descanso ya avanzado "
                            f"(+{retraso}s > {max_retraso}s)")
                continue
            if p.evento_id in self._apuestas_auto:
                continue            # ya apostado en este partido (memoria)
            # Candado PERSISTENTE: sobrevive a reinicios de la app. El set de
            # memoria se pierde al reiniciar y el bot repetía la apuesta si el
            # partido seguía en su descanso de ~15 min (duplicados de hoy).
            try:
                ya_guardada = base_datos.existe_apuesta(p.evento_id)
            except base_datos.ErrorBaseDatos:
                ya_guardada = False
            if ya_guardada:
                self._apuestas_auto.add(p.evento_id)
                rechazar(p, "ya hay una apuesta guardada de este partido")
                continue
            if es_fallback and solo_propio:
                rechazar(p, "modelo de respaldo (modo solo modelo propio)")
                continue
            if ligas_validadas and (p.clave_liga or "") not in ligas_validadas:
                rechazar(p, f"liga fuera de las validadas ({p.clave_liga})")
                continue
            if res.veredicto not in ("OVER", "UNDER"):
                rechazar(p, "veredicto sin decidir")
                continue
            if abs(res.diferencia) < umbral:
                rechazar(p, f"|Δ| {abs(res.diferencia):.1f} < {umbral:.1f}")
                continue

            # --- ¿la casa tiene ABIERTO el lado que queremos jugar? ---
            lado = res.veredicto
            cuota = p.cuota_over if lado == "OVER" else p.cuota_under
            disponible = p.hay_cuota_over if lado == "OVER" else p.hay_cuota_under
            if not cuota or cuota <= 1.0:
                rechazar(p, f"la casa no publica cuota del {lado}")
                continue
            if not disponible:
                rechazar(p, f"{lado} SUSPENDIDO por la casa")
                continue

            # --- ¿es rentable con esa cuota? (EV = P*cuota - 1) ---
            prob_lado = res.prob_over if lado == "OVER" else (
                None if res.prob_over is None else 1 - res.prob_over)
            if prob_lado is None:
                prob_lado = 0.5
            esperado = prob_lado * cuota - 1.0
            if esperado < ev_min:
                rechazar(p, f"EV {esperado * 100:+.1f}% < {ev_min * 100:.1f}%")
                continue

            try:
                stake = float(self.var_stake.get())
            except (ValueError, tk.TclError):
                stake = float(STAKE_POR_DEFECTO)
            if stake <= 0:
                rechazar(p, "stake a 0")
                continue

            apuesta = Apuesta(
                evento_id=p.evento_id,
                partido=p.nombre,
                liga_apuesta=p.liga_nombre or "—",
                liga_modelo=modelo.liga,
                mercado="TOTAL",
                seleccion=lado,
                linea=float(p.linea),
                cuota=float(cuota),
                stake=stake,
                prediccion=res.prediccion,
                diferencia=res.diferencia,
                probabilidad=res.prob_over,
                estado=paperbet.ESTADO_PENDIENTE,
                nota=(f"AUTO descanso2Q ({p.clave_liga}): "
                      f"1Q={p.q1:.0f} 2Q={p.q2:.0f} Δ={res.diferencia:+.1f} "
                      f"EV={esperado * 100:+.1f}% · +{retraso}s"
                      # deja constancia de si se usó el modelo de otra liga
                      + (f" · ⚠ modelo {etiqueta}" if es_fallback else "")),
            )
            error = paperbet.validar(apuesta)
            if error:
                rechazar(p, f"apuesta rechazada: {error}")
                continue
            try:
                id_apuesta = base_datos.guardar_apuesta(apuesta)
            except base_datos.ErrorBaseDatos as exc:
                log_auto(f"[{p.clave_liga}] ERROR al guardar: {exc}")
                self.lbl_auto_estado.config(
                    text=f"AUTO: error al guardar ({exc})", fg=C_ROJO_FG)
                return
            # enlaza la señal registrada con la apuesta tomada (si falla, no pasa nada)
            base_datos.vincular_apuesta(p.evento_id, id_apuesta)
            self._apuestas_auto.add(p.evento_id)
            apostadas += 1
            log_auto(f"[{p.clave_liga}] {p.nombre[:44]} · APUESTA id={id_apuesta} "
                     f"{lado} @ {cuota:.2f} línea={p.linea} stake={stake:.0f} "
                     f"Δ={res.diferencia:+.1f} EV={esperado * 100:+.1f}% "
                     f"modelo={modelo.liga} (n={modelo.n})"
                     + ("  ⚠RESPALDO" if es_fallback else ""))
            self.app.refrescar_apuestas()

        self._motivos_auto = motivos
        self._mostrar_motivos()
        if apostadas:
            self.lbl_auto_estado.config(
                text=f"AUTO: {apostadas} apuesta(s) en el descanso · "
                     f"{len(self._apuestas_auto)} en total",
                fg=C_VERDE)
        elif en_descanso:
            top = motivos.most_common(1)[0][0] if motivos else ""
            self.lbl_auto_estado.config(
                text=(f"AUTO: {en_descanso} en descanso · "
                      f"no aposté porque {top}" if top else
                      f"AUTO: {en_descanso} en descanso, esperando…"),
                fg=C_AZUL)

    def _mostrar_motivos(self) -> None:
        """Enseña por qué NO se ha apostado (antes era invisible)."""
        if not self._motivos_auto:
            self.lbl_auto_motivos.config(text="")
            return
        partes = [f"{n}× {m}" for m, n in self._motivos_auto.most_common(3)]
        self.lbl_auto_motivos.config(
            text="No aposté por: " + " · ".join(partes), fg=C_SUB_FG)

    def _vigilar_transito(self, p, ahora: float) -> None:
        """
        Mide la duración real del descanso 2Q→3Q de cada liga.

        Guarda cuándo se vio por primera vez al partido en descanso; cuando
        el 3Q arranca, registra los minutos transcurridos (autocalibración).
        """
        if descansos.en_ventana_descanso(p):
            self._transitos.setdefault(p.evento_id, (p.clave_liga, ahora))
            return
        marca = self._transitos.pop(p.evento_id, None)
        if marca and p.q3 is not None and not p.finalizado:
            liga, t0 = marca
            minutos = (ahora - t0) / 60.0
            def guardar():
                try:
                    descansos.registrar_descanso_observado(liga, minutos)
                except Exception:   # noqa: BLE001  (la calibración no puede romper el ping)
                    pass
            threading.Thread(target=guardar, daemon=True).start()

    def _actualizar_aviso(self, modelo, origen: str) -> None:
        """
        El pie rojo/verde dice el modelo que SE VA A USAR de verdad.

        Antes era un texto fijo ("está entrenado con Euroliga/NBA, cualquier
        otra liga NO está calibrada") escrito antes de existir los modelos por
        liga; mentía siempre. Ahora se recalcula con el modelo real:
          * verde  -> la liga del partido tiene SU modelo (entrenado con sus
                      partidos reales).
          * rojo   -> la liga no tiene modelo y se cae al del Excel.
        """
        if modelo is None or not modelo.n:
            self.lbl_aviso.config(
                text="Sin modelo usable: no hay datos suficientes para esta liga.",
                fg=C_ROJO_FG)
            return
        if origen.startswith("por liga"):
            self.lbl_aviso.config(
                text=f"✅ LIGA CALIBRADA: modelo propio de {modelo.liga} "
                     f"entrenado con {modelo.n} partidos reales de esa liga.",
                fg=C_VERDE)
        elif "sin datos de" in origen:
            clave = origen.split("sin datos de", 1)[1].strip().rstrip(")")
            self.lbl_aviso.config(
                text=f"⚠ Sin modelo propio de {clave} (aún sin partidos "
                     f"suficientes); se usa el del Excel ({modelo.liga}, "
                     f"n={modelo.n}).",
                fg=C_ROJO_FG)
        else:
            self.lbl_aviso.config(
                text=f"Modelo fijado a mano: {modelo.liga} ({origen}, "
                     f"n={modelo.n}).",
                fg=C_SUB_FG)

    def calcular_flujo(self) -> None:
        p = self._partido_sel
        if p is None:
            return
        self.lbl_partido_sel.config(text=p.nombre)
        self.lbl_q1.config(text=str(p.q1) if p.q1 is not None else "—")
        self.lbl_q2.config(text=str(p.q2) if p.q2 is not None else "—")
        self.lbl_linea.config(text=f1(p.linea) if p.linea else "—")

        modelo, origen = self._modelo_de(p)
        if modelo is not None and modelo.n:
            self.lbl_modelo_info.config(
                text=f"Modelo: {modelo.liga} · {origen} · n={modelo.n} · "
                     f"R²={modelo.r2_dos:.3f} · RMSE test {modelo.rmse_test_dos:.1f} pts",
                fg=C_SUB_FG)
        else:
            self.lbl_modelo_info.config(text="", fg=C_SUB_FG)

        # el aviso refleja el modelo que SE VA A USAR de verdad (antes era un
        # texto fijo de cuando no existían los modelos por liga)
        self._actualizar_aviso(modelo, origen)

        if modelo is None or modelo.n == 0:
            self.lbl_flujo_estado.config(text="Modelo sin datos", fg=C_ROJO_FG)
            self.btn_apostar.config(state="disabled")
            return

        if p.q1 is None:
            self._poner_veredicto("faltan datos")
            self.lbl_pred.config(text="—")
            self.lbl_dif.config(text="—")
            self.lbl_prob.config(text="—")
            self.lbl_flujo_estado.config(text="El partido aún no tiene 1er cuarto.", fg=C_SUB_FG)
            self.btn_apostar.config(state="disabled")
            return

        res = predecir(modelo, float(p.q1),
                       None if not p.q2_completo else float(p.q2),
                       None if p.linea is None else float(p.linea))

        self.lbl_pred.config(text=f1(res.prediccion))
        self.lbl_dif.config(text=f1(res.diferencia))
        self._poner_veredicto(res.veredicto)
        self.lbl_prob.config(text="" if res.prob_over is None else f"{res.prob_over * 100:.1f} %")

        if not p.q2_completo:
            self.lbl_flujo_estado.config(
                text="2Q incompleto: se usa el modelo SOLO con 1Q.", fg=C_ROJO_FG)
        elif p.en_descanso_2q:
            self.lbl_flujo_estado.config(
                text="En descanso del 2Q: 1Q y 2Q finales. ✅", fg=C_VERDE)
        else:
            self.lbl_flujo_estado.config(
                text=f"Datos de {p.periodo or 'partido'}: Q1 y Q2 conservados. ✅", fg=C_VERDE)

        puede = (p.linea is not None and res.prediccion is not None
                 and res.veredicto in ("OVER", "UNDER") and p.mercado_abierto)
        self.btn_apostar.config(state="normal" if puede else "disabled")
        if p.linea is not None and not p.mercado_abierto:
            self.lbl_flujo_estado.config(
                text="Mercado SUSPENDIDO: la línea está obsoleta y no se puede apostar.",
                fg=C_ROJO_FG)

    def _poner_veredicto(self, texto: str) -> None:
        if texto == "OVER":
            bg, fg = C_VERDICTO_OK, C_VERDE
        elif texto == "UNDER":
            bg, fg = C_ROJO_BG, C_ROJO_FG
        else:
            bg, fg = C_HEAD_BG, "#000000"
        self.lbl_veredicto.config(text=texto, bg=bg, fg=fg)

    # ------------------------------------------------------------- apuestas
    def apostar(self) -> None:
        p = self._partido_sel
        if p is None:
            return
        modelo, origen = self._modelo_de(p)
        nombre_modelo = modelo.liga if modelo else self.var_modelo.get()
        if modelo is None or not modelo.n:
            messagebox.showwarning("Modelo sin datos", "No hay modelo para esta liga.")
            return
        if p.linea is None or not p.mercado_abierto:
            messagebox.showwarning(
                "Mercado no disponible",
                "Este partido no tiene línea abierta; no se puede apostar.")
            return

        res = predecir(modelo, float(p.q1),
                       None if not p.q2_completo else float(p.q2),
                       float(p.linea))
        if res.veredicto not in ("OVER", "UNDER"):
            messagebox.showwarning("Faltan datos", "El modelo no puede dar un veredicto.")
            return

        cuota = p.cuota_over if res.veredicto == "OVER" else p.cuota_under
        if not p.mercado_abierto or not cuota or cuota <= 1.0:
            messagebox.showwarning(
                "Cuota no disponible",
                f"El mercado de {res.veredicto} está {'suspendido' if not p.mercado_abierto else 'cerrado'} "
                f"para este partido.\nNo se puede apostar con una línea obsoleta.")
            return

        stake = parsear(self.var_stake.get())
        if stake is None or stake <= 0:
            messagebox.showwarning("Stake inválido", "Escribe un stake mayor que 0.")
            return

        apuesta = Apuesta(
            evento_id=p.evento_id,
            partido=p.nombre,
            liga_apuesta=p.liga_nombre or "—",
            liga_modelo=nombre_modelo,
            mercado="TOTAL",
            seleccion=res.veredicto,
            linea=float(p.linea),
            cuota=float(cuota),
            stake=float(stake),
            prediccion=res.prediccion,
            diferencia=res.diferencia,
            probabilidad=res.prob_over,
            estado=paperbet.ESTADO_PENDIENTE,
        )
        error = paperbet.validar(apuesta)
        if error:
            messagebox.showwarning("Apuesta no válida", error)
            return

        try:
            id_apuesta = base_datos.guardar_apuesta(apuesta)
        except base_datos.ErrorBaseDatos as exc:
            messagebox.showerror("No se pudo guardar en PostgreSQL", str(exc))
            return

        self.app.refrescar_apuestas()
        messagebox.showinfo(
            "Apuesta simulada guardada",
            f"#{id_apuesta}  {apuesta.partido}\n"
            f"{apuesta.seleccion} @ {f1(apuesta.linea)}  cuota {apuesta.cuota:.2f}\n"
            f"Predicción {f1(apuesta.prediccion)}  ({apuesta.diferencia:+.1f} pts)\n"
            f"Prob. {apuesta.probabilidad * 100:.1f}%   Stake {apuesta.stake:,.0f}\n"
            f"Estado: PENDIENTE → se resolverá al terminar el partido.",
        )

    def resolver_pendientes(self) -> None:
        def tarea():
            partidos = api_betplay.obtener_partidos()
            return self._resolver_con(partidos)

        def al_terminar(res):
            if isinstance(res, Exception):
                messagebox.showerror("Error al resolver", str(res))
                return
            n = res
            self.app.refrescar_apuestas()
            self.lbl_flujo_estado.config(
                text=f"{n} apuesta(s) resuelta(s).", fg=C_VERDE if n else C_SUB_FG)

        self.app.en_hilo(tarea, al_terminar)

    def _resolver_automaticas(self, partidos) -> int:
        try:
            return self._resolver_con(partidos)
        except base_datos.ErrorBaseDatos:
            return 0

    # ------------------------------------------------ cuotas reales / cierre
    def _registrar_cuotas(self, partidos) -> None:
        """
        Guarda en `cuotas_historial` los cambios de línea/cuotas reales.

        Es la base para backtests honestos (no existe histórico de cuotas de
        la casa): una fila por cambio de línea/cuotas/periodo y otra de cierre
        cuando el partido termina, con el total final visto en el feed.
        """
        if self._cuotas_ultimas is None:
            try:
                self._cuotas_ultimas = base_datos.ultimas_cuotas()
            except base_datos.ErrorBaseDatos:
                return
        filas = []
        for p in partidos:
            termino = bool(p.finalizado)
            total = (float(p.total_final)
                     if termino and p.total_final is not None else None)
            en_descanso = bool(p.en_descanso_2q)
            clave = (p.linea, p.cuota_over, p.cuota_under, p.period_id or "",
                     termino, total, en_descanso)
            anterior = self._cuotas_ultimas.get(p.evento_id)
            if anterior is not None and tuple(anterior) == clave:
                continue                      # sin cambios: no se toca la BD
            self._cuotas_ultimas[p.evento_id] = clave
            filas.append((
                p.evento_id, p.clave_liga or "", p.nombre,
                p.linea, p.cuota_over, p.cuota_under, p.period_id or "",
                f"{p.reloj_min}:{p.reloj_seg:02d}" if p.es_vivo else "",
                termino, total, en_descanso))
        if filas:
            try:
                base_datos.guardar_cuotas(filas)
            except base_datos.ErrorBaseDatos:
                pass

    def _reconciliar_en_hilo(self) -> None:
        """Cierra/corrige apuestas con el resultado OFICIAL (hilo aparte)."""
        if self._reconciliando:
            return
        self._reconciliando = True
        eventos = set(self._eventos_feed)

        def tarea():
            import reconciliacion as rec
            n = rec.reconciliar(horas=48.0, minimo_edad_horas=2.0,
                                sin_en_feed=eventos, log=log_auto)
            # completa cierre (linea_3q_5min, linea_cierre) y resultado de
            # las señales registradas; idempotente y silencioso
            try:
                base_datos.cerrar_senales()
            except Exception:   # noqa: BLE001
                pass
            return n

        def al_terminar(res):
            self._reconciliando = False
            if isinstance(res, Exception):
                log_auto(f"reconciliacion: {res}")
                return
            if not res:
                return
            self.app.refrescar_apuestas()
            self.lbl_ping_estado.config(
                text=f"{res} apuesta(s) cerrada(s)/corregida(s) con el "
                     f"resultado oficial de API-Sports", fg=C_VERDE)

        self.app.en_hilo(tarea, al_terminar)

    def _resolver_con(self, partidos) -> int:
        pendientes = base_datos.apuestas_pendientes()
        if not pendientes:
            return 0
        indice = {p.evento_id: p for p in partidos}
        resueltas = 0
        for a in pendientes:
            p = indice.get(a.evento_id)
            if p is None or not p.finalizado or p.total_final is None:
                continue
            paperbet.resolver(a, float(p.total_final))
            base_datos.actualizar_resultado(a)
            resueltas += 1
        return resueltas


# ===========================================================================
#  Pestaña APUESTAS — paperbet en PostgreSQL
# ===========================================================================
class PestanaApuestas(ttk.Frame):
    COLUMNAS = ("id", "fecha", "partido", "sel", "linea", "cuota", "stake",
                "pred", "delta", "modelo", "estado", "total", "beneficio")
    TITULOS = {
        "id": "#", "fecha": "Fecha", "partido": "Partido", "sel": "Sel.",
        "linea": "Línea", "cuota": "Cuota", "stake": "Stake", "pred": "Pred.",
        "delta": "Δ", "modelo": "Modelo", "estado": "Estado",
        "total": "Total final", "beneficio": "Beneficio",
    }
    ANCHOS = {
        "id": 45, "fecha": 125, "partido": 245, "sel": 62, "linea": 55, "cuota": 55,
        "stake": 75, "pred": 55, "delta": 55, "modelo": 78, "estado": 85,
        "total": 70, "beneficio": 85,
    }

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=10)
        self.app = app
        self._construir()

    def _construir(self) -> None:
        top = tk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        tk.Label(top, text="PAPERBET — apuestas simuladas en PostgreSQL",
                 font=F_TITULO, bg=C_TITULO_BG, fg=C_TITULO_FG, anchor="w", padx=10,
                 pady=4).pack(fill="x")

        # 8 cajas: en rejilla de 4 columnas se adaptan a cualquier ancho
        # (en pantallas estrechas ya no se salen de la ventana)
        resumen = tk.Frame(self)
        resumen.pack(fill="x", pady=(0, 6))
        self.labels_resumen: dict[str, tk.Label] = {}
        claves = [
            ("total", "Apuestas"), ("pendientes", "Pendientes"), ("ganadas", "Ganadas"),
            ("perdidas", "Perdidas"), ("anuladas", "Anuladas"),
            ("aciertos", "% Aciertos"), ("roi", "ROI"), ("beneficio", "Beneficio"),
        ]
        for col in range(4):
            resumen.columnconfigure(col, weight=1, uniform="resumen")
        for i, (clave, texto) in enumerate(claves):
            caja = tk.Frame(resumen, bg=C_AZUL_CLARO, padx=8, pady=4,
                            highlightbackground=C_AZUL, highlightthickness=1)
            caja.grid(row=i // 4, column=i % 4, sticky="ew", padx=(0, 6), pady=(0, 4))
            tk.Label(caja, text=texto, font=F_PEQUE, bg=C_AZUL_CLARO, fg=C_AZUL).pack()
            lbl = tk.Label(caja, text="—", font=("Calibri", 12, "bold"), bg=C_AZUL_CLARO)
            lbl.pack()
            self.labels_resumen[clave] = lbl

        marco = tk.Frame(self)
        marco.pack(fill="both", expand=True, pady=(0, 6))
        self.tree = ttk.Treeview(marco, columns=self.COLUMNAS, show="headings", height=12)
        for col in self.COLUMNAS:
            self.tree.heading(col, text=self.TITULOS[col])
            self.tree.column(col, width=self.ANCHOS[col],
                             anchor="w" if col == "partido" else "center")
        vsb = ttk.Scrollbar(marco, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(marco, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        self.tree.tag_configure("ganada", foreground=C_VERDE, background="#EAF5EA")
        self.tree.tag_configure("perdida", foreground=C_ROJO_FG, background="#FDEDED")
        self.tree.tag_configure("pendiente", foreground=C_AZUL, background="#F3F8FD")

        botones = tk.Frame(self)
        botones.pack(fill="x")
        tk.Button(botones, text="🔄 Actualizar", font=F_TEXTO, width=14,
                  command=self.refrescar).pack(side="left", padx=(0, 6))
        tk.Button(botones, text="✔ Resolver pendientes", font=F_TEXTO, width=20,
                  command=lambda: self.app.pestana_vivo.resolver_pendientes()).pack(
            side="left", padx=(0, 6))
        tk.Button(botones, text="🗑 Eliminar seleccionada", font=F_TEXTO, width=20,
                  command=self.eliminarSeleccionada).pack(side="left", padx=(0, 6))
        tk.Button(botones, text="✍ Liquidar manual...", font=F_TEXTO, width=18,
                  command=self.liquidarManual).pack(side="left", padx=(0, 6))
        tk.Button(botones, text="📄 Exportar CSV", font=F_TEXTO, width=16,
                  command=self.exportarCSV).pack(side="left", padx=(0, 6))
        self.lbl_bd = tk.Label(botones, text="", font=F_PEQUE, fg=C_SUB_FG)
        self.lbl_bd.pack(side="right")

    # ------------------------------------------------------------- acciones
    def refrescar(self) -> None:
        if not self.app.db_ok:
            self.lbl_bd.config(text=self.app.db_error or "Sin conexión a PostgreSQL",
                               fg=C_ROJO_FG)
            return
        try:
            apuestas = base_datos.listar_apuestas()
        except base_datos.ErrorBaseDatos as exc:
            self.app.db_ok = False
            self.app.db_error = str(exc)
            self.lbl_bd.config(text=str(exc), fg=C_ROJO_FG)
            return

        r = paperbet.resumir(apuestas)
        self.labels_resumen["total"].config(text=str(r.total))
        self.labels_resumen["pendientes"].config(text=str(r.pendientes))
        self.labels_resumen["ganadas"].config(text=str(r.ganadas))
        self.labels_resumen["perdidas"].config(text=str(r.perdidas))
        self.labels_resumen["anuladas"].config(text=str(r.anuladas))
        self.labels_resumen["aciertos"].config(text="—" if r.aciertos is None else f"{r.aciertos:.1f} %")
        self.labels_resumen["roi"].config(text="—" if r.roi is None else f"{r.roi:+.1f} %")
        ben = self.labels_resumen["beneficio"]
        ben.config(text=f"{r.beneficio:+,.0f}",
                   fg=C_VERDE if r.beneficio > 0 else (C_ROJO_FG if r.beneficio < 0 else "#000000"))

        self.tree.delete(*self.tree.get_children())
        for a in apuestas:
            fecha = a.fecha.strftime("%d/%m/%Y %H:%M") if a.fecha else ""
            tag = {"GANADA": "ganada", "PERDIDA": "perdida"}.get(a.estado, "pendiente")
            self.tree.insert("", "end", iid=str(a.id), tags=(tag,), values=(
                a.id, fecha, a.partido, a.seleccion, f1(a.linea), f"{a.cuota:.2f}",
                f"{a.stake:,.0f}", f1(a.prediccion),
                "" if a.diferencia is None else f"{a.diferencia:+.1f}",
                a.liga_modelo, a.estado,
                "" if a.total_final is None else f1(a.total_final),
                "" if a.beneficio is None else f"{a.beneficio:+,.0f}",
            ))
        try:
            info = base_datos.estadisticas_bd()
            self.lbl_bd.config(
                text=f"PostgreSQL: {base_datos.descripcionConexion()} · {info['apuestas']} filas",
                fg=C_SUB_FG)
        except base_datos.ErrorBaseDatos as exc:
            self.lbl_bd.config(text=str(exc), fg=C_ROJO_FG)

    def eliminarSeleccionada(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Nada seleccionado", "Selecciona una apuesta de la lista.")
            return
        if not messagebox.askyesno("Eliminar", f"¿Eliminar la apuesta #{sel[0]}?"):
            return
        try:
            base_datos.eliminar_apuesta(int(sel[0]))
        except base_datos.ErrorBaseDatos as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.refrescar()

    def liquidarManual(self) -> None:
        """
        Cierra (o corrige) una apuesta a mano con el total final real.

        Para los partidos que ni el feed ni API-Sports pueden cerrar (ligas
        sin datos oficiales): se escribe el total y la app recalcula el
        resultado y el dinero con la misma fórmula de siempre.
        """
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Nada seleccionado",
                                "Selecciona una apuesta de la lista.")
            return
        apuesta_id = int(sel[0])
        total = simpledialog.askfloat(
            "Liquidar manualmente",
            f"Total final real del partido de la apuesta #{apuesta_id}:",
            minvalue=0.0, maxvalue=500.0)
        if total is None:
            return
        try:
            a = next((x for x in base_datos.listar_apuestas() if x.id == apuesta_id),
                     None)
            if a is None:
                return
            if a.estado == paperbet.ESTADO_PENDIENTE:
                paperbet.resolver(a, total)
            else:
                paperbet.recalcular(a, total)
            a.nota = (a.nota or "") + " [liquidada a mano]"
            base_datos.actualizar_resultado(a)
            base_datos.marcar_verificado(a.id)
        except base_datos.ErrorBaseDatos as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.refrescar()

    def exportarCSV(self) -> None:
        """
        Exporta la tabla de APUESTAS a CSV (tal como se ve, cabeceras incluidas).

        Delimitador ';' y BOM UTF-8 para que Excel en español abra el archivo
        con tildes y columnas correctas al doble clic. El orden y el formato de
        cada celda es el mismo que muestra la tabla (respeta lo visible).
        """
        filas = self.tree.get_children()
        if not filas:
            messagebox.showinfo("Nada que exportar",
                                "La tabla de apuestas está vacía.")
            return
        ruta = filedialog.asksaveasfilename(
            title="Exportar apuestas a CSV",
            defaultextension=".csv",
            initialfile=f"apuestas_{datetime.now():%Y-%m-%d_%H%M}.csv",
            filetypes=[("CSV (Excel)", "*.csv"), ("Todos los archivos", "*.*")])
        if not ruta:
            return
        try:
            import csv
            with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
                escritor = csv.writer(fh, delimiter=";")
                escritor.writerow([self.TITULOS[c] for c in self.COLUMNAS])
                for iid in filas:
                    escritor.writerow(self.tree.item(iid, "values"))
        except OSError as exc:
            messagebox.showerror("Error al exportar", str(exc))
            return
        self.lbl_bd.config(
            text=f"CSV exportado: {len(filas)} filas → {ruta}", fg=C_VERDE)
        messagebox.showinfo("Exportado",
                            f"{len(filas)} apuestas exportadas a:\n{ruta}")


# ===========================================================================
#  Pestaña GRÁFICO — dispersión: predicción vs total final (año en curso)
# ===========================================================================
def datos_dispersion() -> dict:
    """
    Datos de los gráficos del año en curso (una sola carga para todo el panel):

      * partidos: [(liga, fecha, pred, final, proxy, sigma)] — TODOS los
        partidos del año con modelo de liga. `proxy` = 2·(q1+q2), la línea de
        referencia del descanso para la calibración, y `sigma` = el RMSE del
        modelo de su liga (el que usa Φ(Δ/σ)).
      * apuestas: (predicción en vivo, total final, estado).
    """
    import modelos as mod_modelos
    anio = datetime.now().year
    conn = base_datos.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT liga, fecha, q1, q2, total FROM muestras "
                "WHERE fecha >= %s", (datetime(anio, 1, 1).date(),))
            filas = cur.fetchall()
    finally:
        conn.close()
    modelos = mod_modelos.cargar_modelos()
    partidos = []
    sin_modelo = 0
    for liga, fecha, q1, q2, total in filas:
        m = modelos.get(liga)
        if m is None or not m.n:
            sin_modelo += 1
            continue
        pred = m.predecir_dos(float(q1), float(q2))
        proxy = 2.0 * (float(q1) + float(q2))
        sigma = m.rmse_test_dos or m.rmse_dos or 0.0
        partidos.append((liga, fecha, pred, float(total), proxy, sigma))
    apuestas = base_datos.listar_apuestas()
    puntos = [(a.prediccion, a.total_final, a.estado)
              for a in apuestas
              if a.prediccion is not None and a.total_final is not None]
    pendientes = sum(1 for a in apuestas if a.total_final is None)
    return {"anio": anio, "partidos": partidos, "apuestas": puntos,
            "pendientes": pendientes, "sin_modelo": sin_modelo}


def calcular_graficos(d: dict) -> dict:
    """
    Cálculos de los 6 gráficos (una vez por refresco, en hilo aparte):

      * residuo: [(pred, total−pred)] y media móvil por tramos de predicción;
      * por liga: (n, RMSE, sesgo, IC95% del sesgo) con n ≥ 30 (muestra
        suficiente); las de menos n solo se cuentan;
      * por mes: RMSE y sesgo con IC;
      * calibración (línea proxy 2·(q1+q2), lado elegido): bins de
        Φ(Δ/σ) vs frecuencia real, ECE y Brier;
      * histograma de residuos + σ usada por Φ vs σ real.
    """
    import math
    from collections import defaultdict

    P = d["partidos"]
    out: dict = {}

    # ---------- residuos + media móvil ----------
    resid = [(p, t - p) for _l, _f, p, t, _x, _s in P]
    out["residuo"] = resid
    ma = []
    if P:
        lo = min(t[2] for t in P)
        hi = max(t[2] for t in P)
        nb = 18
        ancho = (hi - lo) / nb if hi > lo else 1.0
        bins: dict = defaultdict(list)
        for _l, _f, p, t, _x, _s in P:
            i = min(nb - 1, max(0, int((p - lo) / ancho)))
            bins[i].append(t - p)
        for i in sorted(bins):
            v = bins[i]
            if len(v) >= 20:
                ma.append((lo + (i + 0.5) * ancho, sum(v) / len(v), len(v)))
    out["ma"] = ma
    out["rango"] = ((min(t[2] for t in P), max(t[2] for t in P)) if P else (0, 1))

    # ---------- por liga (n>=30) ----------
    acum: dict = defaultdict(lambda: [0.0, 0.0, 0])
    for l, _f, p, t, _x, _s in P:
        e = t - p
        a = acum[l]
        a[0] += e
        a[1] += e * e
        a[2] += 1
    ligas = []
    for l, (s, s2, n) in acum.items():
        if n < 30:
            continue
        rmse = math.sqrt(s2 / n)
        sesgo = s / n
        var = max(0.0, s2 / n - sesgo * sesgo)
        ic = 1.96 * math.sqrt(var / n)
        ligas.append((l, n, rmse, sesgo, ic))
    ligas.sort(key=lambda x: -x[2])
    out["ligas"] = ligas
    out["ligas_cortas"] = sum(1 for v in acum.values() if v[2] < 30)

    # ---------- por mes ----------
    pm: dict = defaultdict(lambda: [0.0, 0.0, 0])
    for _l, f, p, t, _x, _s in P:
        e = t - p
        a = pm[(f.year, f.month)]
        a[0] += e
        a[1] += e * e
        a[2] += 1
    meses = []
    for (y, m), (s, s2, n) in sorted(pm.items()):
        rmse = math.sqrt(s2 / n)
        sesgo = s / n
        var = max(0.0, s2 / n - sesgo * sesgo)
        meses.append((f"{m:02d}/{str(y)[2:]}", n, rmse, sesgo,
                      1.96 * math.sqrt(var / n)))
    out["meses"] = meses

    # ---------- calibración (proxy 2·(q1+q2), lado elegido) ----------
    def phi(z):
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    bins_cal = [[0.0, 0.0, 0] for _ in range(10)]     # sum_p, aciertos, n
    brier = 0.0
    ncal = 0
    for _l, _f, p, t, proxy, s in P:
        if not s or t == proxy:
            continue
        dlt = p - proxy
        prob = phi(abs(dlt) / s)
        ok = (t > proxy) == (dlt >= 0)
        i = min(9, max(0, int((prob - 0.5) * 20)))
        b = bins_cal[i]
        b[0] += prob
        b[1] += 1.0 if ok else 0.0
        b[2] += 1
        brier += (prob - (1.0 if ok else 0.0)) ** 2
        ncal += 1
    cal = [(b[0] / b[2], b[1] / b[2], b[2]) for b in bins_cal if b[2] > 0]
    ece = sum((n / ncal) * abs(pp - fr) for pp, fr, n in cal) if ncal else 0.0
    out["cal"] = {"bins": cal, "ece": ece,
                  "brier": (brier / ncal) if ncal else 0.0, "n": ncal}

    # ---------- histograma de residuos ----------
    hist: dict = defaultdict(int)
    for _p, e in resid:
        clave = int(round(e / 2.0)) * 2          # bins de 2 puntos
        if -80 <= clave <= 80:
            hist[clave] += 1
    n = len(resid)
    media = sum(e for _p, e in resid) / n if n else 0.0
    var = sum((e - media) ** 2 for _p, e in resid) / n if n else 1.0
    sd = math.sqrt(var) if var > 0 else 1.0
    sigma_ref = (sum(t[5] for t in P) / len(P)) if P else 0.0
    p2 = (sum(1 for _p, e in resid if abs(e) > 2 * sd) / n) if n else 0.0
    out["hist"] = {"bins": sorted(hist.items()), "media": media, "sd": sd,
                   "n": n, "sigma_ref": sigma_ref, "p2": p2}
    return out


class PestanaGrafico(ttk.Frame):
    """Panel de gráficos con subpestañas (todo canvas puro, sin dependencias)."""

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=10)
        self.app = app
        self._datos: dict | None = None
        self._calc: dict | None = None
        self._tam: dict = {}
        self._cargando = False
        self._construir()

    def _construir(self) -> None:
        top = tk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        tk.Label(top, text="GRÁFICOS — modelo vs realidad (todo el año)",
                 font=F_TITULO, bg=C_TITULO_BG, fg=C_TITULO_FG, anchor="w",
                 padx=10, pady=4).pack(fill="x")

        barra = tk.Frame(self)
        barra.pack(fill="x", pady=(0, 4))
        tk.Button(barra, text="🔄 Actualizar", font=F_TEXTO, width=14,
                  command=self.refrescar).pack(side="left", padx=(0, 8))
        self.lbl_nube = tk.Label(barra, text="", font=F_PEQUE, fg=C_SUB_FG)
        self.lbl_nube.pack(side="left", padx=(0, 12))
        self.lbl_apuestas = tk.Label(barra, text="", font=F_PEQUE, fg=C_SUB_FG)
        self.lbl_apuestas.pack(side="left")

        self.sub = ttk.Notebook(self)
        self.sub.pack(fill="both", expand=True)
        self.canvas_disp = self._sub("Dispersión")
        self.canvas_res = self._sub("Residuo vs pred")
        self.canvas_ligas = self._sub("Error por liga")
        self.canvas_mes = self._sub("Error por mes")
        self.canvas_cal = self._sub("Calibración")
        self.canvas_hist = self._sub("Histograma")

    def _sub(self, titulo: str) -> tk.Canvas:
        marco = tk.Frame(self.sub)
        self.sub.add(marco, text=f"  {titulo}  ")
        c = tk.Canvas(marco, bg="white", highlightthickness=1,
                      highlightbackground="#CFD8DC")
        c.pack(fill="both", expand=True)
        c.bind("<Configure>", lambda _e, cc=c: self._al_redimensionar(cc))
        return c

    # ------------------------------------------------------------- datos
    def refrescar(self) -> None:
        if self._cargando:
            return
        self._cargando = True
        self.lbl_nube.config(text="cargando…", fg=C_SUB_FG)

        def tarea():
            d = datos_dispersion()
            return d, calcular_graficos(d)

        def al_terminar(res):
            self._cargando = False
            if isinstance(res, Exception):
                self.lbl_nube.config(text=f"Error: {res}", fg=C_ROJO_FG)
                return
            self._datos, self._calc = res
            self._actualizar_resumen()
            self._dibujar_todo()

        self.app.en_hilo(tarea, al_terminar)

    @staticmethod
    def _stats(pares) -> str:
        import math
        n = len(pares)
        if not n:
            return "n=0"
        errs = [p - f for p, f in pares]
        rmse = math.sqrt(sum(e * e for e in errs) / n)
        mae = sum(abs(e) for e in errs) / n
        sesgo = sum(errs) / n
        return (f"n={n:,} · RMSE {rmse:.1f} · MAE {mae:.1f} · "
                f"sesgo {sesgo:+.1f}")

    def _actualizar_resumen(self) -> None:
        d = self._datos
        self.lbl_nube.config(
            text=(f"Partidos {d['anio']}: "
                  f"{self._stats([(t[2], t[3]) for t in d['partidos']])} "
                  f"(sin modelo: {d['sin_modelo']:,})"),
            fg=C_SUB_FG)
        self.lbl_apuestas.config(
            text=(f"Apuestas: "
                  f"{self._stats([(pr, f) for pr, f, _e in d['apuestas']])} "
                  f"· pendientes sin total: {d['pendientes']}"),
            fg=C_SUB_FG)

    # ------------------------------------------------------------- dibujo
    def _pares_dibujo(self):
        return ((self.canvas_disp, self._dibujar_disp),
                (self.canvas_res, self._dibujar_res),
                (self.canvas_ligas, self._dibujar_ligas),
                (self.canvas_mes, self._dibujar_mes),
                (self.canvas_cal, self._dibujar_cal),
                (self.canvas_hist, self._dibujar_hist))

    def _al_redimensionar(self, c: tk.Canvas, _e=None) -> None:
        tam = (c.winfo_width(), c.winfo_height())
        if tam != self._tam.get(c):
            self._tam[c] = tam
            if self._calc:
                for cc, fn in self._pares_dibujo():
                    if cc is c:
                        try:
                            fn(cc)
                        except Exception:   # noqa: BLE001
                            pass
                        break

    def _dibujar_todo(self) -> None:
        if not self._calc:
            return
        for c, fn in self._pares_dibujo():
            try:
                fn(c)
            except Exception:   # noqa: BLE001 — un gráfico no tumba el panel
                continue

    def _dibujar_disp(self, c) -> None:
        import math
        c.delete("all")
        d = self._datos
        W, H = c.winfo_width(), c.winfo_height()
        if not d or W < 80 or H < 80:
            return
        x0, x1, y0, y1 = 52, W - 16, H - 52, 18
        todos = ([t[2] for t in d["partidos"]] + [t[3] for t in d["partidos"]]
                 + [v for pr, f, _e in d["apuestas"] for v in (pr, f)])
        if not todos:
            return
        lo = math.floor(min(todos) / 20) * 20
        hi = math.ceil(max(todos) / 20) * 20
        px = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
        py = lambda v: y0 - (v - lo) / (hi - lo) * (y0 - y1)
        paso = 20 if (hi - lo) <= 240 else 40
        for v in range(int(lo), int(hi) + 1, paso):
            c.create_line(px(v), y0, px(v), y1, fill="#ECEFF1")
            c.create_line(x0, py(v), x1, py(v), fill="#ECEFF1")
            c.create_text(px(v), y0 + 11, text=str(v), font=F_PEQUE, fill=C_SUB_FG)
            c.create_text(x0 - 16, py(v), text=str(v), font=F_PEQUE, fill=C_SUB_FG)
        c.create_line(px(lo), py(lo), px(hi), py(hi), fill="#90A4AE", dash=(5, 3))
        c.create_text(px(hi) - 6, py(hi) + 10, text="y = x", anchor="e",
                      font=F_PEQUE, fill="#90A4AE")
        for _l, _f, pr, f, _x, _s in d["partidos"]:
            c.create_oval(px(pr) - 2, py(f) - 2, px(pr) + 2, py(f) + 2,
                          fill="#CFD8DC", outline="")
        colores = {"GANADA": C_VERDE, "PERDIDA": C_ROJO_FG, "ANULADA": "#F9A825"}
        for pr, f, e in d["apuestas"]:
            colr = colores.get(e, C_AZUL)
            c.create_oval(px(pr) - 4, py(f) - 4, px(pr) + 4, py(f) + 4,
                          fill=colr, outline="#FFFFFF", width=1)
        c.create_text((x0 + x1) // 2, H - 14,
                      text="Predicción del modelo (total)", font=F_LABEL)
        c.create_text(14, (y0 + y1) // 2, text="Total final", angle=90,
                      font=F_LABEL)
        lx, ly = x1 - 150, y1 + 4
        for texto, color in (("partidos del año", "#CFD8DC"),
                             ("apuestas ganadas", C_VERDE),
                             ("apuestas perdidas", C_ROJO_FG),
                             ("anuladas (push)", "#F9A825")):
            c.create_oval(lx, ly + 3, lx + 8, ly + 11, fill=color, outline="")
            c.create_text(lx + 12, ly + 7, text=texto, anchor="w",
                          font=F_PEQUE, fill=C_SUB_FG)
            ly += 14

    def _dibujar_res(self, c) -> None:
        import math
        c.delete("all")
        cl = self._calc
        W, H = c.winfo_width(), c.winfo_height()
        if not cl or W < 80 or H < 80:
            return
        x0, x1, y0, y1 = 52, W - 16, H - 52, 18
        plo, phi_ = cl["rango"]
        emax = max((abs(e) for _p, e in cl["residuo"]), default=30)
        emax = math.ceil(emax / 10) * 10
        px = lambda v: x0 + (v - plo) / (phi_ - plo) * (x1 - x0)
        py = lambda v: y0 - (v + emax) / (2 * emax) * (y0 - y1)
        paso = 20 if (phi_ - plo) <= 240 else 40
        for v in range(int(math.floor(plo / 20) * 20), int(phi_) + 1, paso):
            c.create_line(px(v), y0, px(v), y1, fill="#ECEFF1")
            c.create_text(px(v), y0 + 11, text=str(v), font=F_PEQUE, fill=C_SUB_FG)
        paso_e = max(10, int(emax / 4 / 10) * 10)
        for v in range(-emax, emax + 1, paso_e):
            c.create_line(x0, py(v), x1, py(v), fill="#ECEFF1")
            c.create_text(x0 - 18, py(v), text=f"{v:+d}", font=F_PEQUE, fill=C_SUB_FG)
        c.create_line(x0, py(0), x1, py(0), fill="#90A4AE")
        for p, e in cl["residuo"]:
            c.create_oval(px(p) - 2, py(e) - 2, px(p) + 2, py(e) + 2,
                          fill="#B3D4F5", outline="")
        pts = []
        for p, m, _n in cl["ma"]:
            pts.extend((px(p), py(m)))
        if len(pts) >= 4:
            c.create_line(*pts, fill="#1565C0", width=2)
        for p, m, n in cl["ma"]:
            c.create_oval(px(p) - 3, py(m) - 3, px(p) + 3, py(m) + 3,
                          fill="#1565C0", outline="")
        c.create_text((x0 + x1) // 2, H - 14,
                      text="Predicción del modelo (total)", font=F_LABEL)
        c.create_text(14, (y0 + y1) // 2, text="Residuo (total final − pred)",
                      angle=90, font=F_LABEL)
        c.create_text(x1 - 6, y1 + 6, anchor="ne", font=F_PEQUE, fill="#1565C0",
                      text="— media móvil por tramos (n≥20)")

    def _dibujar_ligas(self, c) -> None:
        c.delete("all")
        cl = self._calc
        W, H = c.winfo_width(), c.winfo_height()
        if not cl or W < 120 or H < 80:
            return
        ligas = cl["ligas"]
        if not ligas:
            return
        y0 = 30
        filas = min(len(ligas), max(6, (H - 60) // 16))
        rh = (H - y0 - 24) / filas
        x_lab, x_bar0 = 8, 172
        x_bar1 = x_bar0 + max(60, int(0.42 * (W - 320)))
        x_ses0, x_ses1 = x_bar1 + 34, max(x_bar1 + 80, W - 66)
        max_rmse = max(l[2] for l in ligas[:filas]) or 1
        max_ses = max(abs(l[3]) + l[4] for l in ligas[:filas]) or 1
        cx = (x_ses0 + x_ses1) / 2
        c.create_text(x_bar0, 14, anchor="w", font=F_PEQUE, fill=C_SUB_FG,
                      text=f"RMSE por liga (n≥30; barras, {filas} de "
                           f"{len(ligas)})")
        c.create_text(cx, 14, anchor="center", font=F_PEQUE, fill=C_SUB_FG,
                      text="Sesgo ± IC95% (puntos, eje centrado en 0)")
        for i, (l, n, rmse, sesgo, ic) in enumerate(ligas[:filas]):
            y = y0 + i * rh + rh / 2
            c.create_text(x_lab, y, anchor="w", font=F_PEQUE,
                          text=l[:26])
            w = (rmse / max_rmse) * (x_bar1 - x_bar0)
            c.create_rectangle(x_bar0, y - rh * 0.28, x_bar0 + w, y + rh * 0.28,
                               fill="#64B5F6", outline="")
            c.create_text(x_bar0 + w + 4, y, anchor="w", font=F_PEQUE,
                          text=f"{rmse:.1f}")
            # sesgo con IC (eje centrado)
            escala = (x_ses1 - x_ses0) / 2 / max_ses
            xs = cx + sesgo * escala
            c.create_line(cx, y - rh * 0.4, cx, y + rh * 0.4, fill="#ECEFF1")
            c.create_line(cx + (sesgo - ic) * escala, y, cx + (sesgo + ic) * escala,
                          y, fill="#90A4AE")
            colr = C_ROJO_FG if sesgo < 0 else C_VERDE
            c.create_oval(xs - 3, y - 3, xs + 3, y + 3, fill=colr, outline="")
            c.create_text(W - 60, y, anchor="w", font=F_PEQUE,
                          text=f"n={n}")
        if len(ligas) > filas:
            c.create_text(8, H - 12, anchor="w", font=F_PEQUE, fill=C_SUB_FG,
                          text=f"(mostrando {filas} de {len(ligas)} ligas, "
                               f"ordenadas por RMSE; {cl['ligas_cortas']} "
                               f"ligas con n<30 excluidas)")

    def _dibujar_mes(self, c) -> None:
        import math
        c.delete("all")
        cl = self._calc
        W, H = c.winfo_width(), c.winfo_height()
        meses = cl["meses"] if cl else []
        if not meses or W < 80 or H < 80:
            return
        x0, x1 = 60, W - 20
        n = len(meses)
        paso_x = (x1 - x0) / max(1, n - 1)
        # --- RMSE por mes (arriba) ---
        yt0, yt1 = H * 0.06, H * 0.42
        rmax = max(m[2] for m in meses) * 1.15 or 1
        py = lambda v: yt1 - (v / rmax) * (yt1 - yt0)
        c.create_text(x0, yt0 - 8, anchor="w", font=F_PEQUE, fill=C_SUB_FG,
                      text=f"RMSE por mes (0 → {rmax:.0f})")
        c.create_line(x0, yt1, x1, yt1, fill="#90A4AE")
        pts = []
        for i, (mes, _nn, rmse, _s, _ic) in enumerate(meses):
            x = x0 + i * paso_x
            pts.extend((x, py(rmse)))
        if len(pts) >= 4:
            c.create_line(*pts, fill="#1565C0", width=2)
        for i, (mes, _nn, rmse, _s, _ic) in enumerate(meses):
            x = x0 + i * paso_x
            c.create_oval(x - 3, py(rmse) - 3, x + 3, py(rmse) + 3,
                          fill="#1565C0", outline="")
            c.create_text(x, py(rmse) - 10, text=f"{rmse:.0f}",
                          font=F_PEQUE, fill="#1565C0")
        # --- sesgo por mes (abajo, con IC) ---
        yb0, yb1 = H * 0.58, H * 0.86
        smax = max(abs(m[3]) + m[4] for m in meses) * 1.2 or 1
        cero = (yb0 + yb1) / 2
        pyb = lambda v: cero - (v / smax) * ((yb1 - yb0) / 2)
        c.create_text(x0, yb0 - 12, anchor="w", font=F_PEQUE, fill=C_SUB_FG,
                      text="Sesgo por mes ± IC95%")
        c.create_line(x0, cero, x1, cero, fill="#90A4AE")
        for i, (mes, _nn, _r, sesgo, ic) in enumerate(meses):
            x = x0 + i * paso_x
            c.create_rectangle(x - 7, cero, x + 7, pyb(sesgo),
                               fill="#FFB74D", outline="")
            c.create_line(x, pyb(sesgo - ic), x, pyb(sesgo + ic), fill="#90A4AE")
            c.create_text(x, yb1 + 12, text=mes, font=F_PEQUE, fill=C_SUB_FG)
        c.create_text(x0 - 44, (yb0 + yb1) // 2, text="sesgo", angle=90,
                      font=F_PEQUE, fill=C_SUB_FG)

    def _dibujar_cal(self, c) -> None:
        c.delete("all")
        cl = self._calc
        W, H = c.winfo_width(), c.winfo_height()
        if not cl or W < 80 or H < 80:
            return
        cal = cl["cal"]
        lado = max(60, min(W - 90, H - 90))
        x0, y0 = 62, H - 52
        x1, y1 = x0 + lado, y0 - lado
        px = lambda v: x0 + (v - 0.5) / 0.5 * (x1 - x0)
        py = lambda v: y0 - (v - 0.5) / 0.5 * (y0 - y1)
        for v in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
            c.create_line(px(v), y0, px(v), y1, fill="#ECEFF1")
            c.create_line(x0, py(v), x1, py(v), fill="#ECEFF1")
            c.create_text(px(v), y0 + 11, text=f"{v:.1f}", font=F_PEQUE,
                          fill=C_SUB_FG)
            c.create_text(x0 - 16, py(v), text=f"{v:.1f}", font=F_PEQUE,
                          fill=C_SUB_FG)
        c.create_line(px(0.5), py(0.5), px(1.0), py(1.0), fill="#90A4AE",
                      dash=(5, 3))
        pts = []
        for pp, fr, _n in cal["bins"]:
            pts.extend((px(pp), py(fr)))
        if len(pts) >= 4:
            c.create_line(*pts, fill="#1565C0", width=2)
        for pp, fr, n in cal["bins"]:
            r = min(9, 3 + n ** 0.5 / 18)
            c.create_oval(px(pp) - r, py(fr) - r, px(pp) + r, py(fr) + r,
                          fill="#1565C0", outline="")
        c.create_text((x0 + x1) // 2, H - 14,
                      text="Probabilidad predicha Φ(|Δ|/σ)  (línea proxy "
                           "2·(q1+q2))", font=F_LABEL)
        c.create_text(14, (y0 + y1) // 2, text="Frecuencia real del lado "
                      "elegido", angle=90, font=F_LABEL)
        c.create_text(x1 + 10, y1, anchor="nw", font=F_PEQUE, fill=C_SUB_FG,
                      text=(f"n = {cal['n']:,}\nECE = {cal['ece'] * 100:.2f} pp\n"
                            f"Brier = {cal['brier']:.4f}\n(el tamaño del punto "
                            f"= n del bin)"))

    def _dibujar_hist(self, c) -> None:
        import math
        c.delete("all")
        cl = self._calc
        W, H = c.winfo_width(), c.winfo_height()
        if not cl or W < 80 or H < 80:
            return
        hist = cl["hist"]
        bins = [(k, v) for k, v in hist["bins"] if -60 <= k <= 60]
        if not bins:
            return
        x0, x1, y0, y1 = 56, W - 16, H - 52, 26
        vmax = max(v for _k, v in bins) or 1
        lo, hi = -62, 62
        ancho = 2
        px = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
        py = lambda v: y0 - (v / (vmax * 1.1)) * (y0 - y1)
        for v in range(-60, 61, 20):
            c.create_line(px(v), y0, px(v), y1, fill="#ECEFF1")
            c.create_text(px(v), y0 + 11, text=f"{v:+d}", font=F_PEQUE,
                          fill=C_SUB_FG)
        sd = hist["sd"]
        n = hist["n"]
        # curva normal con la σ REAL de los residuos (misma área que el hist.)
        curva = []
        paso = 1.0
        xv = lo + 1
        while xv <= hi:
            y = (n * ancho / (sd * math.sqrt(2 * math.pi))
                 * math.exp(-0.5 * (xv / sd) ** 2))
            curva.extend((px(xv), py(y)))
            xv += paso
        for k, v in bins:
            c.create_rectangle(px(k - 1), py(v), px(k + 1), py(0),
                               fill="#B0BEC5", outline="")
        if len(curva) >= 4:
            c.create_line(*curva, fill="#C62828", width=2)
        c.create_line(x0, py(0), x1, py(0), fill="#90A4AE")
        c.create_text((x0 + x1) // 2, H - 14,
                      text="Residuo (total final − pred), bins de 2 pts",
                      font=F_LABEL)
        c.create_text(14, (y0 + y1) // 2, text="Frecuencia", angle=90,
                      font=F_LABEL)
        c.create_text(x1 - 6, y1 - 4, anchor="ne", font=F_PEQUE, fill=C_SUB_FG,
                      text=(f"n = {n:,}  ·  media {hist['media']:+.1f}  ·  "
                            f"σ real {sd:.1f}  ·  σ que usa Φ (media "
                            f"modelos) {hist['sigma_ref']:.1f}\n"
                            f"P(|e|>2σ) real: {hist['p2'] * 100:.1f}%  "
                            f"(normal: 4.6%)  —  curva roja: normal con la "
                            f"σ real"))


# ===========================================================================
#  Ventana principal
# ===========================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Predicciones en vivo — Excel → Python + API BetPlay + Paperbet")
        self._cerrando = False
        self.ajustar_a_pantalla()
        self.excel = RUTA_DEFECTO
        self.modelos: dict[str, Modelo] = {}
        # modelos MCO entrenados con partidos reales de cada liga (ver modelos.py)
        self.modelos_ligas: dict[str, Modelo] = {}
        self.db_ok = False
        self.db_error = ""

        # Calibración de probabilidades (config.json -> "calibracion"): el
        # factor agranda/encoje el RMSE con el que se calcula Φ(Δ/RMSE). Lo
        # fija `calibracion.py --aplicar` tras medir la fiabilidad real.
        try:
            cal = base_datos.cargar_config().get("calibracion", {}) or {}
            motor.FACTOR_RMSE = max(0.1, float(cal.get("factor_rmse", 1.0)))
        except Exception:   # noqa: BLE001
            pass

        self._menu()
        self._cuerpo()
        self._barra_estado()

        self.cargar_datos()
        self._iniciar_base_datos()

    def ajustar_a_pantalla(self) -> None:
        """
        Ajusta la ventana al monitor donde se esté abriendo.

        El objetivo es que NUNCA sea más alta (ni más ancha) que la pantalla:
        se mide el tamaño disponible y la ventana se centra con ese tamaño.
        El contenido, si no cabe, se estira o se desplaza en vertical (ver
        `Pestana`, que lleva barra de desplazamiento cuando hace falta).
        """
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        # margen: barra de tareas de Windows + marco/título de la ventana
        disp_ancho, disp_alto = sw - 20, sh - 110
        ancho = max(720, min(1240, disp_ancho))
        alto = max(480, min(900, disp_alto))
        x = max(0, (sw - ancho) // 2)
        y = max(0, (sh - alto - 70) // 2)
        self.geometry(f"{ancho}x{alto}+{x}+{y}")
        self.minsize(min(720, ancho), min(480, alto))
        self.resizable(True, True)

    def destroy(self) -> None:  # noqa: D102
        """Cierra limpiando los timers del sondeo (si no, Tk avisa al cerrar)."""
        self._cerrando = True
        v = getattr(self, "pestana_vivo", None)
        if v is not None:
            v._auto_activo = False
            if getattr(v, "_job_auto", None):
                try:
                    self.after_cancel(v._job_auto)
                except Exception:   # noqa: BLE001
                    pass
                v._job_auto = None
        try:
            super().destroy()
        except tk.TclError:
            # al cerrar, los widgets dejan de existir mientras se desmontan
            pass

    # ---------------------------------------------------------------- menú
    def _menu(self) -> None:
        menubar = tk.Menu(self)
        archivo = tk.Menu(menubar, tearoff=0)
        archivo.add_command(label="Abrir Excel de datos...", command=self.abrir_excel)
        archivo.add_command(label="Recargar datos", command=self.cargar_datos)
        archivo.add_separator()
        archivo.add_command(label="Salir", command=self.destroy)
        menubar.add_cascade(label="Archivo", menu=archivo)

        bd = tk.Menu(menubar, tearoff=0)
        bd.add_command(label="Probar conexión", command=self.probar_bd)
        bd.add_command(label="Configurar PostgreSQL...", command=self.configurar_bd)
        bd.add_command(label="Ver esquema SQL...", command=self.ver_esquema)
        menubar.add_cascade(label="Base de datos", menu=bd)

        mod = tk.Menu(menubar, tearoff=0)
        mod.add_command(label="Ver modelos por liga...", command=self.ver_modelos)
        mod.add_command(label="(Re)entrenar modelos con las muestras guardadas",
                        command=self.entrenar_modelos)
        mod.add_command(label="Descargar histórico oficial (Euroliga/EuroCup/NBA...)",
                        command=self.descargar_historico)
        menubar.add_cascade(label="Modelos", menu=mod)

        ayuda = tk.Menu(menubar, tearoff=0)
        ayuda.add_command(label="Acerca de", command=self.acerca_de)
        menubar.add_cascade(label="Ayuda", menu=ayuda)
        self.config(menu=menubar)

    # -------------------------------------------------------------- cuerpo
    def _cuerpo(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 4))

        self.pestanas = {
            "EUROLIGA": Pestana(self.notebook, "EUROLIGA", self),
            "NBA": Pestana(self.notebook, "NBA", self),
        }
        self.pestana_vivo = PestanaVivo(self.notebook, self)
        self.pestana_apuestas = PestanaApuestas(self.notebook, self)
        self.pestana_grafico = PestanaGrafico(self.notebook, self)

        self.notebook.add(self.pestanas["EUROLIGA"], text="  EUROLIGA  ")
        self.notebook.add(self.pestanas["NBA"], text="  NBA  ")
        self.notebook.add(self.pestana_vivo, text="  ⚡ EN VIVO  ")
        self.notebook.add(self.pestana_apuestas, text="  💰 APUESTAS  ")
        self.notebook.add(self.pestana_grafico, text="  📈 GRÁFICO  ")
        # al entrar en GRÁFICO se recargan los datos (picks nuevos del día)
        self.notebook.bind("<<NotebookTabChanged>>", self._al_cambiar_pestana)

    def _al_cambiar_pestana(self, _e=None) -> None:
        try:
            actual = self.notebook.nametowidget(self.notebook.select())
        except Exception:   # noqa: BLE001
            return
        if actual is self.pestana_grafico:
            self.pestana_grafico.refrescar()

    def _barra_estado(self) -> None:
        self.estado = tk.Label(self, text="", anchor="w", font=("Calibri", 9),
                               fg=C_SUB_FG, padx=10, pady=4)
        self.estado.pack(fill="x", side="bottom")

    # ------------------------------------------------------------- hilos
    def en_hilo(self, tarea, al_terminar) -> None:
        """
        Ejecuta `tarea()` en un hilo de fondo y llama a `al_terminar(resultado)`
        en el hilo principal de Tk.

        Usa un sondeo con `after` en vez de llamar a `after()` desde el hilo,
        porque Tk rechaza esas llamadas si mainloop todavía no arrancó.
        Si `tarea()` lanza una excepción, `al_terminar` recibe la excepción.
        """
        caja = {"hecho": False, "resultado": None, "error": None}

        def trabajo():
            try:
                caja["resultado"] = tarea()
            except Exception as exc:  # noqa: BLE001
                caja["error"] = exc
            caja["hecho"] = True

        threading.Thread(target=trabajo, daemon=True).start()

        def comprobar():
            if self._cerrando:      # la ventana ya no existe: no tocar nada
                return
            if not caja["hecho"]:
                self.after(150, comprobar)
                return
            al_terminar(caja["error"] if caja["error"] is not None else caja["resultado"])

        self.after(150, comprobar)

    # ------------------------------------------------------------- acciones
    def abrir_excel(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Selecciona el Excel de datos",
            filetypes=[("Libro de Excel", "*.xlsx"), ("Todos los archivos", "*.*")])
        if ruta:
            self.excel = ruta
            self.cargar_datos()

    def cargar_datos(self) -> None:
        if not os.path.exists(self.excel):
            self.estado.config(text=f"Sin datos: {self.excel}", fg=C_ROJO_FG)
            return
        ok = True
        for liga, pestana in self.pestanas.items():
            modelo = motor.cargar_modelo(self.excel, liga)
            self.modelos[liga] = modelo
            pestana.fijar_modelo(modelo)
            if modelo.errores:
                ok = False
        total = sum(m.n for m in self.modelos.values())
        if ok:
            self.estado.config(
                text=f"Datos: {os.path.basename(self.excel)}  |  {total} partidos cargados",
                fg=C_SUB_FG)

    # ------------------------------------------------------------ modelos
    def cargar_modelos_ligas(self) -> None:
        """Carga los modelos MCO entrenados por liga desde PostgreSQL."""
        import modelos as mod_modelos

        def al_terminar(res):
            if isinstance(res, Exception):
                self.estado.config(
                    text=(self.estado.cget("text") or "") +
                         f"  | ⚠ modelos: {str(res)[:80]}", fg=C_ROJO_FG)
                return
            self.modelos_ligas = res
            self.pestana_vivo.actualizar_modelos_liga()
            if res:
                self.estado.config(
                    text=(self.estado.cget("text") or "") +
                         f"  | {len(res)} modelos por liga", fg=C_SUB_FG)

        self.en_hilo(mod_modelos.cargar_modelos, al_terminar)

    def modelo_para_partido(self, p, seleccion: str) -> tuple[Modelo, str]:
        """
        Modelo que corresponde a un partido, según el desplegable de la pestaña.

        Con "AUTO (por liga)" se usa el modelo entrenado con partidos reales de
        la liga del partido; si esa liga todavía no tiene modelo suficiente, se
        cae al modelo del Excel (EUROLIGA/NBA) y se avisa de que no está
        calibrado.
        """
        if seleccion != "AUTO":
            m = self.modelos.get(seleccion)
            if m is None and seleccion in self.modelos_ligas:
                m = self.modelos_ligas[seleccion]
            return (m or Modelo(liga=seleccion, origen=""), f"Excel/{seleccion}")

        clave = getattr(p, "clave_liga", "") or ""
        m = self.modelos_ligas.get(clave)
        if m is not None and m.n:
            return m, f"por liga ({clave})"
        # sin modelo propio: el más parecido del Excel, pero avisando
        base = "NBA" if "nba" in clave or "wnba" in clave else "EUROLIGA"
        return (self.modelos.get(base) or Modelo(liga=base, origen=""),
                f"Excel/{base} (sin datos de {clave or 'la liga'})")

    def ver_modelos(self) -> None:
        import modelos as mod_modelos

        def tarea():
            return mod_modelos.cargar_modelos(), mod_modelos.contar_muestras()

        def al_terminar(res):
            if isinstance(res, Exception):
                messagebox.showerror("No se pudieron leer los modelos", str(res))
                return
            modelos, muestras = res
            self._ventana_modelos(modelos, muestras)

        self.en_hilo(tarea, al_terminar)

    def _ventana_modelos(self, modelos, muestras) -> None:
        v = tk.Toplevel(self)
        v.title("Modelos por liga")
        v.geometry("760x460")
        tk.Label(v, text="Modelos MCO entrenados con partidos reales de cada liga",
                 font=F_LABEL, anchor="w").pack(fill="x", padx=10, pady=(10, 4))
        cols = ("liga", "muestras", "n", "r2", "rmse", "media", "fecha")
        titulos = {"liga": "Liga (termKey)", "muestras": "Muestras", "n": "N modelo",
                   "r2": "R²", "rmse": "RMSE test", "media": "Media", "fecha": "Último"}
        anchos = {"liga": 150, "muestras": 85, "n": 70, "r2": 70, "rmse": 95,
                  "media": 70, "fecha": 140}
        tree = ttk.Treeview(v, columns=cols, show="headings", height=18)
        for c in cols:
            tree.heading(c, text=titulos[c])
            tree.column(c, width=anchos[c], anchor="w" if c == "liga" else "center")
        tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        for liga in sorted(set(muestras) | set(modelos)):
            m = modelos.get(liga)
            tree.insert("", "end", values=(
                liga, muestras.get(liga, 0), m.n if m else "—",
                f"{m.r2_dos:.3f}" if m else "—",
                f"{m.rmse_test_dos:.1f}" if m else "—",
                f"{m.media_total:.1f}" if m else "—",
                motor.fecha_excel_a_texto(m.fecha_max) if m and m.fecha_max else "—",
            ))
        tk.Label(v, text="Las ligas sin modelo necesitan más partidos: se entrenan solas "
                        "cuando la pestaña EN VIVO registra sus resultados.",
                 font=F_PEQUE, fg=C_SUB_FG, anchor="w").pack(fill="x", padx=10, pady=(0, 8))

    def entrenar_modelos(self) -> None:
        import modelos as mod_modelos

        self.estado.config(text="Entrenando modelos por liga…", fg=C_AZUL)
        self.update_idletasks()

        def al_terminar(res):
            if isinstance(res, Exception):
                messagebox.showerror("No se pudo entrenar", str(res))
                self.estado.config(text="Entrenamiento fallido", fg=C_ROJO_FG)
                return
            self.modelos_ligas = res
            self.pestana_vivo.actualizar_modelos_liga()
            self.estado.config(
                text=f"{len(res)} modelos entrenados y guardados en PostgreSQL",
                fg=C_VERDE)
            self.ver_modelos()

        self.en_hilo(mod_modelos.entrenar_todo, al_terminar)

    def descargar_historico(self) -> None:
        import historico

        if not messagebox.askyesno(
                "Descargar histórico",
                "Descarga partidos ACABADOS con sus cuartos desde la API oficial\n"
                "de Euroliga (Euroliga + EuroCup) y de ESPN (NBA, WNBA, NBL).\n\n"
                "Son unas 600 peticiones: tardará unos minutos.\n"
                "¿Continuar?"):
            return
        self.estado.config(text="Descargando histórico (Euroliga/EuroCup/NBA…)…",
                           fg=C_AZUL)
        self.update_idletasks()

        def al_terminar(res):
            if isinstance(res, Exception):
                messagebox.showerror("No se pudo descargar", str(res))
                self.estado.config(text="Descarga fallida", fg=C_ROJO_FG)
                return
            muestras, guardadas = res
            self.estado.config(
                text=f"Histórico: {len(muestras)} partidos descargados, "
                     f"{guardadas} muestras nuevas", fg=C_VERDE)
            self.entrenar_modelos()

        def tarea():
            import modelos as mod_modelos
            muestras = historico.descargartodo(
                n_temporadas=2,
                al_avanzar=lambda f, n: self.estado.config(
                    text=f"Histórico: {f} → {n if n is not None else ''} partidos…",
                    fg=C_AZUL))
            guardadas = mod_modelos.guardar_muestras(muestras)
            return muestras, guardadas

        self.en_hilo(tarea, al_terminar)

    def recolectar_resultados(self, partidos) -> int:
        """
        Guarda en PostgreSQL los partidos acabados con sus cuartos, para que las
        ligas sin histórico oficial (ACB, BBL, Portugal, Chile...) vayan
        entrenando su modelo solas. Devuelve las muestras nuevas.
        """
        from datetime import date
        import modelos as mod_modelos
        muestras = []
        for p in partidos:
            if not p.finalizado or p.q1 is None or p.q2 is None:
                continue
            if p.q3 is None or p.q4 is None or not p.total_parcial:
                continue
            if p.total_parcial != p.q1 + p.q2 + p.q3 + p.q4:
                continue      # aún no se han registrado todos los cuartos
            muestras.append(historico.Muestra(
                liga=p.clave_liga or "sin_liga",
                fecha=date.fromisoformat(p.inicio[:10]) if p.inicio else None,
                q1=float(p.q1), q2=float(p.q2), q3=float(p.q3), q4=float(p.q4),
                total=float(p.total_parcial),
                partido=p.nombre, fuente="app:vivo"))
        if not muestras:
            return 0
        return mod_modelos.guardar_muestras(muestras)

    # ---------------------------------------------------------- base datos
    def _iniciar_base_datos(self) -> None:
        def tarea():
            base_datos.crear_esquema()
            return base_datos.probar_conexion()

        def al_terminar(res):
            if isinstance(res, Exception):
                self.db_ok, self.db_error = False, str(res)
            else:
                self.db_ok, self.db_error = res
            if self.db_ok:
                self.estado.config(
                    text=self.estado.cget("text") + f"  |  ✅ {base_datos.descripcionConexion()}",
                    fg=C_SUB_FG)
            else:
                self.estado.config(
                    text=(self.estado.cget("text") or "") +
                         f"  |  ⚠ PostgreSQL: {self.db_error[:160]}", fg=C_ROJO_FG)
            self.refrescar_apuestas()

        self.en_hilo(tarea, al_terminar)

    def probar_bd(self) -> None:
        ok, msg = base_datos.probar_conexion()
        self.db_ok = ok
        self.db_error = msg if not ok else ""
        if ok:
            try:
                base_datos.crear_esquema()
            except base_datos.ErrorBaseDatos as exc:
                ok, msg = False, str(exc)
        (messagebox.showinfo if ok else messagebox.showerror)("PostgreSQL", msg)
        self.refrescar_apuestas()

    def configurar_bd(self) -> None:
        cfg = base_datos.cargar_config()
        p = cfg.get("postgres", {})
        win = tk.Toplevel(self)
        win.title("Configurar PostgreSQL")
        win.geometry("420x260")
        win.resizable(False, False)
        campos = [("host", "Host"), ("port", "Puerto"), ("dbname", "Base de datos"),
                  ("user", "Usuario"), ("password", "Contraseña")]
        entradas = {}
        for i, (clave, texto) in enumerate(campos):
            tk.Label(win, text=texto + ":", font=F_LABEL).grid(
                row=i, column=0, sticky="w", padx=12, pady=6)
            var = tk.StringVar(value=str(p.get(clave, "")))
            e = tk.Entry(win, textvariable=var, font=F_TEXTO, width=26,
                         show="*" if clave == "password" else "")
            e.grid(row=i, column=1, padx=12, pady=6)
            entradas[clave] = var

        def guardar():
            nuevo = dict(cfg)
            nuevo["postgres"] = {
                "host": entradas["host"].get().strip(),
                "port": int(entradas["port"].get() or 5432),
                "dbname": entradas["dbname"].get().strip(),
                "user": entradas["user"].get().strip(),
                "password": entradas["password"].get(),
            }
            base_datos.guardar_config(nuevo)
            win.destroy()
            self.probar_bd()

        tk.Button(win, text="Guardar y probar", font=F_LABEL, width=18,
                  command=guardar).grid(row=len(campos), column=0, columnspan=2, pady=14)

    def ver_esquema(self) -> None:
        win = tk.Toplevel(self)
        win.title("Esquema SQL")
        win.geometry("700x480")
        txt = tk.Text(win, font=("Consolas", 10), wrap="none")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", base_datos.ESQUEMA.strip())
        txt.config(state="disabled")

    def refrescar_apuestas(self) -> None:
        try:
            self.pestana_apuestas.refrescar()
        except tk.TclError:
            pass

    def acerca_de(self) -> None:
        messagebox.showinfo(
            "Acerca de",
            "Predicciones en vivo\n\n"
            "1) Motor de predicción: regresión MCO idéntica al Excel (LINEST/FORECAST).\n"
            "2) EN VIVO: polling a la API de BetPlay (Kambi) cada N segundos,\n"
            "   flujo 1Q + 2Q y veredicto OVER/UNDER.\n"
            "3) APUESTAS: paperbet simulado y persistido en PostgreSQL.\n\n"
            "Solo simulación: no se envían apuestas reales.",
        )


def main() -> int:
    app = App()
    # descubre las ligas una vez arrancado el bucle de eventos (nunca desde un hilo)
    app.after(300, app.pestana_vivo.cargar_ligas)
    app.after(800, app.cargar_modelos_ligas)
    # en cuanto se abre ya está vigiando los descansos y refrescando cada 5 s
    app.after(1200, app.pestana_vivo.arrancar_automatico)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
