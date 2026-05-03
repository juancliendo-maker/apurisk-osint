"""Generadores de reporte - APURISK 1.0"""
from .html_dashboard import generar_dashboard_html
from .docx_report import generar_reporte_docx
from .reporte_24h import generar_reporte_24h_html, generar_reporte_24h_docx
from .alertas_report import generar_alertas_html, generar_alertas_docx
from .pdf_reports import generar_reporte_diario_pdf, generar_reporte_semanal_pdf
from .ejecutivo_diario import generar_ejecutivo_docx, generar_ejecutivo_pdf

__all__ = [
    "generar_dashboard_html", "generar_reporte_docx",
    "generar_reporte_24h_html", "generar_reporte_24h_docx",
    "generar_alertas_html", "generar_alertas_docx",
    "generar_reporte_diario_pdf", "generar_reporte_semanal_pdf",
    "generar_ejecutivo_docx", "generar_ejecutivo_pdf",
]
