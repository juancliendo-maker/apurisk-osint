"""Generador PDF corporativo para Reporte de Riesgo Político Minero.

Diseño:
  - Tema corporativo serio (NO dark theme del dashboard)
  - Portada con score global y semáforo
  - Tabla de contenidos
  - 12 secciones numeradas con maquetación profesional
  - Gráficos simples de matriz P×I y semáforo por factor
  - Footer con disclaimer legal en cada página
  - Encabezado con marca y período

Output: PDF de ~15-20 páginas listo para entrega a cliente corporativo.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, KeepTogether, HRFlowable,
)
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.legends import Legend


# =====================================================================
# PALETA CORPORATIVA
# =====================================================================
COLOR_AZUL_CORP = colors.HexColor("#1e3a5f")       # azul corporativo profundo
COLOR_AZUL_CLARO = colors.HexColor("#3b6ea8")
COLOR_GRIS_OSCURO = colors.HexColor("#2c3e50")
COLOR_GRIS_TEXTO = colors.HexColor("#4a5568")
COLOR_GRIS_CLARO = colors.HexColor("#f7fafc")
COLOR_GRIS_BORDE = colors.HexColor("#e2e8f0")
COLOR_ROJO_CRIT = colors.HexColor("#c53030")
COLOR_NARANJA = colors.HexColor("#dd6b20")
COLOR_AMARILLO = colors.HexColor("#d69e2e")
COLOR_VERDE = colors.HexColor("#38a169")
COLOR_BLANCO = colors.HexColor("#ffffff")

COLOR_POR_NIVEL = {
    "CRÍTICO": COLOR_ROJO_CRIT,
    "ALTO": COLOR_NARANJA,
    "MEDIO": COLOR_AMARILLO,
    "BAJO": COLOR_VERDE,
}


# =====================================================================
# ENCABEZADO Y PIE DE PÁGINA
# =====================================================================
def _header_footer(canvas_obj, doc, metadata):
    """Pinta encabezado y pie en cada página."""
    canvas_obj.saveState()
    width, height = A4

    # ENCABEZADO
    canvas_obj.setFillColor(COLOR_AZUL_CORP)
    canvas_obj.rect(0, height - 1.5 * cm, width, 1.5 * cm, fill=1, stroke=0)
    canvas_obj.setFillColor(COLOR_BLANCO)
    canvas_obj.setFont("Helvetica-Bold", 11)
    canvas_obj.drawString(2 * cm, height - 0.95 * cm,
                          "APURISK · Riesgo Político Minero · Reporte Semanal")
    canvas_obj.setFont("Helvetica", 9)
    canvas_obj.drawRightString(width - 2 * cm, height - 0.95 * cm,
                               metadata.get("periodo", ""))

    # PIE DE PÁGINA
    canvas_obj.setStrokeColor(COLOR_GRIS_BORDE)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(2 * cm, 1.6 * cm, width - 2 * cm, 1.6 * cm)
    canvas_obj.setFillColor(COLOR_GRIS_TEXTO)
    canvas_obj.setFont("Helvetica", 7.5)
    disclaimer = ("Análisis OSINT basado en fuentes abiertas. No constituye asesoría "
                  "financiera, legal ni de inversión. Para uso interno del cliente.")
    canvas_obj.drawString(2 * cm, 1.15 * cm, disclaimer)
    canvas_obj.setFont("Helvetica-Bold", 8)
    canvas_obj.drawRightString(width - 2 * cm, 1.15 * cm,
                               f"Página {canvas_obj.getPageNumber()}")
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.drawString(2 * cm, 0.7 * cm,
                          f"Generado: {metadata.get('generado', '')[:19]} · "
                          f"Cliente: {metadata.get('solicitante', '—')}")
    canvas_obj.restoreState()


# =====================================================================
# ESTILOS DE TEXTO
# =====================================================================
def _build_styles():
    s = getSampleStyleSheet()
    estilos = {
        "h1_portada": ParagraphStyle(
            "h1_portada", parent=s["Heading1"],
            fontName="Helvetica-Bold", fontSize=28,
            textColor=COLOR_AZUL_CORP, alignment=TA_CENTER,
            spaceAfter=18, leading=34,
        ),
        "sub_portada": ParagraphStyle(
            "sub_portada", parent=s["Normal"],
            fontName="Helvetica", fontSize=14,
            textColor=COLOR_GRIS_TEXTO, alignment=TA_CENTER,
            spaceAfter=24, leading=18,
        ),
        "h1": ParagraphStyle(
            "h1", parent=s["Heading1"],
            fontName="Helvetica-Bold", fontSize=18,
            textColor=COLOR_AZUL_CORP,
            spaceBefore=12, spaceAfter=8, leading=24,
        ),
        "h2": ParagraphStyle(
            "h2", parent=s["Heading2"],
            fontName="Helvetica-Bold", fontSize=13,
            textColor=COLOR_AZUL_CLARO,
            spaceBefore=10, spaceAfter=6, leading=18,
        ),
        "h3": ParagraphStyle(
            "h3", parent=s["Heading3"],
            fontName="Helvetica-Bold", fontSize=11,
            textColor=COLOR_GRIS_OSCURO,
            spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=s["Normal"],
            fontName="Helvetica", fontSize=10,
            textColor=COLOR_GRIS_OSCURO, alignment=TA_JUSTIFY,
            spaceAfter=6, leading=14,
        ),
        "body_small": ParagraphStyle(
            "body_small", parent=s["Normal"],
            fontName="Helvetica", fontSize=8.5,
            textColor=COLOR_GRIS_TEXTO, alignment=TA_JUSTIFY,
            spaceAfter=4, leading=12,
        ),
        "callout": ParagraphStyle(
            "callout", parent=s["Normal"],
            fontName="Helvetica-Bold", fontSize=11,
            textColor=COLOR_AZUL_CORP, alignment=TA_LEFT,
            spaceBefore=8, spaceAfter=8, leading=15,
            backColor=COLOR_GRIS_CLARO,
            borderColor=COLOR_AZUL_CLARO, borderWidth=0,
            borderPadding=10, leftIndent=8, rightIndent=8,
        ),
        "score_big": ParagraphStyle(
            "score_big", parent=s["Normal"],
            fontName="Helvetica-Bold", fontSize=64,
            textColor=COLOR_AZUL_CORP, alignment=TA_CENTER,
            spaceAfter=4, leading=70,
        ),
        "score_label": ParagraphStyle(
            "score_label", parent=s["Normal"],
            fontName="Helvetica-Bold", fontSize=14,
            textColor=COLOR_GRIS_TEXTO, alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "url": ParagraphStyle(
            "url", parent=s["Normal"],
            fontName="Helvetica-Oblique", fontSize=8,
            textColor=COLOR_AZUL_CLARO, alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer", parent=s["Normal"],
            fontName="Helvetica-Oblique", fontSize=8,
            textColor=COLOR_GRIS_TEXTO, alignment=TA_JUSTIFY,
            spaceBefore=6, leading=11,
        ),
    }
    return estilos


# =====================================================================
# COMPONENTES VISUALES AVANZADOS
# =====================================================================

def _matriz_pxi_visual(factores, ancho=16 * cm, alto=12 * cm):
    """Matriz P×I visual con cuadrícula coloreada y burbujas.

    Reemplaza el gráfico de barras plano por una matriz profesional 4x4
    con áreas de color (verde / amarillo / naranja / rojo) y burbujas
    posicionadas según probabilidad × impacto.
    """
    d = Drawing(ancho, alto)
    # Origen del área de matriz
    x0, y0 = 50, 30
    w = ancho - 80
    h = alto - 60

    # 4 zonas de color (de menor a mayor riesgo)
    # Cuadrante inferior-izquierdo: verde (bajo prob × bajo impacto)
    # Cuadrante superior-derecho: rojo (alto prob × alto impacto)
    zonas = [
        # (x, y, w, h, color)
        (x0, y0, w/2, h/2, colors.HexColor("#d4f1d4")),         # bajo-bajo verde claro
        (x0 + w/2, y0, w/2, h/2, colors.HexColor("#fff3cd")),   # alto-bajo amarillo claro
        (x0, y0 + h/2, w/2, h/2, colors.HexColor("#fff3cd")),   # bajo-alto amarillo
        (x0 + w/2, y0 + h/2, w/2, h/2, colors.HexColor("#ffd6d6")),  # alto-alto rojo claro
    ]
    for zx, zy, zw, zh, c in zonas:
        d.add(Rect(zx, zy, zw, zh, fillColor=c, strokeColor=colors.HexColor("#d0d0d0"), strokeWidth=0.5))

    # Líneas medianas para separar zonas explícitamente
    d.add(Rect(x0 + w/2 - 0.5, y0, 1, h, fillColor=colors.HexColor("#999999"),
                strokeColor=None))
    d.add(Rect(x0, y0 + h/2 - 0.5, w, 1, fillColor=colors.HexColor("#999999"),
                strokeColor=None))

    # Etiquetas de ejes
    # Eje X (Probabilidad)
    d.add(String(x0 + w/2, 10, "PROBABILIDAD →",
                  textAnchor="middle", fontName="Helvetica-Bold", fontSize=8,
                  fillColor=colors.HexColor("#333333")))
    d.add(String(x0, y0 - 8, "0", fontSize=7, fillColor=colors.HexColor("#666666")))
    d.add(String(x0 + w, y0 - 8, "100", fontSize=7, fillColor=colors.HexColor("#666666"),
                  textAnchor="end"))
    d.add(String(x0 + w/2, y0 - 8, "50", textAnchor="middle", fontSize=7,
                  fillColor=colors.HexColor("#666666")))

    # Eje Y (Impacto) - texto rotado dentro de la fila
    d.add(String(15, y0 + h/2, "IMPACTO →", textAnchor="middle",
                  fontName="Helvetica-Bold", fontSize=8, fillColor=colors.HexColor("#333333")))

    # Burbujas para cada factor
    paleta_burbuja = {
        "CRÍTICO": colors.HexColor("#c53030"),
        "ALTO": colors.HexColor("#dd6b20"),
        "MEDIO": colors.HexColor("#d69e2e"),
        "BAJO": colors.HexColor("#38a169"),
    }
    for i, f in enumerate(factores):
        # Posición en la matriz
        px = x0 + (f["probabilidad"] / 100.0) * w
        py = y0 + (f["impacto"] / 100.0) * h
        # Tamaño de burbuja según score
        radio = max(7, min(16, f["score"] / 7))
        color = paleta_burbuja.get(f["nivel"], colors.HexColor("#666666"))
        # Burbuja
        d.add(Rect(px - radio, py - radio, radio * 2, radio * 2,
                    fillColor=color, strokeColor=colors.white, strokeWidth=1))
        # Número del factor adentro
        d.add(String(px, py - 3, str(i + 1), textAnchor="middle",
                      fontSize=8, fillColor=colors.white,
                      fontName="Helvetica-Bold"))

    return d


def _matriz_pxi_leyenda(factores, styles):
    """Leyenda numerada de la matriz P×I."""
    rows = [["#", "Factor de Riesgo", "P", "I", "Score", "Nivel"]]
    for i, f in enumerate(factores, 1):
        color_nivel = COLOR_POR_NIVEL.get(f["nivel"], COLOR_GRIS_TEXTO)
        rows.append([
            str(i),
            Paragraph(f["nombre"], styles["body_small"]),
            str(f["probabilidad"]),
            str(f["impacto"]),
            f"{f['score']}",
            Paragraph(f'<font color="{color_nivel.hexval()}"><b>● {f["nivel"]}</b></font>',
                       styles["body_small"]),
        ])
    t = Table(rows, colWidths=[0.8 * cm, 7.5 * cm, 1.2 * cm, 1.2 * cm, 1.5 * cm, 2.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL_CORP),
        ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),  # # centrado
        ("ALIGN", (2, 0), (4, -1), "CENTER"),  # números centrados
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _hallazgos_criticos_box(hallazgos, styles):
    """Caja destacada con top hallazgos críticos para portada."""
    if not hallazgos:
        return Paragraph("<i>Sin hallazgos críticos en la semana.</i>", styles["body_small"])
    rows = []
    for h in hallazgos[:5]:
        icono = h.get("icono", "●")
        titulo = h.get("titulo", "—")
        valor = h.get("valor", "—")
        impl = h.get("implicacion", "—")
        rows.append([
            Paragraph(f"<font size=14>{icono}</font>", styles["body"]),
            Paragraph(f"<b>{titulo}</b><br/><font size=8 color='#666666'>{valor}</font><br/><font size=8>{impl}</font>",
                       styles["body_small"]),
        ])
    t = Table(rows, colWidths=[1 * cm, 15 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffaf0")),
        ("BOX", (0, 0), (-1, -1), 1, COLOR_AZUL_CORP),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _alertas_semaforo_table(alertas, styles):
    """Tabla de alertas tempranas con semáforo de color."""
    if not alertas:
        return Paragraph("<i>Sin alertas tempranas activas.</i>", styles["body_small"])

    rows = [[
        Paragraph("<b>●</b>", styles["body_small"]),
        Paragraph("<b>Factor</b>", styles["body_small"]),
        Paragraph("<b>Plazo</b>", styles["body_small"]),
        Paragraph("<b>Responsable sugerido</b>", styles["body_small"]),
        Paragraph("<b>Acción inmediata</b>", styles["body_small"]),
    ]]
    for a in alertas[:10]:
        color_map = {"rojo": "#c53030", "naranja": "#dd6b20", "amarillo": "#d69e2e"}
        color = color_map.get(a.get("color", "amarillo"), "#666666")
        rows.append([
            Paragraph(f'<font size=14 color="{color}">●</font>', styles["body_small"]),
            Paragraph(f"<b>{a['factor']}</b><br/><font size=7 color='#666666'>{a.get('indicador','')}</font>",
                       styles["body_small"]),
            Paragraph(a.get("plazo", "—"), styles["body_small"]),
            Paragraph(a.get("responsable_sugerido", "—"), styles["body_small"]),
            Paragraph(a.get("accion_inmediata", "—"), styles["body_small"]),
        ])
    t = Table(rows, colWidths=[0.8 * cm, 4.5 * cm, 2 * cm, 4.5 * cm, 5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL_CORP),
        ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _implicacion_callout(titulo, texto, styles, color=None):
    """Recuadro destacado de 'Implicaciones para su empresa' con color."""
    color = color or COLOR_AZUL_CORP
    contenido = [
        Paragraph(f"💼 <b>IMPLICACIONES PARA SU EMPRESA: {titulo}</b>", styles["body_small"]),
        Paragraph(texto, styles["body_small"]),
    ]
    t = Table([[c] for c in contenido], colWidths=[16 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f7ff")),
        ("BOX", (0, 0), (-1, -1), 1, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _dato_contundente_box(item, styles):
    """Caja con un dato/cifra contundente del sector."""
    indicador = item.get("indicador", "—")
    desc = item.get("descripcion", "")
    fuente = item.get("fuente", "")
    impl = item.get("implicacion", "")

    data = [
        [Paragraph(f"<b>{indicador}</b>", styles["score_label"])],
        [Paragraph(desc, styles["body_small"])],
        [Paragraph(f"<i>Fuente: {fuente}</i>", styles["body_small"])],
        [Paragraph(f"<b>→</b> {impl}", styles["body_small"])],
    ]
    t = Table(data, colWidths=[7.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 1, COLOR_AZUL_CLARO),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


# =====================================================================
# COMPONENTES VISUALES (originales)
# =====================================================================

def _score_box(score, nivel, styles, ancho=8 * cm, alto=4.5 * cm):
    """Caja grande con el score global y nivel."""
    color = COLOR_POR_NIVEL.get(nivel, COLOR_AZUL_CORP)
    data = [
        [Paragraph(f"<b>{score}</b>", styles["score_big"])],
        [Paragraph(f"<b>{nivel}</b>", styles["score_label"])],
        [Paragraph("Score Global Sector Minero (0-100)", styles["body_small"])],
    ]
    t = Table(data, colWidths=[ancho], rowHeights=[2.8 * cm, 0.9 * cm, 0.7 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 2, color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _semaforo_table(semaforo_dict, styles):
    """Tabla con semáforo por factor (8 filas)."""
    header = [
        Paragraph("<b>Factor de Riesgo</b>", styles["body_small"]),
        Paragraph("<b>Score</b>", styles["body_small"]),
        Paragraph("<b>Nivel</b>", styles["body_small"]),
    ]
    rows = [header]
    for fid, info in semaforo_dict.items():
        color = COLOR_POR_NIVEL.get(info["nivel"], COLOR_GRIS_TEXTO)
        # Celda con bullet de color y texto
        nivel_cell = Paragraph(
            f'<font color="{color.hexval()}">●</font> <b>{info["nivel"]}</b>',
            styles["body_small"]
        )
        rows.append([
            Paragraph(info["nombre"], styles["body_small"]),
            Paragraph(f'<b>{info["score"]}</b>', styles["body_small"]),
            nivel_cell,
        ])

    t = Table(rows, colWidths=[8.5 * cm, 2 * cm, 4 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL_CORP),
        ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
        ("BACKGROUND", (0, 1), (-1, -1), COLOR_GRIS_CLARO),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _chart_factores_pxi(factores, ancho=16 * cm, alto=9 * cm):
    """Gráfico de barras horizontales de los 8 factores."""
    d = Drawing(ancho, alto)
    chart = HorizontalBarChart()
    chart.x = 140
    chart.y = 10
    chart.height = alto - 25
    chart.width = ancho - 160
    chart.data = [[f["score"] for f in factores]]
    chart.categoryAxis.categoryNames = [f["nombre"][:35] for f in factores]
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.bars[0].fillColor = COLOR_AZUL_CLARO
    chart.bars.strokeWidth = 0
    chart.categoryAxis.labels.fontSize = 7.5
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 8
    chart.valueAxis.gridStrokeColor = COLOR_GRIS_BORDE
    d.add(chart)
    return d


def _alerta_box(alerta_principal, styles):
    """Caja destacada con la alerta principal de la semana."""
    titulo = alerta_principal.get("titulo", "Sin alerta crítica")
    resumen = alerta_principal.get("resumen", "")
    region = alerta_principal.get("region", "—")
    fuente = alerta_principal.get("fuente", "—")

    contenido = [
        Paragraph("<b>ALERTA PRINCIPAL DE LA SEMANA</b>", styles["body_small"]),
        Paragraph(f"<b>{titulo}</b>", styles["h3"]),
        Paragraph(resumen[:400], styles["body_small"]),
        Paragraph(f"<i>Región: {region} · Fuente: {fuente}</i>", styles["body_small"]),
    ]
    t = Table([[c] for c in contenido], colWidths=[16 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 1, COLOR_ROJO_CRIT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _bullet_list(items, styles, max_n=10):
    """Lista con bullets a partir de strings o dicts con 'titulo'."""
    if not items:
        return [Paragraph("<i>Sin elementos detectados en el período.</i>", styles["body_small"])]
    out = []
    for it in items[:max_n]:
        if isinstance(it, str):
            out.append(Paragraph(f"• {it}", styles["body"]))
        elif isinstance(it, dict):
            titulo = it.get("titulo") or it.get("nombre") or ""
            url = it.get("url", "")
            fuente = it.get("fuente", "")
            line = f"• {titulo}"
            if fuente:
                line += f" <font size=8 color='#666666'>[{fuente}]</font>"
            out.append(Paragraph(line, styles["body"]))
            if url:
                out.append(Paragraph(f"&nbsp;&nbsp;&nbsp;<i>{url[:90]}</i>", styles["url"]))
    return out


# =====================================================================
# SECCIONES DEL REPORTE
# =====================================================================

def _portada(analisis, styles):
    """Página de portada vendible: score grande + tendencia + top hallazgos."""
    meta = analisis["metadata"]
    s1 = analisis["seccion_1_resumen_ejecutivo"]
    s0 = analisis.get("seccion_0_hallazgos_criticos", {})

    items = []
    items.append(Spacer(1, 1.2 * cm))
    items.append(Paragraph("INFORME EJECUTIVO · SEMANAL", styles["sub_portada"]))
    items.append(Paragraph("Análisis de Riesgo Político<br/>del Sector Minero",
                            styles["h1_portada"]))
    items.append(Spacer(1, 0.4 * cm))
    items.append(Paragraph(f"<b>{meta.get('empresa', 'Sector Minero Peruano')}</b>",
                            styles["sub_portada"]))
    items.append(Paragraph(meta.get("periodo", ""), styles["sub_portada"]))
    items.append(Spacer(1, 0.6 * cm))

    # Caja de score + tendencia (lado a lado)
    score_widget = _score_box(s1["score_global"], s1["nivel"], styles,
                                ancho=8.5 * cm, alto=4 * cm)
    tendencia_text = s0.get("tendencia", "↔ ESTABLE")
    n_crit = s0.get("n_factores_criticos", 0)
    n_altos = s0.get("n_factores_altos", 0)
    tendencia_widget = Table(
        [[Paragraph("<b>TENDENCIA</b>", styles["body_small"])],
         [Paragraph(f"<b>{tendencia_text}</b>", styles["score_label"])],
         [Paragraph(f"{n_crit} factor(es) CRÍTICO · {n_altos} factor(es) ALTO",
                    styles["body_small"])],
         [Paragraph(f"<b>→ {s0.get('recomendacion_inmediata', '—')}</b>",
                    styles["body_small"])]],
        colWidths=[8.5 * cm], rowHeights=[None, None, None, None]
    )
    tendencia_widget.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 2, COLOR_AZUL_CORP),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    items.append(Table([[score_widget, tendencia_widget]],
                        colWidths=[8.5 * cm, 8.5 * cm]))
    items.append(Spacer(1, 0.6 * cm))

    # Top hallazgos
    items.append(Paragraph("HALLAZGOS CLAVE DE LA SEMANA", styles["h2"]))
    items.append(_hallazgos_criticos_box(s0.get("hallazgos", []), styles))
    items.append(Spacer(1, 0.6 * cm))

    items.append(Paragraph(
        f"<i>Preparado por: <b>APURISK · OSINT Riesgo Político Perú</b></i><br/>"
        f"<i>Solicitante: {meta.get('solicitante', '—')}</i> · "
        f"<i>Semana ISO {meta.get('semana_iso', '?')} · {meta.get('año', '')}</i><br/>"
        f"<font size=8 color='#999999'>Documento confidencial. Para uso exclusivo del solicitante. "
        f"Reproducción prohibida sin autorización.</font>",
        styles["body_small"]
    ))
    return items


def _seccion_hipotesis(analisis, styles):
    """Sección dedicada a la hipótesis del analista + URLs/docs aportados."""
    s = analisis.get("seccion_hipotesis", {})
    items = [
        Paragraph("Hipótesis y Marco de Análisis", styles["h1"]),
        Spacer(1, 0.3 * cm),
        Paragraph("Hipótesis del analista", styles["h2"]),
        Paragraph(s.get("hipotesis_analista", "—"), styles["callout"]),
        Spacer(1, 0.3 * cm),
    ]
    urls = s.get("urls_adjuntas", [])
    if urls:
        items.append(Paragraph(f"URLs aportadas por el analista ({len(urls)})", styles["h2"]))
        for u in urls:
            items.append(Paragraph(f"• <a href='{u.get('url','')}' color='#3b6ea8'>{u.get('url','')[:90]}</a>",
                                    styles["body_small"]))
    docs = s.get("documentos_adjuntos", [])
    if docs:
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph(f"Documentos analizados ({len(docs)})", styles["h2"]))
        for d in docs:
            items.append(Paragraph(f"• {d.get('nombre','documento')} "
                                    f"<font size=8 color='#666666'>"
                                    f"({d.get('tamano_chars', 0)} caracteres procesados)</font>",
                                    styles["body_small"]))
    items.append(Spacer(1, 0.4 * cm))
    items.append(Paragraph("Marco metodológico", styles["h2"]))
    items.append(Paragraph(s.get("marco_metodologico", "—"), styles["body"]))
    return items


def _seccion_datos_contundentes(analisis, styles):
    """Sección con cifras clave del sector minero peruano."""
    datos = analisis.get("seccion_datos_contundentes", [])
    items = [
        Paragraph("Datos Contundentes del Sector Minero Peruano", styles["h1"]),
        Spacer(1, 0.2 * cm),
        Paragraph(
            "Cifras de referencia clave para dimensionar el contexto en el que se "
            "desarrolla la operación. Permite al ejecutivo capturar la magnitud "
            "de los riesgos y oportunidades en segundos.",
            styles["body"]
        ),
        Spacer(1, 0.5 * cm),
    ]
    # Grid 2 columnas
    rows = []
    for i in range(0, len(datos), 2):
        col1 = _dato_contundente_box(datos[i], styles)
        col2 = _dato_contundente_box(datos[i + 1], styles) if i + 1 < len(datos) else ""
        rows.append([col1, col2])
    if rows:
        t = Table(rows, colWidths=[8 * cm, 8 * cm])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        items.append(t)
    return items


def _seccion_matriz_pxi(analisis, styles):
    """Sección con matriz P×I visual + leyenda numerada."""
    factores = analisis["factores_pxi"]
    items = [
        Paragraph("Matriz P×I de Factores de Riesgo", styles["h1"]),
        Spacer(1, 0.2 * cm),
        Paragraph(
            "Mapeo visual de los 13 factores propietarios del sector minero. "
            "Cada burbuja representa un factor posicionado según su probabilidad "
            "y su impacto sectorial. Color según nivel de riesgo: "
            "<font color='#c53030'>● CRÍTICO</font> · "
            "<font color='#dd6b20'>● ALTO</font> · "
            "<font color='#d69e2e'>● MEDIO</font> · "
            "<font color='#38a169'>● BAJO</font>.",
            styles["body"]
        ),
        Spacer(1, 0.3 * cm),
        _matriz_pxi_visual(factores),
        Spacer(1, 0.4 * cm),
        Paragraph("Leyenda de factores numerados", styles["h2"]),
        _matriz_pxi_leyenda(factores, styles),
    ]
    return items


def _seccion_stakeholders_ampliados(analisis, styles):
    """Sección de stakeholders ampliados: formales + ilícitos + sociales + ONGs."""
    s = analisis.get("seccion_stakeholders_ampliados", {})
    items = [
        Paragraph("Stakeholders Ampliados del Ecosistema Minero", styles["h1"]),
        Paragraph(s.get("diagnostico", "—"), styles["callout"]),
        Spacer(1, 0.3 * cm),
    ]

    bloques = [
        ("Actores formales del Estado", s.get("actores_formales_estado", []), COLOR_AZUL_CORP),
        ("Actores ilícitos detectados en cobertura semanal", s.get("actores_iliticos_detectados", []), COLOR_ROJO_CRIT),
        ("Actores sociales y sindicales", s.get("actores_sociales", []), COLOR_NARANJA),
        ("ONGs y observadores institucionales", s.get("ongs_observadores", []), COLOR_AZUL_CLARO),
        ("Actores internacionales relevantes", s.get("actores_internacionales", []), COLOR_AZUL_CLARO),
    ]
    for titulo, lista, color in bloques:
        if not lista:
            continue
        items.append(Paragraph(f"<font color='{color.hexval()}'><b>{titulo}</b></font>", styles["h2"]))
        rows = []
        for actor in lista[:10]:
            rows.append([
                Paragraph(f"<b>{actor.get('actor','—')}</b>", styles["body_small"]),
                Paragraph(actor.get("rol", actor.get("tipo", "—")), styles["body_small"]),
                Paragraph(actor.get("postura", actor.get("presencia", "—")), styles["body_small"]),
            ])
        t = Table([["Actor", "Rol", "Postura/Presencia"]] + rows,
                   colWidths=[6 * cm, 6 * cm, 4 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), color),
            ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        items.append(t)
        items.append(Spacer(1, 0.3 * cm))
    return items


def _seccion_alertas_tempranas(analisis, styles):
    """Sección de alertas tempranas semaforizadas."""
    s = analisis.get("seccion_alertas_tempranas", {})
    alertas = s.get("alertas_top", [])
    items = [
        Paragraph("Alertas Tempranas Semaforizadas", styles["h1"]),
        Paragraph(
            "Alertas accionables ordenadas por severidad. Cada alerta incluye "
            "indicador, plazo de acción sugerido, responsable interno y la acción "
            "inmediata recomendada para mitigación.",
            styles["body"]
        ),
        Spacer(1, 0.2 * cm),
        Paragraph(
            f"<b>{s.get('total_alertas_criticas', 0)} críticas (rojo)</b> · "
            f"<b>{s.get('total_alertas_altas', 0)} altas (naranja)</b> · "
            f"<b>{s.get('total_alertas_medias', 0)} medias (amarillo)</b>",
            styles["callout"]
        ),
        Spacer(1, 0.3 * cm),
        _alertas_semaforo_table(alertas, styles),
    ]
    return items


def _seccion_recomendaciones_por_plazo(analisis, styles):
    """Sección con recomendaciones agrupadas por horizonte temporal."""
    s = analisis.get("seccion_recomendaciones_por_plazo", {})
    items = [
        Paragraph("Recomendaciones Operativas por Plazo", styles["h1"]),
        Paragraph(
            f"<b>Responsable global:</b> {s.get('responsable_global', '—')} · "
            f"<b>Frecuencia de revisión:</b> {s.get('frecuencia_revision', '—')}",
            styles["callout"]
        ),
        Spacer(1, 0.3 * cm),
    ]
    plazos = [
        ("🔴 ACCIONES INMEDIATAS (0-7 días)", "acciones_inmediatas_0_7_dias", COLOR_ROJO_CRIT),
        ("🟠 ACCIONES TÁCTICAS (8-30 días)", "acciones_tacticas_8_30_dias", COLOR_NARANJA),
        ("🟢 ACCIONES ESTRATÉGICAS (31-90 días)", "acciones_estrategicas_31_90_dias", COLOR_VERDE),
    ]
    for titulo, key, color in plazos:
        acciones = s.get(key, [])
        items.append(Paragraph(f"<font color='{color.hexval()}'><b>{titulo}</b></font>", styles["h2"]))
        if not acciones:
            items.append(Paragraph("<i>Sin acciones específicas en este horizonte.</i>",
                                    styles["body_small"]))
        else:
            for a in acciones:
                items.append(Paragraph(f"• {a}", styles["body"]))
        items.append(Spacer(1, 0.2 * cm))
    return items


def _seccion_implicaciones_cliente(analisis, styles):
    """Sección con implicaciones específicas para la operación del cliente."""
    s = analisis.get("seccion_implicaciones_cliente", {})
    empresa = s.get("empresa_referida", "su empresa")
    items = [
        Paragraph(f"Implicaciones para {empresa}", styles["h1"]),
        Paragraph(
            "Para cada factor de riesgo identificado, se detalla su implicación "
            "directa sobre la operación, finanzas, reputación y compliance del "
            "cliente. Este vínculo entre análisis y consecuencia es el corazón "
            "del producto APURISK.",
            styles["body"]
        ),
        Spacer(1, 0.4 * cm),
    ]
    for impl in s.get("implicaciones", []):
        color_nivel = COLOR_POR_NIVEL.get(impl["nivel"], COLOR_GRIS_TEXTO)
        items.append(_implicacion_callout(
            f'{impl["factor"]} (Score {impl["score"]} · {impl["nivel"]})',
            impl["implicacion"], styles,
            color=color_nivel,
        ))
        items.append(Spacer(1, 0.2 * cm))
    return items


def _seccion_bibliografia(analisis, styles):
    """Sección de fuentes y bibliografía."""
    s = analisis.get("seccion_fuentes_bibliografia", {})
    items = [
        Paragraph("Fuentes y Bibliografía", styles["h1"]),
        Paragraph(
            f"<b>Total fuentes únicas APURISK consultadas:</b> {s.get('n_fuentes_unicas', 0)}",
            styles["callout"]
        ),
        Spacer(1, 0.2 * cm),
    ]
    # Top fuentes APURISK
    fuentes_top = s.get("fuentes_apurisk", [])
    if fuentes_top:
        items.append(Paragraph("Fuentes principales consultadas (por volumen)", styles["h2"]))
        rows = [[f["nombre"], str(f["n_articulos"])] for f in fuentes_top[:20]]
        t = Table([["Fuente", "Artículos"]] + rows, colWidths=[12 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL_CORP),
            ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        items.append(t)
        items.append(Spacer(1, 0.3 * cm))

    # Fuentes especializadas
    especializadas = s.get("fuentes_especializadas_internacionales", [])
    if especializadas:
        items.append(Paragraph("Fuentes especializadas internacionales de referencia",
                                styles["h2"]))
        for f in especializadas:
            items.append(Paragraph(f"• {f}", styles["body_small"]))
    return items


def _tabla_contenidos(styles):
    items = [
        Paragraph("Tabla de Contenidos", styles["h1"]),
        Spacer(1, 0.4 * cm),
    ]
    secciones = [
        ("Hallazgos Críticos · Portada", 1),
        ("Hipótesis y Marco de Análisis", 3),
        ("Datos Contundentes del Sector", 4),
        ("Matriz P×I de Factores de Riesgo (visual)", 5),
        ("1. Resumen ejecutivo", 7),
        ("2. Perfil de la operación monitoreada", 8),
        ("3. Pulso comunitario", 9),
        ("4. Bloqueos y movilizaciones", 10),
        ("5. Riesgo regulatorio sectorial", 11),
        ("6. Posición política nacional sobre minería", 12),
        ("7. Riesgo socioambiental", 13),
        ("8. Minería ilegal y crimen organizado transnacional", 14),
        ("9. Presión internacional · Estados Unidos", 15),
        ("10. Capital Markets y sentimiento inversor", 16),
        ("11. Corrupción sectorial e índices internacionales", 17),
        ("12. Implicaciones para su empresa (por factor)", 18),
        ("13. Stakeholders ampliados (formales + ilícitos + sociales)", 19),
        ("14. Alertas tempranas semaforizadas", 20),
        ("15. Escenarios prospectivos 30-90 días", 21),
        ("16. Recomendaciones operativas por plazo", 22),
        ("17. Fuentes y bibliografía", 23),
    ]
    data = [[Paragraph(t, styles["body"]), Paragraph(str(p), styles["body"])]
            for t, p in secciones]
    t = Table(data, colWidths=[14 * cm, 2 * cm])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(t)
    return items


def _seccion_1(analisis, styles):
    s = analisis["seccion_1_resumen_ejecutivo"]
    items = [
        Paragraph("1. Resumen Ejecutivo", styles["h1"]),
        Spacer(1, 0.2 * cm),
        Paragraph(s["headline"], styles["callout"]),
        Spacer(1, 0.4 * cm),
        Paragraph("Semáforo por dimensión de riesgo", styles["h2"]),
        _semaforo_table(s["semaforo"], styles),
        Spacer(1, 0.4 * cm),
        Paragraph("Matriz P×I de factores de riesgo", styles["h2"]),
        _chart_factores_pxi(analisis["factores_pxi"]),
        Spacer(1, 0.4 * cm),
        _alerta_box(s["alerta_principal_semana"], styles),
    ]
    return items


def _seccion_2(analisis, styles):
    s = analisis["seccion_2_perfil_operacion"]
    items = [
        Paragraph("2. Perfil de la Operación Monitoreada", styles["h1"]),
        Paragraph(f"<b>Empresa / sector:</b> {s['empresa']}", styles["body"]),
        Paragraph(f"<b>Alcance geográfico:</b> {s['alcance_geografico']}", styles["body"]),
        Paragraph(f"<b>Departamentos de operación:</b> {', '.join(s['departamentos_operacion'])}", styles["body"]),
        Spacer(1, 0.3 * cm),
        Paragraph("Unidades mineras en zona de monitoreo", styles["h2"]),
    ]
    if s["unidades_mineras_zona"]:
        unidades = [[u["departamento"], u["unidad"]] for u in s["unidades_mineras_zona"]]
        t = Table([["Departamento", "Unidad / Operador"]] + unidades,
                   colWidths=[4 * cm, 12 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL_CORP),
            ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        items.append(t)
    items.append(Spacer(1, 0.3 * cm))
    items.append(Paragraph("Fuentes OSINT monitoreadas", styles["h2"]))
    items.append(Paragraph(", ".join(s["fuentes_monitoreadas"]), styles["body_small"]))
    return items


def _seccion_3(analisis, styles):
    s = analisis["seccion_3_pulso_comunitario"]
    items = [
        Paragraph("3. Pulso Comunitario", styles["h1"]),
        Paragraph(s["diagnostico"], styles["callout"]),
        Paragraph(f"<b>Indicadores:</b> {s['n_comunidades_activas']} comunidades activas · "
                  f"{s['n_demandas_detectadas']} demandas detectadas", styles["body"]),
        Spacer(1, 0.3 * cm),
        Paragraph("Demandas y movilizaciones de la semana", styles["h2"]),
    ]
    items.extend(_bullet_list(s["demandas_top"], styles, max_n=8))
    if s["conflictos_comunales"]:
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Conflictos comunales activos detectados", styles["h2"]))
        items.extend(_bullet_list(s["conflictos_comunales"], styles, max_n=8))
    return items


def _seccion_4(analisis, styles):
    s = analisis["seccion_4_bloqueos_movilizaciones"]
    items = [
        Paragraph("4. Bloqueos y Movilizaciones", styles["h1"]),
        Paragraph(s["tendencia"], styles["callout"]),
        Paragraph(f"<b>Bloqueos esta semana:</b> {s['bloqueos_semana']} · "
                  f"<b>Movilizaciones:</b> {s['movilizaciones_semana']}", styles["body"]),
        Spacer(1, 0.3 * cm),
    ]
    if s["bloqueos_detallados"]:
        items.append(Paragraph("Bloqueos detectados", styles["h2"]))
        items.extend(_bullet_list(s["bloqueos_detallados"], styles, max_n=10))
    if s["movilizaciones_detalladas"]:
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Movilizaciones", styles["h2"]))
        items.extend(_bullet_list(s["movilizaciones_detalladas"], styles, max_n=10))
    return items


def _seccion_5(analisis, styles):
    s = analisis["seccion_5_riesgo_regulatorio"]
    items = [
        Paragraph("5. Riesgo Regulatorio Sectorial", styles["h1"]),
        Paragraph(s["diagnostico"], styles["callout"]),
    ]
    if s["proyectos_ley_relevantes"]:
        items.append(Paragraph("Proyectos de ley con impacto sectorial", styles["h2"]))
        items.extend(_bullet_list(s["proyectos_ley_relevantes"], styles, max_n=10))
    items.append(Spacer(1, 0.3 * cm))
    items.append(Paragraph("Noticias regulatorias de la semana", styles["h2"]))
    items.extend(_bullet_list(s["noticias_regulatorias"], styles, max_n=10))
    return items


def _seccion_6(analisis, styles):
    s = analisis["seccion_6_posicion_politica"]
    items = [
        Paragraph("6. Posición Política Nacional sobre Minería", styles["h1"]),
        Paragraph(s["diagnostico"], styles["callout"]),
        Paragraph("Declaraciones de figuras del Ejecutivo y sectoriales", styles["h2"]),
    ]
    items.extend(_bullet_list(s["declaraciones_oficiales"], styles, max_n=10))
    return items


def _seccion_7(analisis, styles):
    s = analisis["seccion_7_riesgo_socioambiental"]
    items = [
        Paragraph("7. Riesgo Socioambiental", styles["h1"]),
        Paragraph(s["diagnostico"], styles["callout"]),
        Paragraph(f"<b>Incidentes detectados en la semana:</b> {s['n_incidentes']}", styles["body"]),
        Spacer(1, 0.3 * cm),
        Paragraph("Eventos socioambientales reportados", styles["h2"]),
    ]
    items.extend(_bullet_list(s["incidentes_ambientales"], styles, max_n=10))
    return items


def _seccion_8(analisis, styles):
    s = analisis["seccion_8_inteligencia_regional"]
    items = [
        Paragraph("8. Inteligencia Regional Específica", styles["h1"]),
        Paragraph(s["diagnostico"], styles["callout"]),
        Paragraph(f"<b>Items de cobertura regional:</b> {s['n_items_regionales']}", styles["body"]),
        Spacer(1, 0.3 * cm),
        Paragraph("Cobertura de medios regionales", styles["h2"]),
    ]
    items.extend(_bullet_list(s["cobertura_regional"], styles, max_n=10))
    return items


def _seccion_9(analisis, styles):
    s = analisis["seccion_9_stakeholders"]
    items = [
        Paragraph("9. Stakeholders Relevantes Mapeados", styles["h1"]),
        Paragraph(s["diagnostico"], styles["body"]),
        Spacer(1, 0.3 * cm),
        Paragraph("Instituciones con mayor mención", styles["h2"]),
    ]
    if s["instituciones_top"]:
        inst = [[Paragraph(str(item[0]) if isinstance(item, (list, tuple)) else str(item), styles["body_small"]),
                 Paragraph(str(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else "—",
                            styles["body_small"])]
                 for item in s["instituciones_top"][:8]]
        t = Table([["Institución", "Menciones"]] + inst, colWidths=[12 * cm, 4 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL_CORP),
            ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        items.append(t)
    if s.get("ongs_activas"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("ONGs ambientales activas en Perú", styles["h2"]))
        items.append(Paragraph(", ".join(s["ongs_activas"]), styles["body_small"]))
    return items


def _seccion_10(analisis, styles):
    s = analisis["seccion_10_sentimiento_mediatico"]
    items = [
        Paragraph("10. Sentimiento Mediático", styles["h1"]),
        Paragraph(s["diagnostico"], styles["callout"]),
        Spacer(1, 0.2 * cm),
    ]
    data = [
        ["Polaridad", "Conteo"],
        ["Positivo", str(s["positivo"])],
        ["Negativo", str(s["negativo"])],
        ["Neutral", str(s["neutral"])],
        ["Polaridad neta (-1 a +1)", str(s["polaridad_neta"])],
    ]
    t = Table(data, colWidths=[8 * cm, 4 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL_CORP),
        ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [COLOR_BLANCO, COLOR_GRIS_CLARO]),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS_BORDE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, COLOR_GRIS_BORDE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    items.append(t)
    return items


def _seccion_11(analisis, styles):
    s = analisis["seccion_11_escenarios"]
    items = [
        Paragraph("11. Escenarios Prospectivos a 30-90 días", styles["h1"]),
        Paragraph(f"<b>Indicador clave a monitorear:</b> {s['indicador_clave_a_monitorear']}",
                  styles["callout"]),
        Spacer(1, 0.3 * cm),
    ]
    for label, key in [("Escenario Base", "escenario_base"),
                        ("Escenario de Deterioro", "escenario_deterioro"),
                        ("Escenario de Crisis", "escenario_crisis")]:
        e = s[key]
        items.append(Paragraph(f"{label} <font color='#666666'>· Probabilidad: {e['probabilidad']}%</font>", styles["h2"]))
        items.append(Paragraph(f"<b>{e['titulo']}</b>", styles["h3"]))
        items.append(Paragraph(e["descripcion"], styles["body"]))
        items.append(Paragraph("<b>Disparadores observables:</b>", styles["body_small"]))
        for d in e["disparadores_observables"]:
            items.append(Paragraph(f"• {d}", styles["body_small"]))
        items.append(Spacer(1, 0.3 * cm))
    return items


def _seccion_11b_mineria_ilegal(analisis, styles):
    """Sección avanzada: Minería ilegal y Crimen Organizado Transnacional."""
    s = analisis.get("seccion_11_mineria_ilegal_crimen", {})
    if not s:
        return []
    items = [
        Paragraph("11.B Minería Ilegal y Crimen Organizado Transnacional", styles["h1"]),
        Paragraph(s.get("diagnostico", ""), styles["callout"]),
        Paragraph(
            f"<b>Items minería ilegal:</b> {s.get('n_items_ilegal', 0)} · "
            f"<b>Narco-minería:</b> {s.get('n_items_narco_mineria', 0)} · "
            f"<b>Crimen clasificado:</b> {len(s.get('crimen_organizado_clasificado', []))}",
            styles["body"]
        ),
        Spacer(1, 0.3 * cm),
    ]
    if s.get("items_mineria_ilegal"):
        items.append(Paragraph("Minería ilegal y artesanal informal", styles["h2"]))
        items.extend(_bullet_list(s["items_mineria_ilegal"], styles, max_n=8))
    if s.get("items_narco_mineria"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Narco-minería, lavado de oro y crimen transnacional", styles["h2"]))
        items.extend(_bullet_list(s["items_narco_mineria"], styles, max_n=8))
    if s.get("departamentos_criticos"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Departamentos críticos identificados", styles["h2"]))
        items.append(Paragraph(", ".join(s["departamentos_criticos"]), styles["body"]))
    if s.get("actores_relevantes"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Actores institucionales de control y cooperación", styles["h2"]))
        for actor in s["actores_relevantes"]:
            items.append(Paragraph(f"• {actor}", styles["body_small"]))
    return items


def _seccion_12b_presion_eeuu(analisis, styles):
    """Sección avanzada: Presión internacional EEUU."""
    s = analisis.get("seccion_12_presion_eeuu", {})
    if not s:
        return []
    items = [
        Paragraph("12.B Presión Internacional · Estados Unidos", styles["h1"]),
        Paragraph(s.get("diagnostico", ""), styles["callout"]),
        Paragraph(f"<b>Items detectados:</b> {s.get('n_items', 0)}", styles["body"]),
        Spacer(1, 0.3 * cm),
    ]
    if s.get("items_presion_eeuu"):
        items.append(Paragraph("Cobertura de presión bilateral / sanciones", styles["h2"]))
        items.extend(_bullet_list(s["items_presion_eeuu"], styles, max_n=10))
    if s.get("mecanismos_clave"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Mecanismos de EEUU a monitorear", styles["h2"]))
        for m in s["mecanismos_clave"]:
            items.append(Paragraph(f"• {m}", styles["body_small"]))
    if s.get("impacto_potencial_minero"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Impacto potencial sobre minería formal peruana", styles["h2"]))
        for imp in s["impacto_potencial_minero"]:
            items.append(Paragraph(f"• {imp}", styles["body_small"]))
    return items


def _seccion_13_capital_markets(analisis, styles):
    """Sección avanzada: Capital Markets y sentimiento inversor."""
    s = analisis.get("seccion_13_capital_markets", {})
    if not s:
        return []
    items = [
        Paragraph("13. Capital Markets y Sentimiento Inversor", styles["h1"]),
        Paragraph(s.get("diagnostico", ""), styles["callout"]),
        Paragraph(
            f"<b>Sentimiento neto:</b> {s.get('sentimiento_label', 'NEUTRAL')} · "
            f"<b>Items negativos:</b> {s.get('sentimiento_neg', 0)} · "
            f"<b>Items positivos:</b> {s.get('sentimiento_pos', 0)} · "
            f"<b>Total cobertura:</b> {s.get('n_items', 0)}",
            styles["body"]
        ),
        Spacer(1, 0.3 * cm),
    ]
    if s.get("items_capital_mercado"):
        items.append(Paragraph("Cobertura de mercados y capital", styles["h2"]))
        items.extend(_bullet_list(s["items_capital_mercado"], styles, max_n=10))
    if s.get("indicadores_a_monitorear"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Indicadores clave a monitorear", styles["h2"]))
        for ind in s["indicadores_a_monitorear"]:
            items.append(Paragraph(f"• {ind}", styles["body_small"]))
    return items


def _seccion_14_corrupcion(analisis, styles):
    """Sección avanzada: Corrupción sectorial y captura institucional."""
    s = analisis.get("seccion_14_corrupcion", {})
    if not s:
        return []
    items = [
        Paragraph("14. Corrupción Sectorial y Captura Institucional", styles["h1"]),
        Paragraph(s.get("diagnostico", ""), styles["callout"]),
        Paragraph(f"<b>Items detectados:</b> {s.get('n_items', 0)}", styles["body"]),
        Spacer(1, 0.3 * cm),
    ]
    if s.get("items_corrupcion"):
        items.append(Paragraph("Cobertura de corrupción sectorial", styles["h2"]))
        items.extend(_bullet_list(s["items_corrupcion"], styles, max_n=10))
    if s.get("indices_referencia"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Índices internacionales de referencia", styles["h2"]))
        for ind in s["indices_referencia"]:
            items.append(Paragraph(f"• {ind}", styles["body_small"]))
    if s.get("instituciones_clave"):
        items.append(Spacer(1, 0.3 * cm))
        items.append(Paragraph("Instituciones de control y anticorrupción", styles["h2"]))
        for inst in s["instituciones_clave"]:
            items.append(Paragraph(f"• {inst}", styles["body_small"]))
    return items


def _seccion_12(analisis, styles):
    s = analisis["seccion_12_recomendaciones"]
    items = [
        Paragraph("12. Recomendaciones Operativas", styles["h1"]),
        Paragraph(f"<b>Horizonte de revisión:</b> {s['horizonte_recomendado_revision']} · "
                  f"<b>Responsable sugerido:</b> {s['comite_responsable_sugerido']}",
                  styles["callout"]),
        Spacer(1, 0.3 * cm),
        Paragraph("Acciones recomendadas para la semana entrante", styles["h2"]),
    ]
    for r in s["recomendaciones"]:
        items.append(Paragraph(f"• {r}", styles["body"]))
    items.append(Spacer(1, 0.6 * cm))
    items.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_GRIS_BORDE))
    items.append(Spacer(1, 0.3 * cm))
    items.append(Paragraph(
        "DISCLAIMER: Este análisis fue elaborado por APURISK con base exclusivamente en "
        "fuentes abiertas (OSINT). No constituye asesoría financiera, legal, ni de inversión, "
        "y no debe interpretarse como inteligencia clasificada ni opinión política partidaria. "
        "Las recomendaciones son sugerencias preliminares de acción que deben ser validadas "
        "por el equipo directivo del cliente y, cuando corresponda, por asesores legales "
        "especializados. APURISK no asume responsabilidad por decisiones tomadas "
        "exclusivamente sobre la base de este reporte.",
        styles["disclaimer"]
    ))
    return items


# =====================================================================
# FUNCIÓN PRINCIPAL
# =====================================================================

def generar_reporte_minera_pdf(output_path: str, analisis: dict) -> str:
    """Genera el PDF de Riesgo Político Minero.

    Args:
        output_path: ruta destino del archivo PDF
        analisis: dict producido por riesgo_minera.analizar_riesgo_minera()

    Returns:
        ruta del archivo generado
    """
    meta = analisis["metadata"]
    styles = _build_styles()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2.2 * cm, bottomMargin=2.2 * cm,
        title=f"APURISK Riesgo Minero {meta.get('semana_iso', '?')}-{meta.get('año', '')}",
        author="APURISK · OSINT Riesgo Político Perú",
        subject="Reporte semanal de riesgo político sector minero",
    )

    story = []
    # PORTADA
    story.extend(_portada(analisis, styles))
    story.append(PageBreak())

    # TABLA DE CONTENIDOS
    story.extend(_tabla_contenidos(styles))
    story.append(PageBreak())

    # === NUEVAS SECCIONES PREMIUM (rediseño vendible mayo 2026) ===
    # Hipótesis del analista + URLs/docs aportados
    story.extend(_seccion_hipotesis(analisis, styles))
    story.append(PageBreak())

    # Datos contundentes del sector minero peruano
    story.extend(_seccion_datos_contundentes(analisis, styles))
    story.append(PageBreak())

    # Matriz P×I visual con burbujas
    story.extend(_seccion_matriz_pxi(analisis, styles))
    story.append(PageBreak())

    # === SECCIONES ANALÍTICAS (12 originales) ===
    analiticas = [
        _seccion_1, _seccion_2, _seccion_3, _seccion_4,
        _seccion_5, _seccion_6, _seccion_7,
        _seccion_11b_mineria_ilegal,
        _seccion_12b_presion_eeuu,
        _seccion_13_capital_markets,
        _seccion_14_corrupcion,
    ]
    for fn in analiticas:
        story.extend(fn(analisis, styles))
        story.append(PageBreak())

    # === SECCIONES VENDIBLES DE CIERRE ===
    # Implicaciones específicas para la empresa cliente
    story.extend(_seccion_implicaciones_cliente(analisis, styles))
    story.append(PageBreak())

    # Stakeholders ampliados (formales + ilícitos + sociales + ONGs)
    story.extend(_seccion_stakeholders_ampliados(analisis, styles))
    story.append(PageBreak())

    # Alertas tempranas semaforizadas
    story.extend(_seccion_alertas_tempranas(analisis, styles))
    story.append(PageBreak())

    # Escenarios prospectivos (existente)
    story.extend(_seccion_11(analisis, styles))
    story.append(PageBreak())

    # Recomendaciones por plazo (0-7 / 8-30 / 31-90 días)
    story.extend(_seccion_recomendaciones_por_plazo(analisis, styles))
    story.append(PageBreak())

    # Fuentes y bibliografía
    story.extend(_seccion_bibliografia(analisis, styles))

    # Generar con header/footer por página
    doc.build(
        story,
        onFirstPage=lambda c, d: _header_footer(c, d, meta),
        onLaterPages=lambda c, d: _header_footer(c, d, meta),
    )
    return output_path
