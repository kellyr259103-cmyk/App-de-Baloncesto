# -*- coding: utf-8 -*-
"""
motor.py — Motor de cálculo de predicciones.

Refactorización a Python de la hoja PREDICCION del archivo:
    "Predicciones en vivo basque.xlsx"

El motor:
  1. Lee las hojas EUROLIGA_DATOS / NBA_DATOS del propio Excel (sin dependencias externas).
  2. Reconstruye exactamente los modelos que Excel arma con LINEST/FORECAST:
        - Modelo 2 variables : TOTAL = b0 + b1*(1Q) + b2*(2Q)      -> INDEX(LINEST(G, C:D, ...),1,n)
        - Modelo 1 variable  : TOTAL = a0 + a1*(1Q)                -> FORECAST(1Q, G, C)
        - Referencia naive   : 1Q * 4
  3. Devuelve el mismo veredicto OVER / UNDER que el Excel.

Orden de los coeficientes de LINEST (verificado empíricamente con el propio Excel):
    INDEX(LINEST(...),1,1) -> coeficiente de la ULTIMA columna de X  (2Q / 2C)
    INDEX(LINEST(...),1,2) -> coeficiente de la PRIMERA columna de X (1Q / 1C)
    INDEX(LINEST(...),1,3) -> termino independiente (interseccion)
    INDEX(LINEST(...),3,1) -> R^2
"""

from __future__ import annotations

import os
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Ruta por defecto del libro original (prototipo en Excel)
EXCEL_POR_DEFECTO = os.path.join(
    os.path.expanduser("~"), "OneDrive", "Documentos", "Predicciones en vivo basque.xlsx"
)

# Hojas del libro y sus configuraciones
HOJAS = {
    "EUROLIGA": "EUROLIGA_DATOS",
    "NBA": "NBA_DATOS",
}

# Valores fijados a mano en el Excel (celdas B16:B18 y F16:F18).
# Son los "RMSE de test" que el autor pegó desde su validación externa.
RMSE_TEST = {
    "EUROLIGA": {"dos": 13.1, "uno": 15.3, "naive": 25.5},
    "NBA": {"dos": 14.7, "uno": 17.2, "naive": 28.6},
}


# --------------------------------------------------------------------------- #
#  Lectura del .xlsx (stdlib: zipfile + ElementTree)                           #
# --------------------------------------------------------------------------- #
def _leer_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    out: list[str] = []
    for si in root.findall(f"{NS_MAIN}si"):
        # <si><t>texto</t></si>  o  <si><r><t>..</t></r><r>..</r></si>
        t = si.find(f"{NS_MAIN}t")
        if t is not None:
            out.append(t.text or "")
            continue
        out.append("".join((r.find(f"{NS_MAIN}t").text or "") for r in si.findall(f"{NS_MAIN}r")))
    return out


def _mapa_hojas(zf: zipfile.ZipFile) -> dict[str, str]:
    """Nombre de hoja -> ruta interna de su XML (ej. 'EUROLIGA_DATOS' -> 'xl/worksheets/sheet4.xml')."""
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_a_ruta = {
        rel.get("Id"): rel.get("Target")
        for rel in rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }
    mapa: dict[str, str] = {}
    for sheet in wb.find(f"{NS_MAIN}sheets").findall(f"{NS_MAIN}sheet"):
        nombre = sheet.get("name")
        rid = sheet.get(f"{NS_REL}id")
        objetivo = rid_a_ruta[rid]
        if objetivo.startswith("/"):
            objetivo = objetivo[1:]
        elif not objetivo.startswith("xl/"):
            objetivo = "xl/" + objetivo
        mapa[nombre] = objetivo
    return mapa


def _valor_celda(celda, strings: list[str]):
    t = celda.get("t")
    if t == "inlineStr":
        is_el = celda.find(f"{NS_MAIN}is")
        return "".join((x.text or "") for x in is_el.iter(f"{NS_MAIN}t")) if is_el is not None else None
    v = celda.find(f"{NS_MAIN}v")
    if v is None or v.text is None:
        return None
    if t == "s":
        return strings[int(v.text)]
    if t == "b":
        return v.text == "1"
    if t in ("str", "e"):
        return v.text
    try:
        return float(v.text)
    except ValueError:
        return v.text


def leer_hoja(excel: str, nombre_hoja: str) -> dict[int, dict[str, object]]:
    """
    Devuelve {número_de_fila: {columna: valor}} con las celdas de la hoja indicada.
    """
    with zipfile.ZipFile(excel) as zf:
        strings = _leer_shared_strings(zf)
        ruta = _mapa_hojas(zf)[nombre_hoja]
        root = ET.fromstring(zf.read(ruta))

    filas: dict[int, dict[str, object]] = {}
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        return filas
    for fila in sheet_data.findall(f"{NS_MAIN}row"):
        n = int(fila.get("r"))
        celdas: dict[str, object] = {}
        for c in fila.findall(f"{NS_MAIN}c"):
            ref = c.get("r")
            col = "".join(ch for ch in ref if ch.isalpha())
            val = _valor_celda(c, strings)
            if val is not None:
                celdas[col] = val
        if celdas:
            filas[n] = celdas
    return filas


# --------------------------------------------------------------------------- #
#  Álgebra: MCO igual que LINEST                                               #
# --------------------------------------------------------------------------- #
def _resolver_3x3(a: list[list[float]], b: list[float]) -> tuple[float, float, float]:
    """Eliminación gaussiana con pivoteo parcial (ecuaciones normales 3x3)."""
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    n = 3
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(m[i][k]))
        m[k], m[p] = m[p], m[k]
        for i in range(k + 1, n):
            f = m[i][k] / m[k][k]
            for j in range(k, n + 1):
                m[i][j] -= f * m[k][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / m[i][i]
    return x[0], x[1], x[2]


# --------------------------------------------------------------------------- #
#  Modelo                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class Modelo:
    liga: str
    origen: str
    n: int = 0
    media_total: float = 0.0
    media_1q: float = 0.0
    media_2q: float = 0.0
    # Modelo 2 variables: TOTAL = b0 + b1*1Q + b2*2Q
    b0: float = 0.0
    b1: float = 0.0
    b2: float = 0.0
    # Modelo 1 variable : TOTAL = a0 + a1*1Q
    a0: float = 0.0
    a1: float = 0.0
    r2_dos: float = 0.0
    r2_uno: float = 0.0
    rmse_dos: float = 0.0
    rmse_uno: float = 0.0
    rmse_naive: float = 0.0
    # RMSE de test fijados en el Excel
    rmse_test_dos: float = 0.0
    rmse_test_uno: float = 0.0
    rmse_test_naive: float = 0.0
    fecha_min: Optional[float] = None
    fecha_max: Optional[float] = None
    errores: list[str] = field(default_factory=list)

    # --- predicciones ------------------------------------------------------ #
    def predecir_dos(self, q1: float, q2: float) -> float:
        """Equivalente a: b2*2Q + b1*1Q + b0  (orden LINEST del Excel)."""
        return self.b0 + self.b1 * q1 + self.b2 * q2

    def predecir_uno(self, q1: float) -> float:
        """Equivalente a FORECAST(1Q, G, C)."""
        return self.a0 + self.a1 * q1

    def naive(self, q1: float) -> float:
        """Equivalente a 1Q * 4."""
        return q1 * 4.0

    # --- información ------------------------------------------------------- #
    @property
    def mejora_error(self) -> float:
        """% en que baja el error al añadir el 2Q (fila 21 del Excel)."""
        if not self.rmse_test_uno:
            return 0.0
        return (self.rmse_test_uno - self.rmse_test_dos) / self.rmse_test_uno * 100.0

    @property
    def titulo(self) -> str:
        if self.n:
            return (
                f"{self.n} partidos reales  |  R2(1Q+2Q)={self.r2_dos:.3f}  |  "
                f"RMSE test {self.rmse_test_dos} pts"
            )
        return "sin datos"


def _ajustar(datos: list[tuple[float, float, float]], liga: str, origen: str) -> Modelo:
    m = Modelo(liga=liga, origen=origen, n=len(datos))
    if not datos:
        m.errores.append(f"No hay partidos válidos en la hoja {HOJAS.get(liga, liga)}.")
        return m

    n = float(len(datos))
    s1 = s2 = sy = 0.0
    s11 = s22 = s12 = s1y = s2y = syy = 0.0
    for x1, x2, y in datos:
        s1 += x1
        s2 += x2
        sy += y
        s11 += x1 * x1
        s22 += x2 * x2
        s12 += x1 * x2
        s1y += x1 * y
        s2y += x2 * y
        syy += y * y

    m.media_1q = s1 / n
    m.media_2q = s2 / n
    m.media_total = sy / n

    # ---- Modelo 2 variables (LINEST con constante) ------------------------
    m.b1, m.b2, m.b0 = _resolver_3x3(
        [[s11, s12, s1], [s12, s22, s2], [s1, s2, n]],
        [s1y, s2y, sy],
    )

    # ---- Modelo 1 variable (FORECAST) -------------------------------------
    mx, my = m.media_1q, m.media_total
    sxx = s11 - n * mx * mx
    sxy = s1y - n * mx * my
    m.a1 = sxy / sxx
    m.a0 = my - m.a1 * mx

    # ---- R^2 y RMSE -------------------------------------------------------
    sst = syy - n * my * my          # suma total de cuadrados (centrada)
    sse = 0.0
    sse1 = 0.0
    sse4 = 0.0
    for x1, x2, y in datos:
        sse += (y - (m.b0 + m.b1 * x1 + m.b2 * x2)) ** 2
        sse1 += (y - (m.a0 + m.a1 * x1)) ** 2
        sse4 += (y - x1 * 4.0) ** 2
    m.r2_dos = 1.0 - sse / sst
    m.r2_uno = 1.0 - sse1 / sst
    m.rmse_dos = (sse / n) ** 0.5
    m.rmse_uno = (sse1 / n) ** 0.5
    m.rmse_naive = (sse4 / n) ** 0.5

    # ---- RMSE de test fijados en el Excel --------------------------------
    t = RMSE_TEST.get(liga, {})
    m.rmse_test_dos = t.get("dos", m.rmse_dos)
    m.rmse_test_uno = t.get("uno", m.rmse_uno)
    m.rmse_test_naive = t.get("naive", m.rmse_naive)
    return m


def cargar_modelo(excel: str, liga: str) -> Modelo:
    """
    Carga la base de datos de la liga desde el Excel y ajusta los modelos.

    Reproduce el rango dinámico del Excel:
        EUROLIGA_DATOS!$G$2:INDEX($G:$G, COUNTA($A:$A))
    Es decir: se toman tantas filas como celdas con contenido haya en la columna A
    (incluida la cabecera), tal como hace COUNTA.
    """
    nombre_hoja = HOJAS[liga]
    try:
        filas = leer_hoja(excel, nombre_hoja)
    except FileNotFoundError:
        m = Modelo(liga=liga, origen=excel)
        m.errores.append(f"No se encontró el archivo: {excel}")
        return m
    except zipfile.BadZipFile:
        m = Modelo(liga=liga, origen=excel)
        m.errores.append("El archivo no es un .xlsx válido (¿está dañado?).")
        return m
    except PermissionError:
        m = Modelo(liga=liga, origen=excel)
        m.errores.append(
            "No se pudo abrir el Excel (¿está abierto en Microsoft Excel?). "
            "Ciérralo y vuelve a pulsar 'Recargar datos'."
        )
        return m

    # COUNTA($A:$A) -> celdas con contenido en la columna A
    count_a = sum(1 for celdas in filas.values() if celdas.get("A") not in (None, ""))
    limite = count_a  # INDEX(...,COUNTA) => última fila a usar

    datos: list[tuple[float, float, float]] = []
    fechas: list[float] = []
    for n_fila in sorted(filas):
        if n_fila < 2 or n_fila > limite:
            continue
        c = filas[n_fila]
        x1, x2, y = c.get("C"), c.get("D"), c.get("G")
        if not all(isinstance(v, float) for v in (x1, x2, y)):
            continue  # el Excel también ignora las filas incompletas en COUNT/RSQ
        datos.append((x1, x2, y))
        f = c.get("A")
        if isinstance(f, float):
            fechas.append(f)

    modelo = _ajustar(datos, liga, excel)
    if fechas:
        modelo.fecha_min = min(fechas)
        modelo.fecha_max = max(fechas)
    return modelo


def fecha_excel_a_texto(serial: float) -> str:
    """Convierte un número de serie de Excel en dd/mm/aaaa."""
    from datetime import datetime, timedelta
    try:
        base = datetime(1899, 12, 30)
        return (base + timedelta(days=float(serial))).strftime("%d/%m/%Y")
    except Exception:
        return str(serial)


# --------------------------------------------------------------------------- #
#  Predicción completa (equivalente a la fila 5..21 de la hoja PREDICCION)     #
# --------------------------------------------------------------------------- #
@dataclass
class Resultado:
    q1: Optional[float]
    q2: Optional[float]
    linea: Optional[float]
    modelo: Modelo
    prediccion: Optional[float] = None
    solo_1q: Optional[float] = None
    naive: Optional[float] = None
    diferencia: Optional[float] = None
    veredicto: str = "faltan datos"
    modelo_usado: str = ""
    prob_over: Optional[float] = None

    @property
    def excel_valores(self) -> dict[str, object]:
        """Valores de las celdas equivalentes del Excel (para comparación)."""
        return {
            "B8/F8  PREDICCION TOTAL": self.prediccion,
            "B9/F9  solo con 1Q": self.solo_1q,
            "B10/F10 naive 1Q*4": self.naive,
            "B11/F11 menos linea": self.diferencia,
            "B12/F12 VEREDICTO": self.veredicto,
            "B14/F14 R2 1Q+2Q": self.modelo.r2_dos,
            "B15/F15 R2 solo 1Q": self.modelo.r2_uno,
            "B19/F19 partidos": self.modelo.n,
            "B20/F20 promedio": self.modelo.media_total,
        }


def _normal_cdf(z: float) -> float:
    """Función de distribución normal estándar Φ(z) (extra, no está en el Excel)."""
    import math
    return 0.5 * (1.0 + math.erf(z / 2.0 ** 0.5))


def predecir(modelo: Modelo, q1, q2, linea) -> Resultado:
    """
    Reproduce la lógica de la hoja PREDICCION:

        SI(B5="","",
            SI(B6="", B9, b2*2Q + b1*1Q + b0))
        B11 = B8 - B7
        B12 = SI(B11="","faltan datos",SI(B11>=0,"OVER","UNDER"))
    """
    r = Resultado(q1=q1, q2=q2, linea=linea, modelo=modelo)

    if modelo.n == 0:
        r.veredicto = "faltan datos"
        return r

    if q1 is None:                      # SI(B5="","" ...)
        r.veredicto = "faltan datos"
        return r

    r.naive = modelo.naive(q1)          # B10
    r.solo_1q = modelo.predecir_uno(q1)  # B9

    if q2 is None:                      # SI(B6="", B9, ...)
        r.prediccion = r.solo_1q
        r.modelo_usado = "solo 1Q"
    else:
        r.prediccion = modelo.predecir_dos(q1, q2)
        r.modelo_usado = "1Q + 2Q"

    if r.prediccion is not None and linea is not None:
        r.diferencia = r.prediccion - linea            # B11
        r.veredicto = "OVER" if r.diferencia >= 0 else "UNDER"   # B12
        sigma = modelo.rmse_test_dos if q2 is not None else modelo.rmse_test_uno
        if sigma:
            r.prob_over = _normal_cdf(r.diferencia / sigma)
    else:
        r.veredicto = "faltan datos"

    return r


# --------------------------------------------------------------------------- #
#  Utilidades de parseo de entrada del usuario                                 #
# --------------------------------------------------------------------------- #
def parsear(texto: str) -> Optional[float]:
    """Convierte lo que escribe el usuario en número. Vacío -> None (igual que en Excel)."""
    if texto is None:
        return None
    t = texto.strip().replace(",", ".")
    if t == "":
        return None
    try:
        return float(t)
    except ValueError:
        return None
