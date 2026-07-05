"""THALOS · Reporte por Tema (versión OSINT, con ventana) — Fase 3-3b.

Reporte enfocado en UN tema, leído del motor parametrizado (PR #55) sobre la
ventana elegida (7/15/30d). Lectura de datos OSINT, sin criterio del analista
(el criterio es la versión Analítica, 3-3c). Reutiliza la Plantilla Base THALOS
y las mismas funciones del motor que /admin/inteligencia y el Reporte A Manual,
ahora pasando dias=N.

Honestidad C1 (obligatoria): actividad/velocidad se windowean a la ventana;
gravedad, factores P×I y Score Nacional son estructurales — se imprime la
etiqueta md['ventana_nota'] en la nota de integridad.
"""
from __future__ import annotations
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.charts.linecharts import HorizontalLineChart

from . import thalos_base as T
from .reporte_a import _color_gravedad, _fmt, escape_txt

RANGO_DIAS = {"24h": 1, "7d": 7, "15d": 15, "30d": 30}


def _flecha_tray(et: str) -> str:
    return {"ASCENSO": "▲ ASCENSO", "DECLIVE": "▼ DECLIVE"}.get(et, "= ESTABLE")


def _situacion_bloque(g: dict, st: dict):
    """Card con gravedad/actividad/velocidad/urgencia + cuadrante coloreado."""
    grav = g.get("y", 0)
    col = _color_gravedad(grav)
    urg = g.get("urgencia", "—")
    cuad = g.get("cuadrante", "—")
    def _metric(label, valor, color=T.NAVY):
        return [Paragraph(f'<font size="9" color="#999999">{label}</font>',
                          ParagraphStyle("ml", fontName=T.FONT_BODY, fontSize=9, textColor=T.GRIS_META)),
                Paragraph(f'<font size="22">{valor}</font>',
                          ParagraphStyle("mv", fontName=T.FONT_TITLE, fontSize=22,
                                         textColor=color, leading=25))]
    fila = [_metric("Gravedad", _fmt(grav, 0), col),
            _metric("Actividad", _fmt(g.get("x", 0), 1)),
            _metric("Velocidad 7d", f'{g.get("velocidad",0):+.1f}')]
    tbl = Table([[c[1] for c in fila]], colWidths=[1.8 * inch] * 3)
    tbl.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    lbls = Table([[c[0] for c in fila]], colWidths=[1.8 * inch] * 3)
    # chip de urgencia + cuadrante
    chip = Table([[Paragraph(f'<b>{escape_txt(str(urg))}</b>',
                             ParagraphStyle("u", fontName=T.FONT_TITLE, fontSize=13,
                                            textColor=T.BLANCO, alignment=TA_CENTER))]],
                 colWidths=[2.0 * inch])
    chip.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col),
                              ("BOX", (0, 0), (-1, -1), 0.5, T.ORO),
                              ("ROUNDEDCORNERS", [5, 5, 5, 5]),
                              ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return [lbls, tbl, Spacer(1, 10),
            Paragraph(f'Cuadrante: <b>{escape_txt(cuad)}</b>', st["body"]),
            chip]


def _grafico_trayectoria(serie: list, dias: int):
    """Línea del Score Nacional a lo largo de la ventana (desde snapshots)."""
    if not serie or len(serie) < 2:
        return Paragraph(
            "Trayectoria: datos de snapshots insuficientes en esta ventana para trazar la curva.",
            T.estilos()["body"])
    d = Drawing(T.PAGE_W - 2 * T.MARGEN_LAT, 160)
    lc = HorizontalLineChart()
    lc.x = 40; lc.y = 30; lc.width = T.PAGE_W - 2 * T.MARGEN_LAT - 70; lc.height = 110
    vals = [float(p["v"]) for p in serie]
    lc.data = [vals]
    # etiquetas: pocas fechas para no saturar
    n = len(serie)
    step = max(1, n // 6)
    lc.categoryAxis.categoryNames = [
        (serie[i]["t"][5:10] if i % step == 0 else "") for i in range(n)]
    lc.categoryAxis.labels.fontName = T.FONT_BODY; lc.categoryAxis.labels.fontSize = 7
    lc.valueAxis.valueMin = 0; lc.valueAxis.valueMax = 100; lc.valueAxis.valueStep = 20
    lc.valueAxis.labels.fontName = T.FONT_BODY; lc.valueAxis.labels.fontSize = 8
    lc.valueAxis.gridStrokeColor = T.GRIS_CLARO; lc.valueAxis.gridStrokeWidth = 0.5
    lc.valueAxis.visibleGrid = 1
    lc.lines[0].strokeColor = T.ROJO_CRIT; lc.lines[0].strokeWidth = 2.5
    d.add(lc)
    d.add(Line(40, 8, 56, 8, strokeColor=T.ROJO_CRIT, strokeWidth=2.5))
    d.add(String(60, 5, f"Score Nacional · ventana {dias}d", fontName=T.FONT_BODY,
                 fontSize=8, fillColor=T.GRIS_CUERPO))
    return d


def generar_reporte_tema_osint(db_path: str, tema: str, dias: int,
                               snapshot: dict, construir_semaforo,
                               conteos_bd: dict = None) -> bytes | None:
    """Genera el PDF del Reporte por Tema (OSINT) sobre la ventana `dias`.

    Reutiliza el motor parametrizado: construir_semaforo(dias=N),
    listar_actores_por_activacion(dias=N), cargar_factores_pxi_por_tema,
    calcular_proyecciones(dias=N), serie_trayectoria(dias=N).
    Devuelve bytes, o None si no hay foto.
    """
    from ..storage.config_loader import (
        listar_actores_por_activacion, cargar_factores_pxi_por_tema,
        calcular_proyecciones, serie_trayectoria,
    )
    from ..utils.timezone_pe import now_pe_iso
    osint = (snapshot or {}).get("osint_motor")
    if not osint:
        return None
    md = construir_semaforo(osint, db_path, dias=dias)
    globo = next((g for g in md.get("globos_b", []) if g["tema"] == tema), None)
    if globo is None:
        return None
    ventana_nota = md.get("ventana_nota") or (
        "Factores P×I y Score Nacional: última lectura estructural.")

    actores = listar_actores_por_activacion(db_path, tema, dias=dias)
    factores = cargar_factores_pxi_por_tema(db_path, tema)
    temas_datos = [{"tema": g["tema"], "actividad": g.get("x", 0.0),
                    "velocidad": g.get("velocidad", 0.0), "gravedad": g.get("y", 0.0)}
                   for g in md.get("globos_b", [])]
    proy = calcular_proyecciones(db_path, temas_datos, dias=dias)
    pb = next((r for r in proy.get("proyeccion_b", []) if r["tema"] == tema), None)
    pa = next((r for r in proy.get("proyeccion_a", []) if r["tema"] == tema), None)
    h_obj = 30 if 30 in proy.get("horizontes", []) else (proy.get("horizontes") or [30])[-1]
    serie = serie_trayectoria(db_path, dias)

    st = T.estilos()
    nom_tema = tema.replace("_", " ").title()
    ahora = now_pe_iso()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=T.MARGEN_LAT, rightMargin=T.MARGEN_LAT,
                            topMargin=T.MARGEN_SUP, bottomMargin=T.MARGEN_INF,
                            title=f"Reporte OSINT por Tema · {nom_tema}")
    doc._fecha_footer = ahora[:10]
    doc._header_meta = f"REPORTE POR TEMA · OSINT · {dias}d"
    gen = (snapshot.get("generado") or ahora)[:16].replace("T", " ")
    doc._portada = {
        "titulo": f"Reporte OSINT por Tema",
        "subtitulo": f"{nom_tema} · Monitoreo de situación · Ventana {dias} días",
        "tema_rango": f"Datos: ventana {dias}d · foto {gen} (America/Lima)",
        "metadata": [
            ("Tipo", f"OSINT por Tema · {dias} días"),
            ("Tema", nom_tema),
            ("Generado", ahora[:16].replace("T", " ") + " (America/Lima)"),
            ("Clasificación", "USO INTERNO"),
        ],
    }
    S = [PageBreak()]

    # ── Pág. 2 — Situación del tema ──
    S.append(Paragraph(f"Situación — {nom_tema}", st["h1"]))
    S.append(T.linea_oro())
    S += _situacion_bloque(globo, st)
    S.append(Spacer(1, 10))
    S.append(Paragraph(
        f"Gravedad estructural (estable entre ventanas). Actividad y velocidad "
        f"medidas sobre la ventana de {dias} días.", st["body"]))

    S.append(PageBreak())

    # ── Pág. 3 — Análisis ──
    S.append(Paragraph("Análisis", st["h1"]))
    S.append(T.linea_oro())
    S.append(Paragraph("Actores del tema (por índice de activación CVO)", st["h2"]))
    if actores:
        filas = []
        for a in actores[:6]:
            idx = a.get("indice_activacion")
            filas.append([a.get("nombre", "—"), _fmt(a.get("peso_calculado", 0), 0),
                          f"{idx:.1f}" if idx is not None else "—",
                          f'{_flecha_tray(a.get("trayectoria_etiqueta","ESTABLE"))} '
                          f'({a.get("trayectoria_en_tema",0):+g})'])
        S.append(T.tabla_profesional(["Actor", "Peso", "CVO", "Trayectoria"], filas,
                                     [2.4 * inch, 0.9 * inch, 0.9 * inch, 2.2 * inch]))
    else:
        S.append(Paragraph("Sin actores vinculados a este tema.", st["body"]))
    S.append(Spacer(1, 12))
    S.append(Paragraph("Factores de riesgo (P×I) del tema", st["h2"]))
    if factores:
        filas = [[f.get("nombre") or f.get("factor_id"), _fmt(f.get("probabilidad")),
                  _fmt(f.get("impacto")), _fmt(f.get("score")), (f.get("nivel") or "—")]
                 for f in factores[:4]]
        S.append(T.tabla_profesional(["Factor", "Prob.", "Imp.", "Score", "Nivel"], filas,
                                     [2.6 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch, 1.1 * inch]))
    else:
        S.append(Paragraph("Sin factores P×I definidos para este tema.", st["body"]))
    S.append(Spacer(1, 12))
    S.append(Paragraph("Trayectoria (ventana)", st["h2"]))
    S.append(_grafico_trayectoria(serie, dias))

    S.append(PageBreak())

    # ── Pág. 4 — Proyección + Integridad (con etiqueta C1) ──
    S.append(Paragraph("Proyección e integridad", st["h1"]))
    S.append(T.linea_oro())
    if pb and pa:
        g0, g30 = pb.get("base", 0), pb.get(f"h{h_obj}", pb.get("base", 0))
        a0, a30 = pa.get("hoy", 0), pa.get(f"h{h_obj}", pa.get("hoy", 0))
        S.append(Paragraph(
            f"Proyección a {h_obj} días — gravedad: <b>{_fmt(g0,0)} → {_fmt(g30,0)}</b> · "
            f"actividad: <b>{_fmt(a0,1)} → {_fmt(a30,1)}</b>. "
            "Lectura del motor (tendencia + puntos de quiebre), no criterio.", st["body"]))
    else:
        S.append(Paragraph("Proyección no disponible para este tema.", st["body"]))
    S.append(Spacer(1, 12))
    cb = conteos_bd or {}
    partes = []
    if cb.get("total") is not None:
        partes.append(f"{cb['total']:,} artículos en BD")
    if cb.get("ultimas_24h") is not None:
        partes.append(f"{cb['ultimas_24h']} capturados en 24h")
    partes.append(f"ventana analizada: {dias} días")
    S.append(T.recuadro_ejecutivo(
        "INTEGRIDAD DE DATOS",
        " · ".join(partes) + ".<br/>"
        f"Última actualización de datos (snapshot): {gen} (America/Lima).<br/><br/>"
        f"<b>Honestidad de datos:</b> {escape_txt(ventana_nota)}<br/>"
        "Es lectura pura del motor OSINT, sin criterio del analista "
        "(eso corresponde a la versión Analítica del reporte).", st))

    doc.build(S, onFirstPage=T.dibujar_portada, onLaterPages=T.header_footer)
    return buf.getvalue()
