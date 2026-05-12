"""Generador de Reporte Analítico OSINT de Riesgo Político — APURISK 1.0

Genera un PDF profesional con las 14 secciones estructuradas:
  1. Resumen ejecutivo
  2. Datos básicos del caso
  3. Descripción del evento o caso
  4. Evaluación del riesgo político (6 dimensiones)
  5. Tendencia del caso
  6. Actores relevantes (tabla)
  7. Cobertura mediática
  8. Proyección mediática
  9. Impacto político
  10. Escenarios prospectivos (Desescalada/Continuidad/Escalada)
  11. Alertas tempranas a monitorear
  12. Evaluación de confiabilidad
  13. Conclusión analítica
  14. Recomendación para el analista

Diseño profesional con paleta APURISK (azul corporativo + acentos por nivel).
"""
from __future__ import annotations
import html as _html
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY


# Paleta APURISK
AZUL = colors.HexColor("#1E40AF")
AZUL_OSC = colors.HexColor("#0F172A")
AZUL_CLARO = colors.HexColor("#3B82F6")
GRIS = colors.HexColor("#64748B")
GRIS_BG = colors.HexColor("#F8FAFC")
GRIS_LINEA = colors.HexColor("#CBD5E1")
ROJO = colors.HexColor("#DC2626")
NARANJA = colors.HexColor("#EA580C")
AMARILLO = colors.HexColor("#D48C0B")
VERDE = colors.HexColor("#16A34A")
MORADO = colors.HexColor("#7C3AED")

_MESES = {1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
          7: "jul", 8: "ago", 9: "set", 10: "oct", 11: "nov", 12: "dic"}


def _color_nivel(nivel):
    return {
        "CRÍTICO": ROJO, "CRITICO": ROJO,
        "ALTO": NARANJA,
        "MODERADO": AMARILLO, "MEDIO": AMARILLO,
        "BAJO": VERDE,
    }.get((nivel or "").upper(), GRIS)


def _esc(s):
    if s is None:
        return ""
    return _html.escape(str(s), quote=True)


def _fmt_dt_full(iso_str):
    if not iso_str:
        return "—"
    try:
        s = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return f"{dt.day:02d} de {_MESES.get(dt.month, '?')} de {dt.year} · {dt.strftime('%H:%M')} PET"
    except Exception:
        return str(iso_str)[:20]


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontSize=20, textColor=AZUL_OSC,
                                  alignment=TA_LEFT, fontName="Helvetica-Bold", spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontSize=11,
                                     textColor=AZUL, fontName="Helvetica-Oblique", spaceAfter=2),
        "meta": ParagraphStyle("meta", parent=ss["Normal"], fontSize=9, textColor=GRIS,
                                 spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=13, textColor=AZUL,
                               fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, textColor=AZUL_OSC,
                               fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, leading=13.5,
                                 textColor=AZUL_OSC, alignment=TA_JUSTIFY),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8.5, leading=11,
                                  textColor=GRIS),
        "url": ParagraphStyle("url", parent=ss["Normal"], fontSize=7.5, textColor=AZUL,
                                leading=10, leftIndent=6),
        "kpi_label": ParagraphStyle("kpi_label", parent=ss["Normal"], fontSize=7.5,
                                       textColor=GRIS, alignment=TA_CENTER,
                                       fontName="Helvetica-Bold"),
        "kpi_value": ParagraphStyle("kpi_value", parent=ss["Normal"], fontSize=22,
                                       alignment=TA_CENTER, fontName="Helvetica-Bold"),
    }


def generar_reporte_caso_pdf(output_path: str, analisis: dict) -> str:
    """Genera el PDF analítico con 14 secciones a partir del dict producido
    por caso_analyzer.analizar_caso()."""

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title="APURISK · Reporte OSINT de Riesgo Político", author="APURISK 1.0",
    )
    e = _styles()
    story = []

    inp = analisis.get("input", {})
    caso = inp.get("caso", "")
    comentario = inp.get("comentario", "")
    nivel = analisis.get("nivel_riesgo", "—")
    cn = _color_nivel(nivel)
    score = analisis.get("score_global", 0)
    tendencia = analisis.get("tendencia", "—")

    # ============== CABECERA ==============
    story.append(Paragraph(
        f'<font color="#0F172A">APURISK 1.0</font> '
        f'<font color="#1E40AF" size="13"> · Reporte OSINT de Riesgo Político</font>',
        e["title"]))
    story.append(Paragraph(f"República del Perú · Análisis estructurado de un caso", e["subtitle"]))
    story.append(Paragraph(f"Generado: {_esc(_fmt_dt_full(analisis.get('generado_en')))}", e["meta"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=AZUL, spaceBefore=2, spaceAfter=10))

    # ============== 1. RESUMEN EJECUTIVO ==============
    story.append(Paragraph("1. Resumen ejecutivo", e["h1"]))

    cob = analisis.get("cobertura", {})
    n_medios = cob.get("n_medios_distintos", 0)
    n_24h = analisis.get("menciones_24h", 0)
    sint = (
        f'El caso analizado <b>"{_esc(caso[:120])}"</b> se ubica en un nivel de riesgo '
        f'<b><font color="{cn.hexval()}">{nivel}</font></b> '
        f'(score agregado <b>{score}/100</b>) con tendencia <b>{_esc(tendencia)}</b>. '
        f'La razón principal de la tendencia: {_esc(analisis.get("razon_tendencia", "—"))}. '
        f'Se han identificado {len(analisis.get("actores", {}))} actores relevantes, '
        f'{len(analisis.get("regiones", {}))} regiones afectadas y {len(analisis.get("sectores", {}))} '
        f'sectores económicos comprometidos. La cobertura mediática es <b>{_esc(cob.get("diversidad", "—"))}</b> '
        f'con {n_medios} medios distintos cubriendo el caso y {n_24h} menciones en las últimas 24 horas. '
        f'Naturaleza del evento: <b>{_esc(analisis.get("naturaleza", "—"))}</b>.'
    )
    story.append(Paragraph(sint, e["body"]))
    story.append(Spacer(1, 6))

    # KPIs
    kpis = [[
        Paragraph("NIVEL RIESGO", e["kpi_label"]),
        Paragraph("SCORE", e["kpi_label"]),
        Paragraph("TENDENCIA", e["kpi_label"]),
        Paragraph("MENCIONES 24h", e["kpi_label"]),
    ], [
        Paragraph(f'<font color="{cn.hexval()}">{_esc(nivel)}</font>', e["kpi_value"]),
        Paragraph(f'<font color="{cn.hexval()}">{score}</font>', e["kpi_value"]),
        Paragraph(_esc(tendencia), e["kpi_value"]),
        Paragraph(f"{n_24h}", e["kpi_value"]),
    ]]
    kpi_tbl = Table(kpis, colWidths=[4.4*cm]*4, rowHeights=[0.55*cm, 1.1*cm])
    kpi_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GRIS_BG),
        ("BOX", (0, 0), (-1, -1), 0.4, GRIS_LINEA),
        ("LINEAFTER", (0, 0), (-2, -1), 0.4, GRIS_LINEA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(kpi_tbl)

    # ============== 2. DATOS BÁSICOS DEL CASO ==============
    story.append(Paragraph("2. Datos básicos del caso", e["h1"]))
    datos = [
        ["Nombre del caso", _esc(caso[:120] or "—")],
        ["Fecha del análisis", _esc(_fmt_dt_full(analisis.get("generado_en")))],
        ["Periodo monitoreado", _esc(inp.get("periodo", "—"))],
        ["Nivel de profundidad", _esc(inp.get("profundidad", "ESTÁNDAR"))],
        ["Solicitante", _esc(inp.get("solicitante") or "—")],
        ["Región(es) involucrada(s)", _esc(", ".join(list(analisis.get("regiones", {}).keys())[:5]) or "—")],
        ["Tipo de riesgo / naturaleza", _esc(analisis.get("naturaleza", "—"))],
        ["Nivel de riesgo", _esc(nivel)],
        ["Tendencia", _esc(tendencia)],
        ["Fuentes consultadas", f"{analisis.get('confiabilidad',{}).get('n_articulos',0)} artículos · "
                                   f"{analisis.get('confiabilidad',{}).get('n_alertas',0)} alertas · "
                                   f"{len(analisis.get('urls_analizadas', []))} URLs"],
    ]
    t = Table(datos, colWidths=[5.5*cm, 11.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), AZUL),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (1, 0), (-1, -1), [colors.white, GRIS_BG]),
        ("BOX", (0, 0), (-1, -1), 0.3, GRIS_LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, GRIS_LINEA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    if inp.get("urls"):
        story.append(Paragraph("URLs iniciales analizadas:", e["small"]))
        for u in inp["urls"][:5]:
            story.append(Paragraph(f'🔗 <a href="{_esc(u)}" color="#1E40AF">{_esc(u)}</a>', e["url"]))

    # ============== 3. DESCRIPCIÓN DEL EVENTO ==============
    story.append(Paragraph("3. Descripción del evento o caso", e["h1"]))
    desc = (
        f'<b>Descripción inicial del analista:</b> {_esc(caso)}. '
    )
    if comentario:
        desc += f'<b>Hipótesis del analista (no verificada):</b> "{_esc(comentario)}". '
    desc += (
        f'El análisis identificó {analisis.get("menciones_7d", 0)} menciones del caso en '
        f'los últimos 7 días distribuidas como: {analisis.get("menciones_24h", 0)} en las '
        f'últimas 24h, {analisis.get("menciones_72h", 0) - analisis.get("menciones_24h", 0)} '
        f'entre 24h y 72h, y el resto en el período de 3 a 7 días previos. '
        f'La cobertura involucra principalmente a actores institucionales y sociales '
        f'identificados en la sección 6.'
    )
    story.append(Paragraph(desc, e["body"]))

    # ============== 4. EVALUACIÓN DEL RIESGO POLÍTICO (6 DIMENSIONES) ==============
    story.append(Paragraph("4. Evaluación del riesgo político", e["h1"]))
    dim = analisis.get("dimensiones", {})
    dim_data = [["Dimensión", "Score (0-100)", "Clasificación"]]
    cat_map = [
        ("Institucional", "institucional"),
        ("Social", "social"),
        ("Electoral", "electoral"),
        ("Económico", "economico"),
        ("Mediático", "mediatico"),
        ("Seguridad", "seguridad"),
    ]
    for nombre, key in cat_map:
        v = dim.get(key, 0)
        if v >= 70:
            clase = "🔴 Alta atención"
        elif v >= 45:
            clase = "🟡 Vigilancia"
        else:
            clase = "🟢 Bajo"
        dim_data.append([nombre, f"{v}", clase])

    t = Table(dim_data, colWidths=[6*cm, 3.5*cm, 7.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS_BG]),
        ("BOX", (0, 0), (-1, -1), 0.3, GRIS_LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, GRIS_LINEA),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Paragraph(
        f"<b>Score global del caso:</b> <font color=\"{cn.hexval()}\">{score}/100 · {_esc(nivel)}</font>. "
        f"El cálculo agrega las 6 dimensiones, donde un riesgo CRÍTICO requiere ≥75, ALTO ≥55 "
        f"y MODERADO ≥35. Justificación de la calificación se basa en el patrón de actores "
        f"presentes y volumen de cobertura mediática.",
        e["body"]
    ))

    # ============== 5. TENDENCIA DEL CASO ==============
    story.append(Paragraph("5. Tendencia del caso", e["h1"]))
    color_tend = {"Escalada": ROJO, "En desarrollo": NARANJA, "Recurrente": AMARILLO,
                   "Estancado": GRIS, "Latente": MORADO, "Desescalada": VERDE}.get(tendencia, GRIS)
    story.append(Paragraph(
        f'<b>Clasificación:</b> <font color="{color_tend.hexval()}"><b>{_esc(tendencia)}</b></font>. '
        f'<br/><b>Razón:</b> {_esc(analisis.get("razon_tendencia", "—"))}'
        f'<br/><b>Volumen temporal:</b> {analisis.get("menciones_24h", 0)} menciones últimas 24h · '
        f'{analisis.get("menciones_72h", 0)} últimas 72h · {analisis.get("menciones_7d", 0)} últimos 7 días.',
        e["body"]
    ))

    # ============== 6. ACTORES RELEVANTES ==============
    story.append(Paragraph("6. Actores relevantes", e["h1"]))
    actores = analisis.get("actores", {})
    if actores:
        rows = [["Actor", "Menciones", "Tipo", "Nivel de riesgo"]]
        for act, n in list(actores.items())[:15]:
            tipo = "Institucional" if any(k in act for k in ["Ejecutivo", "Congreso",
                                            "Judicial", "Fiscal", "TC", "JNE", "ONPE",
                                            "Mininter", "BCR", "MEF", "Cancillería",
                                            "Contraloría", "Defensoría", "FFAA", "PNP"]) \
                    else "Social" if any(k in act for k in ["Comunidades", "Sindicatos"]) \
                    else "Político" if "Partidos" in act \
                    else "Empresarial" if "Empresas" in act \
                    else "Mediático" if "Medios" in act \
                    else "Otro"
            riesgo = "🔴 Alto" if n >= 5 else "🟡 Medio" if n >= 2 else "🟢 Bajo"
            rows.append([act, str(n), tipo, riesgo])
        t = Table(rows, colWidths=[7*cm, 2.5*cm, 4*cm, 3.5*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS_BG]),
            ("BOX", (0, 0), (-1, -1), 0.3, GRIS_LINEA),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, GRIS_LINEA),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("Sin actores identificados en la cobertura.", e["body"]))

    if analisis.get("regiones"):
        story.append(Paragraph(
            f'<b>Regiones afectadas:</b> ' + ", ".join(
                f"{r} ({n})" for r, n in analisis["regiones"].items()
            ), e["small"]))
    if analisis.get("sectores"):
        story.append(Paragraph(
            f'<b>Sectores comprometidos:</b> ' + ", ".join(
                f"{s} ({n})" for s, n in analisis["sectores"].items()
            ), e["small"]))

    # ============== 7. COBERTURA MEDIÁTICA ==============
    story.append(PageBreak())
    story.append(Paragraph("7. Cobertura mediática", e["h1"]))
    cob = analisis.get("cobertura", {})
    story.append(Paragraph(
        f'<b>Diversidad de cobertura:</b> {_esc(cob.get("diversidad", "—"))} · '
        f'{cob.get("n_medios_distintos", 0)} medios distintos. '
        f'<b>Distribución:</b> {cob.get("n_nacional", 0)} medios nacionales / oficiales · '
        f'{cob.get("n_internacional", 0)} internacionales · '
        f'{cob.get("n_redes", 0)} redes sociales · '
        f'{cob.get("n_encuestas", 0)} encuestadoras.',
        e["body"]))

    if cob.get("top_medios"):
        rows = [["Medio", "Menciones"]]
        for m, n in cob["top_medios"][:10]:
            rows.append([_esc(m[:60]), str(n)])
        t = Table(rows, colWidths=[12*cm, 3*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS_BG]),
            ("BOX", (0, 0), (-1, -1), 0.3, GRIS_LINEA),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, GRIS_LINEA),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)

    # ============== 8. PROYECCIÓN MEDIÁTICA ==============
    story.append(Paragraph("8. Proyección mediática", e["h1"]))
    pm = analisis.get("proyeccion_mediatica", {})
    rows = [["Horizonte", "Probabilidad", "Clasificación"]]
    for k in ("24h", "48h", "72h", "semana"):
        if k in pm:
            label = {"24h": "Próximas 24 horas", "48h": "Próximas 48 horas",
                     "72h": "Próximas 72 horas", "semana": "Próxima semana"}[k]
            rows.append([label, f"{pm[k]['prob']}%", _esc(pm[k]["clase"])])
    t = Table(rows, colWidths=[7*cm, 4*cm, 6*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS_BG]),
        ("BOX", (0, 0), (-1, -1), 0.3, GRIS_LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, GRIS_LINEA),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Paragraph(
        f'<b>Pase de redes a medios tradicionales:</b> {_esc(pm.get("pasa_a_medios_tradicionales", "—"))} · '
        f'<b>Probabilidad de internacionalización:</b> {_esc(pm.get("internacionalizacion", "—"))}',
        e["small"]
    ))

    # ============== 9. IMPACTO POLÍTICO ==============
    story.append(Paragraph("9. Impacto político", e["h1"]))
    dim = analisis.get("dimensiones", {})
    if dim.get("institucional", 0) >= 60:
        impacto_t = "Alto: el caso impacta a múltiples poderes del Estado y puede traducirse en " \
                     "acciones de control político (mociones, citaciones, denuncias constitucionales)."
    elif dim.get("electoral", 0) >= 60:
        impacto_t = "Alto: el caso afecta la estabilidad del proceso electoral y puede generar " \
                     "impugnaciones o cuestionamientos al sistema."
    elif dim.get("social", 0) >= 60:
        impacto_t = "Alto: el caso involucra a actores sociales organizados con capacidad de " \
                     "movilización y presión callejera."
    elif dim.get("seguridad", 0) >= 60:
        impacto_t = "Alto: el caso implica riesgo a la seguridad pública con posible despliegue " \
                     "de fuerza estatal o reacciones armadas."
    elif dim.get("economico", 0) >= 60:
        impacto_t = "Alto: el caso afecta variables económicas clave (mercados, sectores productivos, " \
                     "operaciones empresariales)."
    elif dim.get("mediatico", 0) >= 60:
        impacto_t = "Moderado-Alto: el caso domina la agenda mediática y puede modelar la opinión " \
                     "pública sobre temas conexos."
    else:
        impacto_t = "Moderado: el caso se mantiene como tema relevante sin desplazar otros temas " \
                     "de la agenda nacional."
    story.append(Paragraph(impacto_t, e["body"]))

    # ============== 10. ESCENARIOS PROSPECTIVOS ==============
    story.append(Paragraph("10. Escenarios prospectivos", e["h1"]))
    for esc_dict in analisis.get("escenarios", []):
        story.append(Paragraph(
            f'<b>● {_esc(esc_dict["nombre"])}</b> (probabilidad estimada: {_esc(esc_dict["probabilidad"])})',
            e["h2"]))
        story.append(Paragraph(_esc(esc_dict["descripcion"]), e["body"]))
        story.append(Paragraph(
            f'<b>Detonantes:</b> {_esc(esc_dict["detonantes"])}', e["small"]))
        story.append(Paragraph(
            f'<b>Impacto político probable:</b> {_esc(esc_dict["impacto_politico"])}', e["small"]))
        story.append(Spacer(1, 4))

    # ============== 11. ALERTAS TEMPRANAS ==============
    story.append(Paragraph("11. Alertas tempranas a monitorear", e["h1"]))
    for alerta in analisis.get("alertas_tempranas", []):
        story.append(Paragraph(f'• {_esc(alerta)}', e["body"]))

    # ============== 12. EVALUACIÓN DE CONFIABILIDAD ==============
    story.append(Paragraph("12. Evaluación de confiabilidad", e["h1"]))
    conf = analisis.get("confiabilidad", {})
    story.append(Paragraph(
        f"Sobre {conf.get('n_articulos', 0)} artículos y {conf.get('n_alertas', 0)} alertas analizadas:",
        e["body"]))
    rows = [
        ["Tipo de fuente", "Cantidad", "% del total"],
        ["Confirmada (oficial)", str(conf.get("confirmada", 0)), f"{conf.get('%_oficial', 0)}%"],
        ["Probable (medios)", str(conf.get("probable", 0)), f"{conf.get('%_medios', 0)}%"],
        ["No confirmada (redes sociales)", str(conf.get("no_confirmada", 0)), f"{conf.get('%_redes', 0)}%"],
    ]
    t = Table(rows, colWidths=[8*cm, 3*cm, 4*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS_BG]),
        ("BOX", (0, 0), (-1, -1), 0.3, GRIS_LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, GRIS_LINEA),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Paragraph(
        "<i>Las afirmaciones provenientes de redes sociales no verificadas se presentan como "
        "tales y NO deben ser tratadas como hechos confirmados. La información etiquetada como "
        "'Probable' requiere contrastación con al menos una fuente oficial cuando sea posible.</i>",
        e["small"]))

    # ============== 13. CONCLUSIÓN ANALÍTICA ==============
    story.append(Paragraph("13. Conclusión analítica", e["h1"]))
    if nivel == "CRÍTICO":
        concl = (
            f'El caso representa un <b>riesgo crítico</b> para la estabilidad política del Perú. '
            f'Su tendencia <b>{_esc(tendencia)}</b> sugiere que requiere atención inmediata por parte '
            f'de los stakeholders identificados. La concentración de actores institucionales y '
            f'la diversidad de cobertura indican que es un evento con potencial de generar '
            f'consecuencias políticas significativas en el corto plazo.'
        )
    elif nivel == "ALTO":
        concl = (
            f'El caso muestra un <b>riesgo alto</b> con tendencia <b>{_esc(tendencia)}</b>. '
            f'Aunque aún no alcanza niveles críticos, su evolución debe monitorearse con '
            f'atención especial en las próximas 48-72 horas. La combinación de actores '
            f'involucrados puede generar dinámicas que escalen rápidamente si no hay acciones '
            f'institucionales adecuadas.'
        )
    elif nivel == "MODERADO":
        concl = (
            f'El caso se ubica en un nivel de <b>riesgo moderado</b>. La tendencia '
            f'<b>{_esc(tendencia)}</b> permite mantener un monitoreo regular sin requerir '
            f'movilización extraordinaria de recursos analíticos. Sin embargo, se debe estar '
            f'atento a posibles detonantes que puedan modificar el escenario.'
        )
    else:
        concl = (
            f'El caso presenta un <b>riesgo bajo</b> según las dimensiones evaluadas. '
            f'La cobertura es limitada y los actores involucrados no muestran capacidad '
            f'de movilización significativa en este momento. Se recomienda monitoreo pasivo '
            f'y documentación del caso para futuras referencias.'
        )
    story.append(Paragraph(concl, e["body"]))

    # ============== 14. RECOMENDACIÓN PARA EL ANALISTA ==============
    story.append(Paragraph("14. Recomendación para el analista", e["h1"]))
    for rec in analisis.get("recomendaciones", []):
        story.append(Paragraph(f'• {_esc(rec)}', e["body"]))

    # ============== FOOTER ==============
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRIS_LINEA,
                              spaceBefore=2, spaceAfter=4))
    story.append(Paragraph(
        f"APURISK 1.0 · Plataforma OSINT de Riesgos Políticos del Perú · "
        f"Análisis algorítmico basado en {conf.get('n_articulos', 0)} artículos, "
        f"{conf.get('n_alertas', 0)} alertas y {len(inp.get('urls', []))} URLs proporcionadas. "
        f"Generado: {_esc(_fmt_dt_full(analisis.get('generado_en')))}. "
        f"Este reporte es una síntesis analítica que requiere validación por parte del analista. "
        f"No constituye una conclusión definitiva.",
        ParagraphStyle("footer", parent=getSampleStyleSheet()["Normal"],
                        fontSize=7, textColor=GRIS, fontName="Helvetica-Oblique",
                        alignment=TA_JUSTIFY)
    ))

    doc.build(story)
    return output_path
