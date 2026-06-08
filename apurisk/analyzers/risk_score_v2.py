"""Score Riesgo Político Nacional v2 · Sprint 1.1.

ARQUITECTURA APROBADA (ver conversación de revisión de fórmulas):

  Score Nacional = composite ponderado de 5 dimensiones estratégicas
                 × factor de convergencia
                 + corrección por baseline histórico
                 + suavizado temporal con excepción de evento crítico
                 ⊕ confidence_score como metadata

  5 dimensiones:
    1. Gobernabilidad e institucionalidad      25%
    2. Conflictividad social y territorial     25%
    3. Seguridad interna y crimen organizado   20%
    4. Economía política y clima empresarial   15%
    5. Relaciones exteriores                   15%

  4 horizontes temporales:
    - 24h  → alerta táctica inmediata
    - 7d   → presión coyuntural acumulada
    - 30d  → tendencia política operativa
    - 90d  → riesgo estratégico estructural

PLAN DE SPRINTS:
  Sprint 1.1 → Esqueleto + feature flag + tabla SQLite     ✅ ESTE SPRINT
  Sprint 1.2 → Deduplicación + mapeo categorías → 5 dims
  Sprint 1.3 → Cálculo por dimensión + integración EDI variable
  Sprint 1.4 → 4 horizontes temporales con decay exponencial
  Sprint 1.5 → Modificadores fuente/actor/escalamiento/persistencia
  Sprint 1.6 → Suavizado temporal + detección evento crítico (LLM)
  Sprint 1.7 → Confidence score (5 componentes)
  Sprint 1.8 → Validación paralela 7 días + endpoint diagnóstico

REGLA DE GOBIERNO:
  Toda modificación de pesos, umbrales, decays o componentes debe pasar
  por config.yaml → score_engine. NO hardcodear nada.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import math
import json


# =====================================================================
# MAPEO · 12 categorías legacy → 5 dimensiones canónicas v2
# =====================================================================
# El sistema v1 captura keywords en 12 categorías (ver config.yaml
# indicadores_riesgo). Cada categoría aporta a una o más dimensiones v2
# con un peso (suma de pesos hacia una categoría = 1.0).
MAPEO_CATEGORIAS_A_DIMENSIONES: dict[str, dict[str, float]] = {
    # GOBERNABILIDAD E INSTITUCIONALIDAD
    "estabilidad_gobierno": {"gobernabilidad": 1.0},
    "polarizacion":         {"gobernabilidad": 1.0},
    "violencia_electoral":  {"gobernabilidad": 1.0},
    "intervencion_militar": {"gobernabilidad": 1.0},
    "corrupcion":           {"gobernabilidad": 0.6,
                              "economia_politica": 0.4},

    # CONFLICTIVIDAD SOCIAL Y TERRITORIAL
    "conflictos_sociales":  {"conflictividad_social": 1.0},

    # SEGURIDAD INTERNA Y CRIMEN ORGANIZADO
    "seguridad":            {"seguridad_crimen": 1.0},

    # ECONOMÍA POLÍTICA Y CLIMA EMPRESARIAL
    "riesgo_regulatorio":   {"economia_politica": 1.0},
    "presion_economica":    {"economia_politica": 1.0},

    # RELACIONES EXTERIORES Y PRESIÓN INTERNACIONAL
    "tensiones_fronterizas":  {"relaciones_exteriores": 1.0},
    "crisis_migratoria":      {"relaciones_exteriores": 1.0},
    "tensiones_diplomaticas": {"relaciones_exteriores": 1.0},
}


# =====================================================================
# BASELINES POR DIMENSIÓN · Perú 2026 (calibración inicial)
# =====================================================================
# Cada dimensión tiene un "estado de fondo" del país en ausencia de
# eventos negativos del día. Refleja la realidad estructural peruana.
# Se ajustarán con datos reales en Sprint 1.7 (validación paralela).
BASELINE_PERU_2026: dict[str, float] = {
    "gobernabilidad":         42,  # Tensión recurrente Ejecutivo-Congreso
    "conflictividad_social":  48,  # Conflictividad estructural alta
    "seguridad_crimen":       55,  # Crimen organizado en expansión
    "economia_politica":      35,  # Marco institucional empresarial estable
    "relaciones_exteriores":  22,  # Posición internacional sin grandes shocks
}


# =====================================================================
# UMBRALES SEMÁFORO · 5 niveles (vs los 3 del v1)
# =====================================================================
# Estos umbrales se sobrescriben con config.yaml > score_engine > umbrales_semaforo
UMBRALES_DEFAULT: dict[str, int] = {
    "bajo":            24,
    "moderado_bajo":   44,
    "moderado_activo": 64,
    "alto":            79,
    "critico":         100,
}

ETIQUETAS_SEMAFORO: dict[str, dict[str, str]] = {
    "bajo":            {"label": "BAJO",            "color": "verde"},
    "moderado_bajo":   {"label": "MODERADO BAJO",   "color": "verde-amarillo"},
    "moderado_activo": {"label": "MODERADO ACTIVO", "color": "ambar"},
    "alto":            {"label": "ALTO",            "color": "naranja"},
    "critico":         {"label": "CRÍTICO",         "color": "rojo"},
}


# =====================================================================
# HELPERS · Volumen ajustado, decay exponencial, clasificación semáforo
# =====================================================================
def volumen_ajustado(n_eventos: int, max_score: float = 100) -> float:
    """Convierte número de eventos en puntaje log-normalizado.

    Evita que volumen crezca lineal: pasar de 1 a 5 eventos es relevante,
    pasar de 80 a 120 no infla artificialmente. Tope en 100.

    Fórmula: min(100, log(1 + n) * 20)
    """
    if n_eventos <= 0:
        return 0.0
    return min(max_score, math.log(1 + n_eventos) * 20)


def decay_exponencial(dias_atras: int, lambda_diario: float) -> float:
    """Peso temporal de un evento según su antigüedad.

    Un evento de hoy pesa 1.0, uno de hace N días pesa exp(-lambda*N).

    Args:
        dias_atras: días desde que ocurrió el evento (entero positivo).
        lambda_diario: tasa de decaimiento configurada por horizonte.

    Returns:
        Factor multiplicativo entre 0 y 1.
    """
    if dias_atras <= 0 or lambda_diario <= 0:
        return 1.0
    return math.exp(-lambda_diario * dias_atras)


def clasificar_semaforo(score: float,
                          umbrales: dict[str, int] | None = None) -> dict[str, str]:
    """Score 0-100 → {nivel, label, color, descripcion}.

    Devuelve el nivel canónico del semáforo de 5 niveles + etiqueta del
    diccionario ETIQUETAS_SEMAFORO + color (token JSON consumido por la
    Plantilla Madre v1.0).
    """
    u = umbrales or UMBRALES_DEFAULT
    if score <= u["bajo"]:
        nivel = "bajo"
    elif score <= u["moderado_bajo"]:
        nivel = "moderado_bajo"
    elif score <= u["moderado_activo"]:
        nivel = "moderado_activo"
    elif score <= u["alto"]:
        nivel = "alto"
    else:
        nivel = "critico"
    et = ETIQUETAS_SEMAFORO[nivel]
    return {
        "nivel": nivel,
        "label": et["label"],
        "color": et["color"],
    }


# =====================================================================
# CÁLCULO POR DIMENSIÓN · Sprint 1.3
# =====================================================================
def calcular_score_dimension(
    eventos_dim: list[dict],
    baseline: float,
    alpha: float = 0.85,
) -> tuple[float, dict]:
    """Score 0-100 de una dimensión estratégica del riesgo nacional.

    Fórmula:

      score_dim = baseline + (severidad_agregada - baseline) × alpha

      donde:
        severidad_agregada = max(scores_eventos_ajustados) + log(1+n)×5
        scores_eventos_ajustados = ev.severidad_base
                                   × ev.factor_confirmacion
                                   × ev.peso_en_dimension

    Interpretación doctrinal:

      · Sin eventos → score = baseline (vuelve al "estado de fondo" del país).
      · Con eventos → mezcla baseline con severidad observada con peso α=0.85.
        Esto evita que un día con 1 evento de severidad media catapulte el
        score a 100, pero garantiza que eventos graves tengan impacto real.
      · El log(1+n)*5 es el componente de "volumen confirmado" — añade poco
        cuando hay 1 evento, mucho cuando hay 6+ eventos en la misma
        dimensión (señal de patrón, no de noticia aislada).

    Args:
        eventos_dim: lista de eventos de event_clustering.clasificar_eventos_a_dimensiones.
        baseline: punto de partida estructural de la dimensión (0-100).
        alpha: peso de la severidad observada vs el baseline (default 0.85).

    Returns:
        (score, metadata) donde metadata es dict con:
          - n_eventos: número de eventos en la dimensión
          - severidad_max: el evento más severo ajustado
          - severidad_agg: severidad agregada usada para el score
          - delta_vs_baseline: diferencia score - baseline
    """
    if not eventos_dim:
        return baseline, {
            "n_eventos": 0,
            "severidad_max": 0.0,
            "severidad_agg": 0.0,
            "delta_vs_baseline": 0.0,
        }

    # Score ajustado por evento:
    #   · Si el evento ya pasó por aplicar_modificadores (Sprint 1.5):
    #     usa severidad_modificada (incluye fuente/actor/escal/pers/conf).
    #   · Si no: usa severidad_base × factor_confirmacion (Sprint 1.4 y previos).
    # En ambos casos multiplica por peso_en_dimension del mapeo a dimensiones.
    def _sev_efectiva(ev: dict) -> float:
        if "severidad_modificada" in ev:
            return float(ev["severidad_modificada"]) * float(ev.get("peso_en_dimension", 1.0))
        return (
            float(ev.get("severidad_base", 40))
            * float(ev.get("factor_confirmacion", 1.0))
            * float(ev.get("peso_en_dimension", 1.0))
        )

    scores_ajust = [_sev_efectiva(ev) for ev in eventos_dim]
    severidad_max = max(scores_ajust)
    # Volumen confirmado (log saturado para no inflar lineal)
    severidad_agg = min(100, severidad_max + math.log(1 + len(scores_ajust)) * 5)

    # Mezcla baseline + observación
    score = baseline + (severidad_agg - baseline) * alpha
    score = min(100, max(0, score))

    return score, {
        "n_eventos": len(eventos_dim),
        "severidad_max": round(severidad_max, 1),
        "severidad_agg": round(severidad_agg, 1),
        "delta_vs_baseline": round(score - baseline, 1),
    }


# =====================================================================
# UTILIDADES TEMPORALES — Sprint 1.4
# =====================================================================
def _dias_atras(fecha_evento: str, hoy: datetime | None = None) -> int:
    """Días entre fecha_evento (YYYY-MM-DD) y hoy. 0 si invalido o futuro."""
    if hoy is None:
        hoy = datetime.now(timezone.utc)
    try:
        dt = datetime.strptime(str(fecha_evento)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        d = (hoy - dt).days
        return max(0, d)
    except Exception:
        return 0


# =====================================================================
# SCORE POR HORIZONTE TEMPORAL · Sprint 1.4
# =====================================================================
def calcular_score_horizonte(
    eventos: list[dict],
    horizonte: str,
    edi_actual: float | None,
    config_score_engine: dict,
    hoy: datetime | None = None,
) -> dict:
    """Calcula el score 0-100 para un horizonte temporal específico.

    Args:
        eventos: lista de eventos dedupeados (output de dedupear_eventos)
        horizonte: 'h24' | 'h7d' | 'h30d' | 'h90d'
        edi_actual: 0-100 (POSITIVO, 100=óptimo)
        config_score_engine: dict con configuración del motor
        hoy: timestamp de referencia (default = ahora UTC, para testing)

    Pipeline:
        1. Filtra eventos cuya fecha cae en la ventana del horizonte
        2. Aplica decay exponencial al `severidad_base` de cada evento
        3. Clasifica eventos en las 5 dimensiones
        4. Calcula sub-score por dimensión (calcular_score_dimension)
        5. Composite ponderado con `pesos_dimension`
        6. Integra EDI invertido con el `peso_edi_por_horizonte` específico
        7. Clasifica en semáforo

    Returns:
        dict con score, sub_scores, ventana_dias, decay_lambda, peso_edi,
        n_eventos_en_ventana, semaforo, metadata por dimensión.
    """
    if hoy is None:
        hoy = datetime.now(timezone.utc)

    ventana_dias = config_score_engine.get("ventana_dias_por_horizonte", {
        "h24": 1, "h7d": 7, "h30d": 30, "h90d": 90,
    }).get(horizonte, 7)

    lambda_decay = config_score_engine.get("decay_lambda_por_horizonte", {
        "h24": 0.00, "h7d": 0.15, "h30d": 0.05, "h90d": 0.02,
    }).get(horizonte, 0.10)

    peso_edi = config_score_engine.get("peso_edi_por_horizonte", {
        "h24": 0.05, "h7d": 0.10, "h30d": 0.15, "h90d": 0.20,
    }).get(horizonte, 0.15)

    pesos_dim = config_score_engine.get("pesos_dimension", {
        "gobernabilidad":         0.25,
        "conflictividad_social":  0.25,
        "seguridad_crimen":       0.20,
        "economia_politica":      0.15,
        "relaciones_exteriores":  0.15,
    })

    # ---- 1-2. Filtrar por ventana + aplicar decay exponencial ----
    eventos_ventana = []
    for ev in eventos:
        d = _dias_atras(ev.get("fecha", ""), hoy=hoy)
        if d > ventana_dias:
            continue
        decay = decay_exponencial(d, lambda_decay)
        ev_copia = dict(ev)
        ev_copia["severidad_base"] = ev["severidad_base"] * decay
        ev_copia["_dias_atras"] = d
        ev_copia["_decay_aplicado"] = round(decay, 3)
        eventos_ventana.append(ev_copia)

    # ---- 2bis. Aplicar modificadores (Sprint 1.5) ----
    # Enriquece cada evento con factor_fuente/actor/escalamiento/persistencia
    # y calcula severidad_modificada que reemplaza a la severidad_base
    # cruda en el cálculo posterior por dimensión.
    try:
        from .event_modifiers import aplicar_modificadores_lista
    except ImportError:
        from apurisk.analyzers.event_modifiers import aplicar_modificadores_lista
    # Nota: archive se pasaría aquí cuando esté disponible para persistencia
    eventos_ventana = aplicar_modificadores_lista(eventos_ventana, archive=None)

    # ---- 3. Clasificar a dimensiones ----
    try:
        from .event_clustering import clasificar_eventos_a_dimensiones
    except ImportError:
        from apurisk.analyzers.event_clustering import clasificar_eventos_a_dimensiones

    por_dim = clasificar_eventos_a_dimensiones(eventos_ventana)

    # ---- 4. Cálculo por dimensión ----
    sub_scores = {}
    metadata = {}
    for dim in pesos_dim.keys():
        baseline = BASELINE_PERU_2026.get(dim, 40)
        s, meta = calcular_score_dimension(por_dim.get(dim, []), baseline=baseline)
        sub_scores[dim] = s
        metadata[dim] = meta

    # ---- 5. Composite ponderado ----
    score_bruto = sum(sub_scores[d] * w for d, w in pesos_dim.items())

    # ---- 6. Integración EDI invertido ----
    if edi_actual is not None and 0 <= edi_actual <= 100:
        riesgo_estado_derecho = 100 - edi_actual
        score_pre = score_bruto * (1 - peso_edi) + riesgo_estado_derecho * peso_edi
    else:
        riesgo_estado_derecho = None
        score_pre = score_bruto

    score_final = min(100, max(0, score_pre))

    # ---- 7. Semáforo ----
    semaforo = clasificar_semaforo(
        score_final,
        umbrales=config_score_engine.get("umbrales_semaforo"),
    )

    return {
        "horizonte": horizonte,
        "ventana_dias": ventana_dias,
        "decay_lambda": lambda_decay,
        "peso_edi": peso_edi,

        "score": round(score_final, 1),
        "score_bruto_sin_edi": round(score_bruto, 1),
        "nivel": semaforo["nivel"],
        "label": semaforo["label"],
        "color": semaforo["color"],

        "sub_scores": {d: round(s, 1) for d, s in sub_scores.items()},
        "metadata_dimension": metadata,

        "n_eventos_en_ventana": len(eventos_ventana),
        "riesgo_estado_derecho": riesgo_estado_derecho,
    }


# =====================================================================
# FUNCIÓN PRINCIPAL · Sprint 1.4 — Score Nacional = composite de 4 horizontes
# =====================================================================
def calcular_score_nacional_v2(
    snapshot: dict,
    archive: Any = None,
    edi_actual: float | None = None,
    config: dict | None = None,
    hoy: datetime | None = None,
) -> dict:
    """Calcula el Score Riesgo Político Nacional v2.

    El Score Nacional General es ahora una **combinación ponderada** de
    los 4 horizontes temporales:

        score_nacional = 0.30 × score_24h
                       + 0.30 × score_7d
                       + 0.25 × score_30d
                       + 0.15 × score_90d

    (60% situación inmediata + 40% tendencia estructural).
    Los pesos del composite son configurables vía config.yaml.

    Cada horizonte:
      - Filtra eventos por ventana temporal (1/7/30/90 días)
      - Aplica decay exponencial específico (λ=0/0.15/0.05/0.02)
      - Integra EDI con peso variable (5/10/15/20%)

    Args:
        snapshot: snapshot OSINT
        archive: ApuriskArchive (no usado aún)
        edi_actual: 0-100 POSITIVO
        config: dict del config.yaml completo
        hoy: timestamp de referencia (default ahora UTC, para tests)

    Returns:
        dict canónico con score_nacional, los 4 horizontes y trazabilidad.
    """
    cfg = (config or {}).get("score_engine", {})

    # Pesos del composite (sumar 1.0)
    pesos_h = cfg.get("peso_composite_horizonte", {
        "h24":  0.30,
        "h7d":  0.30,
        "h30d": 0.25,
        "h90d": 0.15,
    })

    # ---- 1. Extraer noticias y dedupear ----
    noticias = _extraer_noticias_de_snapshot(snapshot)
    try:
        from .event_clustering import dedupear_eventos
    except ImportError:
        from apurisk.analyzers.event_clustering import dedupear_eventos
    eventos = dedupear_eventos(noticias)

    # ---- 1bis. Detección de evento crítico (Sprint 1.6) ----
    # Antes de calcular horizontes para que su resultado pueda romper el
    # suavizado temporal y aparecer en el dict de salida.
    try:
        from .event_critico import detectar_evento_critico, debe_omitir_suavizado
    except ImportError:
        from apurisk.analyzers.event_critico import detectar_evento_critico, debe_omitir_suavizado
    usar_llm = cfg.get("evento_critico_usar_llm", True)
    evento_critico = detectar_evento_critico(eventos, usar_llm=usar_llm)
    omitir_suavizado = debe_omitir_suavizado(evento_critico)

    # ---- 2. Calcular los 4 horizontes ----
    horizontes = {}
    for h in ("h24", "h7d", "h30d", "h90d"):
        horizontes[h] = calcular_score_horizonte(
            eventos=eventos,
            horizonte=h,
            edi_actual=edi_actual,
            config_score_engine=cfg,
            hoy=hoy,
        )

    # ---- 3. Score Nacional pre-suavizado = composite ponderado ----
    score_pre_suavizado = sum(
        horizontes[h]["score"] * pesos_h[h] for h in pesos_h.keys()
    )
    score_pre_suavizado = min(100, max(0, score_pre_suavizado))

    # ---- 3bis. Suavizado temporal (Sprint 1.6) ----
    # score_hoy = α × calculado_hoy + (1-α) × score_ayer
    # Si hay evento crítico con alta confianza → omitir suavizado (salto OK).
    # Si no hay score_ayer disponible → tampoco suavizar (primer ciclo).
    alpha = float(cfg.get("suavizado_alpha", 0.65))
    score_ayer = _leer_score_ayer_archive(archive, hoy=hoy)
    if omitir_suavizado:
        score_nacional = score_pre_suavizado
        suavizado_info = {
            "aplicado": False,
            "razon": f"evento_critico: {evento_critico.get('tipo')} "
                     f"(confianza {evento_critico.get('confianza')})",
            "alpha": alpha,
            "score_ayer": score_ayer,
        }
    elif score_ayer is not None:
        score_nacional = alpha * score_pre_suavizado + (1 - alpha) * score_ayer
        suavizado_info = {
            "aplicado": True,
            "razon": "suavizado normal sin evento crítico",
            "alpha": alpha,
            "score_ayer": score_ayer,
            "score_pre_suavizado": round(score_pre_suavizado, 1),
        }
    else:
        score_nacional = score_pre_suavizado
        suavizado_info = {
            "aplicado": False,
            "razon": "sin score_ayer disponible (primer ciclo o archivo vacío)",
            "alpha": alpha,
            "score_ayer": None,
        }
    score_nacional = round(min(100, max(0, score_nacional)), 1)

    # ---- 4. Semáforo del score nacional ----
    semaforo = clasificar_semaforo(
        score_nacional,
        umbrales=cfg.get("umbrales_semaforo"),
    )

    # ---- 5. Sub-scores del score nacional = composite de sub-scores h24/h7d ----
    # Para el resumen ejecutivo (Plantilla Madre necesita las 5 dimensiones)
    sub_scores_nacional = {}
    for dim in horizontes["h24"]["sub_scores"].keys():
        sub_scores_nacional[dim] = round(
            sum(horizontes[h]["sub_scores"][dim] * pesos_h[h] for h in pesos_h.keys()),
            1
        )

    return {
        "schema_version": "score_v2.sprint_1_4",
        "generado_en": datetime.now(timezone.utc).isoformat(),

        # Score nacional general (composite ponderado de 4 horizontes)
        "score_nacional": round(score_nacional, 1),
        "nivel": semaforo["nivel"],
        "label": semaforo["label"],
        "color": semaforo["color"],

        # Composición del score nacional
        "composite_pesos": pesos_h,

        # Sub-scores por dimensión (composite de los horizontes)
        "sub_scores": sub_scores_nacional,

        # Los 4 horizontes con su detalle completo
        "horizontes": horizontes,

        # EDI integrado (información transversal)
        "edi_actual": edi_actual,
        "riesgo_estado_derecho": (100 - edi_actual) if edi_actual is not None else None,

        # Modificadores (Sprint 1.5)
        "modificadores": {
            "factor_convergencia": 1.0,
            "factor_persistencia_global": 1.0,
            "factor_calidad_fuentes": 1.0,
            "_pendiente": "Sprint 1.5",
        },

        # Confidence (Sprint 1.7)
        "confidence": _calcular_confidence(eventos, len(noticias), archive,
                                              cfg, hoy=hoy),

        # Suavizado temporal (Sprint 1.6)
        "suavizado": suavizado_info,

        # Evento crítico detectado (Sprint 1.6)
        "evento_critico": evento_critico,

        # Trazabilidad
        "n_eventos_dedupeados": len(eventos),
        "n_articulos_origen": len(noticias),
        "top_eventos": [
            {
                "event_id": e["event_id"],
                "titular": e["titulares"][0] if e.get("titulares") else "",
                "actor": e["actor"],
                "ubicacion": e["ubicacion"],
                "tipo": e["tipo"],
                "fecha": e["fecha"],
                "severidad_base": e["severidad_base"],
                "n_fuentes": e["n_fuentes"],
            }
            for e in eventos[:3]
        ],
    }


# =====================================================================
# ORQUESTADOR DE VALIDACIÓN PARALELA · Sprint 1.8
# =====================================================================
def ejecutar_score_paralelo(
    snapshot: dict,
    archive: Any = None,
    edi_actual: float | None = None,
    config: dict | None = None,
    score_v1: dict | None = None,
    hoy: datetime | None = None,
    persistir: bool = True,
) -> dict:
    """Ejecuta v1 + v2 lado a lado y guarda la comparación.

    Para la fase de validación paralela 7 días. Cada día, el scheduler (o
    el llamador manual) invoca esta función con el snapshot actual:

      1. Si no se pasa score_v1, lo calcula desde el snapshot
         (usa risk_score.calcular_riesgo_global)
      2. Calcula score_v2 con calcular_score_nacional_v2
      3. Construye dict comparativo con comparar_v1_v2
      4. Persiste en scores_paralelos via guardar_comparacion
      5. Devuelve la comparación completa

    Args:
        snapshot: snapshot OSINT
        archive: ApuriskArchive para baseline + persistencia
        edi_actual: 0-100 POSITIVO
        config: dict completo del config.yaml
        score_v1: si ya tienes el output de v1, pásalo (evita recalcular).
                  Si None, intenta calcularlo.
        hoy: timestamp de referencia (para tests deterministas)
        persistir: si True, guarda en scores_paralelos

    Returns:
        dict de comparación listo para inspección.
    """
    # 1. v2 siempre lo calculamos
    out_v2 = calcular_score_nacional_v2(
        snapshot=snapshot,
        archive=archive,
        edi_actual=edi_actual,
        config=config,
        hoy=hoy,
    )

    # 2. v1: si no lo pasaron, intentamos calcularlo desde el snapshot
    if score_v1 is None:
        score_v1 = _calcular_v1_desde_snapshot(snapshot, config)
    if score_v1 is None:
        score_v1 = {"global": 0.0, "nivel": "DESCONOCIDO"}

    # 3. Construir comparación (propaga `hoy` para tests deterministas)
    comp = comparar_v1_v2(score_v1, out_v2, hoy=hoy)

    # 4. Persistir si corresponde
    persistido = False
    if persistir and archive is not None:
        persistido = guardar_comparacion(archive, comp)

    return {
        "score_v1": score_v1,
        "score_v2": out_v2,
        "comparacion": comp,
        "persistido": persistido,
    }


def _calcular_v1_desde_snapshot(snapshot: dict, config: dict | None) -> dict | None:
    """Intenta invocar calcular_riesgo_global (v1) usando datos del snapshot.

    Si el snapshot no trae los inputs necesarios (artículos + temas +
    conflictos + pesos), devuelve None y la comparación se hace sin v1.
    """
    try:
        try:
            from .risk_score import calcular_riesgo_global
        except ImportError:
            from apurisk.analyzers.risk_score import calcular_riesgo_global

        articulos = (
            snapshot.get("articulos")
            or snapshot.get("articles")
            or snapshot.get("noticias")
            or []
        )
        temas = snapshot.get("temas") or {"conteos": {}}
        conflictos = snapshot.get("conflictos") or []
        cfg = config or {}
        pesos = cfg.get("indicadores_riesgo", {
            "estabilidad_gobierno": 0.16,
            "conflictos_sociales": 0.13,
            "violencia_electoral": 0.10,
            "riesgo_regulatorio": 0.08,
            "corrupcion": 0.10,
            "seguridad": 0.09,
            "intervencion_militar": 0.08,
            "tensiones_fronterizas": 0.07,
            "polarizacion": 0.06,
            "crisis_migratoria": 0.05,
            "tensiones_diplomaticas": 0.05,
            "presion_economica": 0.03,
        })
        return calcular_riesgo_global(articulos, temas, conflictos, pesos)
    except Exception:
        return None


def leer_scores_paralelos(
    archive: Any,
    dias: int = 14,
) -> list[dict]:
    """Devuelve los últimos `dias` registros de scores_paralelos.

    Más recientes primero. Lista vacía si archive es None o tabla vacía.
    """
    if archive is None:
        return []
    try:
        with archive._conn() as c:
            rows = c.execute(
                """
                SELECT fecha, generado_en,
                       score_v1, nivel_v1,
                       score_v2, nivel_v2,
                       score_v2_24h, score_v2_7d, score_v2_30d, score_v2_90d,
                       confidence_v2,
                       sub_scores_v2,
                       delta_v2_v1,
                       revision_decision, revision_humana, revision_fecha
                  FROM scores_paralelos
                 ORDER BY fecha DESC
                 LIMIT ?
                """,
                (dias,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def marcar_revision(
    archive: Any,
    fecha: str,
    decision: str,
    nota: str = "",
) -> bool:
    """Marca un día de scores_paralelos como revisado.

    Args:
        archive: ApuriskArchive
        fecha: 'YYYY-MM-DD' del día a marcar
        decision: 'aprobado' | 'rechazado' | 'pendiente'
        nota: comentario libre del analista
    """
    if archive is None or not fecha:
        return False
    if decision not in ("aprobado", "rechazado", "pendiente"):
        return False
    try:
        with archive._conn() as c:
            c.execute(
                """
                UPDATE scores_paralelos
                   SET revision_decision = ?,
                       revision_humana   = ?,
                       revision_fecha    = ?
                 WHERE fecha = ?
                """,
                (decision, nota or None,
                 datetime.now(timezone.utc).isoformat(),
                 fecha),
            )
            return c.total_changes > 0
    except Exception:
        return False


# =====================================================================
# WRAPPER · Confidence score (Sprint 1.7)
# =====================================================================
def _calcular_confidence(
    eventos: list[dict],
    n_articulos: int,
    archive: Any,
    cfg: dict,
    hoy: datetime | None = None,
) -> dict:
    """Wrapper que enriquece eventos con modificadores (si no lo están) y
    calcula el confidence_score. Aísla la importación para evitar dependencias
    circulares.
    """
    try:
        from .confidence_score import calcular_confidence_score
        from .event_modifiers import aplicar_modificadores_lista
    except ImportError:
        from apurisk.analyzers.confidence_score import calcular_confidence_score
        from apurisk.analyzers.event_modifiers import aplicar_modificadores_lista

    # Asegurar que los eventos tienen factor_fuente / factor_confirmacion
    # (puede que no los tengan si no pasaron por calcular_score_horizonte)
    eventos_enriquecidos = eventos
    if eventos and "factor_fuente" not in eventos[0]:
        eventos_enriquecidos = aplicar_modificadores_lista(eventos, archive=archive)

    # Permitir override de pesos vía config.yaml
    pesos_cfg = cfg.get("confidence_pesos") if cfg else None

    return calcular_confidence_score(
        eventos=eventos_enriquecidos,
        n_articulos_origen=n_articulos,
        archive=archive,
        pesos=pesos_cfg,
        hoy=hoy,
    )


# =====================================================================
# LECTURA DE SCORE PREVIO PARA SUAVIZADO (Sprint 1.6)
# =====================================================================
def _leer_score_ayer_archive(archive: Any, hoy: datetime | None = None) -> float | None:
    """Devuelve el score_v2 del día previo más reciente desde scores_paralelos.

    Estrategia:
      · Busca la fila más reciente con score_v2 NO NULL anterior a hoy
      · Si la última corrida fue hace >7 días, considera el suavizado
        como "primer ciclo" (devuelve None — no suavizar)
      · En cualquier error devuelve None (no suavizar)
    """
    if archive is None:
        return None
    if hoy is None:
        hoy = datetime.now(timezone.utc)
    fecha_hoy = hoy.strftime("%Y-%m-%d")
    try:
        with archive._conn() as c:
            row = c.execute(
                """
                SELECT fecha, score_v2
                  FROM scores_paralelos
                 WHERE fecha < ?
                   AND score_v2 IS NOT NULL
                 ORDER BY fecha DESC
                 LIMIT 1
                """,
                (fecha_hoy,),
            ).fetchone()
        if not row:
            return None
        # Verificar que no esté demasiado antiguo (>7 días)
        try:
            fecha_prev = datetime.strptime(row["fecha"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dias = (hoy - fecha_prev).days
            if dias > 7:
                return None
        except Exception:
            pass
        return float(row["score_v2"])
    except Exception:
        return None


def _extraer_noticias_de_snapshot(snapshot: dict) -> list[dict]:
    """Extrae lista uniforme de noticias del snapshot OSINT.

    El snapshot puede traer las noticias en distintas estructuras según el
    ciclo de captura. Esta función las normaliza a una sola lista para
    que el pipeline v2 reciba siempre el mismo formato.
    """
    if not snapshot:
        return []
    # Estructuras probadas a aceptar:
    for k in ("articulos", "articles", "noticias", "items"):
        v = snapshot.get(k)
        if isinstance(v, list):
            return v
    # Snapshot tipo {"data": {"items": [...]}}
    data = snapshot.get("data") or snapshot.get("snapshot_data")
    if isinstance(data, dict):
        for k in ("articulos", "articles", "noticias", "items"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


# =====================================================================
# COMPARACIÓN v1 ↔ v2 (para validación paralela 7 días)
# =====================================================================
def comparar_v1_v2(score_v1: dict, score_v2: dict,
                     hoy: datetime | None = None) -> dict:
    """Devuelve estructura comparativa lista para guardar en
    tabla scores_paralelos.

    Args:
        score_v1: output de calcular_riesgo_global() (legacy)
        score_v2: output de calcular_score_nacional_v2() (nuevo)
        hoy: timestamp de referencia (para tests deterministas)

    Returns:
        dict con campos para INSERT en scores_paralelos.
    """
    if hoy is None:
        hoy = datetime.now(timezone.utc)
    s1 = float(score_v1.get("global", 0))
    s2 = float(score_v2.get("score_nacional", 0))
    return {
        "fecha": hoy.strftime("%Y-%m-%d"),
        "generado_en": hoy.isoformat(),
        "score_v1": s1,
        "nivel_v1": score_v1.get("nivel"),
        "score_v2": s2,
        "nivel_v2": score_v2.get("label"),
        "score_v2_24h":  score_v2.get("horizontes", {}).get("h24", {}).get("score"),
        "score_v2_7d":   score_v2.get("horizontes", {}).get("h7d", {}).get("score"),
        "score_v2_30d":  score_v2.get("horizontes", {}).get("h30d", {}).get("score"),
        "score_v2_90d":  score_v2.get("horizontes", {}).get("h90d", {}).get("score"),
        "confidence_v2": score_v2.get("confidence", {}).get("score"),
        "sub_scores_v2": json.dumps(
            score_v2.get("sub_scores", {}), ensure_ascii=False
        ),
        "modificadores_v2": json.dumps(
            score_v2.get("modificadores", {}), ensure_ascii=False
        ),
        "delta_v2_v1": round(s2 - s1, 2),
        "explicacion": None,         # Sprint 1.8 lo llena con LLM
        "revision_humana": None,     # rellenado vía endpoint diagnóstico
        "revision_decision": "pendiente",
        "revision_fecha": None,
    }


# =====================================================================
# PERSISTENCIA · guardar comparación en tabla scores_paralelos
# =====================================================================
def guardar_comparacion(archive: Any, comparacion: dict) -> bool:
    """Inserta una fila en scores_paralelos. ON CONFLICT actualiza.

    Args:
        archive: ApuriskArchive con _conn() válido
        comparacion: dict salida de comparar_v1_v2()

    Returns:
        True si se persistió bien.
    """
    if archive is None:
        return False
    try:
        with archive._conn() as c:
            c.execute("""
                INSERT INTO scores_paralelos (
                    fecha, generado_en,
                    score_v1, nivel_v1,
                    score_v2, nivel_v2,
                    score_v2_24h, score_v2_7d, score_v2_30d, score_v2_90d,
                    confidence_v2,
                    sub_scores_v2, modificadores_v2,
                    delta_v2_v1, explicacion,
                    revision_humana, revision_decision, revision_fecha
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fecha) DO UPDATE SET
                    generado_en = excluded.generado_en,
                    score_v1 = excluded.score_v1,
                    nivel_v1 = excluded.nivel_v1,
                    score_v2 = excluded.score_v2,
                    nivel_v2 = excluded.nivel_v2,
                    score_v2_24h = excluded.score_v2_24h,
                    score_v2_7d  = excluded.score_v2_7d,
                    score_v2_30d = excluded.score_v2_30d,
                    score_v2_90d = excluded.score_v2_90d,
                    confidence_v2 = excluded.confidence_v2,
                    sub_scores_v2 = excluded.sub_scores_v2,
                    modificadores_v2 = excluded.modificadores_v2,
                    delta_v2_v1 = excluded.delta_v2_v1
            """, (
                comparacion["fecha"], comparacion["generado_en"],
                comparacion["score_v1"], comparacion["nivel_v1"],
                comparacion["score_v2"], comparacion["nivel_v2"],
                comparacion["score_v2_24h"], comparacion["score_v2_7d"],
                comparacion["score_v2_30d"], comparacion["score_v2_90d"],
                comparacion["confidence_v2"],
                comparacion["sub_scores_v2"], comparacion["modificadores_v2"],
                comparacion["delta_v2_v1"], comparacion["explicacion"],
                comparacion["revision_humana"], comparacion["revision_decision"],
                comparacion["revision_fecha"],
            ))
            return True
    except Exception as e:
        print(f"  [score_v2] error guardando comparación: {e}")
        return False
