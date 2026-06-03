"""Helpers de branding APURISK OSINT · THALOS para inserción en PDFs.

Provee funciones que pueden insertarse en flowables de ReportLab para
mantener consistencia visual entre todos los reportes generados.

Uso típico:
    from .branding import thalos_logo_drawing, thalos_text_header

    story.append(thalos_logo_drawing(width=180))
    story.append(thalos_text_header(styles))
"""
from __future__ import annotations
import logging
from pathlib import Path
from reportlab.platypus import Paragraph, Spacer
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

log = logging.getLogger("apurisk.branding")

# Paths a los SVG (versión dark para PDFs sobre fondo blanco)
_THIS_DIR = Path(__file__).resolve().parent.parent
LOGO_SVG_DARK = _THIS_DIR / "static" / "thalos-logo-pdf.svg"

# Naming y tagline corporativos (fuente única de verdad)
BRAND_NAME = "APURISK OSINT"
BRAND_TAGLINE = "Strategic Intelligence for Complex Decisions"
BRAND_COMPANY = "THALOS"
BRAND_FUTURE_PRODUCT = "APURISK SIM-CRISIS"

# Colores corporativos
COLOR_NAVY = "#0f172a"
COLOR_ACCENT = "#3b82f6"
COLOR_TEXT_SECONDARY = "#475569"
COLOR_TEXT_TERTIARY = "#94a3b8"


def thalos_logo_drawing(width: float = 180):
    """Devuelve un Drawing de ReportLab con el logo THALOS vectorial.

    Args:
        width: ancho deseado en puntos (default 180, ≈ 6.3 cm).

    Returns:
        Drawing escalado proporcionalmente. Si svglib no está disponible
        o el SVG no puede cargarse, devuelve None y se debe usar el
        fallback de texto (`thalos_text_header()`).
    """
    try:
        from svglib.svglib import svg2rlg
    except ImportError:
        log.warning("svglib no disponible — fallback a header de texto")
        return None
    if not LOGO_SVG_DARK.exists():
        log.warning("SVG dark del logo no existe: %s", LOGO_SVG_DARK)
        return None
    try:
        drawing = svg2rlg(str(LOGO_SVG_DARK))
        if drawing is None:
            return None
        # Escalar proporcionalmente al ancho deseado
        original_width = drawing.width or 240.0
        scale = width / original_width
        drawing.width = drawing.width * scale
        drawing.height = drawing.height * scale
        drawing.scale(scale, scale)
        return drawing
    except Exception as e:
        log.warning("Error renderizando logo SVG: %s", e)
        return None


def thalos_text_header(align: str = "left") -> list:
    """Fallback de texto cuando el logo SVG no puede insertarse.

    Args:
        align: 'left' o 'center'.

    Returns:
        Lista de flowables (Paragraph + Spacer) que reproducen el branding
        en texto: "THALOS" grande + tagline + APURISK OSINT.
    """
    ta = TA_CENTER if align == "center" else TA_LEFT
    estilo_thalos = ParagraphStyle(
        name="ThalosBrand",
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=32,
        textColor=COLOR_NAVY,
        alignment=ta,
        spaceAfter=2,
    )
    estilo_tagline = ParagraphStyle(
        name="ThalosTagline",
        fontName="Helvetica",
        fontSize=10,
        leading=12,
        textColor=COLOR_TEXT_SECONDARY,
        alignment=ta,
        spaceAfter=4,
    )
    estilo_product = ParagraphStyle(
        name="ThalosProduct",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=COLOR_ACCENT,
        alignment=ta,
        spaceAfter=2,
    )
    return [
        Paragraph(f"<b>{BRAND_COMPANY}</b>", estilo_thalos),
        Paragraph(BRAND_TAGLINE, estilo_tagline),
        Paragraph(f"{BRAND_NAME} · OSINT Platform", estilo_product),
    ]


def thalos_header_block(width: float = 180, align: str = "left") -> list:
    """Devuelve un bloque de header completo: logo SVG o fallback texto.

    Args:
        width: ancho del logo en puntos.
        align: alineación si se usa fallback de texto.

    Returns:
        Lista de flowables lista para insertar en el story del PDF.
    """
    drawing = thalos_logo_drawing(width=width)
    if drawing is not None:
        return [drawing, Spacer(1, 0.3 * cm)]
    return thalos_text_header(align=align) + [Spacer(1, 0.3 * cm)]


def thalos_footer_line() -> str:
    """Línea de footer estándar para PDFs.

    Returns:
        String HTML (usable en Paragraph) con el branding corporativo.
    """
    return (
        f'<font color="{COLOR_NAVY}"><b>{BRAND_NAME}</b></font> · '
        f'{BRAND_TAGLINE} · '
        f'Powered by <b>{BRAND_COMPANY}</b> · '
        f'Próximamente: <i>{BRAND_FUTURE_PRODUCT}</i>'
    )


def thalos_pdf_metadata() -> dict:
    """Metadata estándar para PDFs (author, subject, creator).

    Returns:
        Dict con keys que pueden pasarse a SimpleDocTemplate.
    """
    return {
        "author": f"{BRAND_NAME} · {BRAND_COMPANY}",
        "creator": f"{BRAND_COMPANY} — {BRAND_TAGLINE}",
        "subject": f"{BRAND_NAME} Intelligence Report",
    }
