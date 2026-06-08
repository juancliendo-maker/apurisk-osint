"""Confidence Score · Sprint 1.7.

Mide la **confianza analítica** del Score Nacional v2 con un valor 0-100 que
acompaña al reporte ejecutivo. Permite al cliente C-level diferenciar entre:

  · Riesgo alto con alta confianza  → decisión inmediata informada
  · Riesgo alto con baja confianza  → atención + verificación adicional
  · Riesgo bajo con baja información → no es seguridad, es opacidad
  · Riesgo moderado con señales contradictorias → revisar manualmente

5 COMPONENTES CANÓNICOS (del brief):

  1. calidad_fuentes            30%  — qué tan buenas son las fuentes del ciclo
  2. confirmacion_independiente 25%  — % de eventos con ≥2 fuentes
  3. consistencia_narrativa     20%  — concordancia entre fuentes del cluster
  4. datos_historicos           15%  — disponibilidad de baseline histórico
  5. baja_duplicacion           10%  — ratio de compresión del dedup

CLASIFICACIÓN:
  · 0-39   → BAJA  (advertir al lector)
  · 40-69  → MEDIA (uso ejecutivo con cautela)
  · 70-100 → ALTA  (decisión sin disclaimer adicional)

DOCTRINA:

  El confidence score NO modifica el score nacional. Es metadata que
  acompaña al output para que el cliente sepa cuánto confiar en el número.
  Un score nacional 70 con confianza 35 es muy distinto a 70 con confianza 85.
"""
from __future__ import annotations
from typing import Any
from datetime import datetime, timezone, timedelta


# =====================================================================
# PESOS CANÓNICOS (del brief del usuario)
# =====================================================================
PESOS_CONFIDENCE: dict[str, float] = {
    "calidad_fuentes":            0.30,
    "confirmacion_independiente": 0.25,
    "consistencia_narrativa":     0.20,
    "datos_historicos":           0.15,
    "baja_duplicacion":           0.10,
}


# =====================================================================
# COMPONENTE 1 · CALIDAD DE FUENTES (30%)
# =====================================================================
def _componente_calidad_fuentes(eventos: list[dict]) -> tuple[float, dict]:
    """Promedio de factor_fuente de los eventos del ciclo, normalizado a 0-100.

    Mapeo: factor_fuente 1.20 (oficial primaria) → 100
           factor_fuente 1.00 (medio nacional)   → 70
           factor_fuente 0.40 (red social NV)    → 25
           factor_fuente 0.20 (rumor anónimo)    → 0

    Fórmula: ((promedio - 0.20) / (1.20 - 0.20)) × 100  → [0, 100]

    Si no hay eventos → 0 (sin información para evaluar calidad).
    """
    if not eventos:
        return 0.0, {"factor_fuente_promedio": None, "n_eventos": 0,
                      "razon": "sin eventos en el ciclo"}

    factores = [
        float(ev.get("factor_fuente", 1.00))
        for ev in eventos
        if "factor_fuente" in ev
    ]
    if not factores:
        # Si los eventos no pasaron por modificadores (Sprint 1.5),
        # asumimos calidad media (medio_nacional = 1.00)
        factores = [1.00] * len(eventos)

    promedio = sum(factores) / len(factores)
    # Normalizar a 0-100 entre rumor (0.20) y oficial (1.20)
    score = max(0.0, min(100.0, (promedio - 0.20) / 1.00 * 100))

    return score, {
        "factor_fuente_promedio": round(promedio, 3),
        "n_eventos": len(eventos),
        "n_con_modificador": len(factores),
    }


# =====================================================================
# COMPONENTE 2 · CONFIRMACIÓN INDEPENDIENTE (25%)
# =====================================================================
def _componente_confirmacion(eventos: list[dict]) -> tuple[float, dict]:
    """% de eventos con n_fuentes ≥ 2 de tipos potencialmente distintos.

    Sprint 1.7 versión mínima: cuenta n_fuentes >= 2 como confirmado.
    Una versión futura (Sprint 4) puede exigir que las 2+ fuentes sean
    de TIPOS distintos (oficial + medio + agencia internacional, etc.).

    Score = (confirmados / total_eventos) × 100
    Bonus de +15 si más de la mitad están confirmados con n_fuentes ≥ 3.
    """
    if not eventos:
        return 0.0, {"n_eventos": 0, "razon": "sin eventos en el ciclo"}

    confirmados = sum(1 for ev in eventos if ev.get("n_fuentes", 1) >= 2)
    altamente_confirmados = sum(1 for ev in eventos if ev.get("n_fuentes", 1) >= 3)

    base = (confirmados / len(eventos)) * 100
    bonus = 15 if altamente_confirmados / len(eventos) > 0.5 else 0
    score = min(100.0, base + bonus)

    return score, {
        "n_eventos": len(eventos),
        "n_confirmados": confirmados,
        "n_altamente_confirmados": altamente_confirmados,
        "pct_confirmados": round(confirmados / len(eventos) * 100, 1),
    }


# =====================================================================
# COMPONENTE 3 · CONSISTENCIA NARRATIVA (20%)
# =====================================================================
def _componente_consistencia(eventos: list[dict]) -> tuple[float, dict]:
    """Concordancia entre fuentes del mismo cluster.

    Sprint 1.7 versión mínima: como las severidades de fuentes individuales
    no se rastrean separadamente tras el clustering, usamos como proxy:

      · factor_confirmacion promedio (refleja log(1+n_fuentes))
      · normalizado a 0-100: 1.00 (sin replicación) → 0
                              1.25 (replicación moderada) → 50
                              1.50 (replicación alta) → 100

    Una versión futura (con embeddings semánticos) puede medir
    desviación estándar de severidad reportada por cada fuente.

    Para eventos huérfanos (n_fuentes=1), asumimos consistencia 50
    (no es inconsistente, simplemente no tiene contraparte).
    """
    if not eventos:
        return 0.0, {"n_eventos": 0, "razon": "sin eventos en el ciclo"}

    factores_conf = [
        float(ev.get("factor_confirmacion", 1.00))
        for ev in eventos
    ]
    promedio_conf = sum(factores_conf) / len(factores_conf)
    # Mapear: 1.00 → 0, 1.25 → 50, 1.50 → 100
    score = max(0.0, min(100.0, (promedio_conf - 1.00) / 0.50 * 100))

    # Si hay eventos huérfanos (n_fuentes=1), asignar consistencia neutra
    huérfanos = sum(1 for ev in eventos if ev.get("n_fuentes", 1) == 1)
    if huérfanos == len(eventos):
        # Todos huérfanos: confianza media (50)
        score = 50.0

    return score, {
        "factor_confirmacion_promedio": round(promedio_conf, 3),
        "n_eventos": len(eventos),
        "n_huerfanos": huérfanos,
    }


# =====================================================================
# COMPONENTE 4 · DATOS HISTÓRICOS (15%)
# =====================================================================
def _componente_datos_historicos(archive: Any,
                                    hoy: datetime | None = None,
                                    dias_objetivo: int = 28) -> tuple[float, dict]:
    """Días de archive disponibles, normalizado a `dias_objetivo` (28 default).

    Lee scores_paralelos y cuenta días distintos con score_v2 NO NULL en los
    últimos `dias_objetivo` días. Más días = más capacidad de calcular
    baseline robusto = mayor confianza.

    Si archive no disponible o tabla vacía → 0.
    Cap en 100 cuando se alcanza dias_objetivo.
    """
    if archive is None:
        return 0.0, {"razon": "archive no disponible", "dias_disponibles": 0}

    if hoy is None:
        hoy = datetime.now(timezone.utc)

    try:
        fecha_corte = (hoy - timedelta(days=dias_objetivo)).strftime("%Y-%m-%d")
        with archive._conn() as c:
            row = c.execute(
                """
                SELECT COUNT(DISTINCT fecha) as n
                  FROM scores_paralelos
                 WHERE score_v2 IS NOT NULL
                   AND fecha >= ?
                """,
                (fecha_corte,),
            ).fetchone()
        dias = int(row["n"]) if row else 0
        score = min(100.0, (dias / dias_objetivo) * 100)
        return score, {
            "dias_disponibles": dias,
            "dias_objetivo": dias_objetivo,
            "ratio": round(dias / dias_objetivo, 2),
        }
    except Exception as e:
        return 0.0, {"razon": f"error consultando archive: {e}"}


# =====================================================================
# COMPONENTE 5 · BAJA DUPLICACIÓN (10%)
# =====================================================================
def _componente_baja_duplicacion(n_eventos: int,
                                    n_articulos: int) -> tuple[float, dict]:
    """Ratio de compresión del clustering (más compresión = más limpio).

    Fórmula: ratio_compresion = 1 - n_eventos/n_articulos
    Mapeo:   0% comprimido (1:1, sin dedup posible) → 30
             50% comprimido                          → 65
             85% comprimido                          → 100

    PENALIZACIÓN: si n_articulos < 5, capamos en 60 porque no hay volumen
    suficiente para evaluar duplicación con sentido.
    """
    if n_articulos <= 0:
        return 0.0, {"razon": "sin artículos en el ciclo"}

    ratio = 1.0 - (n_eventos / max(1, n_articulos))
    # Linear: 0 → 30, 1 → 100
    score = 30 + (ratio * 70)

    if n_articulos < 5:
        score = min(score, 60.0)
        razon_cap = f"penalizado (n_articulos={n_articulos} < 5)"
    else:
        razon_cap = None

    return score, {
        "ratio_compresion": round(ratio, 3),
        "n_eventos": n_eventos,
        "n_articulos": n_articulos,
        "cap_aplicado": razon_cap,
    }


# =====================================================================
# COMPOSITE · cálculo final
# =====================================================================

def calcular_confidence_score(
    eventos: list[dict],
    n_articulos_origen: int,
    archive: Any = None,
    pesos: dict | None = None,
    hoy: datetime | None = None,
) -> dict:
    """Calcula el confidence_score 0-100 con los 5 componentes.

    Args:
        eventos: lista de eventos dedupeados (con modificadores ya aplicados)
        n_articulos_origen: cuántos artículos crudos había antes del dedup
        archive: ApuriskArchive para componente datos_historicos
        pesos: override de los pesos canónicos (debe sumar 1.0)
        hoy: timestamp de referencia (para tests)

    Returns:
        dict con:
          · score: 0-100
          · nivel: 'baja' | 'media' | 'alta'
          · etiqueta: 'BAJA' | 'MEDIA' | 'ALTA'
          · componentes: {nombre: {score, peso, metadata}}
    """
    pesos = pesos or PESOS_CONFIDENCE

    cf, m_cf = _componente_calidad_fuentes(eventos)
    ci, m_ci = _componente_confirmacion(eventos)
    cn, m_cn = _componente_consistencia(eventos)
    dh, m_dh = _componente_datos_historicos(archive, hoy=hoy)
    bd, m_bd = _componente_baja_duplicacion(len(eventos), n_articulos_origen)

    componentes = {
        "calidad_fuentes":            {"score": round(cf, 1), "peso": pesos["calidad_fuentes"],            "metadata": m_cf},
        "confirmacion_independiente": {"score": round(ci, 1), "peso": pesos["confirmacion_independiente"], "metadata": m_ci},
        "consistencia_narrativa":     {"score": round(cn, 1), "peso": pesos["consistencia_narrativa"],     "metadata": m_cn},
        "datos_historicos":           {"score": round(dh, 1), "peso": pesos["datos_historicos"],           "metadata": m_dh},
        "baja_duplicacion":           {"score": round(bd, 1), "peso": pesos["baja_duplicacion"],           "metadata": m_bd},
    }

    score = sum(c["score"] * c["peso"] for c in componentes.values())
    score = round(max(0.0, min(100.0, score)), 1)

    if score < 40:
        nivel = "baja"
        etiqueta = "BAJA"
    elif score < 70:
        nivel = "media"
        etiqueta = "MEDIA"
    else:
        nivel = "alta"
        etiqueta = "ALTA"

    return {
        "score": score,
        "nivel": nivel,
        "etiqueta": etiqueta,
        "componentes": componentes,
    }
