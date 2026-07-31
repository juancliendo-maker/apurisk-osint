"""THALOS · Reporte Político por Caso — render del PDF (sub-fase 5).

RENDER PURO: dibuja el reporte I-VII que ya ensambló y persistió la sub-fase 4b
en `analisis_json`. NO llama al modelo, NO recalcula el análisis. Si el objeto no
está o está incompleto, lo dice — no inventa contenido.

Sistema visual: thalos_base (el mismo del AP24, su hermano mayor: mismo nivel de
acabado). Portada Navy sólido, header/footer canónicos, tipografía embebida.

LAS DOS VOCES — requisito central del producto:
  · I-IV  describen el material (voz de la máquina): registro descriptivo normal.
  · V     es el JUICIO DEL ANALISTA: bloque atribuido visualmente distinto —
          filete vertical ORO al margen, encabezado en versalitas, acento
          PURPURA_ANALISIS y firma al pie. El lector debe distinguir sin
          ambigüedad dónde termina la descripción y empieza el juicio.
"""
from __future__ import annotations
import re
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether,
)

from . import thalos_base as T
from .reporte_a import escape_txt

FIRMA_ANALISTA = "Cnel. (r) Juan Carlos Liendo O'Connor"

# Títulos de las secciones descriptivas tal como los emite el motor (4a).
_ORDEN_DESCRIPTIVAS = [
    "I. SÍNTESIS DEL CASO",
    "II. LA PREGUNTA Y EL MATERIAL",
    "III. DESARROLLO EN LA VENTANA",
    "IV. CONEXIONES Y CONTEXTO",
]


# ── Piezas visuales ───────────────────────────────────────────────────────────
def _encabezado(texto: str, st: dict) -> Paragraph:
    return Paragraph(escape_txt(texto.upper()), st["h2"])


def _bloque(header, contenido: list) -> list:
    """Encabezado + línea de oro + contenido, con el encabezado pegado a su primer
    flowable (nunca queda huérfano al pie de página)."""
    cab = [header, T.linea_oro()]
    if contenido:
        return [KeepTogether(cab + [contenido[0]])] + contenido[1:]
    return [KeepTogether(cab)]


def _parrafos(cuerpo: str, st: dict) -> list:
    """Prosa del análisis. El texto ya viene resuelto por el arnés (sin marcadores
    [Pn] ni URLs crudas): aquí solo se maqueta."""
    return [Paragraph(escape_txt(p.strip()), st["body"])
            for p in (cuerpo or "").split("\n") if p.strip()]


def _recuadro_silencios(silencios: list, st: dict) -> Table:
    """Los silencios son un HALLAZGO del arnés (escenarios sin material que los
    sostenga), no un vacío que ocultar: se muestran destacados."""
    cuerpo = "<br/>".join(f"· {escape_txt(s)}" for s in silencios)
    inner = [
        Paragraph("SILENCIOS DEL EXPEDIENTE", ParagraphStyle(
            "sil_t", fontName=T.FONT_TITLE, fontSize=11, leading=14,
            textColor=T.AMBAR_ALTO, spaceAfter=4)),
        Paragraph(cuerpo, ParagraphStyle(
            "sil_b", fontName=T.FONT_BODY, fontSize=10, leading=14,
            textColor=T.GRIS_CUERPO)),
        Paragraph("Escenarios candidatos sin ninguna pieza que los sostenga en el "
                  "material reunido.", ParagraphStyle(
                      "sil_n", fontName=T.FONT_BODY, fontSize=8.5, leading=11,
                      textColor=T.GRIS_META, spaceBefore=4)),
    ]
    t = Table([[inner]], colWidths=[T.PAGE_W - 2 * T.MARGEN_LAT])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), T.colors.HexColor("#FDF6E3")),
        ("BOX", (0, 0), (-1, -1), 0.8, T.AMBAR_ALTO),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


# ── SECCIÓN V — la voz del analista, visualmente inconfundible ────────────────
def _espectro_kent(termino: str, vocabulario: list) -> Table:
    """Espectro estimativo (estilo TIE-001): todo el vocabulario de Kent como
    chips, con el término elegido DESTACADO y el resto atenuado. Comunica que el
    juicio se emitió dentro de una escala controlada, no en el vacío."""
    vocab = list(vocabulario or [])
    if termino and termino not in vocab:
        vocab = [termino] + vocab
    if not vocab:
        vocab = [termino or "—"]
    # Mismo cuerpo en activo e inactivo: el destaque es color+borde, no tamaño.
    # (Un activo más grande se parte en más líneas y descuadra la fila.)
    st_on = ParagraphStyle("k_on", fontName=T.FONT_TITLE, fontSize=7.5, leading=10,
                           textColor=T.BLANCO, alignment=TA_CENTER)
    st_off = ParagraphStyle("k_off", fontName=T.FONT_BODY, fontSize=7.5, leading=10,
                            textColor=T.GRIS_META, alignment=TA_CENTER)
    celdas, estilos = [], [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, k in enumerate(vocab):
        activo = (k == termino)
        celdas.append(Paragraph(escape_txt(k), st_on if activo else st_off))
        if activo:
            estilos += [("BACKGROUND", (i, 0), (i, 0), T.PURPURA_ANALISIS),
                        ("BOX", (i, 0), (i, 0), 0.8, T.ORO)]
        else:
            estilos += [("BACKGROUND", (i, 0), (i, 0), T.colors.HexColor("#EFEFF4")),
                        ("BOX", (i, 0), (i, 0), 0.4, T.colors.HexColor("#DCDCE4"))]
    ancho = (T.PAGE_W - 2 * T.MARGEN_LAT - 0.55 * inch) / max(1, len(vocab))
    t = Table([celdas], colWidths=[ancho] * len(vocab))
    t.setStyle(TableStyle(estilos))
    return t


def _bloque_voz_analista(seccion_v: dict, vocabulario: list, st: dict) -> list:
    """SECCIÓN V con MARCA DE DOS VOCES: filete vertical ORO al margen izquierdo,
    encabezado en versalitas, acento púrpura y firma del analista al pie."""
    interior = []
    interior.append(Paragraph(
        "J U I C I O &nbsp; D E L &nbsp; A N A L I S T A",
        ParagraphStyle("v_t", fontName=T.FONT_TITLE, fontSize=12, leading=15,
                       textColor=T.PURPURA_ANALISIS, spaceAfter=2)))
    interior.append(Paragraph(
        "Esta sección NO la redacta el sistema: es la valoración del analista sobre "
        "el material descrito en las secciones anteriores.",
        ParagraphStyle("v_n", fontName=T.FONT_BODY, fontSize=8.5, leading=11,
                       textColor=T.GRIS_META, spaceAfter=8)))

    filas = (seccion_v or {}).get("horizontes") or []
    if not filas:
        interior.append(Paragraph("El analista no registró proyección para este caso.",
                                  st["body"]))
    for f in filas:
        interior.append(Paragraph(
            f"HORIZONTE {f.get('horizonte_dias')} DÍAS",
            ParagraphStyle("v_h", fontName=T.FONT_TITLE, fontSize=9.5, leading=12,
                           textColor=T.NAVY, spaceBefore=8, spaceAfter=3)))
        interior.append(_espectro_kent(f.get("kent") or "", vocabulario))
        if f.get("prosa"):
            interior.append(Paragraph(escape_txt(f["prosa"]), ParagraphStyle(
                "v_p", fontName=T.FONT_BODY, fontSize=10.5, leading=15,
                textColor=T.GRIS_CUERPO, spaceBefore=5)))

    interior.append(Spacer(1, 10))
    interior.append(Paragraph(
        escape_txt(FIRMA_ANALISTA),
        ParagraphStyle("v_f", fontName=T.FONT_TITLE, fontSize=9.5, leading=12,
                       textColor=T.PURPURA_ANALISIS, alignment=TA_LEFT)))

    # Filete vertical ORO: columna estrecha con fondo, al margen izquierdo.
    ancho_util = T.PAGE_W - 2 * T.MARGEN_LAT
    t = Table([["", interior]], colWidths=[0.055 * inch, ancho_util - 0.055 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), T.ORO),
        ("BACKGROUND", (1, 0), (1, 0), T.colors.HexColor("#F7F6FB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 14),
        ("RIGHTPADDING", (1, 0), (1, 0), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    return [t]


# ── SECCIÓN VI — dos clases probatorias ───────────────────────────────────────
def _tabla_clase(hechos: list, con_enlace: bool) -> Table:
    """Tabla de una clase probatoria. En la clase verificable el título es un
    hipervínculo LIMPIO (nunca se imprime la URL cruda, como en el AP24)."""
    st_h = ParagraphStyle("c_h", fontName=T.FONT_TITLE, fontSize=9.5,
                          textColor=T.NAVY, leading=12)
    st_n = ParagraphStyle("c_n", fontName=T.FONT_TITLE, fontSize=9.5,
                          textColor=T.NAVY, leading=12, alignment=TA_CENTER)
    st_t = ParagraphStyle("c_t", fontName=T.FONT_BODY, fontSize=9.5,
                          textColor=T.GRIS_CUERPO, leading=12.5)
    st_l = ParagraphStyle("c_l", fontName=T.FONT_BODY, fontSize=9.5,
                          textColor=T.NAVY, leading=12.5)
    st_f = ParagraphStyle("c_f", fontName=T.FONT_BODY, fontSize=9,
                          textColor=T.GRIS_META, leading=12)
    data = [[Paragraph("ID", st_h), Paragraph("Hecho citado", st_h),
             Paragraph("Fuente", st_h)]]
    for h in hechos:
        titulo = escape_txt((h.get("titulo") or "—")[:150])
        url = (h.get("url") or "").strip()
        if con_enlace and url:
            celda = Paragraph(f'<link href="{escape_txt(url)}">{titulo}</link>', st_l)
        else:
            celda = Paragraph(titulo, st_t)
        # En la clase de expediente el nombre del archivo ya es el título: repetirlo
        # en «Fuente» no informa. Se declara su naturaleza probatoria.
        if h.get("procedencia") == "documento_analista":
            fuente = "Documento del analista"
        else:
            fuente = h.get("fuente") or h.get("nombre_archivo") or "—"
        data.append([Paragraph(escape_txt(h.get("id_cita") or "—"), st_n), celda,
                     Paragraph(escape_txt(str(fuente)[:28]), st_f)])
    t = Table(data, colWidths=[0.45 * inch, 4.25 * inch, 1.4 * inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), T.GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 1, T.ORO),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, T.ORO),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, T.colors.HexColor("#EADFB0")),
        ("BACKGROUND", (0, 1), (-1, -1), T.BLANCO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _contenido_hechos(seccion_vi: dict, st: dict) -> list:
    """Las dos clases, cada una rotulada. Una clase vacía NO se dibuja."""
    out = []
    clases = (seccion_vi or {}).get("clases") or []
    for cl in clases:
        verificable = cl.get("clase") == "FUENTE ABIERTA VERIFICABLE"
        nota = ("Enlaces públicos: el lector puede comprobar cada pieza."
                if verificable else
                "Material incorporado por el analista: no verificable por el lector.")
        out.append(Paragraph(escape_txt(cl.get("clase") or ""), ParagraphStyle(
            "cl_t", fontName=T.FONT_TITLE, fontSize=10, leading=13,
            textColor=T.NAVY, spaceBefore=8, spaceAfter=2)))
        out.append(Paragraph(nota, ParagraphStyle(
            "cl_n", fontName=T.FONT_BODY, fontSize=8.5, leading=11,
            textColor=T.GRIS_META, spaceAfter=5)))
        out.append(_tabla_clase(cl.get("hechos") or [], con_enlace=verificable))
    if not out:
        out = [Paragraph("Sin hechos citados en el expediente.", st["body"])]
    return out


# ── SECCIÓN VII — nota de material ────────────────────────────────────────────
def _contenido_nota(seccion_vii: dict, st: dict) -> list:
    s = seccion_vii or {}
    partes = [
        f"Expediente: {s.get('total_piezas', 0)} piezas · "
        f"{s.get('incluidas', 0)} incluidas · {s.get('citadas', 0)} citadas "
        f"({s.get('citadas_verificables', 0)} de fuente abierta verificable, "
        f"{s.get('citadas_expediente', 0)} material del analista)."
    ]
    if s.get("fallidas"):
        partes.append(f"Piezas con extracción fallida ({len(s['fallidas'])}), no citables: "
                      + "; ".join(f"{f.get('titulo')} — {f.get('nota_error')}"
                                  for f in s["fallidas"][:6]) + ".")
    if s.get("excluidas"):
        partes.append(f"Excluidas por el analista ({len(s['excluidas'])}): "
                      + "; ".join(str(e.get("titulo")) for e in s["excluidas"][:6]) + ".")
    if s.get("convencion"):
        partes.append(s["convencion"])
    return [T.recuadro_ejecutivo("NOTA DE MATERIAL",
                                 "<br/><br/>".join(escape_txt(p) for p in partes), st)]


# ── Render ────────────────────────────────────────────────────────────────────
def generar_reporte_caso_pdf(db_path: str, reporte_id: int) -> dict:
    """Renderiza el PDF THALOS del Reporte por Caso. Devuelve {pdf, estado, nota}.

    RENDER PURO: toma el objeto I-VII persistido por la sub-fase 4b. No llama al
    modelo ni recalcula nada. Si el análisis no está ensamblado, devuelve
    estado 'error' con el motivo (no inventa un PDF vacío).
    """
    from ..storage.config_loader import (
        obtener_analisis_caso, obtener_caso_meta, cargar_vocabulario_kent,
    )
    from ..utils.timezone_pe import now_pe_iso

    an = obtener_analisis_caso(db_path, reporte_id)
    if not an:
        return {"pdf": None, "estado": "error",
                "nota": "El caso no tiene análisis generado (sub-fases 4a/4b)"}
    if an.get("estado") != "ok":
        return {"pdf": None, "estado": "error",
                "nota": an.get("nota") or "El análisis del caso no está disponible"}
    if not an.get("completo"):
        return {"pdf": None, "estado": "error",
                "nota": "El reporte no está ensamblado (falta finalizar la proyección)"}

    meta = obtener_caso_meta(db_path, reporte_id) or {}
    try:
        vocab = cargar_vocabulario_kent(db_path)
    except Exception:
        vocab = []
    pregunta = an.get("pregunta") or meta.get("pregunta") or "—"
    ventana = an.get("ventana_dias") or meta.get("ventana_dias") or "—"
    ahora = now_pe_iso()

    T.registrar_fuentes_thalos()
    st = T.estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=T.MARGEN_LAT, rightMargin=T.MARGEN_LAT,
                            topMargin=T.MARGEN_SUP, bottomMargin=T.MARGEN_INF,
                            title="Reporte Político por Caso · THALOS")
    doc._fecha_footer = ahora[:10]
    doc._header_meta = "REPORTE POLÍTICO POR CASO · THALOS"
    doc._portada = {
        "titulo": "Reporte Político por Caso",
        "subtitulo": pregunta,
        "tema_rango": f"Ventana: {ventana} días  ·  Corte: {ahora[:16].replace('T', ' ')} (Lima)",
        "metadata": [
            ("Tipo", "Análisis por caso · THALOS"),
            ("Generado", ahora[:16].replace("T", " ") + " (America/Lima)"),
            ("Material", f"{(an.get('seccion_vii') or {}).get('citadas', 0)} piezas citadas"),
            ("Clasificación", "USO INTERNO"),
        ],
    }

    S = [PageBreak()]

    # La pregunta, en cabecera del cuerpo: es el eje del reporte.
    S.append(T.recuadro_ejecutivo(
        "LA PREGUNTA DEL CASO",
        escape_txt(pregunta) +
        f"<br/><br/>Ventana de análisis: {escape_txt(str(ventana))} días.", st))
    S.append(Spacer(1, 14))

    # ── I-IV: la voz de la máquina (registro descriptivo) ──
    secciones = {s.get("nombre"): s.get("cuerpo") for s in (an.get("secciones") or [])}
    for nombre in _ORDEN_DESCRIPTIVAS:
        if nombre not in secciones:
            continue
        S += _bloque(_encabezado(nombre, st), _parrafos(secciones[nombre], st))
        S.append(Spacer(1, 8))
        # los silencios cuelgan de la sección II (es donde se ordenan escenarios)
        if nombre == "II. LA PREGUNTA Y EL MATERIAL" and an.get("silencios"):
            S.append(_recuadro_silencios(an["silencios"], st))
            S.append(Spacer(1, 10))
    # secciones fuera del orden canónico (p.ej. volcado genérico): no se pierden
    for s in (an.get("secciones") or []):
        if s.get("nombre") not in _ORDEN_DESCRIPTIVAS:
            S += _bloque(_encabezado(s.get("nombre") or "ANÁLISIS", st),
                         _parrafos(s.get("cuerpo"), st))
            S.append(Spacer(1, 8))

    # ── V: la voz del analista (bloque atribuido, marca de dos voces) ──
    S.append(PageBreak())
    S += _bloque(_encabezado("V. PROYECCIÓN", st),
                 _bloque_voz_analista(an.get("seccion_v") or {}, vocab, st))
    S.append(Spacer(1, 14))

    # ── VI: hechos citados en dos clases probatorias ──
    S += _bloque(_encabezado("VI. HECHOS CITADOS", st),
                 _contenido_hechos(an.get("seccion_vi") or {}, st))
    S.append(Spacer(1, 14))

    # ── VII: nota de material ──
    S += _bloque(_encabezado("VII. NOTA DE MATERIAL", st),
                 _contenido_nota(an.get("seccion_vii") or {}, st))

    doc.build(S, onFirstPage=T.dibujar_portada, onLaterPages=T.header_footer)
    return {"pdf": buf.getvalue(), "estado": "completado", "nota": None}
