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
               "Formato por hecho:  título | fuente | fecha (Lima) | resumen.",
               "NO se envían URLs: en HECHOS CITADOS escribe solo título | fuente; "
               "el sistema añade el enlace.", ""]
    for a in articulos:
        fecha = (a.get("capturado_en") or a.get("published") or "")[:16].replace("T", " ")
        fuente = a.get("source_name") or "fuente"
        titulo = (a.get("title") or "").strip()
        resumen = (a.get("summary") or "").strip()
        campos = [titulo or "—", fuente, fecha or "—",
                  (resumen[:220] if resumen else "—")]
        lineas.append("- " + " | ".join(campos))
    lineas += ["", "Redacta el Reporte de Riesgo Político siguiendo EXACTAMENTE la "
               "estructura y las reglas del system. Usa solo estos hechos; en cada "
               "desarrollo cita la fuente (según [medio]). No escribas URLs."]
    return "\n".join(lineas)


def _quitar_tildes(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def _norm_hdr(linea: str) -> str:
    """Normaliza una línea para detectar encabezados: sin tildes, MAYÚS, sin
    numeración/markdown al inicio, sin dos puntos/guiones al final, espacios
    colapsados."""
    s = _quitar_tildes(linea).upper().strip()
    s = re.sub(r"^[\s#>*_\-–—0-9.)(]+", "", s)     # numeración / markdown inicial
    s = re.sub(r"[\s:：.\-–—_]+$", "", s)            # puntuación final
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# Firmas tolerantes de cada sección (sobre la línea normalizada). El orden
# importa: lo más específico primero. Cubren variaciones del modelo (tildes,
# dos puntos, texto ligeramente distinto del encabezado).
_SIG_SECCIONES = [
    ("SÍNTESIS DEL DÍA",                  r"\bSINTESIS\b"),
    ("LAS ÚLTIMAS 24 HORAS EN DESARROLLO",
     r"\bULTIMAS?\s+24\s+HORAS\b|\bDESARROLLOS?\s+PRINCIPALES\b|\bDESARROLLO\b"),
    ("CONEXIONES Y CONTEXTO",             r"\bCONEXION(?:ES)?\b"),
    ("HECHOS CITADOS",                    r"\bHECHOS\s+CITADOS\b|\bHECHOS\b"),
    ("NOTA DE MATERIAL",                  r"\bNOTA\b"),
]


def _detectar_seccion(linea: str):
    """Devuelve la sección canónica si la línea ES un encabezado (tolerante), o
    None. Exige que la línea sea corta y de pocas palabras (encabezado, no una
    oración del cuerpo que mencione la palabra)."""
    norm = _norm_hdr(linea)
    if not norm or len(norm) > 55 or len(norm.split()) > 8:
        return None
    for canon, pat in _SIG_SECCIONES:
        if re.search(pat, norm):
            return canon
    return None


# Formas normalizadas EXACTAS de los encabezados (para limpiar marcadores que el
# modelo deja colgando dentro del cuerpo, p.ej. "LAS ÚLTIMAS 24 HORAS EN
# DESARROLLO" al final de SÍNTESIS). Match exacto → seguro (no borra prosa que
# apenas contenga una palabra suelta como "desarrollo").
_MARCADORES_NORM = {
    _norm_hdr(x) for x in (
        "SÍNTESIS DEL DÍA", "LAS ÚLTIMAS 24 HORAS EN DESARROLLO",
        "ÚLTIMAS 24 HORAS EN DESARROLLO", "DESARROLLOS PRINCIPALES",
        "CONEXIONES Y CONTEXTO", "HECHOS CITADOS", "NOTA DE MATERIAL",
    )
}


def _limpiar_cuerpo(cuerpo: str) -> str:
    """Quita marcadores de sección que se filtraron dentro de un cuerpo:
    - líneas que SON exactamente un encabezado (marcador huérfano);
    - un encabezado pegado al final del último párrafo (las últimas k palabras
      normalizan a un encabezado exacto). Match exacto → no borra prosa legítima.
    """
    if not cuerpo:
        return cuerpo
    lineas = [ln for ln in cuerpo.split("\n") if _norm_hdr(ln) not in _MARCADORES_NORM]
    txt = "\n".join(lineas).strip()
    palabras = txt.split()
    # greedy: probar la cola MÁS LARGA primero, para no dejar restos ("LAS")
    # cuando el marcador existe con y sin artículo inicial en _MARCADORES_NORM.
    for k in range(min(7, len(palabras) - 1), 1, -1):
        if _norm_hdr(" ".join(palabras[-k:])) in _MARCADORES_NORM:
            # conservar el punto final de la oración; quitar solo separadores
            # de marcador (— : ; y espacios), no el punto ni la coma legítimos.
            txt = " ".join(palabras[:-k]).rstrip(" \t—–:;·")
            break
    return txt.strip()


def _parsear_secciones(texto: str) -> list:
    """Divide la salida del LLM en [(sección_canónica, cuerpo)] de forma ROBUSTA.

    Detección de encabezados línea a línea, tolerante a tildes, mayúsc/minúsc,
    espacios, dos puntos finales y numeración/markdown. Anti-pérdida: cualquier
    texto antes del primer encabezado se conserva (se antepone a la 1ª sección);
    si NO se detecta ningún encabezado, se vuelca todo el texto bajo un
    encabezado genérico (nunca se descarta contenido). Cada cuerpo se limpia de
    marcadores de sección filtrados.
    """
    if not texto or not texto.strip():
        return []
    lineas = texto.split("\n")
    marcas = []  # (idx_linea, canon)
    vistos = set()
    for i, ln in enumerate(lineas):
        canon = _detectar_seccion(ln)
        if canon and canon not in vistos:
            vistos.add(canon)
            marcas.append((i, canon))
    if not marcas:
        # No se pudo segmentar: volcar TODO bajo un encabezado genérico.
        return [("ANÁLISIS COMPLETO", texto.strip())]
    out = []
    # preámbulo (texto antes del primer encabezado) → no se pierde
    preambulo = "\n".join(lineas[:marcas[0][0]]).strip()
    for j, (idx, canon) in enumerate(marcas):
        fin = marcas[j + 1][0] if j + 1 < len(marcas) else len(lineas)
        cuerpo = "\n".join(lineas[idx + 1:fin]).strip().lstrip("—-:").strip()
        if j == 0 and preambulo:
            cuerpo = (preambulo + "\n" + cuerpo).strip()
        out.append((canon, _limpiar_cuerpo(cuerpo)))
    return out


def _secciones_faltantes(secciones: list) -> list:
    """Cuáles de las 5 secciones esperadas NO se detectaron (para log)."""
    presentes = {s for s, _ in secciones}
    return [s for s in _SECCIONES if s not in presentes]


# ── HECHOS CITADOS: parseo + enlace LIMPIO (sobre el título) ──────────────────
def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/")


def _norm_titulo(s: str) -> str:
    """Normaliza un título para casarlo con la BD (sin tildes, minúsculas, sin
    puntuación, espacios colapsados)."""
    s = _quitar_tildes(s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _dominio(url: str) -> str:
    """Dominio corto (sin esquema ni www) para una etiqueta clicable legible."""
    m = re.match(r"https?://([^/]+)", url or "")
    host = (m.group(1) if m else (url or "")).lower()
    return host[4:] if host.startswith("www.") else host


def _indexar_articulos(articulos: list) -> dict:
    return {_norm_titulo(a.get("title") or ""): a
            for a in articulos if (a.get("title") or "").strip()}


def _match_articulo(titulo: str, idx: dict, articulos: list):
    """Casa el título citado por el modelo con un artículo de la BD para
    recuperar su URL (el enlace NO viene del modelo). Exacto → subcadena →
    solape de tokens ≥0.6."""
    nt = _norm_titulo(titulo)
    if not nt:
        return None
    if nt in idx:
        return idx[nt]
    for a in articulos:
        at = _norm_titulo(a.get("title") or "")
        if at and (nt in at or at in nt) and abs(len(at) - len(nt)) < 40:
            return a
    toks = set(nt.split())
    if not toks:
        return None
    mejor, mejor_sc = None, 0.0
    for a in articulos:
        at = set(_norm_titulo(a.get("title") or "").split())
        if not at:
            continue
        sc = len(toks & at) / len(toks | at)
        if sc > mejor_sc:
            mejor, mejor_sc = a, sc
    return mejor if mejor_sc >= 0.6 else None


def _parse_hechos_citados(cuerpo: str, articulos: list) -> list:
    """Parsea la lista "[n]. título | fuente" de HECHOS CITADOS y ATA la URL
    desde la BD (match de título). El enlace NUNCA sale del modelo → no hay
    URLs crudas ni inventadas. Devuelve [{n, titulo, fuente, url}] en orden.
    """
    filas = []
    if not cuerpo:
        return filas
    idx = _indexar_articulos(articulos)
    for ln in cuerpo.split("\n"):
        ln = ln.strip()
        m = re.match(r"^\[?(\d+)\]?[.)]\s*(.+)$", ln)
        if not m:
            continue
        n, resto = m.group(1), m.group(2).strip()
        # descartar cualquier URL cruda que el modelo pudiera haber colado
        resto = re.sub(r"https?://\S+", "", resto).strip()
        partes = [p.strip(" |[]") for p in re.split(r"\s*\|\s*", resto) if p.strip(" |[]")]
        titulo = partes[0] if partes else resto.strip(" |[]")
        fuente = partes[1] if len(partes) >= 2 else ""
        if not titulo:
            continue
        art = _match_articulo(titulo, idx, articulos)
        url = (art.get("url") or "").strip() if art else ""
        if not fuente and art:
            fuente = art.get("source_name") or ""
        filas.append({"n": n, "titulo": titulo, "fuente": fuente, "url": url})
    return filas


# ── Render de la lista de HECHOS CITADOS (enlace limpio sobre el título) ───────
def _tabla_hechos_citados(filas: list) -> Table:
    """Lista de hechos citados: el TÍTULO es el hipervínculo clicable (limpio,
    sin ristra de URL cruda). Columnas: N° · Hecho (enlace) · Fuente."""
    st_h = ParagraphStyle("hc_h", fontName=T.FONT_TITLE, fontSize=10,
                          textColor=T.NAVY, leading=12)
    st_n = ParagraphStyle("hc_n", fontName=T.FONT_TITLE, fontSize=10,
                          textColor=T.NAVY, leading=12, alignment=TA_CENTER)
    st_t = ParagraphStyle("hc_t", fontName=T.FONT_BODY, fontSize=10,
                          textColor=T.GRIS_CUERPO, leading=13, alignment=TA_LEFT)
    st_link = ParagraphStyle("hc_link", fontName=T.FONT_BODY, fontSize=10,
                             textColor=T.NAVY, leading=13, alignment=TA_LEFT)
    st_f = ParagraphStyle("hc_f", fontName=T.FONT_BODY, fontSize=9,
                          textColor=T.GRIS_META, leading=12, alignment=TA_LEFT)
    data = [[Paragraph("N°", st_h), Paragraph("Hecho citado", st_h),
             Paragraph("Fuente", st_h)]]
    for f in filas:
        titulo = escape_txt((f["titulo"] or "")[:160])
        url = (f.get("url") or "").strip()
        if url:
            # el enlace va SOBRE el título (texto legible en navy), no la ristra
            hecho = Paragraph(f'<link href="{escape_txt(url)}">{titulo}</link>', st_link)
        else:
            hecho = Paragraph(titulo, st_t)
        data.append([
            Paragraph(escape_txt(str(f["n"])), st_n),
            hecho,
            Paragraph(escape_txt((f["fuente"] or "—")[:28]), st_f),
        ])
    t = Table(data, colWidths=[0.42 * inch, 4.28 * inch, 1.5 * inch], repeatRows=1)
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


# ── ACTORES POLÍTICOS EN RIESGO — datos del MOTOR REAL (no IA) ─────────────────
def _rol_actor(tipo: str, territorio: str) -> str:
    """ROL = clasificación real del actor (tipo + territorio del motor). Decisión
    del Coronel: sin campo 'rol'/'sector' dedicado, se compone de lo que existe."""
    partes = [p.strip().capitalize() for p in (tipo, territorio) if p and p.strip()]
    return " · ".join(partes) if partes else "—"


def _tema_legible(tema: str) -> str:
    s = (tema or "").replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else "—"


def _nivel_riesgo_tema(y: float):
    """Nivel + color THALOS del riesgo, derivado de la gravedad del tema (semáforo):
    ALTO ámbar/naranja · MEDIO amarillo · BAJO verde · CRÍTICO rojo."""
    try:
        v = float(y)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 80:
        return "CRÍTICO", T.ROJO_CRIT
    if v >= 60:
        return "ALTO", T.AMBAR_ALTO
    if v >= 40:
        return "MEDIO", T.AMARILLO_BAJO
    return "BAJO", T.VERDE_INFO


def _actores_en_riesgo(db_path: str, globos: list, top_n: int = 6) -> list:
    """Filas de la tabla 'Actores políticos en riesgo' desde el MOTOR de actores.

    Fuente: listar_actores_por_activacion (por tema, con índice de activación) +
    listar_actores (para tipo/territorio del ROL). VINCULADO A = tema-amenaza;
    RIESGO = nivel de gravedad de ese tema (semáforo). Dedup por actor (se queda
    con su amenaza de mayor gravedad). Orden por riesgo desc. Sin datos → [].
    """
    from ..storage.config_loader import listar_actores, listar_actores_por_activacion
    grav = {g.get("tema"): g.get("y", 0) for g in (globos or []) if g.get("tema")}
    temas = sorted((t for t, y in grav.items() if (y or 0) > 0),
                   key=lambda t: grav[t], reverse=True)
    if not temas:
        return []
    # tipo/territorio por actor (listar_actores trae territorio; la otra no)
    meta = {}
    try:
        for a in listar_actores(db_path, pais="PE", solo_activos=True):
            meta[a.get("id")] = (a.get("tipo") or "", a.get("territorio") or "")
    except Exception:
        pass
    filas, vistos = [], set()
    for tema in temas:
        y = grav[tema]
        nivel, color = _nivel_riesgo_tema(y)
        for act in listar_actores_por_activacion(db_path, tema, pais="PE"):
            aid = act.get("id")
            if aid in vistos:
                continue
            vistos.add(aid)
            tipo, terr = meta.get(aid, (act.get("tipo") or "", ""))
            filas.append({
                "actor": (act.get("nombre") or "—").strip(),
                "rol": _rol_actor(tipo, terr),
                "vinculado": _tema_legible(tema),
                "nivel": nivel, "color": color,
                "_grav": y or 0, "_act": act.get("indice_activacion") or 0,
            })
    filas.sort(key=lambda f: (f["_grav"], f["_act"]), reverse=True)
    return filas[:max(1, int(top_n or 6))]


def _tabla_actores_riesgo(filas: list) -> Table:
    """Tabla ACTOR | ROL | VINCULADO A | RIESGO, con el nivel coloreado (THALOS)."""
    st_h = ParagraphStyle("ar_h", fontName=T.FONT_TITLE, fontSize=9.5,
                          textColor=T.NAVY, leading=12)
    st_c = ParagraphStyle("ar_c", fontName=T.FONT_BODY, fontSize=9.5,
                          textColor=T.GRIS_CUERPO, leading=12, alignment=TA_LEFT)
    data = [[Paragraph("ACTOR", st_h), Paragraph("ROL", st_h),
             Paragraph("VINCULADO A", st_h), Paragraph("RIESGO", st_h)]]
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), T.GRIS_CLARO),
        ("BOX", (0, 0), (-1, -1), 1, T.ORO),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, T.ORO),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, T.colors.HexColor("#EADFB0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    for i, f in enumerate(filas, start=1):
        # texto del nivel: navy sobre amarillo (claro), blanco sobre el resto
        txt = T.NAVY if f["color"] == T.AMARILLO_BAJO else T.BLANCO
        st_r = ParagraphStyle(f"ar_r{i}", fontName=T.FONT_TITLE, fontSize=9,
                              textColor=txt, alignment=TA_CENTER, leading=11)
        data.append([
            Paragraph(escape_txt(f["actor"][:40]), st_c),
            Paragraph(escape_txt(f["rol"][:34]), st_c),
            Paragraph(escape_txt(f["vinculado"][:34]), st_c),
            Paragraph(escape_txt(f["nivel"]), st_r),
        ])
        estilo.append(("BACKGROUND", (3, i), (3, i), f["color"]))
    t = Table(data, colWidths=[1.9 * inch, 1.7 * inch, 1.8 * inch, 0.9 * inch],
              repeatRows=1)
    t.setStyle(TableStyle(estilo))
    return t


def _bloque_actores_riesgo(db_path: str, globos: list, st: dict, top_n: int) -> list:
    """Bloque completo: título + subtítulo + tabla (o nota honesta si no hay datos)."""
    filas = _actores_en_riesgo(db_path, globos, top_n)
    sub = Paragraph(
        "Principales actores vinculados a las amenazas de las últimas 24 horas",
        ParagraphStyle("ar_sub", fontName=T.FONT_BODY, fontSize=10, textColor=T.GRIS_META))
    cab = Paragraph("ACTORES POLÍTICOS EN RIESGO", st["h2"])
    if not filas:
        log.info("AP24: sin actores en riesgo vinculados a las amenazas del período")
        cuerpo = [sub, Spacer(1, 6),
                  Paragraph("Sin actores en riesgo significativo en el período.", st["body"])]
    else:
        cuerpo = [sub, Spacer(1, 6), _tabla_actores_riesgo(filas)]
    return _bloque_seccion(cab, cuerpo)


def _encabezado_seccion(enc: str, st: dict) -> Paragraph:
    """Encabezado de sección. Caso especial (punto 1): la sección de desarrollos
    se rotula como título grande "RIESGO POLÍTICO" + bajada pequeña, en tipo
    oración y mismo color, en la MISMA línea. El resto: mayúscula sostenida."""
    if enc == "LAS ÚLTIMAS 24 HORAS EN DESARROLLO":
        h2 = st["h2"]
        estilo = ParagraphStyle("sec_dev", parent=h2, leading=h2.fontSize + 4)
        # h2.fontSize (título) grande; bajada más pequeña, mismo color (NAVY)
        return Paragraph(
            f'<font size="{h2.fontSize:.0f}">RIESGO POLÍTICO</font>'
            f'<font size="13">&nbsp;&nbsp;Las últimas 24 horas en desarrollo</font>',
            estilo)
    return Paragraph(escape_txt(enc.upper()), st["h2"])


def _bloque_seccion(header, contenido: list) -> list:
    """Encabezado (Paragraph) + línea de oro + contenido, con el encabezado
    pegado a su primer flowable (KeepTogether) para que nunca quede huérfano."""
    cab = [header, T.linea_oro()]
    if contenido:
        return [KeepTogether(cab + [contenido[0]])] + contenido[1:]
    return [KeepTogether(cab)]


# Alternancia: "(Fuente — URL)" | URL suelta. Se procesa sobre texto CRUDO (para
# casar la URL con urls_ok, cuyas claves llevan '&' sin escapar) escapando cada
# segmento literal e insertando los tags con href escapado.
_LINK_RE = re.compile(
    r"\(([^()]*?)[\s—,:\-]+(https?://[^\s)]+)\)"   # (Fuente — URL)
    r"|(https?://[^\s)\]<]+)")                       # URL suelta


def _linkificar_prosa(raw: str, urls_ok: dict) -> str:
    """Convierte cualquier URL cruda del texto en enlace LIMPIO y elimina la
    ristra de caracteres. Nunca deja el https://... visible como texto.

    - "(Fuente — URL)" → "(Fuente)" con la fuente clicable si la URL es nuestra.
    - URL suelta → enlace sobre el dominio corto (si es nuestra); si es ajena o
      no resoluble, se elimina.
    Devuelve markup seguro (texto escapado + tags <link>). urls_ok:
    {url_normalizada: url_original}."""
    raw = raw or ""
    if "http" not in raw:
        return escape_txt(raw)

    def _resolver(u):
        return urls_ok.get(_norm_url(u.rstrip(".,;)]")), "")

    partes, last = [], 0
    for m in _LINK_RE.finditer(raw):
        partes.append(escape_txt(raw[last:m.start()]))
        if m.group(3) is None:  # patrón (Fuente — URL)
            fuente = (m.group(1) or "").strip(" ,;:—-") or "Fuente"
            url = _resolver(m.group(2))
            if url:
                partes.append(f'(<link href="{escape_txt(url)}">'
                              f'<font color="#0F3A66">{escape_txt(fuente)}</font></link>)')
            else:
                partes.append("(" + escape_txt(fuente) + ")")
        else:                   # URL suelta
            url = _resolver(m.group(3))
            if url:
                partes.append(f'<link href="{escape_txt(url)}">'
                              f'<font color="#0F3A66">{escape_txt(_dominio(url))}</font></link>')
            # ajena/cruda → se descarta (nunca imprimir la ristra)
        last = m.end()
    partes.append(escape_txt(raw[last:]))
    s = "".join(partes)
    s = re.sub(r"\(\s*[—,:\-]?\s*\)", "", s)   # paréntesis vacíos residuales
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s


# Subtítulo de bloque: el modelo lo prefija con «» » (marcador del prompt v4).
_RE_SUBTITULO = re.compile(r"^\s*[»›▸]+\s*(.+)$")


def _estilo_subtitulo(st: dict) -> ParagraphStyle:
    """Subtítulo temático de bloque: negrita, algo mayor que el cuerpo, resalta."""
    return ParagraphStyle("dev_sub", fontName=T.FONT_TITLE, fontSize=12.5,
                          leading=15, textColor=T.NAVY, spaceBefore=6, spaceAfter=2)


def _parrafo_prosa(linea: str, urls_ok: dict, st: dict) -> Paragraph:
    """Párrafo de prosa. Si la línea es un subtítulo de bloque (prefijo »), se
    renderiza en negrita/resaltado; si no, prosa con URLs limpias."""
    m = _RE_SUBTITULO.match(linea)
    if m:
        return Paragraph(f"<b>{escape_txt(m.group(1).strip())}</b>", _estilo_subtitulo(st))
    return Paragraph(_linkificar_prosa(linea.strip(), urls_ok), st["body"])


def _render_narrativa(secciones: list, st: dict, filas_hechos: list,
                      articulos: list, urls_ok: dict) -> list:
    S = []
    for enc, cuerpo in secciones:
        if enc == "HECHOS CITADOS":
            contenido = _contenido_hechos(filas_hechos, articulos, st)
        else:
            contenido = [_parrafo_prosa(p, urls_ok, st)
                         for p in cuerpo.split("\n") if p.strip()]
        S += _bloque_seccion(_encabezado_seccion(enc, st), contenido)
        S.append(Spacer(1, 6))
    return S


def _nota_pesos_score() -> str:
    """Nota metodológica (punto 5): explica en lenguaje simple que el Score
    Global NO es el promedio de los riesgos, con los pesos REALES leídos del
    motor (config.yaml → score_engine). None si no se pueden leer."""
    from ..storage.config_loader import cargar_pesos_score_nacional
    info = cargar_pesos_score_nacional()
    pesos = info.get("pesos") or []
    if not pesos:
        return None
    detalle = "; ".join(f"{et} {p}%" for et, p in pesos)
    return ("El Score Global no es el promedio de los riesgos mostrados debajo. "
            "Cada dimensión pondera distinto según su impacto en la estabilidad "
            f"nacional: {detalle}. Por eso el valor integral puede diferir del "
            "promedio simple de los indicadores.")


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
    articulos = articulos_ultimas_24h(db_path, par.get("top_n", 150))
    actores = listar_actores(db_path, pais="PE", solo_activos=True)
    ahora = now_pe_iso()

    # URLs de la BD (para linkear prosa de forma segura: solo enlazamos URLs
    # nuestras). El enlace se arma en el código, no lo emite el modelo.
    urls_ok = {}
    for a in articulos:
        u = (a.get("url") or "").strip()
        if u:
            urls_ok[_norm_url(u)] = u

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
                            title="Reporte de Riesgo Político · Últimas 24 Horas")
    estado_txt = "CALIBRACIÓN" if calibracion else "OPERATIVO"
    doc._fecha_footer = ahora[:10] + (" · VERSIÓN DE CALIBRACIÓN" if calibracion else "")
    doc._header_meta = "REPORTE DE RIESGO POLÍTICO · THALOS"
    gen = (snapshot.get("generado") or ahora)[:16].replace("T", " ")
    from datetime import timedelta
    from ..utils.timezone_pe import now_pe
    desde = (now_pe() - timedelta(hours=24)).isoformat(timespec="minutes")[:16].replace("T", " ")
    doc._portada = {
        # Punto 4: título nuevo; sin "Últimas 24 Horas" en el rótulo de portada.
        "titulo": "Reporte de Riesgo Político",
        "subtitulo": "Los Hechos del Día en Contexto",
        "tema_rango": (f"Ventana: {desde} → {ahora[:16].replace('T',' ')} (Lima)"
                       + ("  ·  VERSIÓN DE CALIBRACIÓN" if calibracion else "")),
        "metadata": [
            ("Tipo", "Riesgo Político · 24h · global"),
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
        # ANTI-PÉRDIDA SILENCIOSA: log de secciones esperadas que no llegaron
        # (bug de 2da corrida: se perdían en silencio). El contenido que sí
        # llegó se renderiza igual; nada se descarta.
        faltan = _secciones_faltantes(secciones)
        if faltan:
            log.warning("AP24: secciones no detectadas en la salida del modelo: %s "
                        "(se renderiza lo recibido; %d secciones sí detectadas)",
                        ", ".join(faltan), len(secciones))
        cuerpo_hc = next((c for (s, c) in secciones if s == "HECHOS CITADOS"), "")
        filas_hechos = _parse_hechos_citados(cuerpo_hc, articulos)
        S += _render_narrativa(secciones, st, filas_hechos, articulos, urls_ok)
    else:
        S.append(Paragraph("Narrativa no disponible", st["h2"]))
        S.append(T.linea_oro())
        S.append(Paragraph(escape_txt(nota_deg), st["body"]))
        S.append(Paragraph("Se presenta la foto de métricas y los hechos del día "
                            "como respaldo.", st["body"]))
        S.append(Spacer(1, 6))
        S += _bloque_seccion(_encabezado_seccion("HECHOS CITADOS", st),
                             _contenido_hechos([], articulos, st))

    # ── Página de contexto (propia): "Actores políticos en riesgo" (motor real,
    # no IA) ARRIBA + velocímetro + tablero, TODO en un solo KeepTogether para que
    # la tabla de actores quede JUNTO al velocímetro en la misma página (llena el
    # espacio en blanco de la maqueta y no separa actores del velocímetro).
    # La nota bajo el gauge lleva los pesos REALES del motor (punto 5).
    S.append(PageBreak())
    contexto = []
    contexto += _bloque_actores_riesgo(db_path, globos, st, par.get("actores_top_n", 6))
    contexto.append(Spacer(1, 14))
    cab_tab = Paragraph("Tablero de métricas (semáforo 24h)", st["h2"])
    contexto += _bloque_seccion(
        cab_tab, T.bloque_score_gauge(riesgo, st, nota=_nota_pesos_score()))
    contexto.append(Spacer(1, 8))
    if globos:
        contexto.append(_grid_temas(globos, st))
        contexto.append(Spacer(1, 6))
        contexto.append(T._leyenda_riesgo())
    S.append(KeepTogether(contexto))
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
