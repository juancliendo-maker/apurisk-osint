"""APURISK Executive Synthesis Engine — versión defensiva.

Tolera estructuras de datos parciales o malformadas en el snapshot
(campos que vienen como string en lugar de dict, claves faltantes,
listas vacías). Cada bloque del brief está aislado en try/except
para que un fallo en uno no rompa los demás.



Destila la salida cruda del pipeline OSINT + Intelligence Engine en un
brief ejecutivo C-level de 7 bloques:

  1. STATUS NACIONAL — 5 métricas ejecutivas (operacional, minero,
     corredor sur, criminal, tendencia país) con nivel + delta semanal.
  2. AMENAZAS PRIORITARIAS — Top 3-5 con narrativa estratégica de 2-3
     líneas (LLM Claude con fallback determinístico).
  3. CRITICAL ALERTS — Solo alertas accionables operacionales.
  4. HOTSPOTS — Zonas calientes clasificadas por tipo de riesgo.
  5. IMPLICANCIAS OPERACIONALES — Logística / ESG / Regulatorio /
     Laboral / Continuidad (mapeo regla + narrativa LLM).
  6. OUTLOOK 30 DÍAS — 3 escenarios cualitativos (base/deterioro/crisis)
     derivados de tendencias del Intelligence Engine.
  7. EXECUTIVE INSIGHT — Un único párrafo analítico semanal (LLM).

Filosofía: el motor decide QUÉ decir (selección + estructura), el LLM
decide CÓMO redactarlo. Si el LLM falla, plantillas determinísticas
mantienen el sistema operativo.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..utils.llm_client import (redactar_narrativa, redactar_insight,
                                  llm_disponible, estado_uso)
from ..utils.timezone_pe import now_pe, now_pe_iso

log = logging.getLogger("apurisk.executive")


# =====================================================================
# HELPERS DEFENSIVOS — toleran datos malformados sin romperse
# =====================================================================

def _safe_dict(x, default=None) -> dict:
    """Devuelve x si es dict, si no devuelve default (o {})."""
    if default is None:
        default = {}
    return x if isinstance(x, dict) else default


def _safe_list(x, default=None) -> list:
    """Devuelve x si es lista, si no devuelve default (o [])."""
    if default is None:
        default = []
    return x if isinstance(x, list) else default


def _safe_get(obj, key, default=None):
    """Como obj.get(key, default) pero tolera obj que no sea dict."""
    if not isinstance(obj, dict):
        return default
    return obj.get(key, default)


def _safe_num(x, default=0):
    """Devuelve x si es número, si no devuelve default."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return x
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# =====================================================================
# CONFIGURACIÓN
# =====================================================================

# Mapeo: factor_id → categorías de implicancia operacional que afecta
IMPLICANCIAS_POR_FACTOR = {
    "conflictos_extractivos": ["logistica", "fuerza_laboral", "continuidad", "esg"],
    "paros_regionales":       ["logistica", "fuerza_laboral", "continuidad"],
    "vacancia_presidencial":  ["regulatorio", "reputacional"],
    "censura_gabinete":       ["regulatorio"],
    "renuncia_ministro":      ["regulatorio"],
    "reforma_electoral":      ["regulatorio"],
    "regulacion_sectorial":   ["regulatorio", "esg"],
    "investigacion_corrupcion": ["reputacional", "regulatorio"],
    "deterioro_seguridad":    ["logistica", "fuerza_laboral"],
    "presion_economica":      ["regulatorio", "continuidad"],
    "corrupcion_sistemica":   ["reputacional", "regulatorio"],
    "intervencion_ffaa":      ["continuidad", "logistica"],
    "tensiones_fronterizas":  ["continuidad", "logistica"],
    "crisis_migratoria":      ["fuerza_laboral", "esg"],
    "tensiones_diplomaticas": ["regulatorio", "reputacional"],
    "violencia_electoral":    ["continuidad", "fuerza_laboral", "logistica"],
}

# Factores que aportan al Riesgo Minero específicamente
FACTORES_MINEROS = {
    "conflictos_extractivos", "paros_regionales", "regulacion_sectorial",
    "deterioro_seguridad", "investigacion_corrupcion",
}

# Factores que aportan al Riesgo Criminal
FACTORES_CRIMINALES = {
    "deterioro_seguridad", "corrupcion_sistemica",
}

# Mapeo nivel score → etiqueta ejecutiva
def _etiqueta_nivel(score: float) -> tuple[str, str]:
    """Devuelve (etiqueta, color_token)."""
    if score >= 75:
        return "CRÍTICO", "rojo"
    if score >= 60:
        return "ELEVADO", "naranja"
    if score >= 45:
        return "MODERADO", "ambar"
    if score >= 30:
        return "BAJO", "verde-amarillo"
    return "ESTABLE", "verde"


# =====================================================================
# BLOQUE 1: STATUS NACIONAL
# =====================================================================

def _status_nacional(snapshot: dict, intelligence_brief: dict) -> dict:
    """5 métricas ejecutivas con delta semanal."""
    matriz = [f for f in _safe_list(_safe_get(snapshot, "matriz_riesgo", []))
              if isinstance(f, dict)]

    # El snapshot real (main.py) usa riesgo["global"]. Hay variantes alternativas
    # en otros módulos (score_global, etc.) — tolerar todas.
    riesgo_obj = _safe_get(snapshot, "riesgo", {})
    if isinstance(riesgo_obj, dict):
        # Probar las 3 claves comunes en orden
        score_global = _safe_num(
            riesgo_obj.get("global",
                riesgo_obj.get("score_global",
                    riesgo_obj.get("score", 0))),
            default=0
        )
    else:
        score_global = _safe_num(_safe_get(snapshot, "score_global", 0))

    # Riesgo Minero: media de scores de factores mineros
    scores_mineros = [_safe_num(f.get("score", 0)) for f in matriz
                       if _safe_get(f, "id") in FACTORES_MINEROS]
    riesgo_minero = round(sum(scores_mineros) / len(scores_mineros), 1) if scores_mineros else 0

    # Riesgo Corredor Sur: factor extractivo específicamente
    f_corredor = next((f for f in matriz
                        if _safe_get(f, "id") == "conflictos_extractivos"), {})
    f_corredor = _safe_dict(f_corredor)
    riesgo_corredor = _safe_num(f_corredor.get("score", 0))

    # Riesgo Criminal: media de scores criminales
    scores_criminales = [_safe_num(f.get("score", 0)) for f in matriz
                          if _safe_get(f, "id") in FACTORES_CRIMINALES]
    riesgo_criminal = round(sum(scores_criminales) / len(scores_criminales), 1) if scores_criminales else 0

    # Delta semanal: comparar con baseline si está disponible
    benchmark = _safe_dict(_safe_get(intelligence_brief, "comparative_benchmark"))
    # El benchmark expone delta_vs_4w (no delta_vs_4w_media). Compat con ambos.
    delta_score_global = _safe_num(
        benchmark.get("delta_vs_4w",
            benchmark.get("delta_vs_4w_media", 0)),
        default=0
    )

    # Tendencia país derivada del delta
    if delta_score_global >= 5:
        tendencia_pais = "DETERIORO"
        tendencia_arrow = "↑"
    elif delta_score_global <= -5:
        tendencia_pais = "MEJORA"
        tendencia_arrow = "↓"
    else:
        tendencia_pais = "ESTABLE"
        tendencia_arrow = "→"

    def _construir(nombre, score, sublabel=""):
        etiqueta, color = _etiqueta_nivel(score)
        return {
            "nombre": nombre,
            "score": round(_safe_num(score), 1),
            "etiqueta": etiqueta,
            "color": color,
            "sublabel": sublabel,
        }

    return {
        "operacional_nacional": _construir("Riesgo Operacional Nacional", score_global,
                                            f"Delta 4 sem: {delta_score_global:+.1f}"),
        "minero": _construir("Riesgo Sector Minero", riesgo_minero,
                              f"{len(scores_mineros)} factores agregados"),
        "corredor_sur": _construir("Riesgo Corredor Sur", riesgo_corredor,
                                    str(_safe_get(f_corredor, "tendencia", "→")) + " tendencia"),
        "criminal": _construir("Riesgo Criminal / Seguridad", riesgo_criminal,
                                f"{len(scores_criminales)} indicadores"),
        "tendencia_pais": {
            "nombre": "Tendencia País (4 semanas)",
            "etiqueta": tendencia_pais,
            "delta": round(delta_score_global, 1),
            "arrow": tendencia_arrow,
            "color": {"DETERIORO": "naranja", "MEJORA": "verde",
                      "ESTABLE": "ambar"}[tendencia_pais],
        },
    }


# =====================================================================
# BLOQUE 2: AMENAZAS PRIORITARIAS
# =====================================================================

def _priorizar_amenazas(snapshot: dict, intelligence_brief: dict,
                         top_n: int = 5) -> list[dict]:
    """Top N amenazas filtradas por relevancia operacional + narrativa LLM."""
    matriz = [f for f in _safe_list(_safe_get(snapshot, "matriz_riesgo", []))
              if isinstance(f, dict)]
    if not matriz:
        return []

    # Sort por score descendente
    matriz_ord = sorted(matriz, key=lambda x: -_safe_num(_safe_get(x, "score", 0)))

    # Filtrar: priorizar los que tienen implicancias operacionales mapeadas
    candidatos = []
    for f in matriz_ord:
        fid = _safe_get(f, "id")
        implicancias = IMPLICANCIAS_POR_FACTOR.get(fid, [])
        relevancia_op = sum(1 for x in implicancias
                             if x in {"logistica", "continuidad", "fuerza_laboral"})
        candidatos.append((f, relevancia_op))
    candidatos.sort(key=lambda x: (-x[1], -_safe_num(_safe_get(x[0], "score", 0))))

    top = [c[0] for c in candidatos[:top_n]]

    # Anclar convergencias relevantes del Intelligence Engine
    convergencias = _safe_list(_safe_get(intelligence_brief, "convergencias", []))
    conv_ids = set()
    for c in convergencias:
        c_dict = _safe_dict(c)
        for f in _safe_list(c_dict.get("factores", [])):
            f_dict = _safe_dict(f)
            fid = f_dict.get("id")
            if fid:
                conv_ids.add(fid)

    out = []
    for f in top:
        fid = _safe_get(f, "id")
        narrativa = _narrativa_amenaza(f, snapshot, en_convergencia=(fid in conv_ids))
        out.append({
            "id": fid,
            "nombre": _safe_get(f, "nombre"),
            "categoria": _safe_get(f, "categoria"),
            "score": _safe_get(f, "score"),
            "nivel": _safe_get(f, "nivel"),
            "probabilidad": _safe_get(f, "probabilidad"),
            "impacto": _safe_get(f, "impacto"),
            "tendencia": _safe_get(f, "tendencia"),
            "narrativa": narrativa,
            "en_convergencia": fid in conv_ids,
            "implicancias_categorias": IMPLICANCIAS_POR_FACTOR.get(fid, []),
        })
    return out


def _narrativa_amenaza(factor: dict, snapshot: dict, en_convergencia: bool) -> str:
    """Genera narrativa de 2-3 líneas. LLM si disponible, fallback si no."""
    factor = _safe_dict(factor)
    fid = str(_safe_get(factor, "id", ""))
    nombre = str(_safe_get(factor, "nombre", ""))
    score = _safe_num(_safe_get(factor, "score", 0))
    nivel = str(_safe_get(factor, "nivel", ""))
    tendencia = str(_safe_get(factor, "tendencia", "→"))
    evidencias = [e for e in _safe_list(_safe_get(factor, "evidencias", []))
                   if isinstance(e, dict)][:3]

    # Construir contexto para el LLM
    ev_titulos = "\n".join(
        f"  - {str(e.get('title', ''))[:120]} ({str(e.get('source', '?'))})"
        for e in evidencias
    )
    contexto = (
        f"Factor: {nombre}\n"
        f"Categoría: {factor.get('categoria', '')}\n"
        f"Score: {score}/100 (nivel {nivel})\n"
        f"Probabilidad: {factor.get('probabilidad', '?')}, Impacto: {factor.get('impacto', '?')}\n"
        f"Tendencia: {tendencia}\n"
        f"En convergencia con otros factores: {'sí' if en_convergencia else 'no'}\n"
        f"Evidencia reciente:\n{ev_titulos if ev_titulos else '  (sin evidencias)'}"
    )
    prompt = (
        "Eres un analista de riesgo político corporativo. Redacta una narrativa "
        "ejecutiva de 2 a 3 líneas (máximo 60 palabras) sobre esta amenaza para "
        "un C-level minero. Debe:\n"
        " - Empezar con un verbo en presente o gerundio (no 'El riesgo de...').\n"
        " - Indicar qué está pasando y por qué importa AHORA.\n"
        " - Evitar palabras vacías como 'puede afectar' o 'es importante'.\n"
        " - Cero bullets, cero hashtags, cero preámbulos."
    )

    texto = redactar_narrativa(prompt, contexto, max_tokens=180)
    if texto:
        return texto

    # Fallback determinístico
    arrow = {"↑": "al alza", "↓": "a la baja", "→": "estable"}.get(tendencia, "estable")
    conv = " Convergente con otros factores correlacionados." if en_convergencia else ""
    if evidencias:
        ev0_title = str(evidencias[0].get('title', ''))[:100]
        return (f"{nombre} en nivel {nivel} (score {score}) con tendencia {arrow}.{conv} "
                f"Última evidencia: {ev0_title}.")
    return (f"{nombre} en nivel {nivel} (score {score}) con tendencia {arrow}.{conv} "
            f"Sin evidencia reciente — riesgo estructural latente del factor.")


# =====================================================================
# BLOQUE 3: CRITICAL ALERTS (filtro ejecutivo)
# =====================================================================

# Categorías de alerta que SÍ son accionables para C-level
ALERT_CATEGORIAS_OPERACIONALES = {
    "Conflictos sociales", "Económico", "Seguridad",
    "Estabilidad gubernamental", "Riesgo regulatorio",
    "Corrupción", "Estabilidad gubernamental / Seguridad",
}


def _filtrar_alerts_ejecutivas(snapshot: dict, max_n: int = 8) -> list[dict]:
    """Solo alertas CRÍTICAS o ALTAS operacionales, max N. Deduplica por URL/título."""
    alertas = [a for a in _safe_list(_safe_get(snapshot, "alertas", []))
               if isinstance(a, dict)]
    if not alertas:
        return []

    filtradas = []
    seen_keys = set()  # para deduplicar
    for a in alertas:
        nivel = str(_safe_get(a, "nivel", ""))
        cat = str(_safe_get(a, "categoria", ""))
        if nivel not in ("CRÍTICA", "ALTA"):
            continue
        if cat not in ALERT_CATEGORIAS_OPERACIONALES:
            continue
        titulo = str(_safe_get(a, "titulo", ""))
        url = str(_safe_get(a, "url", "")).strip()

        # Clave de dedupe: URL si existe, sino primeros 80 chars del título
        dedup_key = url.lower() if url else titulo[:80].lower().strip()
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        filtradas.append({
            "nivel": nivel,
            "titulo": titulo,
            "categoria": cat,
            "regla": str(_safe_get(a, "regla", "")),
            "fuente": str(_safe_get(a, "fuente", "")),
            "url": url,
            "hours_ago": _safe_get(a, "hours_ago"),
            "que_paso": titulo[:120],
            "por_que_importa": _por_que_importa(a),
        })

    # Sort: CRÍTICAS primero, después por antigüedad
    filtradas.sort(key=lambda x: (0 if x["nivel"] == "CRÍTICA" else 1,
                                    _safe_num(x.get("hours_ago"), default=999)))
    return filtradas[:max_n]


def _por_que_importa(alerta: dict) -> str:
    """Plantilla determinística por regla. Corto y operacional."""
    regla = alerta.get("regla", "")
    mapping = {
        "BLOQUEO_CORREDOR_MINERO": "Afecta tránsito de mineral y suministros a operación.",
        "BLOQUEO_VIA_NACIONAL": "Disrupción logística regional; convoyes en riesgo.",
        "PARO_REGIONAL": "Posible escalamiento a corte de servicios y bloqueos.",
        "VACANCIA_PRESIDENCIAL": "Transición política con riesgo regulatorio inmediato.",
        "CENSURA_GABINETE": "Recambio ministerial puede frenar trámites sectoriales.",
        "SICARIATO_HOMICIDIO_ORGANIZADO": "Deterioro de seguridad regional con impacto en personal.",
        "NARCOTRAFICO_OPERATIVO": "Presencia de economía ilícita en zona de operación.",
        "MINERIA_ILEGAL": "Competencia ilícita y presión socioambiental sobre operaciones formales.",
        "PROCESO_ELECTORAL": "Año electoral activa volatilidad regulatoria y discursiva.",
        "TOMA_UNIVERSITARIA": "Movilización estudiantil con potencial de escalar a otros sectores.",
        "ASESINATOS_VIOLENCIA_CRITICA": "Riesgo de spillover a personal y operaciones.",
    }
    return mapping.get(regla, "Evento de criticidad operacional. Requiere monitoreo activo.")


# =====================================================================
# BLOQUE 4: HOTSPOTS CLASIFICADOS
# =====================================================================

# Mapeo: regla de alerta → tipo de hotspot (para inyección al mapa)
REGLA_A_TIPO_HOTSPOT = {
    "BLOQUEO_VIA_NACIONAL":          "corredor_logistico",
    "BLOQUEO_CORREDOR_MINERO":       "corredor_logistico",
    "PARO_REGIONAL":                 "corredor_logistico",
    "BLOQUEO_FLUVIAL":               "corredor_logistico",
    "TOMA_UNIVERSITARIA":            "conflicto_social",
    "TOMA_UNIVERSIDAD":              "conflicto_social",
    "PROTESTAS_VIOLENTAS":           "conflicto_social",
    "CONFLICTO_COMUNITARIO":         "conflicto_social",
    "SICARIATO_HOMICIDIO_ORGANIZADO": "violencia",
    "ASESINATOS_VIOLENCIA_CRITICA":  "violencia",
    "ATAQUE_VIOLENCIA":              "violencia",
    "NARCOTRAFICO_OPERATIVO":        "violencia",
    "MINERIA_ILEGAL":                "mineria_ilegal",
    "TENSIONES_FRONTERIZAS":         "frontera",
    "CRISIS_MIGRATORIA":             "frontera",
}


def _alertas_como_hotspots(snapshot: dict) -> list[dict]:
    """Convierte alertas CRÍTICAS/ALTAS de tipos operacionales a eventos
    georreferenciados para el mapa. Usa peru_geo.buscar_coords sobre el título
    para inferir coordenadas reales cuando la alerta no las trae.

    Esto es lo que permite que un paro agrario nacional cubierto por la
    prensa aparezca como múltiples hotspots en el mapa, sin depender de
    ACLED ni Defensoría.
    """
    out = []
    try:
        try:
            from ..data.peru_geo import buscar_coords
        except ImportError:
            from apurisk.data.peru_geo import buscar_coords
    except Exception:
        def buscar_coords(_t):
            return None

    alertas = _safe_list(_safe_get(snapshot, "alertas", []))
    for a in alertas:
        if not isinstance(a, dict):
            continue
        nivel = str(_safe_get(a, "nivel", ""))
        if nivel not in ("CRÍTICA", "ALTA"):
            continue
        regla = str(_safe_get(a, "regla", ""))
        tipo = REGLA_A_TIPO_HOTSPOT.get(regla)
        if not tipo:
            continue

        titulo = str(_safe_get(a, "titulo", ""))
        url = str(_safe_get(a, "url", ""))
        region = str(_safe_get(a, "region", "")) or ""
        fuente = str(_safe_get(a, "fuente", ""))

        # Geocodificar — primero por región si la trae, después por título
        coords = None
        try:
            if region:
                coords = buscar_coords(region)
            if not coords:
                coords = buscar_coords(titulo)
        except Exception:
            coords = None

        lat = coords[0] if coords and len(coords) >= 2 else None
        lon = coords[1] if coords and len(coords) >= 2 else None

        out.append({
            "titulo": titulo,
            "descripcion": titulo,
            "region": region,
            "lat": lat,
            "lon": lon,
            "fuente": f"{fuente} · alerta {regla}",
            "url": url,
            "origen": "alerta",
            "_tipo_hotspot_hint": tipo,  # hint para clasificación posterior
        })
    return out


def _clasificar_hotspots(snapshot: dict) -> list[dict]:
    """Agrupa eventos por tipo de riesgo. Lee de varias fuentes:
      1. acled_events (ACLED API si activa)
      2. conflictos (Defensoría)
      3. crimen_items
      4. alertas CRÍTICAS/ALTAS de tipos operacionales (NUEVO — Tarea A)

    La fuente 4 es crítica: garantiza que cualquier paro/bloqueo cubierto
    por la prensa nacional aparezca en el mapa, aunque ACLED y Defensoría
    no lo hayan reportado todavía.
    """
    # Unificar todas las fuentes en una sola lista normalizada
    eventos = []

    # ACLED events (estructura: event_type, location, country, fatalities, notes, latitude, longitude)
    for ev in _safe_list(_safe_get(snapshot, "acled_events", [])):
        if not isinstance(ev, dict):
            continue
        eventos.append({
            "titulo": str(_safe_get(ev, "event_type", "")) + " - " + str(_safe_get(ev, "notes", ""))[:80],
            "descripcion": str(_safe_get(ev, "notes", "")),
            "region": str(_safe_get(ev, "location", "")) or str(_safe_get(ev, "admin1", "")),
            "lat": _safe_get(ev, "latitude"),
            "lon": _safe_get(ev, "longitude"),
            "fuente": "ACLED",
            "origen": "acled",
        })

    # Conflictos (estructura: title, summary, region, severidad, source_name)
    for ev in _safe_list(_safe_get(snapshot, "conflictos", [])):
        if not isinstance(ev, dict):
            continue
        # conflictos puede tener "raw" anidado
        raw = _safe_dict(_safe_get(ev, "raw", {}))
        eventos.append({
            "titulo": str(_safe_get(ev, "title", "")),
            "descripcion": str(_safe_get(ev, "summary", "")),
            "region": str(_safe_get(ev, "region", "")) or str(raw.get("region", "")),
            "lat": _safe_get(ev, "lat") or _safe_get(raw, "lat"),
            "lon": _safe_get(ev, "lon") or _safe_get(raw, "lon"),
            "fuente": str(_safe_get(ev, "source_name", "")),
            "origen": "defensoria",
        })

    # Crimen items
    for ev in _safe_list(_safe_get(snapshot, "crimen_items", [])):
        if not isinstance(ev, dict):
            continue
        eventos.append({
            "titulo": str(_safe_get(ev, "title", "")),
            "descripcion": str(_safe_get(ev, "summary", "")),
            "region": str(_safe_get(ev, "region", "")),
            "lat": _safe_get(ev, "lat"),
            "lon": _safe_get(ev, "lon"),
            "fuente": str(_safe_get(ev, "source_name", "")),
            "origen": "crimen",
        })

    # FUENTE #4 (NUEVO): alertas CRÍTICAS/ALTAS convertidas a hotspots.
    # Esto cubre el caso donde la prensa reporta un paro/bloqueo masivo
    # pero ACLED y Defensoría aún no lo han mapeado.
    eventos.extend(_alertas_como_hotspots(snapshot))

    # Si no hay nada de las fuentes anteriores, último fallback: revisar campos legacy
    if not eventos:
        eventos = _safe_list(_safe_get(snapshot, "eventos_geo", []))
        mapa = _safe_get(snapshot, "mapa")
        if isinstance(mapa, dict):
            eventos += _safe_list(mapa.get("eventos", []))
        eventos = [e for e in eventos if isinstance(e, dict)]

    tipos = {
        "corredor_logistico": {"label": "Corredores logísticos", "color": "naranja",
                                "keywords": ["corredor", "carretera", "panamericana",
                                              "vía", "bloqueo de ruta"]},
        "mineria_ilegal":     {"label": "Minería ilegal", "color": "ambar",
                                "keywords": ["minería ilegal", "mineria ilegal",
                                              "draga", "informal"]},
        "conflicto_social":   {"label": "Conflicto social activo", "color": "rojo",
                                "keywords": ["paro", "huelga", "marcha",
                                              "protesta", "comunidad"]},
        "violencia":          {"label": "Violencia / criminalidad", "color": "rojo",
                                "keywords": ["sicariato", "asesinato", "homicidio",
                                              "narcotráfico", "extorsión"]},
        "frontera":           {"label": "Frontera / migración", "color": "ambar",
                                "keywords": ["frontera", "migración", "tumbes",
                                              "tacna", "chile"]},
    }

    # Helper para evitar "None" como string literal
    def _str_or_empty(v):
        if v is None:
            return ""
        s = str(v).strip()
        return "" if s.lower() in ("none", "null", "n/a") else s

    # Dedup por URL o título normalizado: el mismo evento puede venir de
    # varias fuentes (alerta + conflicto + nota) y no queremos puntos
    # duplicados sobre el mapa.
    seen_keys = set()

    out = []
    for tipo_id, cfg in tipos.items():
        matches = []
        for ev in eventos:
            # Si el evento ya tiene un hint del tipo (viene de _alertas_como_hotspots),
            # respetarlo. Si no, clasificar por keywords.
            hint = ev.get("_tipo_hotspot_hint")
            if hint:
                if hint != tipo_id:
                    continue
            else:
                titulo_ev = _str_or_empty(_safe_get(ev, "titulo", ""))
                desc_ev = _str_or_empty(_safe_get(ev, "descripcion", ""))
                text = (titulo_ev + " " + desc_ev).lower()
                if not any(kw in text for kw in cfg["keywords"]):
                    continue

            titulo_ev = _str_or_empty(_safe_get(ev, "titulo", ""))
            desc_ev = _str_or_empty(_safe_get(ev, "descripcion", ""))
            region_str = _str_or_empty(_safe_get(ev, "region", ""))
            lugar_str = region_str or _str_or_empty(_safe_get(ev, "lugar", ""))
            url_ev = _str_or_empty(_safe_get(ev, "url", ""))

            # Dedup key: URL si existe, sino primeros 80 chars del título
            dedup_key = url_ev.lower() if url_ev else titulo_ev[:80].lower().strip()
            if dedup_key and dedup_key in seen_keys:
                continue
            if dedup_key:
                seen_keys.add(dedup_key)

            lat = _safe_get(ev, "lat")
            lon = _safe_get(ev, "lon")
            # Geocodificar si no trae coords
            if (lat is None or lon is None):
                try:
                    try:
                        from ..data.peru_geo import buscar_coords
                    except ImportError:
                        from apurisk.data.peru_geo import buscar_coords
                    coords = (buscar_coords(lugar_str) if lugar_str else None
                               ) or buscar_coords(titulo_ev)
                    if coords and len(coords) >= 2:
                        lat, lon = coords[0], coords[1]
                except Exception:
                    pass
            matches.append({
                "titulo": titulo_ev[:120],
                "lugar": lugar_str or "(sin región)",
                "lat": lat,
                "lon": lon,
                "fuente": _str_or_empty(_safe_get(ev, "fuente", "")),
                "origen": _str_or_empty(_safe_get(ev, "origen", "")),
                "url": url_ev,
            })
        if matches:
            out.append({
                "tipo": tipo_id,
                "label": cfg["label"],
                "color": cfg["color"],
                "n_eventos": len(matches),
                "eventos": matches[:25],  # max 25 por hotspot (paros masivos)
            })

    return out


# =====================================================================
# BLOQUE 5: IMPLICANCIAS OPERACIONALES
# =====================================================================

IMPLICANCIA_LABELS = {
    "logistica":     "Logística y convoyes",
    "esg":           "ESG y licencia social",
    "regulatorio":   "Regulatorio y permisos",
    "reputacional":  "Reputacional",
    "fuerza_laboral": "Fuerza laboral",
    "continuidad":   "Continuidad operacional",
}


def _derivar_implicancias(amenazas: list[dict], snapshot: dict) -> dict:
    """Para cada categoría de implicancia, lista las amenazas que la activan
    y una narrativa síntesis (LLM con fallback)."""
    activacion = {k: [] for k in IMPLICANCIA_LABELS}
    for a in amenazas:
        for cat in a.get("implicancias_categorias", []):
            if cat in activacion:
                activacion[cat].append(a)

    out = {}
    for cat, amenazas_activan in activacion.items():
        if not amenazas_activan:
            out[cat] = {
                "label": IMPLICANCIA_LABELS[cat],
                "n_amenazas": 0,
                "estado": "ESTABLE",
                "narrativa": "Sin amenazas activas en esta categoría esta semana.",
                "amenazas_relacionadas": [],
            }
            continue

        # Severidad ponderada
        score_max = max(a.get("score", 0) for a in amenazas_activan)
        if score_max >= 70:
            estado = "ALERTA"
        elif score_max >= 50:
            estado = "ATENCIÓN"
        else:
            estado = "MONITOREO"

        # Narrativa LLM
        nombres = "\n".join(f"  - {a['nombre']} (score {a.get('score', 0)})"
                             for a in amenazas_activan[:4])
        contexto = (
            f"Categoría de implicancia operacional: {IMPLICANCIA_LABELS[cat]}\n"
            f"Estado: {estado}\n"
            f"Amenazas activas:\n{nombres}"
        )
        prompt = (
            f"Eres un analista de continuidad operacional minera. Redacta UNA "
            f"frase de 1 a 2 líneas (máximo 35 palabras) sobre cómo estas "
            f"amenazas afectan específicamente la categoría '{IMPLICANCIA_LABELS[cat]}'. "
            f"Sé concreto y operacional. Cero preámbulos."
        )
        narrativa = redactar_narrativa(prompt, contexto, max_tokens=120)
        if not narrativa:
            # Fallback determinístico
            top = amenazas_activan[0]
            narrativa = (f"{len(amenazas_activan)} amenaza{'s' if len(amenazas_activan)>1 else ''} "
                          f"activa{'s' if len(amenazas_activan)>1 else ''} en esta categoría. "
                          f"Principal: {top['nombre']} (score {top.get('score', 0)}).")

        out[cat] = {
            "label": IMPLICANCIA_LABELS[cat],
            "n_amenazas": len(amenazas_activan),
            "estado": estado,
            "narrativa": narrativa,
            "amenazas_relacionadas": [
                {"id": a["id"], "nombre": a["nombre"], "score": a.get("score", 0)}
                for a in amenazas_activan[:4]
            ],
        }
    return out


# =====================================================================
# BLOQUE 6: OUTLOOK 30 DÍAS (escenarios cualitativos por reglas)
# =====================================================================

def _generar_outlook_30d(snapshot: dict, intelligence_brief: dict) -> dict:
    """3 escenarios cualitativos derivados de Intelligence Engine."""
    convergencias = _safe_list(_safe_get(intelligence_brief, "convergencias", []))
    iw = _safe_dict(_safe_get(intelligence_brief, "indicators_warnings", {}))
    benchmark = _safe_dict(_safe_get(intelligence_brief, "comparative_benchmark", {}))
    matriz = [f for f in _safe_list(_safe_get(snapshot, "matriz_riesgo", []))
              if isinstance(f, dict)]
    riesgo_obj = _safe_get(snapshot, "riesgo", {})
    if isinstance(riesgo_obj, dict):
        score_global = _safe_num(
            riesgo_obj.get("global",
                riesgo_obj.get("score_global",
                    riesgo_obj.get("score", 0))),
            default=0
        )
    else:
        score_global = _safe_num(_safe_get(snapshot, "score_global", 0))
    delta_4w = _safe_num(
        benchmark.get("delta_vs_4w",
            benchmark.get("delta_vs_4w_media", 0)),
        default=0
    )

    # Contar I&W activos (de cualquier escenario)
    n_iw_activos = sum(1 for e_id, e_data in iw.items()
                        if isinstance(e_data, dict) and
                           e_data.get("nivel_alerta") in ("ALTO", "CRÍTICO"))

    # Factor estructural más caliente
    factores_calientes = [f for f in matriz if _safe_num(f.get("score", 0)) >= 60]

    # --- ESCENARIO BASE ---
    if delta_4w >= 3:
        prob_base = 50
    elif delta_4w <= -3:
        prob_base = 40
    else:
        prob_base = 55

    base_tema = (
        f"Continuidad de la dinámica observada con score nacional estabilizado "
        f"alrededor de {score_global:.0f} ± 4 puntos. "
    )
    if factores_calientes:
        top = factores_calientes[0]
        base_tema += f"Factor dominante: {top.get('nombre', '?')}."

    base_indicadores = [
        f"Score global permanece entre {max(0, score_global-5):.0f}-{min(100, score_global+5):.0f}",
        "Sin nuevas convergencias de 3+ factores",
        f"{len(factores_calientes)} factor(es) en zona elevada se mantienen",
    ]

    # --- ESCENARIO DETERIORO ---
    n_convergencias = len(convergencias)
    if n_convergencias >= 2 or delta_4w >= 5:
        prob_deterioro = 35
    elif n_convergencias >= 1:
        prob_deterioro = 28
    else:
        prob_deterioro = 18

    deterioro_tema = (
        f"Activación de patrones convergentes que ya están en formación "
        f"({n_convergencias} convergencia(s) detectada(s)). "
        f"Escalamiento del score nacional hacia rango {min(100, score_global+10):.0f}-"
        f"{min(100, score_global+18):.0f}."
    )
    deterioro_indicadores = []
    for c in convergencias[:2]:
        c_dict = _safe_dict(c)
        deterioro_indicadores.append(
            f"Convergencia '{c_dict.get('tema', '?')}' se consolida con ≥4 factores"
        )
    deterioro_indicadores.append("Aparición de nueva alerta CRÍTICA semanal")
    deterioro_indicadores.append("I&W activos pasan a ≥3 escenarios simultáneos")

    # --- ESCENARIO CRISIS ---
    if n_iw_activos >= 2 and n_convergencias >= 2:
        prob_crisis = 18
    elif n_iw_activos >= 1 or n_convergencias >= 2:
        prob_crisis = 10
    else:
        prob_crisis = 5

    crisis_tema = (
        f"Materialización simultánea de múltiples factores en niveles críticos. "
        f"Score nacional supera 80. Escenario asociado a quiebre operacional "
        f"regional o nacional de duración multi-semanal."
    )
    crisis_indicadores = [
        f"≥{max(2, n_iw_activos+1)} escenarios I&W en estado CRÍTICO simultáneo",
        "Alerta CRÍTICA en categoría 'Conflictos sociales' o 'Seguridad' nueva",
        "Convergencia entre crisis política + bloqueos operacionales",
        "Aparición de actores institucionales en silencio inusual prolongado",
    ]

    # Normalizar probabilidades (deben sumar ~100 con margen de "otros")
    total = prob_base + prob_deterioro + prob_crisis
    if total > 100:
        factor = 95 / total
        prob_base = round(prob_base * factor)
        prob_deterioro = round(prob_deterioro * factor)
        prob_crisis = round(prob_crisis * factor)

    return {
        "ventana": "30 días",
        "metodologia": ("Escenarios cualitativos derivados del Intelligence "
                        "Engine: convergencias + I&W + benchmark histórico."),
        "escenarios": [
            {
                "id": "base",
                "label": "Escenario BASE",
                "probabilidad_pct": prob_base,
                "color": "verde-amarillo",
                "narrativa": base_tema,
                "indicadores_tempranos": base_indicadores,
            },
            {
                "id": "deterioro",
                "label": "Escenario DETERIORO",
                "probabilidad_pct": prob_deterioro,
                "color": "naranja",
                "narrativa": deterioro_tema,
                "indicadores_tempranos": deterioro_indicadores,
            },
            {
                "id": "crisis",
                "label": "Escenario CRISIS",
                "probabilidad_pct": prob_crisis,
                "color": "rojo",
                "narrativa": crisis_tema,
                "indicadores_tempranos": crisis_indicadores,
            },
        ],
    }


# =====================================================================
# BLOQUE 7: EXECUTIVE INSIGHT (un solo párrafo analítico)
# =====================================================================

def _extraer_insight_estrategico(snapshot: dict, intelligence_brief: dict) -> dict:
    """UN solo insight semanal con narrativa LLM (fallback determinístico)."""
    convergencias = _safe_list(_safe_get(intelligence_brief, "convergencias", []))
    anomalias = _safe_list(_safe_get(intelligence_brief, "anomalias", []))
    silencios = _safe_list(_safe_get(intelligence_brief, "silencios_inusuales", []))
    assessment = _safe_dict(_safe_get(intelligence_brief, "strategic_assessment", {}))

    # Construir contexto rico para el LLM
    parts = []
    if convergencias:
        top_conv = _safe_dict(convergencias[0])
        factores_conv = [_safe_dict(f) for f in _safe_list(top_conv.get("factores", []))][:4]
        nombres = ", ".join(str(f.get("nombre", "?")) for f in factores_conv)
        parts.append(f"Convergencia principal: '{top_conv.get('tema', '?')}' "
                      f"con factores: {nombres}")
    if anomalias:
        top_anom = _safe_dict(anomalias[0])
        z = _safe_num(top_anom.get("z_score", 0))
        parts.append(f"Anomalía estadística: {top_anom.get('nombre', '?')} "
                      f"({z:+.1f}σ del baseline)")
    if silencios:
        top_sil = _safe_dict(silencios[0])
        ratio = _safe_num(top_sil.get("ratio_actual", 0))
        parts.append(f"Silencio inusual: actor '{top_sil.get('actor', '?')}' con "
                      f"cobertura {ratio*100:.0f}% del baseline")

    if not parts:
        return {
            "insight": ("Sin convergencias, anomalías o silencios destacables "
                         "esta semana. El entorno estratégico se mantiene en "
                         "configuración estable."),
            "fuente_llm": False,
            "categorias_detectadas": [],
        }

    contexto = "\n".join(parts)
    if assessment:
        assessment_summary = str(assessment.get('summary', ''))[:300]
        contexto += f"\n\nAssessment previo: {assessment_summary}"

    texto = redactar_insight(contexto, max_tokens=300)
    if texto:
        return {
            "insight": texto,
            "fuente_llm": True,
            "categorias_detectadas": [p.split(":")[0] for p in parts],
        }

    # Fallback determinístico
    insight = ("Esta semana el sistema detecta: " + "; ".join(parts) +
                ". El patrón sugiere monitoreo activo de la convergencia entre "
                "los vectores señalados para anticipar escalamiento.")
    return {
        "insight": insight,
        "fuente_llm": False,
        "categorias_detectadas": [p.split(":")[0] for p in parts],
    }


# =====================================================================
# ORQUESTADOR PRINCIPAL
# =====================================================================

def _ejecutar_bloque(nombre: str, fn, *args, default=None):
    """Ejecuta una función de bloque y captura cualquier error sin propagarlo.
    Devuelve (resultado, mensaje_error). Si éxito: (resultado, None).
    Si error: (default, "mensaje").
    """
    try:
        return fn(*args), None
    except Exception as e:
        import traceback
        tb = traceback.format_exc(limit=3)
        log.error("Executive bloque '%s' falló: %s\n%s", nombre, e, tb)
        return default, f"{type(e).__name__}: {e}"


def sintetizar_executive_brief(snapshot_actual: dict,
                                intelligence_brief: dict) -> dict:
    """Orquestador. Devuelve el brief ejecutivo completo en JSON.

    Cada bloque se aísla en try/except para tolerancia a fallos:
    si un bloque falla, los demás se entregan completos y el bloque fallido
    aparece con un campo `_error` describiendo qué pasó.

    Args:
        snapshot_actual: salida del pipeline OSINT.
        intelligence_brief: salida de generar_intelligence_brief().

    Returns:
        Dict con los 7 bloques del Executive Home + metadata + errores parciales.
    """
    snapshot_actual = _safe_dict(snapshot_actual)
    intelligence_brief = _safe_dict(intelligence_brief)

    generado = now_pe()
    valido_hasta = generado + timedelta(hours=4)

    log.info("Executive Synthesis: arrancando síntesis (LLM disponible=%s)",
              llm_disponible())

    errores_bloque = {}

    status, err = _ejecutar_bloque("status_nacional", _status_nacional,
                                     snapshot_actual, intelligence_brief, default={})
    if err: errores_bloque["status_nacional"] = err

    amenazas, err = _ejecutar_bloque("amenazas_prioritarias", _priorizar_amenazas,
                                      snapshot_actual, intelligence_brief, default=[])
    if err: errores_bloque["amenazas_prioritarias"] = err
    amenazas = amenazas if isinstance(amenazas, list) else []

    critical, err = _ejecutar_bloque("critical_alerts", _filtrar_alerts_ejecutivas,
                                      snapshot_actual, default=[])
    if err: errores_bloque["critical_alerts"] = err

    hotspots, err = _ejecutar_bloque("hotspots", _clasificar_hotspots,
                                      snapshot_actual, default=[])
    if err: errores_bloque["hotspots"] = err

    implicancias, err = _ejecutar_bloque("implicancias_operacionales",
                                          _derivar_implicancias,
                                          amenazas, snapshot_actual, default={})
    if err: errores_bloque["implicancias_operacionales"] = err

    outlook, err = _ejecutar_bloque("outlook_30d", _generar_outlook_30d,
                                     snapshot_actual, intelligence_brief, default={})
    if err: errores_bloque["outlook_30d"] = err

    insight, err = _ejecutar_bloque("executive_insight", _extraer_insight_estrategico,
                                     snapshot_actual, intelligence_brief, default={})
    if err: errores_bloque["executive_insight"] = err

    # Determinar modo LLM REAL (no solo si la key está) consultando contador de uso
    uso = estado_uso()
    if not llm_disponible():
        llm_modo = "fallback-deterministico (sin API key)"
    elif uso.get("llamadas", 0) == 0:
        llm_modo = "API key presente — sin llamadas aún (cache)"
    elif uso.get("fallos", 0) >= uso.get("llamadas", 0):
        llm_modo = f"FALLBACK forzado — todas las {uso['llamadas']} llamadas LLM fallaron"
    elif uso.get("fallos", 0) > 0:
        llm_modo = (f"claude-haiku-4-5 (parcial: {uso['llamadas']-uso['fallos']}/"
                    f"{uso['llamadas']} OK, {uso['fallos']} fallidas)")
    else:
        llm_modo = (f"claude-haiku-4-5 ({uso['llamadas']} llamadas, "
                    f"{uso['input']}→{uso['output']} tokens)")

    brief = {
        "schema_version": "executive_brief.v1",
        "generado_en": generado.isoformat(timespec="seconds"),
        "valido_hasta": valido_hasta.isoformat(timespec="seconds"),
        "ttl_horas": 4,
        "llm_modo": llm_modo,
        "status_nacional": status,
        "amenazas_prioritarias": amenazas,
        "critical_alerts": critical,
        "hotspots": hotspots,
        "implicancias_operacionales": implicancias,
        "outlook_30d": outlook,
        "executive_insight": insight,
    }
    if errores_bloque:
        brief["_errores_bloques"] = errores_bloque
        log.warning("Executive Synthesis: %d bloques con errores parciales",
                    len(errores_bloque))
    return brief
