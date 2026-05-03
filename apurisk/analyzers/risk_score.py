"""Cálculo de score de riesgo político global (0-100)."""
from __future__ import annotations
from .sentiment import analizar_sentimiento


def _normalizar(v: float, max_v: float) -> float:
    if max_v <= 0:
        return 0.0
    return min(100.0, (v / max_v) * 100.0)


def calcular_riesgo_global(articles: list, temas: dict, conflictos: list, pesos: dict) -> dict:
    """Calcula score 0-100 por categoría y global ponderado.

    Heurística MVP:
      - estabilidad_gobierno: menciones de vacancia/censura/renuncia normalizado.
      - conflictos_sociales: nº conflictos activos altos + menciones.
      - riesgo_regulatorio: menciones de reforma/decreto/PL controvertidos.
      - polarizacion: razón polarización-keywords / total_artículos.
      - corrupcion: menciones corrupción / total.
      - seguridad: menciones seguridad / total.
    """
    n = max(1, len(articles))
    conteos = temas.get("conteos", {})

    activos_alta = sum(
        1 for c in conflictos
        if (c.raw or {}).get("severidad") == "alta"
        and (c.raw or {}).get("estado") == "activo"
    )

    cat_scores = {
        "estabilidad_gobierno": _normalizar(conteos.get("estabilidad_gobierno", 0), n * 0.5),
        "conflictos_sociales": min(100.0, activos_alta * 15.0 + conteos.get("conflictos_sociales", 0) * 5.0),
        "riesgo_regulatorio": _normalizar(conteos.get("riesgo_regulatorio", 0), n * 0.4),
        "polarizacion": _normalizar(conteos.get("polarizacion", 0), n * 0.3),
        "corrupcion": _normalizar(conteos.get("corrupcion", 0), n * 0.4),
        "seguridad": _normalizar(conteos.get("seguridad", 0), n * 0.3),
    }

    # ajuste por sentimiento agregado
    avg_sent = 0.0
    if articles:
        scores = [analizar_sentimiento((a.title or "") + " " + (a.summary or ""))["score"] for a in articles]
        avg_sent = sum(scores) / len(scores)
    # sentimiento muy negativo penaliza global
    sentiment_factor = max(0.0, -avg_sent) * 15.0  # hasta +15 puntos

    global_score = 0.0
    for cat, peso in pesos.items():
        global_score += cat_scores.get(cat, 0.0) * peso
    global_score = min(100.0, global_score + sentiment_factor)

    if global_score >= 70:
        nivel = "ALTO"
    elif global_score >= 45:
        nivel = "MEDIO"
    else:
        nivel = "BAJO"

    return {
        "global": round(global_score, 1),
        "nivel": nivel,
        "categorias": {k: round(v, 1) for k, v in cat_scores.items()},
        "sentimiento_promedio": round(avg_sent, 3),
        "ajuste_sentimiento": round(sentiment_factor, 1),
    }
