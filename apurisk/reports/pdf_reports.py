"""Generadores de reporte PDF (diario y semanal) — APURISK 1.0.

Usa ReportLab para PDFs profesionales con:
  - Cover page con score y nivel
  - Resumen ejecutivo
  - Top factores de riesgo (matriz P×I)
  - Alertas críticas
  - Headlines con URL clicable
  - Análisis por nuevas dimensiones (FFAA, fronteras, migración, diplomacia)
  - Reporte semanal: comparativos, tendencias, top eventos de la semana
"""
from __future__ import annotations
import json
import glob
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT


# Paleta APURISK
COLOR_AZUL = colors.HexColor("#1E40AF")
COLOR_AZUL_OSC = colors.HexColor("#0F172A")
COLOR_GRIS = colors.HexColor("#64748B")
COLOR_GRIS_BG = colors.HexColor("#F1F5F9")
COLOR_ROJO = colors.HexColor("#DC2626")
COLOR_NARANJA = colors.HexColor("#EA580C")
COLOR_AMARILLO = colors.HexColor("#F59E0B")
COLOR_VERDE = colors.HexColor("#22C55E")


_MESES = {1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
          7: "jul", 8: "ago", 9: "set", 10: "oct", 11: "nov", 12: "dic"}


def _color_nivel(nivel: str):
    return {
        "CRÍTICO": COLOR_ROJO, "CRÍTICA": COLOR_ROJO,
        "ALTO": COLOR_NARANJA, "ALTA": COLOR_NARANJA,
        "MEDIO": COLOR_AMARILLO, "MEDIA": COLOR_AMARILLO,
        "BAJO": COLOR_VERDE, "BAJA": COLOR_VERDE,
    }.get(nivel, COLOR_GRIS)


def _fmt_dt(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"{dt.day:02d} {_MESES[dt.month]} {dt.strftime('%H:%M')}"
    except Exception:
        return iso_str[:16]


def _fmt_dt_full(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"{dt.day:02d} {_MESES[dt.month]} {dt.year} · {dt.strftime('%H:%M')}"
    except Exception:
        return iso_str


def _esc(s):
    """Escapa texto para Paragraph."""
    if not s:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _styles():
    ss = getSampleStyleSheet()
    estilos = {
        "title": ParagraphStyle("title", parent=ss["Title"], fontSize=22, textColor=COLOR_AZUL_OSC,
                                 spaceAfter=8, alignment=TA_LEFT, fontName="Helvetica-Bold"),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontSize=11, textColor=COLOR_GRIS,
                                    spaceAfter=18, alignment=TA_LEFT),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, textColor=COLOR_AZUL,
                              spaceBefore=14, spaceAfter=10, fontName="Helvetica-Bold"),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=COLOR_AZUL_OSC,
                              spaceBefore=10, spaceAfter=6, fontName="Helvetica-Bold"),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=10, leading=14, textColor=COLOR_AZUL_OSC),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8.5, leading=11, textColor=COLOR_GRIS),
        "url": ParagraphStyle("url", parent=ss["Normal"], fontSize=8, leading=10, textColor=COLOR_AZUL,
                               underlineWidth=0.5),
        "kpi_label": ParagraphStyle("kpi_label", parent=ss["Normal"], fontSize=8, textColor=COLOR_GRIS, alignment=TA_CENTER),
        "kpi_value": ParagraphStyle("kpi_value", parent=ss["Normal"], fontSize=24, alignment=TA_CENTER, fontName="Helvetica-Bold"),
    }
    return estilos


# ============================================================================
# REPORTE DIARIO
# ============================================================================
def generar_reporte_diario_pdf(output_path: str, snapshot: dict) -> str:
    """Genera el PDF del reporte diario a partir de un snapshot JSON."""
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title="APURISK · Reporte Diario", author="APURISK 1.0",
    )
    e = _styles()
    story = []
    riesgo = snapshot.get("riesgo", {})
    fecha_gen = snapshot.get("generado", datetime.now().isoformat())
    modo = snapshot.get("modo", "live")

    # ===== Cabecera =====
    story.append(Paragraph("APURISK · Reporte Diario de Riesgo Político", e["title"]))
    story.append(Paragraph(
        f"Plataforma OSINT · Perú · Generado: {_fmt_dt_full(fecha_gen)} · Modo: {modo}",
        e["subtitle"]
    ))

    # ===== KPIs =====
    nivel = riesgo.get("nivel", "—")
    color_n = _color_nivel(nivel)
    kpi_data = [
        [
            Paragraph("Score Global", e["kpi_label"]),
            Paragraph("Alertas Críticas", e["kpi_label"]),
            Paragraph("Conflictos Sociales", e["kpi_label"]),
            Paragraph("Cobertura 24h", e["kpi_label"]),
        ],
        [
            Paragraph(f'<font color="{color_n.hexval()}">{riesgo.get("global", "—")}</font>', e["kpi_value"]),
            Paragraph(f'<font color="{COLOR_ROJO.hexval()}">{len([a for a in snapshot.get("alertas", []) if a["nivel"]=="CRÍTICA"])}</font>', e["kpi_value"]),
            Paragraph(f'{snapshot.get("n_conflictos", 0)}', e["kpi_value"]),
            Paragraph(f'{snapshot.get("n_articulos_24h", 0)}', e["kpi_value"]),
        ],
        [
            Paragraph(f'<font color="{color_n.hexval()}">{nivel}</font>', e["small"]),
            Paragraph(f'{len(snapshot.get("alertas", []))} totales', e["small"]),
            Paragraph(f'severidad alta: {len([c for c in snapshot.get("conflictos", []) if (c.get("raw") or {}).get("severidad")=="alta"])}', e["small"]),
            Paragraph(f'+{snapshot.get("n_tweets", 0)} tweets', e["small"]),
        ],
    ]
    kpi_tbl = Table(kpi_data, colWidths=[4*cm]*4)
    kpi_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 14))

    # ===== Síntesis ejecutiva =====
    story.append(Paragraph("1. Síntesis ejecutiva", e["h1"]))
    cat_dom = max(riesgo.get("categorias", {}).items(), key=lambda x: x[1], default=("—", 0))
    sintesis = (
        f"En las últimas 24 horas se procesaron <b>{snapshot.get('n_articulos_24h', 0)} artículos</b> "
        f"de medios y datasets internacionales y <b>{snapshot.get('n_tweets', 0)} tweets</b>. "
        f"Se activaron <b>{len(snapshot.get('alertas', []))} alertas</b> "
        f"({len([a for a in snapshot.get('alertas', []) if a['nivel']=='CRÍTICA'])} críticas) y se monitorean "
        f"<b>{snapshot.get('n_conflictos', 0)} conflictos sociales</b>. "
        f"Score global: <b><font color=\"{color_n.hexval()}\">{riesgo.get('global', '—')}/100 · {nivel}</font></b>. "
        f"Categoría dominante: <b>{cat_dom[0].replace('_', ' ')}</b> ({cat_dom[1]:.0f}/100). "
        f"Sentimiento agregado: <b>{riesgo.get('sentimiento_promedio', 0)}</b>."
    )
    story.append(Paragraph(sintesis, e["body"]))
    story.append(Spacer(1, 8))

    # ===== Top Factores de Riesgo =====
    story.append(Paragraph("2. Top factores de riesgo (Matriz Probabilidad × Impacto)", e["h1"]))
    matriz = snapshot.get("matriz_riesgo", [])[:7]
    if matriz:
        rows = [["#", "Factor", "Categoría", "Prob", "Imp", "Score", "Tend"]]
        for i, f in enumerate(matriz, 1):
            rows.append([
                str(i),
                f["nombre"],
                f["categoria"],
                str(f["probabilidad"]),
                str(f["impacto"]),
                f"{f['score']} · {f['nivel']}",
                f["tendencia"],
            ])
        tbl = Table(rows, colWidths=[0.6*cm, 5.2*cm, 3.8*cm, 1.2*cm, 1.2*cm, 2.5*cm, 1*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_GRIS_BG]),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, COLOR_GRIS),
            ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)
    story.append(Spacer(1, 12))

    # ===== Alertas críticas =====
    story.append(Paragraph("3. Alertas críticas activas", e["h1"]))
    crit = [a for a in snapshot.get("alertas", []) if a["nivel"] == "CRÍTICA"][:10]
    if not crit:
        story.append(Paragraph("Sin alertas críticas en la ventana actual.", e["body"]))
    for a in crit:
        block = []
        bloque_titulo = (
            f'<font color="{COLOR_ROJO.hexval()}"><b>[{a["nivel"]}]</b></font> '
            f'<b>{_esc(a["titulo"])}</b>'
        )
        block.append(Paragraph(bloque_titulo, e["body"]))
        meta = (
            f'<i>{_esc(a["categoria"])} · '
            f'{_esc(a.get("region") or "—")} · '
            f'{_fmt_dt(a.get("timestamp", ""))} · '
            f'fuente: {_esc(a["fuente"])}</i>'
        )
        block.append(Paragraph(meta, e["small"]))
        block.append(Paragraph(_esc(a.get("resumen", "")), e["body"]))
        block.append(Paragraph(
            f'<b>Acción:</b> {_esc(a.get("accion", ""))}', e["small"]
        ))
        if a.get("url"):
            block.append(Paragraph(
                f'🔗 <a href="{a["url"]}" color="#1E40AF"><u>{_esc(a["url"])}</u></a>',
                e["url"]
            ))
        block.append(Spacer(1, 6))
        story.append(KeepTogether(block))
    story.append(Spacer(1, 6))

    # ===== Análisis por dimensiones nuevas =====
    story.append(Paragraph("4. Análisis por dimensión de riesgo", e["h1"]))
    cats = riesgo.get("categorias", {})
    dim_data = [["Dimensión", "Score 0-100", "Lectura"]]
    cat_names = {
        "estabilidad_gobierno": "Estabilidad gubernamental",
        "conflictos_sociales": "Conflictos sociales",
        "riesgo_regulatorio": "Riesgo regulatorio",
        "polarizacion": "Polarización",
        "corrupcion": "Corrupción",
        "seguridad": "Seguridad ciudadana",
        "intervencion_militar": "Intervención FFAA",
        "tensiones_fronterizas": "Tensiones fronterizas",
        "crisis_migratoria": "Crisis migratoria",
        "tensiones_diplomaticas": "Tensiones diplomáticas",
        "presion_economica": "Presión económica",
    }
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        if v >= 70:
            lectura = "🔴 Alta atención"
        elif v >= 45:
            lectura = "🟡 Vigilancia"
        else:
            lectura = "🟢 Estable"
        dim_data.append([cat_names.get(k, k), f"{v:.1f}", lectura])
    tbl = Table(dim_data, colWidths=[7*cm, 3*cm, 5*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_GRIS_BG]),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, COLOR_GRIS),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 12))

    # ===== Headlines con URL =====
    story.append(PageBreak())
    story.append(Paragraph("5. Cobertura de medios · últimas 24h", e["h1"]))
    arts = sorted(
        [a for a in snapshot.get("articulos", []) if a.get("source_id") != "twitter_x"],
        key=lambda x: x.get("published", ""),
        reverse=True,
    )[:30]
    for a in arts:
        block = []
        block.append(Paragraph(
            f'<font color="{COLOR_AZUL.hexval()}"><b>{_esc(a.get("source_name", ""))}</b></font> · '
            f'<i>{_fmt_dt(a.get("published", ""))}</i>',
            e["small"]
        ))
        block.append(Paragraph(f"<b>{_esc(a.get('title', ''))}</b>", e["body"]))
        if a.get("summary"):
            block.append(Paragraph(_esc(a["summary"][:250]), e["small"]))
        if a.get("url"):
            block.append(Paragraph(
                f'🔗 <a href="{a["url"]}" color="#1E40AF"><u>{_esc(a["url"])}</u></a>',
                e["url"]
            ))
        block.append(Spacer(1, 4))
        story.append(KeepTogether(block))

    # ===== Footer / Notas =====
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "APURISK 1.0 — Plataforma OSINT de Riesgos Políticos del Perú · "
        "Fuentes: medios peruanos (RPP, El Comercio, La República, Gestión, IDL, Infobae, CNN), "
        "Defensoría del Pueblo, Congreso, Twitter/X (API v2), GDELT. "
        "Reporte generado automáticamente cada 30 minutos en modo live.",
        e["small"]
    ))

    doc.build(story)
    return output_path


# ============================================================================
# REPORTE SEMANAL
# ============================================================================
def generar_reporte_semanal_pdf(output_path: str, snapshots_dir: str) -> str:
    """Genera el PDF del reporte semanal agregando los snapshots de los últimos 7 días."""
    snaps = _cargar_snapshots_semana(snapshots_dir)
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title="APURISK · Reporte Semanal", author="APURISK 1.0",
    )
    e = _styles()
    story = []

    # Última semana
    desde = datetime.now() - timedelta(days=7)
    hasta = datetime.now()

    story.append(Paragraph("APURISK · Reporte Semanal de Riesgo Político", e["title"]))
    story.append(Paragraph(
        f"Plataforma OSINT · Perú · Período: {desde.strftime('%d %b %Y')} → {hasta.strftime('%d %b %Y')}",
        e["subtitle"]
    ))

    if not snaps:
        story.append(Paragraph("Sin snapshots disponibles para el período. Ejecuta el sistema en modo --live --watch 1800 para acumular historia.", e["body"]))
        story.append(Paragraph("Como fallback, este reporte muestra el snapshot actual.", e["body"]))
        # carga el snapshot más reciente como referencia
        latest = sorted(glob.glob(str(Path(snapshots_dir) / "apurisk_snapshot_*.json")))
        if latest:
            with open(latest[-1], encoding="utf-8") as f:
                snaps = [json.load(f)]

    if not snaps:
        doc.build(story)
        return output_path

    # ===== Resumen de la semana =====
    story.append(Paragraph("1. Resumen de la semana", e["h1"]))
    n = len(snaps)
    score_avg = sum(s.get("riesgo", {}).get("global", 0) for s in snaps) / max(1, n)
    score_max = max(s.get("riesgo", {}).get("global", 0) for s in snaps)
    score_min = min(s.get("riesgo", {}).get("global", 0) for s in snaps)
    alertas_total = sum(len(s.get("alertas", [])) for s in snaps)
    crit_total = sum(len([a for a in s.get("alertas", []) if a["nivel"] == "CRÍTICA"]) for s in snaps)
    art_total = sum(s.get("n_articulos_24h", 0) for s in snaps)

    color_avg = _color_nivel(_nivel_de_score(score_avg))

    kpi_data = [
        [
            Paragraph("Score Promedio", e["kpi_label"]),
            Paragraph("Score Máximo", e["kpi_label"]),
            Paragraph("Alertas Críticas", e["kpi_label"]),
            Paragraph("Snapshots", e["kpi_label"]),
        ],
        [
            Paragraph(f'<font color="{color_avg.hexval()}">{score_avg:.1f}</font>', e["kpi_value"]),
            Paragraph(f'<font color="{COLOR_ROJO.hexval()}">{score_max:.1f}</font>', e["kpi_value"]),
            Paragraph(f'<font color="{COLOR_ROJO.hexval()}">{crit_total}</font>', e["kpi_value"]),
            Paragraph(f"{n}", e["kpi_value"]),
        ],
        [
            Paragraph(f'min: {score_min:.1f}', e["small"]),
            Paragraph(f'período pico', e["small"]),
            Paragraph(f'{alertas_total} totales', e["small"]),
            Paragraph(f'{art_total} ítems procesados', e["small"]),
        ],
    ]
    tbl = Table(kpi_data, colWidths=[4*cm]*4)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_GRIS_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 14))

    # ===== Tendencia diaria =====
    story.append(Paragraph("2. Evolución diaria del score", e["h1"]))
    rows = [["Fecha", "Score", "Nivel", "Alertas", "Ítems"]]
    for s in snaps:
        rows.append([
            _fmt_dt(s.get("generado", "")),
            f'{s.get("riesgo", {}).get("global", 0):.1f}',
            s.get("riesgo", {}).get("nivel", "—"),
            f'{len(s.get("alertas", []))}',
            f'{s.get("n_articulos_24h", 0)}',
        ])
    tbl = Table(rows, colWidths=[4.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 2.5*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_GRIS_BG]),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, COLOR_GRIS),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 12))

    # ===== Top factores semana (del snapshot más reciente) =====
    last = snaps[-1]
    story.append(Paragraph("3. Factores de riesgo dominantes (snapshot más reciente)", e["h1"]))
    matriz = last.get("matriz_riesgo", [])[:10]
    if matriz:
        rows = [["#", "Factor", "Categoría", "Prob", "Imp", "Score · Nivel"]]
        for i, f in enumerate(matriz, 1):
            rows.append([str(i), f["nombre"], f["categoria"], str(f["probabilidad"]),
                         str(f["impacto"]), f"{f['score']} · {f['nivel']}"])
        tbl = Table(rows, colWidths=[0.6*cm, 5.4*cm, 4*cm, 1.2*cm, 1.2*cm, 2.6*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_GRIS_BG]),
            ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRIS),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, COLOR_GRIS),
            ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
    story.append(Spacer(1, 12))

    # ===== Top eventos críticos de la semana =====
    story.append(PageBreak())
    story.append(Paragraph("4. Top eventos críticos de la semana", e["h1"]))
    eventos = []
    seen = set()
    for s in snaps:
        for a in s.get("alertas", []):
            if a["nivel"] != "CRÍTICA":
                continue
            key = a["titulo"]
            if key in seen:
                continue
            seen.add(key)
            eventos.append(a)
    eventos = eventos[:15]
    if not eventos:
        story.append(Paragraph("Sin eventos críticos en el período.", e["body"]))
    for a in eventos:
        block = []
        block.append(Paragraph(
            f'<font color="{COLOR_ROJO.hexval()}"><b>[{a["nivel"]}]</b></font> '
            f'<b>{_esc(a["titulo"])}</b>', e["body"]
        ))
        block.append(Paragraph(
            f'<i>{_esc(a["categoria"])} · {_esc(a.get("region") or "—")} · '
            f'{_fmt_dt(a.get("timestamp", ""))} · {_esc(a["fuente"])}</i>', e["small"]
        ))
        block.append(Paragraph(_esc(a.get("resumen", "")[:280]), e["body"]))
        if a.get("url"):
            block.append(Paragraph(
                f'🔗 <a href="{a["url"]}" color="#1E40AF"><u>{_esc(a["url"])}</u></a>', e["url"]
            ))
        block.append(Spacer(1, 4))
        story.append(KeepTogether(block))

    # ===== Footer =====
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "APURISK 1.0 · Reporte semanal generado automáticamente. "
        "Para reportes diarios usar PDF correspondiente. "
        "Datos basados en snapshots acumulados en /output durante el período.",
        e["small"]
    ))

    doc.build(story)
    return output_path


def _nivel_de_score(score: float) -> str:
    if score >= 70:
        return "ALTO"
    if score >= 45:
        return "MEDIO"
    return "BAJO"


def _cargar_snapshots_semana(snapshots_dir: str) -> list[dict]:
    """Carga JSONs de snapshots de los últimos 7 días."""
    out = []
    cutoff = datetime.now() - timedelta(days=7)
    for path in sorted(glob.glob(str(Path(snapshots_dir) / "apurisk_snapshot_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            gen = data.get("generado", "")
            try:
                dt = datetime.fromisoformat(gen)
                if dt >= cutoff:
                    out.append(data)
            except Exception:
                out.append(data)
        except Exception:
            continue
    return out
