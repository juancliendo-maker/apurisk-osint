"""Plantilla Madre THALOS v1.0 · Sistema de Diseño Canónico.

Único módulo de referencia visual para TODOS los reportes (Strategic y OSINT).
Toda extensión o modificación de identidad visual debe pasar por este archivo
— nunca por reportes individuales.

REGLAS DE GOBERNANZA:
  · Cambios en paleta, tipografía, logo, footer, márgenes → editar AQUÍ
  · Cada reporte que necesite un componente nuevo, agregarlo a esta plantilla
    y exponerlo como función pública reutilizable
  · Modificaciones particulares por reporte: permitidas SI usan los estilos
    canónicos (no inventan nuevos)
  · Versionado: cambios menores → v1.1, v1.2... · rediseño estructural → v2.0

API PÚBLICA:

  Constantes:
    NAVY_BRAND, BODY_DARK, TXT_SECONDARY, TXT_TERTIARY, BORDER, BG_LIGHT,
    BG_CARD, BG_INSIGHT, ESTABLE, BAJO, MODERADO, ELEVADO, CRITICO,
    PROSPECTIVO, ACCENT

  Estilos:
    estilos_canonicos() → dict con h1, h2, h3, label, body, meta

  Header / Footer:
    header_canonico(fecha_iso, etiqueta_corte) → Flowable
    aplicar_footer_canonico(canvas, doc, footer_marca, fecha, hora_real)

  Componentes:
    bloque_visual_riesgo(score, etiqueta_riesgo, tendencia_*, edi_*,
                         label_bloque) → Flowable
    card_insight(texto) → Flowable
    tabla_canonica(headers, filas, col_widths) → Flowable
    kpi_card(label, valor, etiqueta, color, ancho_cm) → Flowable
    grid_implicancias(items) → Flowable

  Helpers:
    crear_documento(output_path, title, author, subject) → SimpleDocTemplate
    fecha_estilizada(fecha_iso) → (hora, dia, fecha_larga)
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image,
)


# =====================================================================
# CONSTANTES CANÓNICAS · Paleta de Marca THALOS
# =====================================================================
# Marca
NAVY_BRAND = colors.HexColor("#1e3a8a")       # Títulos, líneas, fondos navy
ACCENT = colors.HexColor("#3b82f6")            # Acento azul brillante (uso limitado)
PROSPECTIVO = colors.HexColor("#a855f7")       # Acento morado (EDI / prospectivo)

# Texto
BODY_DARK = colors.HexColor("#0f172a")         # Body — máxima legibilidad
TXT_SECONDARY = colors.HexColor("#475569")     # Texto secundario
TXT_TERTIARY = colors.HexColor("#94a3b8")      # Etiquetas pequeñas

# Fondos y bordes
BORDER = colors.HexColor("#e2e8f0")
BG_LIGHT = colors.HexColor("#f8fafc")
BG_CARD = colors.HexColor("#f1f5f9")
BG_INSIGHT = colors.HexColor("#eff6ff")

# Semáforo de riesgo (5 niveles)
ESTABLE = colors.HexColor("#22c55e")
BAJO = colors.HexColor("#84cc16")
MODERADO = colors.HexColor("#f59e0b")
ELEVADO = colors.HexColor("#f97316")
CRITICO = colors.HexColor("#ef4444")

# Mapping token JSON → color canónico
COLOR_MAP = {
    "verde": ESTABLE,
    "verde-amarillo": BAJO,
    "ambar": MODERADO,
    "naranja": ELEVADO,
    "rojo": CRITICO,
}


def color_por_token(token: str):
    """Token de color del brief JSON → color canónico ReportLab."""
    return COLOR_MAP.get(token or "ambar", MODERADO)


# =====================================================================
# UTILIDADES · Fecha estilizada
# =====================================================================
DIAS_ES = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
MESES_ES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
            "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]


def fecha_estilizada(fecha_iso: str) -> tuple:
    """'2026-06-05' → ('06:00 hrs', 'VIERNES', '05 JUNIO 2026')."""
    try:
        dt = datetime.strptime(fecha_iso[:10], "%Y-%m-%d")
        return ("06:00 hrs",
                DIAS_ES[dt.weekday()],
                f"{dt.day:02d} {MESES_ES[dt.month - 1]} {dt.year}")
    except Exception:
        return ("06:00 hrs", "—", fecha_iso[:10])


# =====================================================================
# ESTILOS CANÓNICOS · Tipografía Helvetica
# =====================================================================
def estilos_canonicos() -> dict:
    """Devuelve dict con los 6 estilos canónicos de la Plantilla Madre."""
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
# HEADER CANÓNICO · Logo NAVY 60pt izq + fecha sobria gris derecha
# =====================================================================
def _cargar_logo_navy(target_height_pt: float = 60):
    """Carga el wordmark THALOS NAVY como Flowable Image."""
    base = Path(__file__).parent.parent / "static"
    png_path = base / "thalos-full-logo-navy.png"
    if not png_path.exists():
        return None
    try:
        from PIL import Image as PILImage
        with PILImage.open(str(png_path)) as im:
            w, h = im.size
        aspect = w / h
        return Image(str(png_path),
                       width=target_height_pt * aspect,
                       height=target_height_pt)
    except Exception:
        return Image(str(png_path),
                       width=target_height_pt * 4.1,
                       height=target_height_pt)


def header_canonico(fecha_iso: str, etiqueta_corte: str = "FECHA DE CORTE"):
    """Header canónico THALOS.

    Args:
        fecha_iso: 'YYYY-MM-DD' o datetime ISO.
        etiqueta_corte: 'FECHA DE CORTE' (Daily 06:00) o 'GENERADO' (24h).

    Returns:
        Flowable Table listo para insertar al inicio del story.
    """
    logo = _cargar_logo_navy(target_height_pt=60)
    if logo is None:
        logo = Paragraph(
            "<b>THALOS</b>",
            ParagraphStyle("logo_fb", fontSize=22, textColor=NAVY_BRAND)
        )

    hora, dia, fecha_larga = fecha_estilizada(fecha_iso)

    # Fecha sobria gris a la derecha (Opción B aprobada)
    fecha_block = Paragraph(
        f"<font color='#94a3b8' size='7'><b>{etiqueta_corte}</b></font><br/>"
        f"<font color='#475569' size='10'><b>{hora} · {dia}</b></font><br/>"
        f"<font color='#475569' size='10'>{fecha_larga}</font>",
        ParagraphStyle("fecha_canonica", fontSize=9, leading=12,
                        alignment=TA_RIGHT)
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


def linea_separadora_navy(thickness: float = 1.5,
                            space_before: float = 0,
                            space_after: float = 4):
    """Línea NAVY canónica bajo el header."""
    return HRFlowable(width="100%", color=NAVY_BRAND, thickness=thickness,
                       spaceBefore=space_before, spaceAfter=space_after)


# =====================================================================
# FOOTER CANÓNICO · Fijo en todas las páginas
# =====================================================================
def aplicar_footer_canonico(canvas, doc, footer_marca: str,
                              fecha: str, hora_real: str = None):
    """Footer corporativo canónico — usar en doc.build(onFirstPage=..., onLaterPages=...).

    Args:
        canvas, doc: argumentos de ReportLab.
        footer_marca: identificador del producto (ej: 'REPORTE DIARIO 06:00').
        fecha: 'YYYY-MM-DD' del corte.
        hora_real: 'HH:MM' (sólo para on-demand) o None.
    """
    canvas.saveState()
    # Línea separadora
    canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
    canvas.setLineWidth(0.4)
    canvas.line(2 * cm, 1.25 * cm, A4[0] - 2 * cm, 1.25 * cm)
    # Línea 1 — Marca
    canvas.setFont("Helvetica-Bold", 7)
    canvas.setFillColor(TXT_SECONDARY)
    canvas.drawCentredString(
        A4[0] / 2, 1.0 * cm,
        "THALOS · Strategic Intelligence for Complex Decisions"
    )
    # Línea 2 — Metadata
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(TXT_TERTIARY)
    if hora_real:
        info = (f"{footer_marca}  ·  Página {doc.page} de 2  ·  "
                f"Generado {fecha} {hora_real} hrs  ·  "
                f"CONFIDENCIAL · USO INTERNO")
    else:
        info = (f"{footer_marca}  ·  Página {doc.page} de 2  ·  "
                f"Generado {fecha}  ·  CONFIDENCIAL · USO INTERNO")
    canvas.drawCentredString(A4[0] / 2, 0.55 * cm, info)
    canvas.restoreState()


# =====================================================================
# COMPONENTE · Card Insight
# =====================================================================
def card_insight(texto: str):
    """Card de Insight estratégico — fondo azul claro + borde lateral NAVY 3pt."""
    card = Table(
        [[Paragraph(
            f"<i>{texto}</i>",
            ParagraphStyle("ins_t", fontSize=10, leading=13, textColor=BODY_DARK,
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


# =====================================================================
# COMPONENTE · Tabla canónica (header NAVY + filas zebra)
# =====================================================================
def tabla_canonica(headers: list, filas: list, col_widths: list):
    """Tabla canónica con header fondo claro + filas zebra + línea NAVY."""
    header_row = [
        Paragraph(f"<font color='#94a3b8' size='6.5'><b>{h}</b></font>",
                   ParagraphStyle(f"th{i}", fontSize=6.5, alignment=TA_LEFT))
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
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tbl


# =====================================================================
# COMPONENTE · KPI Card
# =====================================================================
def kpi_card(label: str, valor: str, etiqueta: str, color, ancho_cm: float = 5.5):
    """KPI Card canónico: label gris arriba / valor grande / etiqueta colorida."""
    color_hex = color.hexval() if hasattr(color, "hexval") else color
    rows = [
        [Paragraph(f"<font color='#94a3b8'><b>{label.upper()}</b></font>",
                    ParagraphStyle("kp1", fontSize=6.5, leading=8, alignment=TA_LEFT))],
        [Paragraph(f"<font color='{color_hex}'><b>{valor}</b></font>",
                    ParagraphStyle("kp2", fontSize=22, leading=24, alignment=TA_LEFT))],
        [Paragraph(f"<font color='{color_hex}'><b>{etiqueta}</b></font>",
                    ParagraphStyle("kp3", fontSize=9, leading=11, alignment=TA_LEFT))],
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


# =====================================================================
# COMPONENTE · Bloque Visual del Riesgo
# Velocímetro IZQ + espacio armónico 3cm + KPI cards apiladas DER
# =====================================================================
def bloque_visual_riesgo(
    score: float, etiqueta_riesgo: str, color_riesgo,
    tendencia_arrow: str, tendencia_delta: float,
    tendencia_label: str, tendencia_color,
    edi_score: int, edi_etiqueta: str,
    edi_arrow: str, edi_delta: float, edi_color,
):
    """Bloque visual canónico del Riesgo — Velocímetro + 2 KPI cards apiladas.

    SIEMPRE va antes de la Lectura Estratégica. El cliente VE antes de LEER.

    Layout (17cm = ancho útil):
        | gauge 6.5cm | spacer 3.0cm | cards 7.5cm |

    Args:
        score: 0-100 riesgo nacional.
        etiqueta_riesgo: 'MODERADO', 'ELEVADO', etc.
        color_riesgo: color de banda semáforo correspondiente.
        tendencia_arrow / _delta / _label / _color: card Tendencia 4 semanas.
        edi_score / _etiqueta / _arrow / _delta / _color: card EDI 7 días.
    """
    # Import diferido para evitar dependencia circular con strategic_daily_brief
    try:
        from .strategic_daily_brief import GaugeRiesgo
    except ImportError:
        from apurisk.reports.strategic_daily_brief import GaugeRiesgo

    gauge = GaugeRiesgo(score, etiqueta_riesgo, ancho=5.5 * cm, alto=4.0 * cm)

    t_hex = tendencia_color.hexval()
    e_hex = edi_color.hexval()
    sign = "+" if tendencia_delta > 0 else ("" if tendencia_delta == 0 else "")

    card_tendencia = Table([
        [Paragraph(
            "<font color='#94a3b8'><b>RIESGO POLÍTICO · PERÚ</b></font>",
            ParagraphStyle("t1", fontSize=6.5, leading=8, alignment=TA_LEFT)
        )],
        [Table(
            [[
                Paragraph(f"<font color='{t_hex}'><b>{tendencia_arrow}</b></font>",
                          ParagraphStyle("ta", fontSize=20, leading=22, alignment=TA_CENTER)),
                Paragraph(f"<font color='{t_hex}'><b>{sign}{tendencia_delta:.1f}</b></font>",
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
            f"<font color='{t_hex}'><b>{tendencia_label}</b></font>  "
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
                Paragraph(f"<font color='{e_hex}'><b>{edi_score}</b></font>",
                          ParagraphStyle("ev", fontSize=20, leading=22, alignment=TA_LEFT)),
                Paragraph(f"<font color='#94a3b8'>/100</font>  "
                          f"<font color='{e_hex}'><b>{edi_etiqueta}</b></font>",
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
        ("BOX", (0, 0), (-1, -1), 0.5, PROSPECTIVO),
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

    # Composición final: 3 columnas con espacio armónico
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


# =====================================================================
# COMPONENTE · Grid Implicancias 3x2
# =====================================================================
def grid_implicancias(items: list):
    """Grid 3x2 de implicancias operacionales con semáforo.

    Args:
        items: lista de 6 tuplas (icono, label, estado, n_amenazas, color).
    """
    estado_color = {
        "ESTABLE": ESTABLE, "MONITOREO": BAJO,
        "ATENCIÓN": MODERADO, "ALERTA": ELEVADO,
    }
    cells = []
    for item in items:
        if len(item) == 5:
            icono, label, estado, n, col = item
        else:
            icono, label, estado, n = item
            col = estado_color.get(estado, ESTABLE)
        cells.append(Paragraph(
            f"<font color='#0f172a' size='9'><b>{icono} {label}</b></font><br/>"
            f"<font color='{col.hexval()}' size='8.5'><b>{estado}</b></font>  "
            f"<font color='#94a3b8' size='7'>· {n}</font>",
            ParagraphStyle("imp", fontSize=8.5, leading=11, alignment=TA_LEFT)
        ))
    # Si hay menos de 6, rellenar
    while len(cells) < 6:
        cells.append(Paragraph("", ParagraphStyle("empty", fontSize=8)))

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
# HELPER · Crear documento canónico
# =====================================================================
def crear_documento(output_path: str, title: str, author: str = "THALOS",
                     subject: str = "") -> SimpleDocTemplate:
    """Factory de SimpleDocTemplate con márgenes canónicos."""
    return SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.6 * cm, bottomMargin=1.4 * cm,
        title=title, author=author, creator=author, subject=subject,
    )


# =====================================================================
# LABELS CANÓNICOS DEL BLOQUE VISUAL POR PRODUCTO
# =====================================================================
LABEL_BLOQUE_VISUAL = {
    "daily": "Reporte de Riesgo Político · ÚLTIMAS 24 HRS",
    "on_demand_24h": "Reporte de Riesgo Político · ÚLTIMAS 24 HRS",
    "weekly": "Reporte de Riesgo Político · ÚLTIMA SEMANA",
    "monthly": "Reporte de Riesgo Político · ÚLTIMO MES",
    "crisis": "ESCENARIO DE RIESGO Político · EVENTO ACTIVO",
}


def label_bloque_visual(producto: str) -> str:
    """Devuelve la etiqueta canónica del bloque visual según producto."""
    return LABEL_BLOQUE_VISUAL.get(producto, LABEL_BLOQUE_VISUAL["daily"])
