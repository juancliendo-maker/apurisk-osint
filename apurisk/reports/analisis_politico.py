"""THALOS · Análisis Político — Últimas 24 Horas (Fase 3-3c · Calibración v2).

Línea de producto SEPARADA del OSINT: pone los HECHOS del día en CONTEXTO
(prosa ejecutiva), no métricas crudas. La narrativa la redacta Claude vía API
bajo un prompt maestro que codifica la doctrina del Coronel (grounding estricto:
solo hechos provistos; describe y contextualiza, NO juzga — sin proyecciones,
hipótesis ni recomendaciones).

Calibración v2 (feedback editorial del Coronel tras la primera corrida real):
  · A la API se le envía la URL de cada artículo (título | fuente | fecha | URL |
    resumen). Atribución obligatoria en el prompt v2.
  · La tabla de hechos del PDF se construye PARSEANDO la sección HECHOS CITADOS
    de la salida (mismo orden). Fallback: si el parseo falla, se cae al método
    anterior (titulares desde BD) con log. Anti-invención: una URL que NO exista
    en el material enviado se imprime sin URL.
  · Sanitización post-proceso (siempre): quita */**/# conservando texto,
    comillas tipográficas → rectas, colapsa 3+ saltos a 2, quita viñetas de
    símbolo sin romper la lista "[n]." de HECHOS CITADOS.
  · Maquetación: KeepTogether en títulos de sección (sin títulos huérfanos) y
    espaciado compacto.
  · Encabezado renombrado: DESARROLLOS PRINCIPALES → LAS ÚLTIMAS 24 HORAS EN
    DESARROLLO. El parser tolera el encabezado viejo como fallback.

Salvaguardas:
  0. La API key vive SOLO en env (ANTHROPIC_API_KEY). Sin key → estado 'error'
     con mensaje claro.
  1. Grounding en el prompt maestro (config-editable).
  2. Fallback: si la API falla tras reintentos, PDF de respaldo (titulares +
     métricas) con nota de degradación visible; estado 'completado'.
  3. Modo calibración: marca visible en portada y footer mientras
     AP24_MODO_CALIBRACION=1.
"""
from __future__ import annotations
import os
import re
import logging
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
from .reporte_a import _grid_temas, _score_global_bloque, _fmt, escape_txt

log = logging.getLogger("apurisk.ap24")

# Secciones canónicas de la salida (v2), en orden de render.
_SECCIONES = ["SÍNTESIS DEL DÍA", "LAS ÚLTIMAS 24 HORAS EN DESARROLLO",
              "CONEXIONES Y CONTEXTO", "HECHOS CITADOS", "NOTA DE MATERIAL"]

# Encabezado viejo (v1) tolerado por el parser y remapeado al nuevo (requisito
# de compatibilidad: si el modelo aún emite el encabezado antiguo, se muestra
# igual bajo el nombre nuevo).
_ALIAS_SECCIONES = {"DESARROLLOS PRINCIPALES": "LAS ÚLTIMAS 24 HORAS EN DESARROLLO"}


# ── Sanitización de la salida del LLM ─────────────────────────────────────────
_MAP_COMILLAS = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
})


def _sanitizar(texto: str) -> str:
    """Limpieza post-proceso (siempre): el prompt exige texto plano, pero un
    modelo puede filtrar markdown o comillas tipográficas. No cambia sustancia.

    - */**/*** y #encabezados markdown → fuera (conservando el texto).
    - comillas tipográficas → rectas.
    - viñetas de símbolo al inicio de línea → fuera, SIN romper la lista "[n]."
      de HECHOS CITADOS (que empieza con dígito o corchete, no con símbolo).
    - 3+ saltos de línea → 2.
    """
    if not texto:
        return ""
    t = texto.translate(_MAP_COMILLAS)
    t = re.sub(r"\*{1,3}", "", t)                 # *, **, ***
    t = re.sub(r"`{1,3}", "", t)                  # backticks
    t = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*", "", t)  # # encabezados markdown
    # viñetas de símbolo (•, ▪, ◦, ·, -, – al inicio); el em dash — se respeta
    t = re.sub(r"(?m)^[ \t]*[•‣▪◦·⁃\-–][ \t]+", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _material_para_llm(articulos: list, globos: list, riesgo: dict,
                       actores: list, ahora_iso: str) -> str:
    """Arma el bloque de material (hechos + métricas) que se envía como user.

    v2: cada hecho va como  título | fuente | fecha (Lima) | URL | resumen,
    para que el modelo pueda atribuir la URL exacta en HECHOS CITADOS.
    """
    lineas = [f"FECHA/HORA (Lima): {ahora_iso}",
              "VENTANA: últimas 24 horas.", "",
              "=== MÉTRICAS DEL MOTOR (para contexto, no para copiar como cifras) ===",
              f"Score Nacional: {riesgo.get('global','—')} ({riesgo.get('nivel','—')})",
              "Semáforo por tema (gravedad / actividad / urgencia / cuadrante):"]
    for g in sorted(globos, key=lambda x: x.get("y", 0), reverse=True):
        lineas.append(
            f"  - {g['tema'].replace('_',' ')}: grav {g.get('y',0):.0f} · "
            f"act {g.get('x',0):.1f} · {g.get('urgencia','—')} · {g.get('cuadrante','—')}")
    if actores:
        lineas.append("Actores de mayor peso: " +
                      ", ".join(f"{a.get('nombre','?')} (peso {a.get('peso_calculado',0):.0f})"
                                for a in actores[:5]))
    lineas += ["", f"=== HECHOS DEL DÍA — {len(articulos)} titulares de fuentes abiertas ===",
               "Formato por hecho:  título | fuente | fecha (Lima) | URL | resumen.",
               "Cita la URL EXACTAMENTE como aparece aquí; si un hecho trae "
               "(sin URL), cita solo la fuente.", ""]
    for a in articulos:
        fecha = (a.get("capturado_en") or a.get("published") or "")[:16].replace("T", " ")
        fuente = a.get("source_name") or "fuente"
        titulo = (a.get("title") or "").strip()
        resumen = (a.get("summary") or "").strip()
        url = (a.get("url") or "").strip()
        campos = [titulo or "—", fuente, fecha or "—",
                  url or "(sin URL)", (resumen[:220] if resumen else "—")]
        lineas.append("- " + " | ".join(campos))
    lineas += ["", "Redacta el Análisis Político siguiendo EXACTAMENTE la estructura "
               "y las reglas del system. Usa solo estos hechos; en cada desarrollo "
               "cita (Fuente — URL) tal como fueron provistas."]
    return "\n".join(lineas)


def _parsear_secciones(texto: str) -> list:
    """Divide la salida del LLM en (encabezado_canónico, cuerpo).

    Tolerante a mayúsculas y al encabezado viejo (remapeado al nuevo). Si un
    canónico y su alias aparecen a la vez, se conserva el primero por posición.
    """
    if not texto:
        return []
    marcas = []
    buscar = list(_SECCIONES) + list(_ALIAS_SECCIONES.keys())
    for sec in buscar:
        m = re.search(re.escape(sec), texto, re.IGNORECASE)
        if m:
            canon = _ALIAS_SECCIONES.get(sec, sec)
            marcas.append((m.start(), canon, m.end()))
    marcas.sort()
    if not marcas:
        return [("ANÁLISIS", texto.strip())]
    # dedup por sección canónica (por si aparecen canónico y alias)
    vistos, limpias = set(), []
    for ini, sec, fin in marcas:
        if sec in vistos:
            continue
        vistos.add(sec)
        limpias.append((ini, sec, fin))
    out = []
    for i, (ini, sec, fin) in enumerate(limpias):
        cuerpo_fin = limpias[i + 1][0] if i + 1 < len(limpias) else len(texto)
        cuerpo = texto[fin:cuerpo_fin].strip().lstrip("—-:").strip()
        out.append((sec, cuerpo))
    return out


# ── Parseo de HECHOS CITADOS + anti-invención de URLs ─────────────────────────
def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/")


def _parse_hechos_citados(cuerpo: str, urls_material: dict) -> list:
    """Parsea la lista "[n]. título | fuente | URL" de HECHOS CITADOS.

    urls_material: {url_normalizada: url_original} de lo REALMENTE enviado a la
    API. Anti-invención: si el modelo cita una URL que no está en ese material,
    se descarta (se imprime el hecho sin URL). Si coincide, se imprime la URL
    original del material (exacta), no la copia del modelo.

    Devuelve [{n, titulo, fuente, url}] en el orden del reporte.
    """
    filas = []
    if not cuerpo:
        return filas
    for ln in cuerpo.split("\n"):
        ln = ln.strip()
        m = re.match(r"^\[?(\d+)\]?[.)]\s*(.+)$", ln)
        if not m:
            continue
        n, resto = m.group(1), m.group(2).strip()
        # extraer URL si aparece en la línea
        url = ""
        um = re.search(r"https?://\S+", resto)
        if um:
            crudo = um.group(0).rstrip(".,;)]")
            resto = (resto[:um.start()] + resto[um.end():]).strip()
            norm = _norm_url(crudo)
            # anti-invención: solo si estaba en el material enviado
            url = urls_material.get(norm, "")
        partes = [p.strip(" |[]") for p in re.split(r"\s*\|\s*", resto) if p.strip(" |[]")]
        titulo = partes[0] if partes else resto.strip(" |[]")
        fuente = partes[1] if len(partes) >= 2 else ""
        if not titulo:
            continue
        filas.append({"n": n, "titulo": titulo, "fuente": fuente, "url": url})
    return filas


# ── Render de la tabla de HECHOS CITADOS ──────────────────────────────────────
def _celda_url(url: str, st_u) -> Paragraph:
    """URL en 9pt gris, clicable y truncada visualmente si es larga."""
    if not url:
        return Paragraph("—", st_u)
    disp = url if len(url) <= 44 else (url[:41] + "…")
    safe_href = escape_txt(url)
    safe_disp = escape_txt(disp)
    return Paragraph(
        f'<link href="{safe_href}"><font color="#999999">{safe_disp}</font></link>',
        st_u)


def _tabla_hechos_citados(filas: list) -> Table:
    """Tabla de referencias construida desde HECHOS CITADOS (mismo orden)."""
    st_h = ParagraphStyle("hc_h", fontName=T.FONT_TITLE, fontSize=10,
                          textColor=T.NAVY, leading=12)
    st_n = ParagraphStyle("hc_n", fontName=T.FONT_TITLE, fontSize=10,
                          textColor=T.NAVY, leading=12, alignment=TA_CENTER)
    st_t = ParagraphStyle("hc_t", fontName=T.FONT_BODY, fontSize=10,
                          textColor=T.GRIS_CUERPO, leading=13, alignment=TA_LEFT)
    st_u = ParagraphStyle("hc_u", fontName=T.FONT_BODY, fontSize=9,
                          textColor=T.GRIS_META, leading=11, alignment=TA_LEFT)
    data = [[Paragraph("N°", st_h), Paragraph("Hecho citado", st_h),
             Paragraph("Fuente", st_h), Paragraph("Referencia", st_h)]]
    for f in filas:
        data.append([
            Paragraph(escape_txt(str(f["n"])), st_n),
            Paragraph(escape_txt((f["titulo"] or "")[:140]), st_t),
            Paragraph(escape_txt((f["fuente"] or "—")[:26]), st_t),
            _celda_url(f["url"], st_u),
        ])
    t = Table(data, colWidths=[0.42 * inch, 3.28 * inch, 1.2 * inch, 1.4 * inch],
              repeatRows=1)
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
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _tabla_hechos_bd(articulos: list) -> Table:
    """Fallback: tabla de titulares desde BD (método anterior a v2)."""
    filas = [[(a.get("title") or "")[:90], (a.get("source_name") or "—")[:22],
              (a.get("capturado_en") or "")[:16].replace("T", " ")]
             for a in articulos[:15]]
    return T.tabla_profesional(["Titular", "Fuente", "Hora (Lima)"], filas,
                               [3.4 * inch, 1.4 * inch, 1.3 * inch])


def _contenido_hechos(filas: list, articulos: list, st: dict) -> list:
    """Contenido de la sección HECHOS CITADOS: tabla parseada, o fallback BD."""
    if filas:
        return [_tabla_hechos_citados(filas)]
    log.warning("AP24: HECHOS CITADOS sin filas parseables — tabla desde BD (fallback)")
    if not articulos:
        return [Paragraph("Sin hechos citados en la ventana de 24 horas.", st["body"])]
    return [_tabla_hechos_bd(articulos)]


def _bloque_seccion(titulo: str, contenido: list, st: dict) -> list:
    """Título + línea de oro + contenido, con el título pegado a su primer
    flowable (KeepTogether) para que nunca quede huérfano al pie de página."""
    cab = [Paragraph(escape_txt(titulo), st["h2"]), T.linea_oro()]
    if contenido:
        return [KeepTogether(cab + [contenido[0]])] + contenido[1:]
    return [KeepTogether(cab)]


def _render_narrativa(secciones: list, st: dict, filas_hechos: list,
                      articulos: list) -> list:
    S = []
    for enc, cuerpo in secciones:
        display = enc.title()
        if enc == "HECHOS CITADOS":
            contenido = _contenido_hechos(filas_hechos, articulos, st)
        else:
            contenido = [Paragraph(escape_txt(p.strip()), st["body"])
                         for p in cuerpo.split("\n") if p.strip()]
        S += _bloque_seccion(display, contenido, st)
        S.append(Spacer(1, 6))
    return S


def generar_analisis_politico_24h(db_path: str, snapshot: dict,
                                  construir_semaforo, conteos_bd: dict = None) -> dict:
    """Genera el PDF del Análisis Político 24h. Devuelve {pdf, estado, nota}.

    estado: 'completado' (con narrativa o con fallback) | 'error' (sin key / sin foto).
    """
    from ..storage.config_loader import (
        cargar_parametros_ap24, articulos_ultimas_24h, listar_actores,
    )
    from ..utils.timezone_pe import now_pe_iso
    from ..utils.llm_client import redactar_con_sistema

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return {"pdf": None, "estado": "error",
                "nota": "API key no configurada — agregar ANTHROPIC_API_KEY en Render"}

    osint = (snapshot or {}).get("osint_motor")
    if not osint:
        return {"pdf": None, "estado": "error", "nota": "Sin snapshot OSINT disponible"}

    par = cargar_parametros_ap24(db_path)
    calibracion = bool(par.get("modo_calibracion", 1))
    md = construir_semaforo(osint, db_path, dias=1)   # ventana 24h para el semáforo
    globos = md.get("globos_b", [])
    riesgo = (snapshot or {}).get("riesgo", {}) or {}
    articulos = articulos_ultimas_24h(db_path, par.get("top_n", 120))
    actores = listar_actores(db_path, pais="PE", solo_activos=True)
    ahora = now_pe_iso()

    # URLs realmente enviadas a la API (para la anti-invención en el parseo).
    urls_material = {}
    for a in articulos:
        u = (a.get("url") or "").strip()
        if u:
            urls_material[_norm_url(u)] = u

    material = _material_para_llm(articulos, globos, riesgo, actores, ahora)
    narrativa, err = redactar_con_sistema(
        par.get("prompt_maestro", ""), material,
        max_tokens=par.get("max_tokens", 3000),
        model=par.get("modelo", "claude-sonnet-4-6"), reintentos=2,
        # Timeout largo (config AP24_TIMEOUT_S, default 120s): generar ~3000
        # tokens tarda 40-90s; el default global de 30s abortaba cada intento.
        timeout_s=par.get("timeout_s", 120))

    degradado = narrativa is None
    nota_deg = None
    if degradado:
        nota_deg = f"Narrativa no disponible — fallo de API ({err}). Versión de respaldo."

    # Sanitización SIEMPRE (texto plano estricto) antes de parsear.
    narrativa_s = _sanitizar(narrativa) if narrativa else narrativa

    # ── Ensamblado del PDF ──
    st = T.estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=T.MARGEN_LAT, rightMargin=T.MARGEN_LAT,
                            topMargin=T.MARGEN_SUP, bottomMargin=T.MARGEN_INF,
                            title="Análisis Político · Últimas 24 Horas")
    estado_txt = "CALIBRACIÓN" if calibracion else "OPERATIVO"
    doc._fecha_footer = ahora[:10] + (" · VERSIÓN DE CALIBRACIÓN" if calibracion else "")
    doc._header_meta = "ANÁLISIS POLÍTICO · 24h · THALOS"
    gen = (snapshot.get("generado") or ahora)[:16].replace("T", " ")
    from datetime import timedelta
    from ..utils.timezone_pe import now_pe
    desde = (now_pe() - timedelta(hours=24)).isoformat(timespec="minutes")[:16].replace("T", " ")
    doc._portada = {
        "titulo": "Análisis Político — Últimas 24 Horas",
        "subtitulo": "Los hechos del día en contexto",
        "tema_rango": (f"Ventana: {desde} → {ahora[:16].replace('T',' ')} (Lima)"
                       + ("  ·  VERSIÓN DE CALIBRACIÓN" if calibracion else "")),
        "metadata": [
            ("Tipo", "Análisis Político · 24h · global"),
            ("Generado", ahora[:16].replace("T", " ") + " (America/Lima)"),
            ("Estado", estado_txt + (" · RESPALDO" if degradado else "")),
            ("Clasificación", "USO INTERNO"),
        ],
    }
    S = [PageBreak()]

    if calibracion:
        S.append(T.recuadro_ejecutivo(
            "VERSIÓN DE CALIBRACIÓN",
            "Este análisis está en fase de calibración de la redacción automática, "
            "sujeta a revisión analítica. No sustituye el juicio del analista.", st))
        S.append(Spacer(1, 10))

    if not degradado:
        secciones = _parsear_secciones(narrativa_s)
        cuerpo_hc = next((c for (s, c) in secciones if s == "HECHOS CITADOS"), "")
        filas_hechos = _parse_hechos_citados(cuerpo_hc, urls_material)
        S += _render_narrativa(secciones, st, filas_hechos, articulos)
    else:
        S.append(Paragraph("Narrativa no disponible", st["h2"]))
        S.append(T.linea_oro())
        S.append(Paragraph(escape_txt(nota_deg), st["body"]))
        S.append(Paragraph("Se presenta la foto de métricas y los hechos del día "
                            "como respaldo.", st["body"]))
        S.append(Spacer(1, 6))
        S += _bloque_seccion("Hechos Citados", _contenido_hechos([], articulos, st), st)

    # Tablero de métricas (grid 8 temas + Score Nacional) — foto de contexto.
    # Fluye tras la narrativa (sin PageBreak forzado, para no dejar huecos); el
    # título se mantiene con su primer bloque para no quedar huérfano.
    S.append(Spacer(1, 14))
    S += _bloque_seccion("Tablero de métricas (semáforo 24h)",
                         _score_global_bloque(riesgo, st), st)
    S.append(Spacer(1, 8))
    if globos:
        S.append(_grid_temas(globos, st))
        S.append(Spacer(1, 6))
        S.append(T._leyenda_riesgo())
    S.append(Spacer(1, 12))

    # ── Integridad de datos ──
    cb = conteos_bd or {}
    partes = []
    if cb.get("total") is not None:
        partes.append(f"{cb['total']:,} artículos en BD")
    partes.append(f"{len(articulos)} titulares en la ventana 24h")
    deg_line = (f"<br/><b>Degradación:</b> {escape_txt(nota_deg)}" if degradado else "")
    S.append(T.recuadro_ejecutivo(
        "INTEGRIDAD DE DATOS",
        " · ".join(partes) + ".<br/>"
        f"Última actualización de datos (snapshot): {gen} (America/Lima).<br/><br/>"
        "Narrativa generada automáticamente a partir de fuentes abiertas (OSINT) y "
        "métricas del sistema, mediante modelo de lenguaje bajo doctrina THALOS. "
        f"Estado: {estado_txt}." + deg_line, st))

    doc.build(S, onFirstPage=T.dibujar_portada, onLaterPages=T.header_footer)
    return {"pdf": buf.getvalue(), "estado": "completado",
            "nota": nota_deg if degradado else (
                "calibración" if calibracion else None)}
