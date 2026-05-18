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
# COMPONENTES VISUALES
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
    """Página de portada con score global, headline y resumen ejecutivo."""
    meta = analisis["metadata"]
    s1 = analisis["seccion_1_resumen_ejecutivo"]

    items = []
    items.append(Spacer(1, 3 * cm))
    items.append(Paragraph("REPORTE SEMANAL", styles["sub_portada"]))
    items.append(Paragraph("Riesgo Político<br/>del Sector Minero", styles["h1_portada"]))
    items.append(Spacer(1, 0.8 * cm))
    items.append(Paragraph(meta.get("empresa", "Sector Minero Peruano"), styles["sub_portada"]))
    items.append(Spacer(1, 0.5 * cm))
    items.append(Paragraph(meta.get("periodo", ""), styles["sub_portada"]))
    items.append(Spacer(1, 1 * cm))
    items.append(_score_box(s1["score_global"], s1["nivel"], styles, ancho=10 * cm, alto=5 * cm))
    items.append(Spacer(1, 0.8 * cm))
    items.append(Paragraph(s1["headline"], styles["callout"]))
    items.append(Spacer(1, 0.5 * cm))
    items.append(_alerta_box(s1["alerta_principal_semana"], styles))
    items.append(Spacer(1, 1 * cm))
    items.append(Paragraph(
        f"<i>Preparado por: APURISK · OSINT Riesgo Político Perú</i><br/>"
        f"<i>Solicitante: {meta.get('solicitante', '—')}</i><br/>"
        f"<i>Semana ISO {meta.get('semana_iso', '?')} · {meta.get('año', '')}</i>",
        styles["body_small"]
    ))
    return items


def _tabla_contenidos(styles):
    items = [
        Paragraph("Tabla de Contenidos", styles["h1"]),
        Spacer(1, 0.4 * cm),
    ]
    secciones = [
        ("1. Resumen ejecutivo", 3),
        ("2. Perfil de la operación monitoreada", 4),
        ("3. Pulso comunitario", 5),
        ("4. Bloqueos y movilizaciones", 6),
        ("5. Riesgo regulatorio sectorial", 7),
        ("6. Posición política nacional sobre minería", 8),
        ("7. Riesgo socioambiental", 9),
        ("8. Inteligencia regional específica", 10),
        ("9. Stakeholders relevantes mapeados", 11),
        ("10. Sentimiento mediático", 12),
        ("11.B Minería ilegal y crimen organizado transnacional", 13),
        ("12.B Presión internacional · Estados Unidos", 14),
        ("13. Capital Markets y sentimiento inversor", 15),
        ("14. Corrupción sectorial y captura institucional", 16),
        ("15. Escenarios prospectivos", 17),
        ("16. Recomendaciones operativas", 18),
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

    # SECCIONES (16 totales: 10 originales + 4 avanzadas + escenarios + recomendaciones)
    secuencia = [
        _seccion_1, _seccion_2, _seccion_3, _seccion_4,
        _seccion_5, _seccion_6, _seccion_7, _seccion_8,
        _seccion_9, _seccion_10,
        # Secciones avanzadas agregadas (mayo 2026)
        _seccion_11b_mineria_ilegal,
        _seccion_12b_presion_eeuu,
        _seccion_13_capital_markets,
        _seccion_14_corrupcion,
        # Cierre
        _seccion_11, _seccion_12,  # Escenarios y recomendaciones (mantienen IDs originales)
    ]
    for n, fn in enumerate(secuencia, start=1):
        story.extend(fn(analisis, styles))
        if n < len(secuencia):
            story.append(PageBreak())

    # Generar con header/footer por página
    doc.build(
        story,
        onFirstPage=lambda c, d: _header_footer(c, d, meta),
        onLaterPages=lambda c, d: _header_footer(c, d, meta),
    )
    return output_path
