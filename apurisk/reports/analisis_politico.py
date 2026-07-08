"""THALOS · Análisis Político — Últimas 24 Horas (Fase 3-3c).

Línea de producto SEPARADA del OSINT: pone los HECHOS del día en CONTEXTO
(prosa ejecutiva), no métricas crudas. La narrativa la redacta Claude vía API
bajo un prompt maestro que codifica la doctrina del Coronel (grounding estricto:
solo hechos provistos; describe y contextualiza, NO juzga — sin proyecciones,
hipótesis ni recomendaciones).

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
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether,
)

from . import thalos_base as T
from .reporte_a import _grid_temas, _score_global_bloque, _fmt, escape_txt

# Secciones v2 (el prompt maestro v2 las exige en este orden). Se tolera el
# encabezado viejo "DESARROLLOS PRINCIPALES" como fallback de transición.
_SECCIONES = ["SÍNTESIS DEL DÍA", "LAS ÚLTIMAS 24 HORAS EN DESARROLLO",
              "DESARROLLOS PRINCIPALES",   # fallback v1 (transición)
              "CONEXIONES Y CONTEXTO", "HECHOS CITADOS", "NOTA DE MATERIAL"]


def _material_para_llm(articulos: list, globos: list, riesgo: dict,
                       actores: list, ahora_iso: str) -> str:
    """Arma el bloque de material (hechos + métricas) que se envía como user.

    Cada artículo incluye su URL (si existe) para que el modelo la cite tal cual.
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
               "Formato: título | fuente | fecha-hora Lima | URL | resumen"]
    for a in articulos:
        fecha = (a.get("capturado_en") or "")[:16].replace("T", " ")
        fuente = a.get("source_name") or "fuente"
        titulo = (a.get("title") or "").strip()
        resumen = (a.get("summary") or "").strip()
        url = (a.get("url") or "").strip()
        partes = [titulo, fuente, fecha]
        if url:
            partes.append(url)
        if resumen:
            partes.append(resumen[:220])
        lineas.append("- " + " | ".join(partes))
    lineas += ["", "Redacta el Análisis Político siguiendo EXACTAMENTE la estructura "
               "y las reglas del system. Solo estos hechos; cita fuente y URL tal "
               "como fueron provistos."]
    return "\n".join(lineas)


def sanitizar_narrativa(texto: str) -> str:
    """Cinturón-y-tirantes: limpia la salida del modelo aunque el prompt v2 ya
    prohíba el marcado. No rompe la lista numerada de HECHOS CITADOS ([n].)."""
    import re
    if not texto:
        return texto
    t = texto
    # negritas/cursivas markdown → conservar el texto interior
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t, flags=re.S)
    t = re.sub(r"\*(.+?)\*", r"\1", t, flags=re.S)
    t = t.replace("*", "")
    # almohadillas de encabezado al inicio de línea
    t = re.sub(r"(?m)^\s*#{1,6}\s*", "", t)
    # comillas tipográficas → rectas
    t = t.replace("“", '"').replace("”", '"')
    t = t.replace("‘", "'").replace("’", "'")
    # viñetas de símbolo al inicio de línea SOLO si el resto es prosa
    # (no tocar líneas numeradas tipo "[n]." o "n." de HECHOS CITADOS)
    def _quitar_vineta(m):
        resto = m.group(1)
        if re.match(r"^\[?\d+\]?[\.\)]", resto):
            return m.group(0)  # línea numerada: intacta
        return resto
    t = re.sub(r"(?m)^\s*[•\-–]\s+(.*)$", _quitar_vineta, t)
    # colapsar 3+ saltos de línea a máximo 2
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _parsear_secciones(texto: str) -> list:
    """Divide la salida del LLM en (encabezado, cuerpo) por las secciones esperadas."""
    import re
    if not texto:
        return []
    marcas = []
    for sec in _SECCIONES:
        m = re.search(re.escape(sec), texto, re.IGNORECASE)
        if m:
            marcas.append((m.start(), sec, m.end()))
    marcas.sort()
    if not marcas:
        return [("ANÁLISIS", texto.strip())]
    out = []
    for i, (ini, sec, fin) in enumerate(marcas):
        cuerpo_ini = fin
        cuerpo_fin = marcas[i + 1][0] if i + 1 < len(marcas) else len(texto)
        cuerpo = texto[cuerpo_ini:cuerpo_fin].strip().lstrip("—-:").strip()
        out.append((sec, cuerpo))
    return out


def parsear_hechos_citados(cuerpo: str, urls_material: set) -> list:
    """Parsea la sección HECHOS CITADOS: líneas '[n]. título | fuente | URL'.

    Anti-invención: solo se conservan URLs que EXISTEN en el material enviado;
    si una URL no matchea, el hecho se conserva SIN URL. Devuelve
    [{titulo, fuente, url}] en el orden del modelo; [] si no parsea nada.
    """
    import re
    hechos = []
    for linea in (cuerpo or "").split("\n"):
        linea = linea.strip()
        m = re.match(r"^\[?(\d+)\]?[\.\)]\s*(.+)$", linea)
        if not m:
            continue
        partes = [p.strip() for p in m.group(2).split("|")]
        if not partes or not partes[0]:
            continue
        titulo = partes[0]
        fuente = partes[1] if len(partes) > 1 else "—"
        url = ""
        if len(partes) > 2:
            candidata = partes[2].strip()
            if candidata in urls_material:
                url = candidata
        hechos.append({"titulo": titulo, "fuente": fuente, "url": url})
    return hechos


def _url_corta(url: str, max_len: int = 58) -> str:
    """Versión visual truncada de la URL (dominio + ruta corta)."""
    u = url.replace("https://", "").replace("http://", "")
    return u if len(u) <= max_len else u[:max_len - 1] + "…"


def _linkificar(texto_escapado: str, urls_material: set) -> str:
    """Convierte URLs del material presentes en el texto en links clicables,
    mostrados en pequeño y gris. Solo URLs que existen en el material."""
    out = texto_escapado
    for u in urls_material:
        esc = escape_txt(u)
        if esc in out:
            out = out.replace(
                esc,
                f'<link href="{esc}"><font size="9" color="#999999">{escape_txt(_url_corta(u))}</font></link>')
    return out


def _render_narrativa(secciones: list, st: dict, urls_material: set) -> list:
    """Render de las secciones de prosa. HECHOS CITADOS NO se renderiza aquí
    (alimenta la tabla de hechos). Títulos con KeepTogether (sin huérfanos)."""
    S = []
    for enc, cuerpo in secciones:
        if enc == "HECHOS CITADOS":
            continue
        parrafos = [p.strip() for p in cuerpo.split("\n") if p.strip()]
        flow = [Paragraph(_linkificar(escape_txt(p), urls_material), st["body"])
                for p in parrafos]
        titulo = [Paragraph(escape_txt(enc.title()), st["h2"]), T.linea_oro()]
        if flow:
            # título + primer párrafo juntos: un encabezado nunca queda huérfano
            S.append(KeepTogether(titulo + flow[:1]))
            S.extend(flow[1:])
        else:
            S.append(KeepTogether(titulo))
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

    # URLs del material: la única fuente de verdad para links (anti-invención)
    urls_material = {(a.get("url") or "").strip() for a in articulos if (a.get("url") or "").strip()}

    hechos_citados = []
    if not degradado:
        # Sanitización cinturón-y-tirantes ANTES de parsear/renderizar
        narrativa = sanitizar_narrativa(narrativa)
        secciones = _parsear_secciones(narrativa)
        # Coherencia estructural: HECHOS CITADOS del modelo alimenta la tabla
        cuerpo_hc = next((c for e, c in secciones if e == "HECHOS CITADOS"), "")
        hechos_citados = parsear_hechos_citados(cuerpo_hc, urls_material)
        S += _render_narrativa(secciones, st, urls_material)
    else:
        S.append(KeepTogether([
            Paragraph("Narrativa no disponible", st["h2"]),
            T.linea_oro(),
            Paragraph(escape_txt(nota_deg), st["body"]),
            Paragraph("Se presenta la foto de métricas y los titulares del día "
                      "como respaldo.", st["body"]),
        ]))

    # Tablero de métricas (grid 8 temas + Score Nacional) — junto a la prosa
    S.append(KeepTogether(
        [Paragraph("Tablero de métricas (semáforo 24h)", st["h2"]), T.linea_oro()]
        + _score_global_bloque(riesgo, st)))
    S.append(Spacer(1, 8))
    if globos:
        S.append(_grid_temas(globos, st))
        S.append(Spacer(1, 4))
        S.append(T._leyenda_riesgo())

    S.append(PageBreak())

    # ── Hechos citados + integridad ──
    # La tabla se construye desde la sección HECHOS CITADOS del modelo (mismo
    # orden de prioridad de la narrativa). Fallback: top titulares desde BD.
    usar_citados = bool(hechos_citados)
    if not usar_citados and not degradado:
        print("[ap24] HECHOS CITADOS no parseó — fallback a titulares de BD")
    titulo_hechos = "Hechos citados" if usar_citados else "Hechos destacados"
    filas = []
    if usar_citados:
        for h in hechos_citados[:15]:
            celda_titulo = escape_txt(h["titulo"][:90])
            if h["url"]:
                celda_titulo += (f'<br/><link href="{escape_txt(h["url"])}">'
                                 f'<font size="9" color="#999999">{escape_txt(_url_corta(h["url"]))}</font></link>')
            filas.append([celda_titulo, escape_txt(h["fuente"][:22])])
        headers = ["Hecho (con enlace)", "Fuente"]
        widths = [4.6 * inch, 1.5 * inch]
    elif articulos:
        for a in articulos[:15]:
            celda_titulo = escape_txt((a.get("title") or "")[:90])
            u = (a.get("url") or "").strip()
            if u:
                celda_titulo += (f'<br/><link href="{escape_txt(u)}">'
                                 f'<font size="9" color="#999999">{escape_txt(_url_corta(u))}</font></link>')
            filas.append([celda_titulo, escape_txt((a.get("source_name") or "—")[:22]),
                          (a.get("capturado_en") or "")[:16].replace("T", " ")])
        headers = ["Titular", "Fuente", "Hora (Lima)"]
        widths = [3.4 * inch, 1.4 * inch, 1.3 * inch]
    if filas:
        # KeepTogether: si no cabe entero, reportlab lo parte igual (degrada con
        # gracia) y la tabla repite su header (repeatRows=1). Lo que evita es el
        # título huérfano al pie de página.
        S.append(KeepTogether([
            Paragraph(titulo_hechos, st["h1"]), T.linea_oro(),
            T.tabla_profesional(headers, filas, widths)]))
    else:
        S.append(KeepTogether([
            Paragraph(titulo_hechos, st["h1"]), T.linea_oro(),
            Paragraph("Sin titulares en las últimas 24h.", st["body"])]))
    S.append(Spacer(1, 10))
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
