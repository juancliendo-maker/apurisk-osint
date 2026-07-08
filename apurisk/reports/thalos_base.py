"""THALOS · Plantilla Base — componentes visuales reutilizables para reportes.

Fase 1 de la Etapa 3. Módulo NUEVO e independiente: NO toca plantilla_madre.py
ni branding.py (que usan otros reportes con otra paleta). Aquí vive el sistema
visual THALOS (navy #0F3A66 + oro #D4AF37, tipografía cercana a Montserrat/Open
Sans, sin rayado zebra) para los Reportes A/B de inteligencia.

Provee:
  · Paleta y tipografía centralizadas (con fallback de fuente)
  · Portada, header y footer canónicos (línea divisora en oro)
  · Componentes A-H: recuadro ejecutivo, tabla profesional, matriz P×I,
    matriz de urgencia, gráfico de tendencia, mapa geográfico, iconografía
    numérica, chips de color, líneas divisoras.
  · construir_demo_pdf(): arma la página demo con todos los componentes.
"""
from __future__ import annotations
import math
from pathlib import Path
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage,
    Flowable, PageBreak, HRFlowable, KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, Circle, String, Rect, Line, Polygon, Wedge
from reportlab.graphics.charts.linecharts import HorizontalLineChart

_STATIC = Path(__file__).resolve().parent.parent / "static"
_FONTS = _STATIC / "fonts"
LOGO_WHITE_SVG = _STATIC / "thalos-mark-white.svg"
LOGO_PDF_SVG = _STATIC / "thalos-logo-pdf.svg"
MAPA_BASE_PNG = _STATIC / "peru-map-base.png"

# ── Paleta THALOS ────────────────────────────────────────────────────────────
NAVY       = colors.HexColor("#0F3A66")   # títulos, estructura, header/footer
ORO        = colors.HexColor("#D4AF37")   # líneas divisoras, bordes, acentos
BLANCO     = colors.HexColor("#FFFFFF")
GRIS_CUERPO = colors.HexColor("#4A4A4A")  # texto
GRIS_CLARO = colors.HexColor("#E8E8E8")   # fondos secundarios
GRIS_META  = colors.HexColor("#999999")

# Acentos por riesgo/función
ROJO_CRIT   = colors.HexColor("#C0392B")
AMBAR_ALTO  = colors.HexColor("#F59E0B")
NARANJA_MOD = colors.HexColor("#FF9800")
AMARILLO_BAJO = colors.HexColor("#FFC107")
VERDE_INFO  = colors.HexColor("#4CAF50")
PURPURA_ANALISIS = colors.HexColor("#6B5B95")
AZUL_TERRITORIO  = colors.HexColor("#2C5AA0")

RIESGO_COLORES = {
    "CRÍTICO": ROJO_CRIT, "CRITICO": ROJO_CRIT,
    "ALTO": AMBAR_ALTO, "MODERADO": NARANJA_MOD, "BAJO": AMARILLO_BAJO,
}

TAGLINE = "Strategic Intelligence for Complex Decisions"

# ── Tipografía THALOS ─────────────────────────────────────────────────────────
# Títulos = Montserrat Bold · cuerpo/metadata = Open Sans Regular.
# Cadena de fallback por si algún .ttf falta: Montserrat/OpenSans → DejaVu →
# Helvetica (built-in). reportlab embebe (subset) las TTF en el PDF → el archivo
# es autónomo y se ve igual en Adobe/Preview aunque no estén instaladas.
import logging as _logging
_log = _logging.getLogger("apurisk.thalos_base")

FONT_TITLE = "Helvetica-Bold"
FONT_BODY = "Helvetica"

# (nombre_reportlab, [candidatos .ttf en orden de preferencia])
_TITLE_CANDIDATOS = ["Montserrat-Bold.ttf", "DejaVuSans-Bold.ttf"]
_BODY_CANDIDATOS = ["OpenSans-Regular.ttf", "DejaVuSans.ttf"]


def _registrar_una(nombre: str, candidatos: list, fallback: str) -> str:
    """Registra el primer .ttf disponible bajo `nombre`; devuelve el fontName a usar."""
    for archivo in candidatos:
        ruta = _FONTS / archivo
        if ruta.exists():
            try:
                pdfmetrics.registerFont(TTFont(nombre, str(ruta)))
                _log.info("✓ %s registrada en reportlab (%s)", nombre, archivo)
                return nombre
            except Exception as e:
                _log.error("✗ Error registrando %s (%s): %s", nombre, archivo, e)
    _log.warning("⚠ %s no encontrada — fallback a %s", nombre, fallback)
    return fallback


def registrar_fuentes_thalos() -> None:
    """Registra las fuentes THALOS (Montserrat Bold + Open Sans) en reportlab.

    Idempotente: se puede llamar varias veces. Se invoca una vez al importar
    el módulo; volver a llamarla no daña nada.
    """
    global FONT_TITLE, FONT_BODY
    FONT_TITLE = _registrar_una("Montserrat-Bold", _TITLE_CANDIDATOS, "Helvetica-Bold")
    FONT_BODY = _registrar_una("OpenSans-Regular", _BODY_CANDIDATOS, "Helvetica")


registrar_fuentes_thalos()

# ── Geometría de página ──────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
MARGEN_SUP = 1.5 * inch
MARGEN_LAT = 1.0 * inch
MARGEN_INF = 1.25 * inch


def estilos() -> dict:
    return {
        "h1": ParagraphStyle("h1", fontName=FONT_TITLE, fontSize=26, leading=30,
                             textColor=NAVY, alignment=TA_LEFT, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName=FONT_TITLE, fontSize=19, leading=23,
                             textColor=NAVY, alignment=TA_LEFT, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=FONT_BODY, fontSize=11, leading=16.5,
                               textColor=GRIS_CUERPO, alignment=TA_JUSTIFY, spaceAfter=6),
        "meta": ParagraphStyle("meta", fontName=FONT_BODY, fontSize=9, leading=12,
                               textColor=GRIS_META, alignment=TA_LEFT),
        "recuadro": ParagraphStyle("recuadro", fontName=FONT_BODY, fontSize=11, leading=16,
                                   textColor=BLANCO, alignment=TA_LEFT),
        "recuadro_tit": ParagraphStyle("recuadro_tit", fontName=FONT_TITLE, fontSize=12,
                                       leading=15, textColor=ORO, alignment=TA_LEFT, spaceAfter=4),
    }


def _logo_drawing(svg_path: Path, width: float, max_h: float = None):
    """Carga un SVG y lo normaliza a `width` usando sus bounds reales.

    Algunos SVG (p.ej. thalos-mark-white) declaran width=2400 con un viewBox
    diminuto → escalar por d.width falla. getBounds() da el bounding box real
    y lo ajustamos exacto, trasladando el contenido al origen.

    max_h: si se pasa y la altura resultante (bh*s) lo excede, se re-escala por
    altura en vez de por ancho. Evita que un logo ~cuadrado escalado a `width`
    quede más alto que su contenedor (banda del header) y se recorte por el
    borde de la página. El Drawing devuelto usa las dimensiones REALES del
    contenido escalado (bw*s, bh*s) para que el centrado del llamador sea exacto.
    """
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics.shapes import Group
        d = svg2rlg(str(svg_path))
        if d is None:
            return None
        try:
            x0, y0, x1, y1 = d.getBounds()
            bw, bh = (x1 - x0), (y1 - y0)
        except Exception:
            x0 = y0 = 0
            bw, bh = (d.width or width), (d.height or width)
        if bw <= 0 or bh <= 0:
            return None
        s = width / bw
        if max_h is not None and bh * s > max_h:
            s = max_h / bh
        g = Group(*d.contents)
        g.transform = (s, 0, 0, s, -x0 * s, -y0 * s)
        out = Drawing(bw * s, bh * s)
        out.add(g)
        return out
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HEADER / FOOTER (páginas de contenido) — línea divisora en ORO
# ══════════════════════════════════════════════════════════════════════════════

def header_footer(canvas, doc):
    canvas.saveState()
    # ── Header: banda navy con logo blanco + metadata ──
    band_h = 0.55 * inch
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - band_h, PAGE_W, band_h, fill=1, stroke=0)
    # Logo acotado en altura al alto de la banda (con ~8pt de padding vertical)
    # para que el globo se vea COMPLETO y proporcionado, sin recortarse por el
    # borde superior de la página.
    logo = _logo_drawing(LOGO_WHITE_SVG, width=0.8 * inch, max_h=band_h - 8)
    if logo is not None:
        logo.drawOn(canvas, MARGEN_LAT, PAGE_H - band_h + (band_h - logo.height) / 2)
    else:
        canvas.setFillColor(BLANCO)
        canvas.setFont(FONT_TITLE, 13)
        canvas.drawString(MARGEN_LAT, PAGE_H - band_h + 0.18 * inch, "THALOS")
    canvas.setFillColor(BLANCO)
    canvas.setFont(FONT_BODY, 9)
    canvas.drawRightString(PAGE_W - MARGEN_LAT, PAGE_H - band_h + 0.20 * inch,
                           doc._header_meta)
    # línea divisora oro bajo el header
    canvas.setStrokeColor(ORO)
    canvas.setLineWidth(1)
    canvas.line(MARGEN_LAT, PAGE_H - band_h - 2, PAGE_W - MARGEN_LAT, PAGE_H - band_h - 2)

    # ── Footer: nº página + fecha (izq) · tagline (der) + línea oro ──
    fy = MARGEN_INF - 0.35 * inch
    canvas.setStrokeColor(ORO)
    canvas.setLineWidth(1)
    canvas.line(MARGEN_LAT, fy + 0.16 * inch, PAGE_W - MARGEN_LAT, fy + 0.16 * inch)
    canvas.setFillColor(GRIS_META)
    canvas.setFont(FONT_BODY, 9)
    canvas.drawString(MARGEN_LAT, fy, f"Página {doc.page} · {doc._fecha_footer}")
    canvas.setFillColor(NAVY)
    canvas.drawRightString(PAGE_W - MARGEN_LAT, fy, TAGLINE)
    canvas.restoreState()


# ══════════════════════════════════════════════════════════════════════════════
# PORTADA — full-bleed navy sólido (un solo color) + logo + metadata
# ══════════════════════════════════════════════════════════════════════════════

def dibujar_portada(canvas, doc):
    """Portada full-bleed (callback onFirstPage): navy sólido + logo + meta.

    Sin banda/acento lateral: la portada es de UN SOLO COLOR (Navy #0F3A66),
    limpia. Los acentos oro quedan solo en la línea divisora y el tagline.
    """
    p = doc._portada
    c = canvas
    c.saveState()
    # fondo navy full-bleed (coords absolutas de página)
    c.setFillColor(NAVY)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    cx = PAGE_W / 2
    # logo centrado (mark blanco), moderado y por encima del título
    logo = _logo_drawing(LOGO_WHITE_SVG, width=1.5 * inch)
    logo_y = PAGE_H - MARGEN_SUP - 1.1 * inch
    if logo is not None:
        logo.drawOn(c, cx - logo.width / 2, logo_y)
    else:
        c.setFillColor(BLANCO)
        c.setFont(FONT_TITLE, 34)
        c.drawCentredString(cx, logo_y, "THALOS")
    y = logo_y - 0.55 * inch
    # título
    c.setFillColor(BLANCO)
    c.setFont(FONT_TITLE, 28)
    c.drawCentredString(cx, y, p["titulo"])
    # subtítulo
    c.setFont(FONT_BODY, 14)
    c.setFillColorRGB(1, 1, 1, alpha=0.9)
    c.drawCentredString(cx, y - 0.5 * inch, p["subtitulo"])
    # tema / rango (oro)
    c.setFillColor(ORO)
    c.setFont(FONT_BODY, 14)
    c.drawCentredString(cx, y - 0.92 * inch, p["tema_rango"])
    # línea divisora horizontal oro
    c.setStrokeColor(ORO)
    c.setLineWidth(1.2)
    c.line(cx - 2.2 * inch, y - 1.25 * inch, cx + 2.2 * inch, y - 1.25 * inch)
    # metadata (blanco 80%)
    c.setFont(FONT_BODY, 9)
    c.setFillColorRGB(1, 1, 1, alpha=0.8)
    my = y - 1.7 * inch
    for label, valor in p["metadata"]:
        c.drawCentredString(cx, my, f"{label}: {valor}")
        my -= 0.24 * inch
    # tagline al pie de la portada (oro)
    c.setFillColor(ORO)
    c.setFont(FONT_BODY, 9)
    c.drawCentredString(cx, MARGEN_INF, TAGLINE)
    c.restoreState()


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTES A-H
# ══════════════════════════════════════════════════════════════════════════════

def linea_oro(thickness: float = 1.3) -> HRFlowable:
    """H. Línea divisora en oro — visible y elegante."""
    return HRFlowable(width="100%", color=ORO, thickness=thickness,
                      spaceBefore=10, spaceAfter=12)


def recuadro_ejecutivo(titulo: str, texto: str, st: dict) -> Table:
    """A. Recuadro ejecutivo — navy con borde oro, texto blanco."""
    inner = []
    if titulo:
        inner.append(Paragraph(titulo, st["recuadro_tit"]))
    inner.append(Paragraph(texto, st["recuadro"]))
    t = Table([[inner]], colWidths=[PAGE_W - 2 * MARGEN_LAT])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("BOX", (0, 0), (-1, -1), 1.5, ORO),
        ("ROUNDEDCORNERS", [5, 5, 5, 5]),
        ("LEFTPADDING", (0, 0), (-1, -1), 15),
        ("RIGHTPADDING", (0, 0), (-1, -1), 15),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    return t


def tabla_profesional(headers: list, filas: list, col_widths: list) -> Table:
    """B. Tabla profesional — borde oro, header gris claro, SIN zebra."""
    st_h = ParagraphStyle("th", fontName=FONT_TITLE, fontSize=10, textColor=NAVY, leading=12)
    st_c = ParagraphStyle("td", fontName=FONT_BODY, fontSize=10, textColor=GRIS_CUERPO, leading=13)
    data = [[Paragraph(str(h), st_h) for h in headers]]
    for fila in filas:
        data.append([Paragraph(str(c), st_c) for c in fila])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 1, ORO),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, ORO),
        # filas blancas, línea inferior oro muy claro (sin zebra)
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#EADFB0")),
        ("BACKGROUND", (0, 1), (-1, -1), BLANCO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]
    t.setStyle(TableStyle(estilo))
    return t


def _celda_coloreada(texto, color, ancho, alto, txt_color=BLANCO):
    st = ParagraphStyle("cc", fontName=FONT_TITLE, fontSize=10, textColor=txt_color,
                        alignment=TA_CENTER, leading=12)
    t = Table([[Paragraph(texto, st)]], colWidths=[ancho], rowHeights=[alto])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("BOX", (0, 0), (-1, -1), 0.5, ORO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    return t


def matriz_pxi(st: dict) -> list:
    """C. Matriz P×I — rejilla probabilidad × impacto coloreada por riesgo."""
    # 4x4: columnas = impacto (bajo→crítico), filas = probabilidad (alta→baja)
    niveles = ["BAJO", "MODERADO", "ALTO", "CRÍTICO"]
    prob_labels = ["Muy alta", "Alta", "Media", "Baja"]
    imp_labels = ["Bajo", "Medio", "Alto", "Crítico"]
    # score = combinación; color por umbral
    def _color(pi):
        if pi >= 12: return ROJO_CRIT
        if pi >= 8:  return AMBAR_ALTO
        if pi >= 4:  return NARANJA_MOD
        return AMARILLO_BAJO
    cell = 0.95 * inch
    lab = ParagraphStyle("lab", fontName=FONT_TITLE, fontSize=9, textColor=NAVY,
                         alignment=TA_CENTER, leading=11)
    # cabecera de columnas
    data = [[""] + [Paragraph(x, lab) for x in imp_labels]]
    for i, pl in enumerate(prob_labels):          # i=0 → prob muy alta (fila superior)
        p = 4 - i                                  # 4..1
        fila = [Paragraph(pl, lab)]
        for j in range(4):
            imp = j + 1                            # 1..4
            pi = p * imp
            fila.append(_celda_coloreada("", _color(pi), cell, cell))
        data.append(fila)
    t = Table(data, colWidths=[0.9 * inch] + [cell] * 4,
              rowHeights=[0.3 * inch] + [cell] * 4)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    ejes = Paragraph("<b>Probabilidad</b> (vertical) × <b>Impacto</b> (horizontal)",
                     ParagraphStyle("ejes", fontName=FONT_BODY, fontSize=9, textColor=NAVY))
    leyenda = _leyenda_riesgo()
    return [ejes, Spacer(1, 4), t, Spacer(1, 6), leyenda]


def matriz_urgencia(st: dict) -> list:
    """D. Matriz de urgencia — gravedad × actividad, cuadrantes marcados."""
    lab = ParagraphStyle("lab", fontName=FONT_TITLE, fontSize=9, textColor=NAVY,
                         alignment=TA_CENTER, leading=11)
    cq = ParagraphStyle("cq", fontName=FONT_TITLE, fontSize=9, textColor=BLANCO,
                        alignment=TA_CENTER, leading=11)
    cell = 1.7 * inch
    # 2x2: filas gravedad (alta/baja), columnas actividad (baja/alta)
    q_ti = _celda_coloreada("GRAVE PERO<br/>SILENCIOSO", NARANJA_MOD, cell, cell)
    q_td = _celda_coloreada("GRAVE Y<br/>ACTIVO", ROJO_CRIT, cell, cell)
    q_bi = _celda_coloreada("TRANQUILO", AMARILLO_BAJO, cell, cell, txt_color=NAVY)
    q_bd = _celda_coloreada("RUIDOSO<br/>PERO MENOR", AMBAR_ALTO, cell, cell)
    data = [
        [Paragraph("Gravedad alta", lab), q_ti, q_td],
        [Paragraph("Gravedad baja", lab), q_bi, q_bd],
        ["", Paragraph("Actividad baja", lab), Paragraph("Actividad alta", lab)],
    ]
    t = Table(data, colWidths=[1.1 * inch, cell, cell],
              rowHeights=[cell, cell, 0.3 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    return [t, Spacer(1, 6), _leyenda_riesgo()]


def _leyenda_riesgo() -> Drawing:
    d = Drawing(430, 22)
    items = [("Crítico", ROJO_CRIT), ("Alto", AMBAR_ALTO),
             ("Moderado", NARANJA_MOD), ("Bajo", AMARILLO_BAJO)]
    x = 0
    for texto, col in items:
        d.add(Rect(x, 4, 14, 14, fillColor=col, strokeColor=ORO, strokeWidth=0.5))
        d.add(String(x + 19, 8, texto, fontName=FONT_BODY, fontSize=9,
                     fillColor=GRIS_CUERPO))
        x += 108
    return d


# ══════════════════════════════════════════════════════════════════════════════
# VELOCÍMETRO (GAUGE) DEL SCORE GLOBAL — componente reutilizable
# ══════════════════════════════════════════════════════════════════════════════
# Bandas discretas del Score Global (0-100). Fuente única de verdad del gauge:
# gobiernan la banda del arco, el color de la aguja/número y la etiqueta de nivel.
# Preservan los cortes del motor (45 y 70) y añaden verde (bajo) y el split
# superior (MUY ALTO / CRÍTICO) pedidos por el Coronel. Paleta THALOS.
GAUGE_BANDAS = [
    (0,  20,  "BAJO",     VERDE_INFO),
    (20, 45,  "MODERADO", AMARILLO_BAJO),
    (45, 70,  "ALTO",     NARANJA_MOD),
    (70, 88,  "MUY ALTO", AMBAR_ALTO),
    (88, 100, "CRÍTICO",  ROJO_CRIT),
]


def nivel_score(score: float):
    """Devuelve (etiqueta, color) del Score Global según GAUGE_BANDAS."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    s = max(0.0, min(100.0, s))
    for lo, hi, lbl, col in GAUGE_BANDAS:
        if lo <= s <= hi:
            return lbl, col
    return GAUGE_BANDAS[-1][2], GAUGE_BANDAS[-1][3]


def _gauge_ang(score: float) -> float:
    """Ángulo (grados) del semicírculo: score 0 → 180° (izq), 100 → 0° (der)."""
    s = max(0.0, min(100.0, float(score)))
    return 180.0 - (s / 100.0) * 180.0


def gauge_score(score: float, width: float = 260) -> Drawing:
    """Velocímetro del Score Global: semicírculo 0-100 con bandas de color,
    aguja al valor actual, y número + etiqueta de nivel debajo del eje.

    Dibujado con primitivas reportlab (Wedge/Polygon/Circle/String) — sin
    dependencias nuevas (matplotlib NO está en el servidor de Render)."""
    try:
        sc = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        sc = 0.0
    R = width / 2.0 - 8
    r = R * 0.60
    cx = width / 2.0
    cy = 52                      # eje elevado: deja sitio abajo para el texto
    d = Drawing(width, cy + R + 8)
    # bandas anulares
    for lo, hi, lbl, col in GAUGE_BANDAS:
        d.add(Wedge(cx, cy, R, _gauge_ang(hi), _gauge_ang(lo), radius1=r,
                    fillColor=col, strokeColor=BLANCO, strokeWidth=1))
    lbl, col = nivel_score(sc)
    # aguja (triángulo esbelto) + cubo central
    ang = math.radians(_gauge_ang(sc))
    perp = ang + math.pi / 2
    tip = (cx + (R - 4) * math.cos(ang), cy + (R - 4) * math.sin(ang))
    b1 = (cx + 5 * math.cos(perp), cy + 5 * math.sin(perp))
    b2 = (cx - 5 * math.cos(perp), cy - 5 * math.sin(perp))
    d.add(Polygon([tip[0], tip[1], b1[0], b1[1], b2[0], b2[1]],
                  fillColor=NAVY, strokeColor=NAVY))
    d.add(Circle(cx, cy, 8, fillColor=NAVY, strokeColor=ORO, strokeWidth=1.2))
    # marcas de escala 0 y 100 (bajo la base del arco, sin tocar las bandas)
    d.add(String(cx - R + 1, cy - 11, "0", fontName=FONT_BODY, fontSize=8,
                 fillColor=GRIS_META, textAnchor="middle"))
    d.add(String(cx + R - 1, cy - 11, "100", fontName=FONT_BODY, fontSize=8,
                 fillColor=GRIS_META, textAnchor="middle"))
    # número + etiqueta de nivel, debajo del eje, en el color de la banda
    d.add(String(cx, cy - 30, f"{sc:.0f}", fontName=FONT_TITLE, fontSize=28,
                 fillColor=col, textAnchor="middle"))
    d.add(String(cx, cy - 44, lbl, fontName=FONT_TITLE, fontSize=11,
                 fillColor=col, textAnchor="middle"))
    return d


def bloque_score_gauge(riesgo: dict, st: dict) -> list:
    """Bloque 'Score Global · Riesgo Nacional' con el velocímetro dentro de una
    tarjeta THALOS (gris claro, borde oro). Reemplaza el número grande.
    Reutilizable por cualquier reporte que muestre el Score Global."""
    score = (riesgo or {}).get("global", 0)
    lbl = Paragraph("Score Global · Riesgo Nacional",
                    ParagraphStyle("sg_lbl", fontName=FONT_BODY, fontSize=10,
                                   textColor=GRIS_META))
    card = Table([[gauge_score(score, width=2.9 * inch)]],
                 colWidths=[PAGE_W - 2 * MARGEN_LAT])
    card.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 1.2, ORO),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("BACKGROUND", (0, 0), (-1, -1), GRIS_CLARO),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    return [lbl, Spacer(1, 4), card]


def grafico_tendencia() -> Drawing:
    """E. Gráfico de tendencia — líneas 2pt, grid gris claro, eje Y desde 0."""
    d = Drawing(PAGE_W - 2 * MARGEN_LAT, 210)
    lc = HorizontalLineChart()
    lc.x = 40
    lc.y = 30
    lc.width = PAGE_W - 2 * MARGEN_LAT - 70
    lc.height = 160
    lc.data = [
        [40, 45, 52, 60, 68, 72],   # serie 1
        [55, 53, 50, 48, 46, 44],   # serie 2
        [20, 28, 30, 42, 55, 63],   # serie 3
    ]
    lc.categoryAxis.categoryNames = ["Ene", "Feb", "Mar", "Abr", "May", "Jun"]
    lc.categoryAxis.labels.fontName = FONT_BODY
    lc.categoryAxis.labels.fontSize = 8
    lc.valueAxis.valueMin = 0            # eje Y desde 0, sin truncar
    lc.valueAxis.valueMax = 100
    lc.valueAxis.valueStep = 20
    lc.valueAxis.labels.fontName = FONT_BODY
    lc.valueAxis.labels.fontSize = 8
    lc.valueAxis.gridStrokeColor = GRIS_CLARO   # grid gris claro #E8E8E8, sutil
    lc.valueAxis.gridStrokeWidth = 0.5
    lc.valueAxis.visibleGrid = 1
    serie_cols = [ROJO_CRIT, AMBAR_ALTO, AZUL_TERRITORIO]
    for i, col in enumerate(serie_cols):
        lc.lines[i].strokeColor = col
        lc.lines[i].strokeWidth = 2.5
    d.add(lc)
    # leyenda
    nombres = ["Riesgo institucional", "Cobertura mediática", "Actividad social"]
    x = 40
    for nom, col in zip(nombres, serie_cols):
        d.add(Line(x, 6, x + 16, 6, strokeColor=col, strokeWidth=2.5))
        d.add(String(x + 20, 3, nom, fontName=FONT_BODY, fontSize=8, fillColor=GRIS_CUERPO))
        x += 165
    return d


class MapaGeografico(Flowable):
    """F. Mapa geográfico — base de relieve + símbolos, escala y norte."""
    def __init__(self, width=None, height=None, simbolos=None):
        super().__init__()
        self.width = width or (PAGE_W - 2 * MARGEN_LAT)
        # proporción del PNG (2150×3039)
        self.height = height or (self.width * 3039 / 2150 * 0.62)
        # simbolos: list[(fx, fy, letra, color)] con fx,fy en 0..1 sobre el mapa
        self.simbolos = simbolos or []

    def wrap(self, aw, ah):
        return (self.width, self.height)

    def draw(self):
        c = self.canv
        c.saveState()
        # base map (recorte superior del PNG para encuadrar Perú continental)
        if MAPA_BASE_PNG.exists():
            from reportlab.lib.utils import ImageReader
            c.drawImage(ImageReader(str(MAPA_BASE_PNG)), 0, 0,
                        width=self.width, height=self.height,
                        preserveAspectRatio=False, mask=None)
        c.setStrokeColor(ORO)
        c.setLineWidth(1)
        c.rect(0, 0, self.width, self.height, fill=0, stroke=1)
        # símbolos: círculos navy con letra blanca, coloreados por riesgo (borde)
        for fx, fy, letra, col in self.simbolos:
            x = fx * self.width
            y = fy * self.height
            r = 11
            c.setFillColor(NAVY)
            c.setStrokeColor(col)
            c.setLineWidth(2)
            c.circle(x, y, r, fill=1, stroke=1)
            c.setFillColor(BLANCO)
            c.setFont(FONT_TITLE, 10)
            c.drawCentredString(x, y - 3.5, letra)
        # barra de escala
        sx, sy, sl = 14, 14, 90
        c.setStrokeColor(BLANCO)
        c.setLineWidth(2)
        c.line(sx, sy, sx + sl, sy)
        c.line(sx, sy - 3, sx, sy + 3)
        c.line(sx + sl, sy - 3, sx + sl, sy + 3)
        c.setFillColor(BLANCO)
        c.setFont(FONT_BODY, 8)
        c.drawString(sx, sy + 5, "0 — 300 km")
        # flecha norte
        nx, ny = self.width - 22, self.height - 34
        c.setFillColor(BLANCO)
        c.setStrokeColor(BLANCO)
        p = c.beginPath()
        p.moveTo(nx, ny + 16)
        p.lineTo(nx - 6, ny)
        p.lineTo(nx + 6, ny)
        p.close()
        c.drawPath(p, fill=1, stroke=0)
        c.setFont(FONT_TITLE, 9)
        c.drawCentredString(nx, ny + 19, "N")
        c.restoreState()


def iconografia_numerica(items: list) -> Drawing:
    """G. Iconografía numérica — círculos navy con número/letra blancos."""
    n = len(items)
    d = Drawing(PAGE_W - 2 * MARGEN_LAT, 60)
    r = 20
    paso = (PAGE_W - 2 * MARGEN_LAT) / n
    for i, txt in enumerate(items):
        cx = paso * i + paso / 2
        d.add(Circle(cx, 30, r, fillColor=NAVY, strokeColor=ORO, strokeWidth=1))
        d.add(String(cx, 24, str(txt), fontName=FONT_TITLE, fontSize=15,
                     fillColor=BLANCO, textAnchor="middle"))
    return d


def chips_color(items: list) -> Drawing:
    """G/chips. Rectángulos redondeados coloreados; envuelve a varias filas."""
    maxw = PAGE_W - 2 * MARGEN_LAT
    gap, chip_h, row_gap = 10, 22, 12
    # precalcular anchos y distribuir en filas que quepan
    anchos = [max(64, 14 + len(t) * 6.8) for t, _, _ in items]
    filas, fila, ancho_fila = [], [], 0.0
    for it, w in zip(items, anchos):
        if fila and ancho_fila + w > maxw:
            filas.append(fila)
            fila, ancho_fila = [], 0.0
        fila.append((it, w))
        ancho_fila += w + gap
    if fila:
        filas.append(fila)
    alto = len(filas) * (chip_h + row_gap)
    d = Drawing(maxw, alto)
    y = alto - chip_h
    for fila in filas:
        x = 0
        for (texto, col, txtcol), w in fila:
            d.add(Rect(x, y, w, chip_h, rx=6, ry=6, fillColor=col,
                       strokeColor=ORO, strokeWidth=0.5))
            d.add(String(x + w / 2, y + 7, texto, fontName=FONT_TITLE, fontSize=9,
                         fillColor=txtcol, textAnchor="middle"))
            x += w + gap
        y -= (chip_h + row_gap)
    return d


# ══════════════════════════════════════════════════════════════════════════════
# DEMO — arma todos los componentes en un PDF
# ══════════════════════════════════════════════════════════════════════════════

def _seccion(st, num, titulo):
    # Spacer generoso antes de cada sección → aire entre componentes (~0.28")
    return [Spacer(1, 20), KeepTogether([
        Paragraph(f"{num}. {titulo}", st["h2"]),
        linea_oro(),
    ])]


def construir_demo_pdf(fecha: str = "2026-07-01") -> bytes:
    """Genera el PDF demo con todos los componentes THALOS. Devuelve bytes."""
    st = estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGEN_LAT, rightMargin=MARGEN_LAT,
        topMargin=MARGEN_SUP, bottomMargin=MARGEN_INF,
        title="THALOS · Demo de Componentes",
    )
    doc._fecha_footer = fecha
    doc._header_meta = "TIE-001/2026 | JULIO 2026 | LIMA · PERÚ"
    doc._portada = {
        "titulo": "Plantilla Base THALOS",
        "subtitulo": "Componentes visuales para reportes de inteligencia",
        "tema_rango": "Demo de componentes · Fase 1",
        "metadata": [
            ("Fecha de generación", fecha),
            ("Clasificación", "USO INTERNO"),
            ("Distribuido a", "Dirección estratégica"),
            ("Próxima actualización", "Fase 3"),
        ],
    }
    S = []

    # Página 1 = portada (dibujada en onFirstPage). Content arranca en pág. 2.
    S.append(PageBreak())

    S.append(Paragraph("Catálogo de componentes", st["h1"]))
    S.append(Paragraph(
        "Esta página reúne todos los componentes reutilizables de la plantilla "
        "base THALOS. No es un reporte: es la infraestructura visual sobre la que "
        "se construirán los Reportes A y B.", st["body"]))

    # 2. Recuadro ejecutivo
    S += _seccion(st, "01", "Recuadro ejecutivo")
    S.append(recuadro_ejecutivo(
        "JUICIO ESTRATÉGICO",
        "La combinación de gravedad estructural sostenida y aceleración mediática "
        "en el tema sitúa el riesgo en el cuadrante de mayor prioridad para la "
        "próxima ventana de 30 días.", st))

    # 3. Tabla profesional
    S += _seccion(st, "02", "Tabla profesional")
    S.append(tabla_profesional(
        ["Actor", "Peso", "Índice CVO", "Trayectoria"],
        [["Gremio minero", "72", "78.4", "ASCENSO (+11)"],
         ["Ejecutivo", "65", "65.2", "ESTABLE (+2)"],
         ["Partidos tradicionales", "41", "41.0", "DECLIVE (-8)"]],
        col_widths=[2.4 * inch, 0.9 * inch, 1.2 * inch, 1.9 * inch]))

    # 4. Matriz P×I
    S += _seccion(st, "03", "Matriz Probabilidad × Impacto")
    S += matriz_pxi(st)

    # 5. Matriz de urgencia
    S += _seccion(st, "04", "Matriz de urgencia (gravedad × actividad)")
    S += matriz_urgencia(st)

    S.append(PageBreak())

    # 6. Gráfico de tendencia
    S += _seccion(st, "05", "Gráfico de tendencia")
    S.append(grafico_tendencia())

    # 7. Mapa geográfico
    S += _seccion(st, "06", "Mapa geográfico")
    S.append(MapaGeografico(simbolos=[
        (0.30, 0.72, "1", ROJO_CRIT),   # Lima aprox
        (0.55, 0.30, "2", AMBAR_ALTO),  # selva central
        (0.62, 0.68, "3", VERDE_INFO),  # sur andino
    ]))

    # 8. Iconografía numérica
    S += _seccion(st, "07", "Iconografía numérica")
    S.append(iconografia_numerica(["01", "02", "03", "04", "05"]))
    S.append(Spacer(1, 6))
    S.append(iconografia_numerica(["S", "M", "I", "C", "G"]))

    # 9. Chips de color
    S += _seccion(st, "08", "Chips de color por función")
    S.append(chips_color([
        ("CRÍTICO", ROJO_CRIT, BLANCO),
        ("ALTO", AMBAR_ALTO, BLANCO),
        ("MODERADO", NARANJA_MOD, BLANCO),
        ("BAJO", AMARILLO_BAJO, NAVY),
        ("AUTOMÁTICO", VERDE_INFO, BLANCO),
        ("ANÁLISIS", PURPURA_ANALISIS, BLANCO),
        ("TERRITORIO", AZUL_TERRITORIO, BLANCO),
    ]))

    doc.build(S, onFirstPage=dibujar_portada, onLaterPages=header_footer)
    return buf.getvalue()
