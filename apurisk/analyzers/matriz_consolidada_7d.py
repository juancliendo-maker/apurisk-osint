"""Matriz P×I 7 días Consolidada · Sprint Bloque 2.

Agrega los factores de riesgo de las últimas N corridas diarias y construye
una vista "foto del periodo" para el Reporte Semanal y dashboard.

Para cada factor de riesgo único (factor_id), calcula sobre los últimos 7 días:

  · prob_media       (probabilidad promedio)
  · prob_max         (probabilidad pico observada)
  · impacto_media    (suele ser constante por factor)
  · score_media      (media geométrica P×I promediada)
  · score_max        (pico de score en el periodo)
  · score_p90        (percentil 90 — robusto a outliers)
  · tendencia_slope  (regresión lineal sobre los 7 puntos)
  · tendencia_label  ('escalada', 'ascenso', 'estable', 'descenso', 'caida')
  · velocidad        (Δ del último día vs el anterior)
  · n_apariciones    (cuántos de los 7 días el factor estuvo presente)
  · serie            (lista [score_dia1, score_dia2, ..., score_dia7])

El resultado se ordena por score_media descendente.

Usado por:
  - Vista /matriz-7d del dashboard analyst (visual)
  - Endpoint /api/matriz/consolidada-7d (JSON crudo)
  - Strategic Weekly Outlook PDF (cuando lo construyamos)
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
import statistics


def _categorizar_tendencia(slope: float, score_media: float) -> tuple[str, str]:
    """Slope (puntos/día) + score base → etiqueta + flecha visual."""
    if slope >= 4.0:
        return ("escalada", "⇈")
    if slope >= 1.5:
        return ("ascenso", "↑")
    if slope >= -1.5:
        return ("estable", "→")
    if slope >= -4.0:
        return ("descenso", "↓")
    return ("caida", "⇊")


def _slope(serie: list[float]) -> float:
    """Pendiente lineal (mínimos cuadrados) sobre puntos consecutivos.

    Asume separación de 1 unidad (1 día). Devuelve puntos/día.
    """
    n = len(serie)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(serie) / n
    num = sum((xs[i] - mean_x) * (serie[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


def _percentil(valores: list[float], p: float) -> float:
    """Percentil simple por interpolación lineal (p en 0..100)."""
    if not valores:
        return 0.0
    if len(valores) == 1:
        return valores[0]
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ordenados) - 1)
    if f == c:
        return ordenados[f]
    d0 = ordenados[f] * (c - k)
    d1 = ordenados[c] * (k - f)
    return d0 + d1


def construir_matriz_consolidada_7d(
    archive: Any,
    dias: int = 7,
    top_n: int | None = None,
    hoy: datetime | None = None,
) -> dict:
    """Construye la matriz consolidada del periodo.

    Args:
        archive: ApuriskArchive con tabla `factores` poblada
        dias: cantidad de días hacia atrás (default 7)
        top_n: si != None, devuelve solo los top N factores por score_media
        hoy: timestamp de referencia (para tests deterministas)

    Returns:
        {
          "periodo": {"desde": "YYYY-MM-DD", "hasta": "YYYY-MM-DD", "dias": 7},
          "n_factores": int,
          "n_corridas": int,
          "factores": [
            {
              "factor_id", "nombre", "categoria",
              "prob_media", "prob_max", "impacto_media",
              "score_media", "score_max", "score_p90",
              "tendencia_slope", "tendencia_label", "tendencia_arrow",
              "velocidad",
              "n_apariciones",
              "serie": [scores ordenados antiguo→reciente],
              "nivel_consolidado": "BAJO|MEDIO|ALTO|CRÍTICO",
            },
            ...
          ]
        }
    """
    if hoy is None:
        hoy = datetime.now(timezone.utc)

    fecha_corte = (hoy - timedelta(days=dias)).strftime("%Y-%m-%d")

    if archive is None:
        return {
            "periodo": {"desde": fecha_corte, "hasta": hoy.strftime("%Y-%m-%d"), "dias": dias},
            "n_factores": 0,
            "n_corridas": 0,
            "factores": [],
            "error": "archive no disponible",
        }

    try:
        with archive._conn() as c:
            # Obtener corridas únicas (1 por día — toma la más reciente del día)
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
                return {
                    "periodo": {"desde": fecha_corte, "hasta": hoy.strftime("%Y-%m-%d"), "dias": dias},
                    "n_factores": 0,
                    "n_corridas": 0,
                    "factores": [],
                }

            # Mantener solo la última corrida por día (cierre del día)
            por_dia: dict[str, dict] = {}
            for r in corridas:
                fecha = r["fecha"]
                if fecha not in por_dia or r["generado"] > por_dia[fecha]["generado"]:
                    por_dia[fecha] = {"id": r["id"], "generado": r["generado"]}

            fechas_orden = sorted(por_dia.keys())
            snapshot_ids = [por_dia[f]["id"] for f in fechas_orden]

            # Trae todos los factores de esas corridas
            placeholders = ",".join("?" * len(snapshot_ids))
            rows = c.execute(
                f"""
                SELECT s.id AS snap_id, substr(s.generado, 1, 10) AS fecha,
                       f.factor_id, f.nombre, f.categoria,
                       f.probabilidad, f.impacto, f.score, f.nivel,
                       f.menciones_24h
                  FROM factores f
                  JOIN snapshots s ON s.id = f.snapshot_id
                 WHERE s.id IN ({placeholders})
                """,
                snapshot_ids,
            ).fetchall()
    except Exception as e:
        return {
            "periodo": {"desde": fecha_corte, "hasta": hoy.strftime("%Y-%m-%d"), "dias": dias},
            "n_factores": 0,
            "n_corridas": 0,
            "factores": [],
            "error": f"{type(e).__name__}: {e}",
        }

    # Agrupar por factor_id
    por_factor: dict[str, dict] = {}
    for r in rows:
        fid = r["factor_id"] or "sin_id"
        if fid not in por_factor:
            por_factor[fid] = {
                "factor_id": fid,
                "nombre": r["nombre"] or fid,
                "categoria": r["categoria"] or "",
                "_por_fecha": {},  # fecha → {prob, impacto, score, nivel}
            }
        por_factor[fid]["_por_fecha"][r["fecha"]] = {
            "prob": r["probabilidad"] or 0,
            "impacto": r["impacto"] or 0,
            "score": r["score"] or 0.0,
            "nivel": r["nivel"] or "",
        }

    # Construir agregados
    factores = []
    for fid, info in por_factor.items():
        probs = [info["_por_fecha"][f]["prob"] for f in fechas_orden if f in info["_por_fecha"]]
        impactos = [info["_por_fecha"][f]["impacto"] for f in fechas_orden if f in info["_por_fecha"]]
        scores = [info["_por_fecha"][f]["score"] for f in fechas_orden if f in info["_por_fecha"]]
        serie = [info["_por_fecha"][f]["score"] if f in info["_por_fecha"] else 0.0
                  for f in fechas_orden]

        if not scores:
            continue

        slope = _slope(serie)
        tendencia_label, tendencia_arrow = _categorizar_tendencia(slope, statistics.mean(scores))

        velocidad = 0.0
        if len(serie) >= 2:
            velocidad = round(serie[-1] - serie[-2], 1)

        score_media = round(statistics.mean(scores), 1)
        # Nivel consolidado por score_media (misma escala que individual)
        if score_media >= 70:
            nivel_consolidado = "CRÍTICO"
        elif score_media >= 55:
            nivel_consolidado = "ALTO"
        elif score_media >= 35:
            nivel_consolidado = "MEDIO"
        else:
            nivel_consolidado = "BAJO"

        factores.append({
            "factor_id": info["factor_id"],
            "nombre": info["nombre"],
            "categoria": info["categoria"],
            "prob_media": round(statistics.mean(probs), 1),
            "prob_max": max(probs),
            "impacto_media": round(statistics.mean(impactos), 1),
            "score_media": score_media,
            "score_max": round(max(scores), 1),
            "score_p90": round(_percentil(scores, 90), 1),
            "tendencia_slope": round(slope, 2),
            "tendencia_label": tendencia_label,
            "tendencia_arrow": tendencia_arrow,
            "velocidad": velocidad,
            "n_apariciones": len(scores),
            "serie": [round(s, 1) for s in serie],
            "nivel_consolidado": nivel_consolidado,
        })

    # Ordenar por score_media descendente
    factores.sort(key=lambda x: x["score_media"], reverse=True)

    if top_n:
        factores = factores[:top_n]

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
    }
