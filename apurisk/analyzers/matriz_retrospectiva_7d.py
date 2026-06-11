"""Matriz Retrospectiva 7 días · P×I + Vectores de Movimiento.

Para cada factor de riesgo del archive, calcula la TENDENCIA DIRECCIONAL
entre hoy y hace 7 días con 6 métricas más un score composite.

DIFERENCIA con matriz_consolidada_7d:
  · consolidada → "foto agregada" del periodo (media + máx + p90)
  · retrospectiva → "movimiento direccional" (ΔP, ΔI, velocidad, consistencia)

Ambas coexisten porque sirven a preguntas distintas:
  · consolidada responde: "¿cuál fue el estado promedio del periodo?"
  · retrospectiva responde: "¿qué se está moviendo y en qué dirección?"

FÓRMULAS (auditables, expuestas en el dashboard):

  ΔP  = P_actual − P_hace_7d                          (delta probabilidad)
  ΔI  = I_actual − I_hace_7d                          (delta impacto)
  VT  = (Score_actual − Score_hace_7d) / 7            (velocidad, puntos/día)
  CT  = 1 − (σ(serie_7d) / μ(serie_7d))               (consistencia 0-1)
  MC  = ΔP × 0.55 + ΔI × 0.45                         (magnitud composite)
  STF = MC × 0.60 + VT × 7 × 0.20 + CT × 100 × 0.05
        + Score_actual × 0.15                         (score final tendencia)

UMBRALES (cada 10 puntos):
  STF ≥ +20  → ESCALANDO    (rojo)
  STF ≥ +10  → SUBIDA       (naranja)
  STF ≥ −10  → ESTABLE      (gris)
  STF ≥ −20  → DESCENSO     (verde-lima)
  STF < −20  → ATENUÁNDOSE  (verde)
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
import statistics


# =====================================================================
# CONSTANTES DE FÓRMULAS
# =====================================================================

# Pesos del score MC (magnitud composite)
PESO_DELTA_P = 0.55
PESO_DELTA_I = 0.45

# Pesos del score STF (tendencia final) — calibración v2
# Eliminado Score_actual para que STF mida TENDENCIA PURA, no nivel actual.
# Esto evita el sesgo positivo que tenía la versión con Score_actual × 0.15.
PESO_MC          = 0.70   # magnitud composite (cuánto cambió P×I)
PESO_VT          = 0.25   # velocidad (puntos/día)
PESO_CT          = 0.05   # consistencia (cuán limpia la tendencia)
PESO_SCORE_HOY   = 0.00   # eliminado en calibración v2

# Multiplicadores para escalar VT y CT al rango de MC y Score
FACTOR_VT = 7        # VT * 7 días → magnitud comparable
FACTOR_CT = 100      # CT 0-1 → 0-100

# Umbrales semáforo (5 niveles cada 10 puntos)
UMBRAL_ESCALANDO    = +20
UMBRAL_SUBIDA       = +10
UMBRAL_ESTABLE_INF  = -10
UMBRAL_DESCENSO_INF = -20

# Colores canónicos (alineados con Plantilla Madre)
COLOR_ESCALANDO    = "#dc2626"  # rojo intenso
COLOR_SUBIDA       = "#f97316"  # naranja
COLOR_ESTABLE      = "#94a3b8"  # gris neutro
COLOR_DESCENSO     = "#84cc16"  # verde-lima
COLOR_ATENUANDOSE  = "#22c55e"  # verde


# =====================================================================
# CÁLCULO DE MÉTRICAS POR FACTOR
# =====================================================================

def calcular_metricas_factor(serie: list[dict]) -> dict:
    """Calcula las 6 métricas de tendencia para un factor a partir de su serie.

    Args:
        serie: lista de registros ordenados ANTIGUO → RECIENTE, cada uno con:
               {prob, impacto, score, fecha}

    Returns:
        dict con ΔP, ΔI, VT, CT, MC, STF, tendencia_label, tendencia_color
    """
    if not serie or len(serie) < 2:
        return _metricas_vacias()

    primero = serie[0]
    ultimo = serie[-1]
    n = len(serie)

    p_actual = float(ultimo.get("prob", 0))
    p_hace_7d = float(primero.get("prob", 0))
    delta_p = p_actual - p_hace_7d

    i_actual = float(ultimo.get("impacto", 0))
    i_hace_7d = float(primero.get("impacto", 0))
    delta_i = i_actual - i_hace_7d

    score_actual = float(ultimo.get("score", 0))
    score_hace_7d = float(primero.get("score", 0))

    # Velocidad puntos/día
    dias_entre = max(1, n - 1)
    vt = (score_actual - score_hace_7d) / dias_entre

    # Consistencia: 1 - (σ/μ)
    scores = [float(r.get("score", 0)) for r in serie]
    mu = statistics.mean(scores) if scores else 0.0
    if mu > 0 and len(scores) > 1:
        sigma = statistics.stdev(scores)
        ct = max(0.0, min(1.0, 1 - (sigma / mu)))
    else:
        ct = 1.0  # serie constante o vacía → consistencia máxima por convención

    # Magnitud composite (MC)
    mc = delta_p * PESO_DELTA_P + delta_i * PESO_DELTA_I

    # Score Final de Tendencia (STF) — TENDENCIA PURA (sin nivel actual)
    stf = (
        mc * PESO_MC
        + vt * FACTOR_VT * PESO_VT
        + ct * FACTOR_CT * PESO_CT
    )

    tendencia = clasificar_tendencia(stf)

    return {
        "p_actual": round(p_actual, 1),
        "p_hace_7d": round(p_hace_7d, 1),
        "delta_p": round(delta_p, 1),

        "i_actual": round(i_actual, 1),
        "i_hace_7d": round(i_hace_7d, 1),
        "delta_i": round(delta_i, 1),

        "score_actual": round(score_actual, 1),
        "score_hace_7d": round(score_hace_7d, 1),

        "vt": round(vt, 2),
        "ct": round(ct, 3),
        "mc": round(mc, 1),
        "stf": round(stf, 1),

        "tendencia_label": tendencia["label"],
        "tendencia_id": tendencia["id"],
        "tendencia_color": tendencia["color"],

        "serie_scores": [round(float(r.get("score", 0)), 1) for r in serie],
        "n_corridas": n,
    }


def _metricas_vacias() -> dict:
    """Metadata por defecto cuando un factor no tiene suficiente historia."""
    return {
        "p_actual": 0, "p_hace_7d": 0, "delta_p": 0,
        "i_actual": 0, "i_hace_7d": 0, "delta_i": 0,
        "score_actual": 0, "score_hace_7d": 0,
        "vt": 0, "ct": 0, "mc": 0, "stf": 0,
        "tendencia_label": "SIN HISTORIA",
        "tendencia_id": "sin_historia",
        "tendencia_color": "#475569",
        "serie_scores": [],
        "n_corridas": 0,
    }


def clasificar_tendencia(stf: float) -> dict:
    """STF → {id, label, color}."""
    if stf >= UMBRAL_ESCALANDO:
        return {"id": "escalando", "label": "ESCALANDO", "color": COLOR_ESCALANDO}
    if stf >= UMBRAL_SUBIDA:
        return {"id": "subida", "label": "SUBIDA", "color": COLOR_SUBIDA}
    if stf >= UMBRAL_ESTABLE_INF:
        return {"id": "estable", "label": "ESTABLE", "color": COLOR_ESTABLE}
    if stf >= UMBRAL_DESCENSO_INF:
        return {"id": "descenso", "label": "DESCENSO", "color": COLOR_DESCENSO}
    return {"id": "atenuandose", "label": "ATENUÁNDOSE", "color": COLOR_ATENUANDOSE}


# =====================================================================
# CONSTRUCCIÓN DESDE EL ARCHIVE
# =====================================================================

def construir_matriz_retrospectiva_7d(
    archive: Any,
    dias: int = 7,
    hoy: datetime | None = None,
) -> dict:
    """Construye la matriz retrospectiva consultando el archive SQLite.

    Para cada factor de riesgo presente en las últimas N corridas (1 por día),
    aplica calcular_metricas_factor() y ordena por |STF| descendente
    (los más movidos primero, sea en una dirección u otra).

    Returns:
        {
          "periodo": {...},
          "n_factores": int,
          "n_corridas": int,
          "factores": [...],          ← ordenados por |STF| desc
          "top_movedores": {
              "escalando": [factores],
              "atenuandose": [factores],
          },
          "formulas": {...},
        }
    """
    if hoy is None:
        hoy = datetime.now(timezone.utc)

    fecha_corte = (hoy - timedelta(days=dias)).strftime("%Y-%m-%d")

    if archive is None:
        return _resultado_vacio(fecha_corte, hoy, "archive no disponible")

    try:
        with archive._conn() as c:
            corridas = c.execute(
                """
                SELECT id, substr(generado, 1, 10) AS fecha, generado
                  FROM snapshots
                 WHERE substr(generado, 1, 10) >= ?
                 ORDER BY generado ASC
                """,
                (fecha_corte,),
            ).fetchall()

            if not corridas:
                return _resultado_vacio(fecha_corte, hoy)

            # Una corrida por día (la más reciente del día)
            por_dia: dict[str, dict] = {}
            for r in corridas:
                fecha = r["fecha"]
                if fecha not in por_dia or r["generado"] > por_dia[fecha]["generado"]:
                    por_dia[fecha] = {"id": r["id"], "generado": r["generado"]}

            fechas_orden = sorted(por_dia.keys())
            snapshot_ids = [por_dia[f]["id"] for f in fechas_orden]
            placeholders = ",".join("?" * len(snapshot_ids))
            rows = c.execute(
                f"""
                SELECT s.id AS snap_id, substr(s.generado, 1, 10) AS fecha,
                       f.factor_id, f.nombre, f.categoria,
                       f.probabilidad, f.impacto, f.score, f.nivel
                  FROM factores f
                  JOIN snapshots s ON s.id = f.snapshot_id
                 WHERE s.id IN ({placeholders})
                """,
                snapshot_ids,
            ).fetchall()
    except Exception as e:
        return _resultado_vacio(fecha_corte, hoy, f"{type(e).__name__}: {e}")

    # Agrupar por factor_id
    por_factor: dict[str, dict] = {}
    for r in rows:
        fid = r["factor_id"] or "sin_id"
        if fid not in por_factor:
            por_factor[fid] = {
                "factor_id": fid,
                "nombre": r["nombre"] or fid,
                "categoria": r["categoria"] or "",
                "_serie": {},  # fecha → registro
            }
        por_factor[fid]["_serie"][r["fecha"]] = {
            "prob": r["probabilidad"] or 0,
            "impacto": r["impacto"] or 0,
            "score": r["score"] or 0.0,
            "fecha": r["fecha"],
        }

    factores = []
    for fid, info in por_factor.items():
        serie_ordenada = [info["_serie"][f] for f in fechas_orden if f in info["_serie"]]
        if len(serie_ordenada) < 2:
            continue  # sin suficiente historia para calcular tendencia

        metricas = calcular_metricas_factor(serie_ordenada)
        factor_completo = {
            "factor_id": info["factor_id"],
            "nombre": info["nombre"],
            "categoria": info["categoria"],
            **metricas,
        }
        factores.append(factor_completo)

    # Ordenar por |STF| descendente — los más movidos arriba
    factores.sort(key=lambda x: abs(x["stf"]), reverse=True)

    # Top movedores
    escalando = [f for f in factores if f["tendencia_id"] in ("escalando", "subida")][:5]
    atenuandose = [f for f in factores if f["tendencia_id"] in ("atenuandose", "descenso")][:5]

    return {
        "periodo": {
            "desde": fechas_orden[0] if fechas_orden else fecha_corte,
            "hasta": fechas_orden[-1] if fechas_orden else hoy.strftime("%Y-%m-%d"),
            "dias": dias,
            "fechas": fechas_orden,
        },
        "n_factores": len(factores),
        "n_corridas": len(fechas_orden),
        "factores": factores,
        "top_movedores": {
            "escalando": escalando,
            "atenuandose": atenuandose,
        },
        "formulas": {
            "delta_p": "ΔP = P_actual − P_hace_7d",
            "delta_i": "ΔI = I_actual − I_hace_7d",
            "vt": "VT = (Score_actual − Score_hace_7d) / 7  [puntos/día]",
            "ct": "CT = 1 − (σ(serie_7d) / μ(serie_7d))  [0–1]",
            "mc": f"MC = ΔP × {PESO_DELTA_P} + ΔI × {PESO_DELTA_I}",
            "stf": (
                f"STF = MC × {PESO_MC} + VT × {FACTOR_VT} × {PESO_VT} "
                f"+ CT × {FACTOR_CT} × {PESO_CT}    [tendencia pura, sin nivel actual]"
            ),
            "umbrales": {
                f"STF ≥ +{UMBRAL_ESCALANDO}": "ESCALANDO",
                f"STF ≥ +{UMBRAL_SUBIDA}": "SUBIDA",
                f"STF ≥ {UMBRAL_ESTABLE_INF}": "ESTABLE",
                f"STF ≥ {UMBRAL_DESCENSO_INF}": "DESCENSO",
                f"STF < {UMBRAL_DESCENSO_INF}": "ATENUÁNDOSE",
            },
        },
    }


def _resultado_vacio(fecha_corte: str, hoy: datetime, error: str | None = None) -> dict:
    resp = {
        "periodo": {
            "desde": fecha_corte,
            "hasta": hoy.strftime("%Y-%m-%d"),
            "dias": 7,
            "fechas": [],
        },
        "n_factores": 0,
        "n_corridas": 0,
        "factores": [],
        "top_movedores": {"escalando": [], "atenuandose": []},
        "formulas": {},
    }
    if error:
        resp["error"] = error
    return resp
