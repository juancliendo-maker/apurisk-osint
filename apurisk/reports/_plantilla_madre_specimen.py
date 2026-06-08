"""Plantilla Madre · PDF Specimen.

Documento de muestra con TODOS los elementos visuales canónicos aprobados
en el inventario (tarea #45). Sirve como referencia visual del sistema de
diseño antes de codificar la plantilla madre como módulo reutilizable.

Contenido ficticio. No usa datos reales.
"""
from datetime import datetime
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image, PageBreak,
)
# Importar el velocímetro canónico desde el módulo Strategic
try:
    from .strategic_daily_brief import GaugeRiesgo
except ImportError:
    from apurisk.reports.strategic_daily_brief import GaugeRiesgo


# =====================================================================
# PALETA CANÓNICA (Bloque 1.2 del inventario aprobado)
# =====================================================================
NAVY_BRAND = colors.HexColor("#1e3a8a")
BODY_DARK = colors.HexColor("#0f172a")
TXT_SECONDARY = colors.HexColor("#475569")
TXT_TERTIARY = colors.HexColor("#94a3b8")
BORDER = colors.HexColor("#e2e8f0")
BG_LIGHT = colors.HexColor("#f8fafc")
BG_CARD = colors.HexColor("#f1f5f9")
BG_INSIGHT = colors.HexColor("#eff6ff")

ESTABLE = colors.HexColor("#22c55e")
BAJO = colors.HexColor("#84cc16")
MODERADO = colors.HexColor("#f59e0b")
ELEVADO = colors.HexColor("#f97316")
CRITICO = colors.HexColor("#ef4444")


# =====================================================================
# TIPOGRAFÍA CANÓNICA (Bloque 1.3)
# =====================================================================
def estilos_canonicos():
    return {
        "h1": ParagraphStyle(
            "h1", fontSize=19, leading=22, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=1,
        ),
        "h2": ParagraphStyle(
            "h2", fontSize=12, leading=14, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=0,
        ),
        "h3": ParagraphStyle(
            "h3", fontSize=13, leading=15, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=3,
        ),
        "label": ParagraphStyle(
            "label", fontSize=9, leading=11, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=2,
            spaceBefore=8,
        ),
        "body": ParagraphStyle(
            "body", fontSize=9.5, leading=12.5, textColor=BODY_DARK,
            fontName="Helvetica", alignment=TA_JUSTIFY, spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "meta", fontSize=8, leading=10, textColor=TXT_TERTIARY,
            fontName="Helvetica", alignment=TA_LEFT,
        ),
    }


# =====================================================================
# HEADER CANÓNICO (Bloque 3)
# =====================================================================
def header_canonico(fecha_iso: str):
    """Logo NAVY izq 60pt + bloque fecha sobrio derecha (Opción B)."""
    base = Path(__file__).parent.parent / "static"
    logo_path = base / "thalos-full-logo-navy.png"

    # Logo NAVY 60pt
    if logo_path.exists():
        from PIL import Image as PILImage
        with PILImage.open(str(logo_path)) as im:
            w, h = im.size
        aspect = w / h
        logo = Image(str(logo_path), width=60 * aspect, height=60)
    else:
        logo = Paragraph("<b>THALOS</b>",
                          ParagraphStyle("logo_fb", fontSize=24,
                                          textColor=NAVY_BRAND))

    # Bloque fecha — Opción B: texto sobrio gris a la derecha
    try:
        dt = datetime.strptime(fecha_iso[:10], "%Y-%m-%d")
        dias = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES",
                "VIERNES", "SÁBADO", "DOMINGO"]
        meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
                  "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
                  "NOVIEMBRE", "DICIEMBRE"]
        dia, mes = dias[dt.weekday()], meses[dt.month - 1]
        fecha_larga = f"{dt.day:02d} {mes} {dt.year}"
    except Exception:
        dia, fecha_larga = "—", fecha_iso[:10]

    fecha_block = Paragraph(
        f"<font color='#94a3b8' size='7'><b>FECHA DE CORTE</b></font><br/>"
        f"<font color='#475569' size='10'><b>06:00 hrs · {dia}</b></font><br/>"
        f"<font color='#475569' size='10'>{fecha_larga}</font>",
        ParagraphStyle("fecha", fontSize=9, leading=12, alignment=TA_RIGHT)
    )

    header = Table(
        [[logo, fecha_block]],
        colWidths=[10 * cm, 7 * cm],
        rowHeights=[1.8 * cm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


# =====================================================================
# COMPONENTES REUTILIZABLES (Bloque 5)
# =====================================================================
def card_insight(texto: str):
    """Card de Insight — fondo azul claro, borde lateral NAVY 3pt."""
    card = Table(
        [[Paragraph(
            f"<i>{texto}</i>",
            ParagraphStyle("ins", fontSize=10, leading=13, textColor=BODY_DARK,
                            fontName="Helvetica", alignment=TA_JUSTIFY,
                            leftIndent=6, rightIndent=6)
        )]],
        colWidths=[17 * cm],
    )
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_INSIGHT),
        ("LINEBEFORE", (0, 0), (0, 0), 3, NAVY_BRAND),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return card


def tabla_canonica(headers: list, filas: list, col_widths: list):
    """Tabla canónica con header zebra + filas alternadas."""
    header_row = [
        Paragraph(f"<font color='#94a3b8' size='7'><b>{h}</b></font>",
                   ParagraphStyle(f"h{i}", fontSize=7, alignment=TA_LEFT))
        for i, h in enumerate(headers)
    ]
    rows = [header_row] + filas
    tbl = Table(rows, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BG_CARD),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BG_LIGHT, colors.white]),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, NAVY_BRAND),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


def bloque_visual_riesgo(score: float, etiqueta_riesgo: str,
                           tendencia_arrow: str, tendencia_delta: float,
                           tendencia_label: str, tendencia_color,
                           edi_score: int, edi_etiqueta: str,
                           edi_arrow: str, edi_delta: float, edi_color):
    """Bloque visual del Riesgo — Velocímetro izquierda + 2 KPI cards apiladas.

    Componente canónico que va SIEMPRE antes de la Lectura Estratégica.
    Da al lector un panorama visual del nivel de riesgo antes de leer el
    insight interpretativo.
    """
    # Velocímetro semicircular — protagonista a la izquierda
    gauge = GaugeRiesgo(score, etiqueta_riesgo, ancho=5.5 * cm, alto=4.0 * cm)

    # KPI cards apiladas a la derecha
    t_color_hex = tendencia_color.hexval()
    e_color_hex = edi_color.hexval()
    sign = "+" if tendencia_delta > 0 else ("" if tendencia_delta == 0 else "")

    card_tendencia = Table([
        [Paragraph(
            "<font color='#94a3b8'><b>RIESGO POLÍTICO · PERÚ</b></font>",
            ParagraphStyle("t1", fontSize=6.5, leading=8, alignment=TA_LEFT)
        )],
        [Table(
            [[
                Paragraph(f"<font color='{t_color_hex}'><b>{tendencia_arrow}</b></font>",
                          ParagraphStyle("ta", fontSize=20, leading=22, alignment=TA_CENTER)),
                Paragraph(f"<font color='{t_color_hex}'><b>{sign}{tendencia_delta:.1f}</b></font>",
                          ParagraphStyle("td", fontSize=15, leading=17, alignment=TA_LEFT)),
            ]],
            colWidths=[1.1 * cm, 3.4 * cm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )],
        [Paragraph(
            f"<font color='{t_color_hex}'><b>{tendencia_label}</b></font>  "
            f"<font color='#94a3b8' size='7'>↑ Mayor = mayor riesgo</font>",
            ParagraphStyle("t3", fontSize=8.5, leading=10, alignment=TA_LEFT)
        )],
    ], colWidths=[5.5 * cm])
    card_tendencia.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    card_edi = Table([
        [Paragraph(
            "<font color='#a855f7'><b>ESTADO DE DERECHO · EDI</b></font>",
            ParagraphStyle("e1", fontSize=6.5, leading=8, alignment=TA_LEFT)
        )],
        [Table(
            [[
                Paragraph(f"<font color='{e_color_hex}'><b>{edi_score}</b></font>",
                          ParagraphStyle("ev", fontSize=20, leading=22, alignment=TA_LEFT)),
                Paragraph(f"<font color='#94a3b8'>/100</font>  "
                          f"<font color='{e_color_hex}'><b>{edi_etiqueta}</b></font>",
                          ParagraphStyle("el", fontSize=9, leading=11, alignment=TA_LEFT)),
            ]],
            colWidths=[1.2 * cm, 3.3 * cm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )],
        [Paragraph(
            f"<font color='#475569'>{edi_arrow} {edi_delta:+.1f} (7d)</font>  "
            f"<font color='#94a3b8' size='7'>↑ Mayor = mejor</font>",
            ParagraphStyle("e3", fontSize=8.5, leading=10, alignment=TA_LEFT)
        )],
    ], colWidths=[5.5 * cm])
    card_edi.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f3ff")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#a855f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    cards_apiladas = Table(
        [[card_tendencia], [card_edi]],
        colWidths=[5.5 * cm],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (0, 0), 0),
            ("TOPPADDING", (0, 1), (0, 1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    # Bloque completo: velocímetro IZQ + ESPACIO ARMÓNICO + cards DER
    # Layout 3 columnas (total 17cm = ancho útil):
    #   - Col 1 (6.5cm): velocímetro a la izquierda con su propio aire
    #   - Col 2 (3.0cm): espacio armónico que separa los dos bloques
    #   - Col 3 (7.5cm): cards apiladas alineadas a la derecha
    bloque = Table(
        [[gauge, "", cards_apiladas]],
        colWidths=[6.5 * cm, 3.0 * cm, 7.5 * cm],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ("ALIGN", (2, 0), (2, 0), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    return bloque


def kpi_card(label: str, valor: str, etiqueta: str, color, ancho_cm=5.5):
    """KPI Card: label gris arriba / valor grande / etiqueta colorida."""
    color_hex = color.hexval() if hasattr(color, 'hexval') else color
    rows = [
        [Paragraph(f"<font color='#94a3b8'><b>{label.upper()}</b></font>",
                    ParagraphStyle("k1", fontSize=6.5, leading=8, alignment=TA_LEFT))],
        [Paragraph(f"<font color='{color_hex}'><b>{valor}</b></font>",
                    ParagraphStyle("k2", fontSize=22, leading=24, alignment=TA_LEFT))],
        [Paragraph(f"<font color='{color_hex}'><b>{etiqueta}</b></font>",
                    ParagraphStyle("k3", fontSize=9, leading=11, alignment=TA_LEFT))],
    ]
    tbl = Table(rows, colWidths=[ancho_cm * cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tbl


def grid_implicancias():
    """Grid 3x2 de implicancias operacionales con semáforo."""
    items = [
        ("🛣 Logística", "ALERTA", "4 amenazas", ELEVADO),
        ("🌱 ESG", "ATENCIÓN", "2 amenazas", MODERADO),
        ("⚖ Regulatorio", "ALERTA", "5 amenazas", ELEVADO),
        ("📣 Reputacional", "ATENCIÓN", "3 amenazas", MODERADO),
        ("👥 Fuerza Laboral", "MONITOREO", "1 amenaza", BAJO),
        ("⚙ Continuidad", "ATENCIÓN", "2 amenazas", MODERADO),
    ]
    cells = []
    for ic_label, estado, n, col in items:
        cells.append(Paragraph(
            f"<font color='#0f172a' size='9'><b>■ {ic_label}</b></font><br/>"
            f"<font color='{col.hexval()}' size='8.5'><b>{estado}</b></font>  "
            f"<font color='#94a3b8' size='7'>· {n}</font>",
            ParagraphStyle("imp", fontSize=8.5, leading=11, alignment=TA_LEFT)
        ))
    grid = Table(
        [[cells[0], cells[1], cells[2]],
         [cells[3], cells[4], cells[5]]],
        colWidths=[5.67 * cm, 5.67 * cm, 5.66 * cm],
        rowHeights=[1.2 * cm, 1.2 * cm],
    )
    grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return grid


# =====================================================================
# CONSTRUCCIÓN DEL SPECIMEN PDF
# =====================================================================
def construir_specimen(output_path: str) -> str:
    """Construye el PDF Specimen con todos los elementos canónicos."""
    styles = estilos_canonicos()
    fecha = "2026-06-05"

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.6 * cm, bottomMargin=1.4 * cm,
        title="Plantilla Madre · Specimen · THALOS",
        author="THALOS",
        subject="Inventario visual canónico",
    )

    story = []

    # ============== HEADER ==============
    story.append(header_canonico(fecha))
    story.append(Spacer(1, 0.15 * cm))
    story.append(HRFlowable(width="100%", color=NAVY_BRAND, thickness=1.5,
                              spaceBefore=0, spaceAfter=4))

    # ============== TÍTULOS H1 + H2 ==============
    story.append(Paragraph("Plantilla Madre · Specimen", styles["h1"]))
    story.append(Paragraph("Sistema de Diseño Canónico · THALOS Strategic Intelligence",
                            styles["h2"]))
    story.append(Spacer(1, 0.25 * cm))

    # ============== ESCENARIO DE RIESGO ==============
    # SIEMPRE va antes del Insight: el cliente VE antes de LEER.
    # Composición: Velocímetro semicircular (izq, protagonista) + espacio
    # armónico 3cm + 2 KPI cards apiladas (Tendencia + EDI) alineadas derecha.
    # El label canónico cambia según el producto:
    #   - Daily Político / 24h On-Demand → "Reporte de Riesgo Político · ÚLTIMAS 24 HRS"
    #   - Weekly Strategic              → "Reporte de Riesgo Político · ÚLTIMA SEMANA"
    #   - Monthly Strategic             → "Reporte de Riesgo Político · ÚLTIMO MES"
    #   - Crisis Brief                  → "ESCENARIO DE RIESGO Político · EVENTO ACTIVO"
    label_bloque = "Reporte de Riesgo Político · ÚLTIMAS 24 HRS"  # ejemplo Daily/24h
    story.append(Paragraph(
        f"◇ {label_bloque}",
        styles["label"]
    ))
    story.append(Paragraph("Visualización ejecutiva del ciclo actual",
                             styles["h3"]))
    story.append(bloque_visual_riesgo(
        score=58, etiqueta_riesgo="MODERADO",
        tendencia_arrow="↑", tendencia_delta=1.1,
        tendencia_label="ESTABLE", tendencia_color=MODERADO,
        edi_score=62, edi_etiqueta="TENSIONADO",
        edi_arrow="↑", edi_delta=7.2, edi_color=MODERADO,
    ))
    story.append(Spacer(1, 0.45 * cm))

    # ============== EJEMPLO DE SECCIÓN: LECTURA ESTRATÉGICA ==============
    # Va DESPUÉS del panorama visual: el cliente ya tiene el contexto del riesgo
    # y ahora lee la interpretación narrativa.
    story.append(Paragraph("⚡ LECTURA ESTRATÉGICA", styles["label"]))
    story.append(Paragraph("Insight del día", styles["h3"]))
    story.append(card_insight(
        "Este es un Card Insight canónico: fondo azul claro suave, borde lateral "
        "navy de 3pt, padding generoso (8pt). Es el único bloque del reporte con "
        "este tratamiento visual — reserva la atención al insight principal del "
        "ciclo. La tipografía interna es Helvetica 10pt en cursiva, color "
        "BODY_DARK para legibilidad ejecutiva. Aparece SIEMPRE después del "
        "panorama visual del riesgo."
    ))
    story.append(Spacer(1, 0.4 * cm))

    # ============== TABLA CANÓNICA ==============
    story.append(Paragraph("◇ TABLA CANÓNICA", styles["label"]))
    story.append(Paragraph("Header fondo claro + filas zebra + línea NAVY", styles["h3"]))
    filas_ejemplo = []
    for nombre, rol, vinc, riesgo, col_riesgo in [
        ("Actor Político A", "Ejecutivo", "Crisis institucional", "ALTO", ELEVADO),
        ("Actor Político B", "Legislativo", "Moción de censura", "CRÍTICA", CRITICO),
        ("Actor Político C", "Judicial", "Designación JNJ", "MEDIO", MODERADO),
        ("Actor Político D", "Fiscalía", "Denuncia constitucional", "ALTO", ELEVADO),
    ]:
        filas_ejemplo.append([
            Paragraph(f"<font color='#0f172a' size='9'><b>{nombre}</b></font>",
                       ParagraphStyle("a1", fontSize=9, leading=11)),
            Paragraph(f"<font color='#475569' size='8.5'>{rol}</font>",
                       ParagraphStyle("a2", fontSize=8.5, leading=10.5)),
            Paragraph(f"<font color='#475569' size='8.5'>{vinc}</font>",
                       ParagraphStyle("a3", fontSize=8.5, leading=10.5)),
            Paragraph(f"<font color='{col_riesgo.hexval()}' size='9'><b>{riesgo}</b></font>",
                       ParagraphStyle("a4", fontSize=9, alignment=TA_CENTER)),
        ])
    story.append(tabla_canonica(
        ["ACTOR", "ROL", "VINCULADO A", "RIESGO"],
        filas_ejemplo,
        col_widths=[4.0 * cm, 4.2 * cm, 6.3 * cm, 2.5 * cm],
    ))
    story.append(Spacer(1, 0.4 * cm))

    # ============== GRID IMPLICANCIAS ==============
    story.append(Paragraph("◇ GRID DE IMPLICANCIAS", styles["label"]))
    story.append(Paragraph("Impacto en las 6 dimensiones del negocio", styles["h3"]))
    story.append(grid_implicancias())
    story.append(Spacer(1, 0.4 * cm))

    # ============== JERARQUÍA TIPOGRÁFICA ==============
    story.append(Paragraph("◇ JERARQUÍA TIPOGRÁFICA", styles["label"]))
    story.append(Paragraph("Helvetica · 6 niveles canónicos", styles["h3"]))
    story.append(Paragraph(
        "<font size='19' color='#1e3a8a'><b>H1 · Título principal del reporte (19pt bold navy)</b></font>",
        ParagraphStyle("ej1", fontSize=19, leading=22)
    ))
    story.append(Spacer(1, 0.1 * cm))
    story.append(Paragraph(
        "<font size='12' color='#1e3a8a'><b>H2 · Subtítulo del producto (12pt bold navy)</b></font>",
        ParagraphStyle("ej2", fontSize=12, leading=14)
    ))
    story.append(Spacer(1, 0.1 * cm))
    story.append(Paragraph(
        "<font size='13' color='#1e3a8a'><b>H3 · Título de sección (13pt bold navy)</b></font>",
        ParagraphStyle("ej3", fontSize=13, leading=15)
    ))
    story.append(Spacer(1, 0.1 * cm))
    story.append(Paragraph(
        "<font size='9' color='#1e3a8a'><b>LABEL · ETIQUETA DE SECCIÓN (9pt bold navy)</b></font>",
        ParagraphStyle("ej4", fontSize=9, leading=11)
    ))
    story.append(Spacer(1, 0.1 * cm))
    story.append(Paragraph(
        "<font size='9.5' color='#0f172a'>Body · Texto corrido (9.5pt regular, color BODY_DARK).</font>",
        ParagraphStyle("ej5", fontSize=9.5, leading=12.5)
    ))
    story.append(Spacer(1, 0.05 * cm))
    story.append(Paragraph(
        "<font size='8' color='#94a3b8'>Meta · Etiquetas pequeñas (8pt regular, color TXT_TERTIARY)</font>",
        ParagraphStyle("ej6", fontSize=8, leading=10)
    ))

    # ============== FOOTER (vía onFirstPage) ==============
    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
        canvas.setLineWidth(0.4)
        canvas.line(2 * cm, 1.25 * cm, A4[0] - 2 * cm, 1.25 * cm)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(TXT_SECONDARY)
        canvas.drawCentredString(
            A4[0] / 2, 1.0 * cm,
            "THALOS · Strategic Intelligence for Complex Decisions"
        )
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(TXT_TERTIARY)
        canvas.drawCentredString(
            A4[0] / 2, 0.55 * cm,
            "PLANTILLA MADRE · SPECIMEN  ·  Página 1 de 1  ·  "
            "Generado 2026-06-05  ·  CONFIDENCIAL · USO INTERNO"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output_path


if __name__ == "__main__":
    out = construir_specimen("/tmp/plantilla_madre_specimen.pdf")
    print(f"Specimen generado: {out}")
