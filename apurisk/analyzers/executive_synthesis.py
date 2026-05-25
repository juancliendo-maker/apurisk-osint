"""APURISK Executive Synthesis Engine.

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

from ..utils.llm_client import redactar_narrativa, redactar_insight, llm_disponible
from ..utils.timezone_pe import now_pe, now_pe_iso

log = logging.getLogger("apurisk.executive")


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
    matriz = snapshot.get("matriz_riesgo", []) or []
    score_global = snapshot.get("riesgo", {}).get("score_global", 0) or 0

    # Riesgo Minero: media de scores de factores mineros
    scores_mineros = [f["score"] for f in matriz if f.get("id") in FACTORES_MINEROS]
    riesgo_minero = round(sum(scores_mineros) / len(scores_mineros), 1) if scores_mineros else 0

    # Riesgo Corredor Sur: factor extractivo específicamente
    f_corredor = next((f for f in matriz if f.get("id") == "conflictos_extractivos"), {})
    riesgo_corredor = f_corredor.get("score", 0) if f_corredor else 0

    # Riesgo Criminal: media de scores criminales
    scores_criminales = [f["score"] for f in matriz if f.get("id") in FACTORES_CRIMINALES]
    riesgo_criminal = round(sum(scores_criminales) / len(scores_criminales), 1) if scores_criminales else 0

    # Delta semanal: comparar con baseline si está disponible
    baseline = intelligence_brief.get("baseline", {}) or {}
    benchmark = intelligence_brief.get("comparative_benchmark", {}) or {}
    delta_score_global = benchmark.get("delta_vs_4w_media", 0) or 0

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
            "score": round(score, 1),
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
                                    f_corredor.get("tendencia", "→") + " tendencia"),
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
    matriz = snapshot.get("matriz_riesgo", []) or []
    if not matriz:
        return []

    # Sort por score descendente; el matriz_riesgo ya viene así pero garantizamos
    matriz_ord = sorted(matriz, key=lambda x: -x.get("score", 0))

    # Filtrar: priorizar los que tienen implicancias operacionales mapeadas
    candidatos = []
    for f in matriz_ord:
        fid = f.get("id")
        implicancias = IMPLICANCIAS_POR_FACTOR.get(fid, [])
        # Bonus de relevancia si afecta logística, continuidad o fuerza laboral
        relevancia_op = sum(1 for x in implicancias
                             if x in {"logistica", "continuidad", "fuerza_laboral"})
        candidatos.append((f, relevancia_op))
    # Re-ordenar: primero por relevancia operacional, después por score
    candidatos.sort(key=lambda x: (-x[1], -x[0].get("score", 0)))

    top = [c[0] for c in candidatos[:top_n]]

    # Anclar convergencias relevantes del Intelligence Engine
    convergencias = intelligence_brief.get("convergencias", []) or []
    conv_ids = set()
    for c in convergencias:
        for f in c.get("factores", []):
            conv_ids.add(f.get("id"))

    out = []
    for f in top:
        narrativa = _narrativa_amenaza(f, snapshot, en_convergencia=(f.get("id") in conv_ids))
        out.append({
            "id": f.get("id"),
            "nombre": f.get("nombre"),
            "categoria": f.get("categoria"),
            "score": f.get("score"),
            "nivel": f.get("nivel"),
            "probabilidad": f.get("probabilidad"),
            "impacto": f.get("impacto"),
            "tendencia": f.get("tendencia"),
            "narrativa": narrativa,
            "en_convergencia": f.get("id") in conv_ids,
            "implicancias_categorias": IMPLICANCIAS_POR_FACTOR.get(f.get("id"), []),
        })
    return out


def _narrativa_amenaza(factor: dict, snapshot: dict, en_convergencia: bool) -> str:
    """Genera narrativa de 2-3 líneas. LLM si disponible, fallback si no."""
    fid = factor.get("id", "")
    nombre = factor.get("nombre", "")
    score = factor.get("score", 0)
    nivel = factor.get("nivel", "")
    tendencia = factor.get("tendencia", "→")
    evidencias = factor.get("evidencias", [])[:3]

    # Construir contexto para el LLM
    ev_titulos = "\n".join(f"  - {e.get('title', '')[:120]} ({e.get('source', '?')})"
                            for e in evidencias)
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
        return (f"{nombre} en nivel {nivel} (score {score}) con tendencia {arrow}.{conv} "
                f"Última evidencia: {evidencias[0].get('title', '')[:100]}.")
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
    """Solo alertas CRÍTICAS o ALTAS operacionales, max N."""
    alertas = snapshot.get("alertas", []) or []
    if not alertas:
        return []

    filtradas = []
    for a in alertas:
        nivel = a.get("nivel", "")
        cat = a.get("categoria", "")
        if nivel not in ("CRÍTICA", "ALTA"):
            continue
        if cat not in ALERT_CATEGORIAS_OPERACIONALES:
            continue
        filtradas.append({
            "nivel": nivel,
            "titulo": a.get("titulo", ""),
            "categoria": cat,
            "regla": a.get("regla", ""),
            "fuente": a.get("fuente", ""),
            "url": a.get("url", ""),
            "hours_ago": a.get("hours_ago"),
            "que_paso": a.get("titulo", "")[:120],
            "por_que_importa": _por_que_importa(a),
        })

    # Sort: CRÍTICAS primero, después por antigüedad
    filtradas.sort(key=lambda x: (0 if x["nivel"] == "CRÍTICA" else 1,
                                    x.get("hours_ago") or 999))
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

def _clasificar_hotspots(snapshot: dict) -> list[dict]:
    """Agrupa eventos georreferenciados por tipo de riesgo."""
    eventos = snapshot.get("eventos_geo", []) or []
    if not eventos:
        # Intentar otra fuente típica
        eventos = snapshot.get("mapa", {}).get("eventos", []) or []

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

    out = []
    for tipo_id, cfg in tipos.items():
        matches = []
        for ev in eventos:
            text = (ev.get("titulo", "") + " " + ev.get("descripcion", "")).lower()
            if any(kw in text for kw in cfg["keywords"]):
                matches.append({
                    "titulo": ev.get("titulo", "")[:120],
                    "lugar": ev.get("region", "") or ev.get("lugar", ""),
                    "lat": ev.get("lat"),
                    "lon": ev.get("lon"),
                    "fuente": ev.get("fuente", ""),
                })
        if matches:
            out.append({
                "tipo": tipo_id,
                "label": cfg["label"],
                "color": cfg["color"],
                "n_eventos": len(matches),
                "eventos": matches[:5],  # max 5 por hotspot
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
    convergencias = intelligence_brief.get("convergencias", []) or []
    iw = intelligence_brief.get("indicators_warnings", {}) or {}
    benchmark = intelligence_brief.get("comparative_benchmark", {}) or {}
    matriz = snapshot.get("matriz_riesgo", []) or []
    score_global = snapshot.get("riesgo", {}).get("score_global", 0) or 0
    delta_4w = benchmark.get("delta_vs_4w_media", 0) or 0

    # Contar I&W activos (de cualquier escenario)
    n_iw_activos = sum(1 for e_id, e_data in iw.items()
                        if isinstance(e_data, dict) and
                           e_data.get("nivel_alerta") in ("ALTO", "CRÍTICO"))

    # Factor estructural más caliente
    factores_calientes = [f for f in matriz if f.get("score", 0) >= 60]

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
        deterioro_indicadores.append(
            f"Convergencia '{c.get('tema', '?')}' se consolida con ≥4 factores"
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
    convergencias = intelligence_brief.get("convergencias", []) or []
    anomalias = intelligence_brief.get("anomalias", []) or []
    silencios = intelligence_brief.get("silencios_inusuales", []) or []
    assessment = intelligence_brief.get("strategic_assessment", {}) or {}

    # Construir contexto rico para el LLM
    parts = []
    if convergencias:
        top_conv = convergencias[0]
        nombres = ", ".join(f.get("nombre", "?") for f in top_conv.get("factores", [])[:4])
        parts.append(f"Convergencia principal: '{top_conv.get('tema', '?')}' "
                      f"con factores: {nombres}")
    if anomalias:
        top_anom = anomalias[0]
        parts.append(f"Anomalía estadística: {top_anom.get('nombre', '?')} "
                      f"({top_anom.get('z_score', 0):+.1f}σ del baseline)")
    if silencios:
        top_sil = silencios[0]
        parts.append(f"Silencio inusual: actor '{top_sil.get('actor', '?')}' con "
                      f"cobertura {top_sil.get('ratio_actual', 0)*100:.0f}% del baseline")

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
        contexto += f"\n\nAssessment previo: {assessment.get('summary', '')[:300]}"

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

def sintetizar_executive_brief(snapshot_actual: dict,
                                intelligence_brief: dict) -> dict:
    """Orquestador. Devuelve el brief ejecutivo completo en JSON.

    Args:
        snapshot_actual: salida del pipeline OSINT.
        intelligence_brief: salida de generar_intelligence_brief().

    Returns:
        Dict con los 7 bloques del Executive Home + metadata.
    """
    generado = now_pe()
    valido_hasta = generado + timedelta(hours=4)

    log.info("Executive Synthesis: arrancando síntesis (LLM disponible=%s)",
              llm_disponible())

    # 1. Status nacional
    status = _status_nacional(snapshot_actual, intelligence_brief)
    # 2. Amenazas prioritarias (con narrativa LLM)
    amenazas = _priorizar_amenazas(snapshot_actual, intelligence_brief, top_n=5)
    # 3. Critical alerts
    critical = _filtrar_alerts_ejecutivas(snapshot_actual, max_n=8)
    # 4. Hotspots clasificados
    hotspots = _clasificar_hotspots(snapshot_actual)
    # 5. Implicancias operacionales (con narrativa LLM)
    implicancias = _derivar_implicancias(amenazas, snapshot_actual)
    # 6. Outlook 30 días
    outlook = _generar_outlook_30d(snapshot_actual, intelligence_brief)
    # 7. Executive insight (LLM)
    insight = _extraer_insight_estrategico(snapshot_actual, intelligence_brief)

    return {
        "schema_version": "executive_brief.v1",
        "generado_en": generado.isoformat(timespec="seconds"),
        "valido_hasta": valido_hasta.isoformat(timespec="seconds"),
        "ttl_horas": 4,
        "llm_modo": "claude-haiku-4-5" if llm_disponible() else "fallback-deterministico",
        "status_nacional": status,
        "amenazas_prioritarias": amenazas,
        "critical_alerts": critical,
        "hotspots": hotspots,
        "implicancias_operacionales": implicancias,
        "outlook_30d": outlook,
        "executive_insight": insight,
    }
