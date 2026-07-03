"""THALOS · Reporte A (Manual / OSINT) — foto situacional ejecutiva global.

Fase 3-2. Documento GLOBAL (los 8 temas en uno), lectura ejecutiva, 3-4 páginas.
Pura LECTURA del motor (semáforo, actores, factores P×I, proyección) — sin
criterio del analista (eso es Reporte B). Reutiliza la Plantilla Base THALOS.

Diagnóstico de rango: el motor no acepta rango de fechas arbitrario; lee la
última foto disponible (snapshot más reciente + ventana 7D horneada). Este
generador usa esa foto y lo marca con honestidad en la nota de integridad.
"""
from __future__ import annotations
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.charts.linecharts import HorizontalLineChart

from . import thalos_base as T


# ── Mapeo de gravedad/nivel → color de riesgo ────────────────────────────────
def _color_gravedad(y: float):
    if y >= 80: return T.ROJO_CRIT
    if y >= 65: return T.AMBAR_ALTO
    if y >= 50: return T.NARANJA_MOD
    return T.AMARILLO_BAJO


def _color_nivel_nacional(nivel: str):
    n = (nivel or "").upper()
    if "CRÍT" in n or "CRIT" in n or "ROJO" in n: return T.ROJO_CRIT
    if "ALTO" in n: return T.AMBAR_ALTO
    if "MEDIO" in n or "MODER" in n: return T.NARANJA_MOD
    return T.AMARILLO_BAJO


def _fmt(v, dec=0):
    try:
        return f"{float(v):.{dec}f}"
    except Exception:
        return "—"


# ══════════════════════════════════════════════════════════════════════════════
# Lectura de datos del motor (última foto) — sin recálculo nuevo
# ══════════════════════════════════════════════════════════════════════════════

def _leer_datos(db_path: str, snapshot: dict, construir_semaforo):
    """Ensambla los datos del Reporte A desde la última foto. Devuelve dict o None."""
    from ..storage.config_loader import (
        listar_actores, cargar_dinamica_actor, cargar_factores_pxi_top,
        calcular_proyecciones,
    )
    osint = (snapshot or {}).get("osint_motor")
    if not osint:
        return None
    md = construir_semaforo(osint, db_path)
    globos = md.get("globos_b", [])
    if not globos:
        return None

    riesgo = (snapshot or {}).get("riesgo", {}) or {}

    # Top 3 actores por peso, con su trayectoria
    actores = []
    for a in listar_actores(db_path, pais="PE", solo_activos=True)[:3]:
        din = cargar_dinamica_actor(db_path, a["id"])
        actores.append({
            "nombre": a.get("nombre", "—"),
            "peso": a.get("peso_calculado", 0),
            "nivel": a.get("nivel", "?"),
            "trayectoria": din.get("etiqueta", "ESTABLE"),
            "trayectoria_base": din.get("trayectoria_base", 0),
        })

    factores = cargar_factores_pxi_top(db_path, 3)

    temas_datos = [{"tema": g["tema"], "actividad": g.get("x", 0.0),
                    "velocidad": g.get("velocidad", 0.0), "gravedad": g.get("y", 0.0)}
                   for g in globos]
    proy = calcular_proyecciones(db_path, temas_datos)

    return {
        "riesgo": riesgo, "globos": globos, "actores": actores,
        "factores": factores, "proy": proy,
        "generado": snapshot.get("generado", ""),
        "n_articulos": snapshot.get("n_articulos"),
        "n_articulos_24h": snapshot.get("n_articulos_24h"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Componentes específicos del Reporte A (reutilizan la paleta/fuentes THALOS)
# ══════════════════════════════════════════════════════════════════════════════

def _score_global_bloque(riesgo: dict, st: dict):
    score = riesgo.get("global", 0)
    nivel = riesgo.get("nivel", "—")
    col = _color_nivel_nacional(nivel)
    inner = Table([[
        Paragraph(f'<font size="46">{_fmt(score,0)}</font>',
                  ParagraphStyle("sg", fontName=T.FONT_TITLE, fontSize=46,
                                 textColor=col, alignment=TA_CENTER, leading=50)),
        Paragraph(f'{escape_txt(str(nivel).upper())}',
                  ParagraphStyle("sn", fontName=T.FONT_TITLE, fontSize=20,
                                 textColor=col, alignment=TA_LEFT, leading=24)),
    ]], colWidths=[2.2 * inch, 3.3 * inch])
    inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 1.2, T.ORO),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("BACKGROUND", (0, 0), (-1, -1), T.GRIS_CLARO),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    lbl = Paragraph("Score Global · Riesgo Nacional",
                    ParagraphStyle("l", fontName=T.FONT_BODY, fontSize=10,
                                   textColor=T.GRIS_META))
    return [lbl, Spacer(1, 4), inner]


def _grid_temas(globos: list, st: dict):
    """Grid 2×4 de los 8 temas, coloreado por gravedad."""
    cell_w = (T.PAGE_W - 2 * T.MARGEN_LAT) / 4
    cell_h = 0.82 * inch
    orden = sorted(globos, key=lambda g: g.get("y", 0), reverse=True)
    celdas = []
    for g in orden:
        nom = g["tema"].replace("_", " ").title()
        y = g.get("y", 0)
        col = _color_gravedad(y)
        txtcol = T.NAVY if col == T.AMARILLO_BAJO else T.BLANCO
        celdas.append(Table([[Paragraph(
            f'<font size="9">{escape_txt(nom)}</font><br/><font size="19"><b>{_fmt(y,0)}</b></font>',
            ParagraphStyle("gt", fontName=T.FONT_TITLE, fontSize=9,
                           textColor=txtcol, alignment=TA_CENTER, leading=13))]],
            colWidths=[cell_w - 6], rowHeights=[cell_h]))
        celdas[-1].setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), col),
            ("BOX", (0, 0), (-1, -1), 0.5, T.ORO),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    filas = [celdas[i:i + 4] for i in range(0, len(celdas), 4)]
    # completar última fila si falta
    while filas and len(filas[-1]) < 4:
        filas[-1].append("")
    grid = Table(filas, colWidths=[cell_w] * 4)
    grid.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 3),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                              ("TOPPADDING", (0, 0), (-1, -1), 3),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return grid


def _grafico_proyeccion(proy: dict):
    """Trend real: gravedad hoy→30d de los 3 temas de mayor gravedad."""
    pb = {r["tema"]: r for r in proy.get("proyeccion_b", [])}
    horizontes = proy.get("horizontes", [15, 30])
    top = sorted(proy.get("proyeccion_b", []), key=lambda r: r.get("base", 0), reverse=True)[:3]
    d = Drawing(T.PAGE_W - 2 * T.MARGEN_LAT, 165)
    lc = HorizontalLineChart()
    lc.x = 40; lc.y = 32; lc.width = T.PAGE_W - 2 * T.MARGEN_LAT - 70; lc.height = 108
    series, nombres = [], []
    cols = [T.ROJO_CRIT, T.AMBAR_ALTO, T.AZUL_TERRITORIO]
    for r in top:
        fila = [r.get("base", 0)] + [r.get(f"h{h}", r.get("base", 0)) for h in horizontes]
        series.append(fila)
        nombres.append(r["tema"].replace("_", " ").title())
    if not series:
        series = [[0, 0, 0]]; nombres = ["—"]
    lc.data = series
    lc.categoryAxis.categoryNames = ["Hoy"] + [f"{h}d" for h in horizontes]
    lc.categoryAxis.labels.fontName = T.FONT_BODY; lc.categoryAxis.labels.fontSize = 8
    lc.valueAxis.valueMin = 0; lc.valueAxis.valueMax = 100; lc.valueAxis.valueStep = 20
    lc.valueAxis.labels.fontName = T.FONT_BODY; lc.valueAxis.labels.fontSize = 8
    lc.valueAxis.gridStrokeColor = T.GRIS_CLARO; lc.valueAxis.gridStrokeWidth = 0.5
    lc.valueAxis.visibleGrid = 1
    for i in range(len(series)):
        lc.lines[i].strokeColor = cols[i % 3]; lc.lines[i].strokeWidth = 2.5
    d.add(lc)
    x = 40
    for nom, col in zip(nombres, cols):
        d.add(Line(x, 8, x + 16, 8, strokeColor=col, strokeWidth=2.5))
        d.add(String(x + 20, 5, nom, fontName=T.FONT_BODY, fontSize=8, fillColor=T.GRIS_CUERPO))
        x += 150
    return d


def escape_txt(s: str) -> str:
    from html import escape as _e
    return _e(s or "")


# ══════════════════════════════════════════════════════════════════════════════
# Generador principal
# ══════════════════════════════════════════════════════════════════════════════

def generar_reporte_a_manual(db_path: str, fecha_solicitud_iso: str,
                             snapshot: dict, construir_semaforo,
                             conteos_bd: dict = None) -> bytes | None:
    """Genera el PDF del Reporte A Manual. Devuelve bytes, o None si no hay foto.

    snapshot: dict del último snapshot (con 'riesgo' y 'osint_motor').
    construir_semaforo: función _construir_datos_semaforo (inyectada desde el web layer).
    conteos_bd: {total, ultimas_24h} reales de la BD (opcional, para la nota).
    """
    datos = _leer_datos(db_path, snapshot, construir_semaforo)
    if datos is None:
        return None

    st = T.estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=T.MARGEN_LAT, rightMargin=T.MARGEN_LAT,
                            topMargin=T.MARGEN_SUP, bottomMargin=T.MARGEN_INF,
                            title="Reporte OSINT · Foto Situacional")
    gen = (datos.get("generado") or fecha_solicitud_iso or "")[:16].replace("T", " ")
    doc._fecha_footer = fecha_solicitud_iso[:10]
    doc._header_meta = "REPORTE A · OSINT · THALOS"
    doc._portada = {
        "titulo": "Reporte OSINT — Foto Situacional",
        "subtitulo": "Estimado de Seguridad Estratégica",
        "tema_rango": f"Datos: última foto disponible · {gen} (America/Lima)",
        "metadata": [
            ("Tipo", "Manual (OSINT) · global"),
            ("Generado", fecha_solicitud_iso[:16].replace("T", " ") + " (America/Lima)"),
            ("Alcance", "Los 8 temas · lectura del motor, sin criterio"),
            ("Clasificación", "USO INTERNO"),
        ],
    }

    S = [PageBreak()]  # portada en pág. 1 (onFirstPage)

    # ── Pág. 2 — Resumen ejecutivo ──
    S.append(Paragraph("Resumen ejecutivo", st["h1"]))
    S.append(T.linea_oro())
    S += _score_global_bloque(datos["riesgo"], st)
    S.append(Spacer(1, 16))
    S.append(Paragraph("Panorama de los 8 temas (gravedad estructural)", st["h2"]))
    S.append(T.linea_oro())
    S.append(_grid_temas(datos["globos"], st))
    S.append(Spacer(1, 6))
    S.append(T._leyenda_riesgo())

    S.append(PageBreak())

    # ── Pág. 3 — Análisis rápido ──
    S.append(Paragraph("Análisis rápido", st["h1"]))
    S.append(T.linea_oro())
    # Actores
    S.append(Paragraph("Actores clave (por peso)", st["h2"]))
    if datos["actores"]:
        fl = {"ASCENSO": "▲ ASCENSO", "DECLIVE": "▼ DECLIVE", "ESTABLE": "= ESTABLE"}
        filas = [[a["nombre"], _fmt(a["peso"], 0),
                  f'{fl.get(a["trayectoria"], a["trayectoria"])} ({a["trayectoria_base"]:+d})']
                 for a in datos["actores"]]
        S.append(T.tabla_profesional(["Actor", "Peso", "Trayectoria"], filas,
                                     [3.0 * inch, 1.1 * inch, 2.3 * inch]))
    else:
        S.append(Paragraph("Sin actores registrados.", st["body"]))
    S.append(Spacer(1, 12))
    # Factores P×I
    S.append(Paragraph("Factores de riesgo (P×I) — top 3", st["h2"]))
    if datos["factores"]:
        filas = [[f.get("nombre") or f.get("factor_id"),
                  _fmt(f.get("probabilidad")), _fmt(f.get("impacto")),
                  _fmt(f.get("score")), (f.get("nivel") or "—")]
                 for f in datos["factores"]]
        S.append(T.tabla_profesional(["Factor", "Prob.", "Imp.", "Score", "Nivel"], filas,
                                     [2.6 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch, 1.1 * inch]))
    else:
        S.append(Paragraph("Sin factores P×I en la última foto.", st["body"]))
    S.append(PageBreak())

    # ── Pág. 4 — Proyección 30d + Nota de integridad ──
    S.append(Paragraph("Proyección a 30 días", st["h1"]))
    S.append(T.linea_oro())
    S.append(Paragraph(
        "Extrapolación de gravedad de los temas de mayor riesgo sobre la última foto "
        "(tendencia + puntos de quiebre definidos). Lectura del motor, no criterio.",
        st["body"]))
    S.append(_grafico_proyeccion(datos["proy"]))
    S.append(Spacer(1, 16))
    S.append(Paragraph("Nota de integridad de datos", st["h2"]))
    S.append(T.linea_oro())
    cb = conteos_bd or {}
    total = cb.get("total")
    u24 = cb.get("ultimas_24h")
    partes = []
    if total is not None:
        partes.append(f"{total:,} artículos en BD")
    if u24 is not None:
        partes.append(f"{u24} capturados en 24h")
    partes.append("8 temas monitoreados")
    S.append(T.recuadro_ejecutivo(
        "FUENTES Y ALCANCE",
        " · ".join(partes) + ".<br/><br/>"
        f"Última actualización de datos (snapshot): {gen} (America/Lima).<br/>"
        "Honestidad de datos: este Reporte A usa la <b>última foto disponible</b> del motor "
        "(ventana 7D horneada en el snapshot más reciente), no una ventana exacta de 24h — "
        "el motor aún no acepta rango de fechas arbitrario. Es lectura pura de datos, "
        "sin criterio del analista (eso corresponde al Reporte B).", st))

    doc.build(S, onFirstPage=T.dibujar_portada, onLaterPages=T.header_footer)
    return buf.getvalue()
