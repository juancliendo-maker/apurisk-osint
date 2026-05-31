"""Estado de Derecho Index (EDI) — Nivel 3 de la doctrina APURISK.

Mide la "salud institucional" del Perú en una escala 0-100 donde:
  - 100 = Sistema funcionando óptimamente (TC con quorum, JNJ activa,
          Contraloría auditando, marco regulatorio estable)
  - 0   = Colapso institucional (parálisis del eje de control)

Compuesto ponderado de 4 sub-componentes:

  1. Independencia Judicial      (30%) — TC, PJ, Corte Suprema
  2. Capacidad de Control        (25%) — JNJ, Contraloría, Defensoría, Fiscalía
  3. Estabilidad Normativa       (25%) — Reformas, vacancia, gabinete, regulación
  4. Convergencia de Crisis      (20%) — I&W, convergencias estructurales

El EDI NO se calcula diariamente porque sería ruido. Se calcula con
ventana móvil de 7 días (snapshot) o se serializa con muestreo diario
para series de 14, 30, 90 días.

Cada cálculo retorna además:
  - Banda de confianza ± basada en varianza últimos 7 días
  - Top 3 drivers (eventos que más impactaron el score)
  - Tendencia vs 7 días atrás (↑/↓/→)
"""
from __future__ import annotations
import math
import statistics
from datetime import datetime, timedelta, timezone


# ==========================================================================
# CONFIGURACIÓN DE PONDERACIONES Y BASELINES
# ==========================================================================

PESOS_SUBCOMPONENTES = {
    "independencia_judicial":  0.30,
    "capacidad_control":       0.25,
    "estabilidad_normativa":   0.25,
    "convergencia_crisis":     0.20,
}

# Estado base de cada sub-componente cuando no hay eventos negativos
# (refleja la "salud institucional estructural" de Perú en 2026)
BASELINE_SUBCOMPONENTES = {
    "independencia_judicial":  78,  # Estructura razonable pero presiones recurrentes
    "capacidad_control":       72,  # Contraloría y Defensoría activas, JNJ con tensiones
    "estabilidad_normativa":   70,  # Marco regulatorio estable pero año electoral
    "convergencia_crisis":     82,  # Convergencias son evento, no estado
}

# Factores P×I → sub-componente al que afectan + peso
# (un factor puede afectar a varios sub-componentes simultáneamente)
MAPEO_FACTORES_SUBCOMPONENTE = {
    "crisis_tc":                 [("independencia_judicial", 1.0)],
    "crisis_pj_corte_suprema":   [("independencia_judicial", 0.7)],
    "crisis_organos_control":    [("capacidad_control", 1.0)],
    "investigacion_corrupcion":  [("capacidad_control", 0.4)],
    "corrupcion_sistemica":      [("capacidad_control", 0.5),
                                   ("independencia_judicial", 0.3)],
    "regulacion_sectorial":      [("estabilidad_normativa", 0.6)],
    "reforma_electoral":         [("estabilidad_normativa", 0.7)],
    "presion_economica":         [("estabilidad_normativa", 0.5)],
    "vacancia_presidencial":     [("estabilidad_normativa", 0.8)],
    "censura_gabinete":          [("estabilidad_normativa", 0.5)],
    "renuncia_ministro":         [("estabilidad_normativa", 0.3)],
}

# Reglas de alertas críticas que penalizan cada sub-componente.
# Formato: (regla, k_coef, cap_max).
# La penalización es: min(cap, k_coef * log(1 + n_alertas_unicas)).
# k_coef calibrado para que con ~30 alertas únicas llegue a ~75% del cap.
# (Recordatorio: las alertas vienen DEDUPLICADAS desde _obtener_alertas_7d)
REGLAS_PENALIZACION = {
    "independencia_judicial": [
        # k=3.5 con cap=18 da: 5→-6.3, 30→-12, 100→-16, 200→cap.
        # Eso preserva discriminación entre crisis moderada vs catastrófica.
        ("CRISIS_TRIBUNAL_CONSTITUCIONAL", 3.5, 18),
        ("CRISIS_PODER_JUDICIAL",          2.8, 12),
        ("CRISIS_INSTITUCIONAL_JUDICIAL",  2.0, 10),  # backward compat
    ],
    "capacidad_control": [
        ("CRISIS_ORGANOS_CONTROL",         3.5, 18),
        ("CORRUPCION_SISTEMICA",           2.2, 10),
        ("INVESTIGACION_FORMAL",           1.5,  8),
    ],
    "estabilidad_normativa": [
        ("VACANCIA_ACTIVADA",              2.8, 14),
        ("CENSURA_GABINETE",               2.3,  9),
        ("RENUNCIA_MINISTRO",              2.0,  8),
        ("REFORMA_INSTITUCIONAL",          1.5,  6),
    ],
    "convergencia_crisis": [],  # convergencias se computan aparte
}


# ==========================================================================
# CÁLCULO DE SUB-COMPONENTES
# ==========================================================================

def _score_subcomponente(nombre: str, snapshot: dict, alertas_7d: list) -> dict:
    """Calcula score 0-100 de un sub-componente individual."""
    baseline = BASELINE_SUBCOMPONENTES[nombre]
    score = baseline
    drivers = []  # eventos que impactaron este score

    # 1) Penalización por factores P×I que afectan este sub-componente
    matriz = snapshot.get("matriz_riesgo", []) or []
    for f in matriz:
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        mapeo = MAPEO_FACTORES_SUBCOMPONENTE.get(fid, [])
        for (subc, peso) in mapeo:
            if subc != nombre:
                continue
            score_factor = f.get("score", 0) or 0
            # Si el factor está en zona alta (>60), penaliza el sub-componente
            if score_factor >= 70:
                penalizacion = 22 * peso
            elif score_factor >= 60:
                penalizacion = 15 * peso
            elif score_factor >= 50:
                penalizacion = 10 * peso
            elif score_factor >= 40:
                penalizacion = 5 * peso
            else:
                penalizacion = 0
            if penalizacion > 0:
                score -= penalizacion
                drivers.append({
                    "tipo": "factor",
                    "id": fid,
                    "nombre": f.get("nombre", fid),
                    "score_factor": score_factor,
                    "impacto": -round(penalizacion, 1),
                })

    # 2) Penalización por alertas críticas/altas en últimos 7 días.
    # Curva LOGARÍTMICA: en lugar de "cap rígido lineal", usamos
    # min(cap, k * log(1 + n)). Esto da gradiente continuo:
    #   5 alertas únicas  → ~6 pts (señal moderada)
    #   30 alertas únicas → ~14 pts (crisis activa)
    #   100 alertas únicas → ~18 pts (saturación cerca del cap)
    # Y distingue magnitudes: 30 vs 100 alertas únicas NO es lo mismo.
    penalizaciones_reglas = REGLAS_PENALIZACION.get(nombre, [])
    for (regla, k_coef, cap) in penalizaciones_reglas:
        n_alertas = sum(
            1 for a in alertas_7d
            if a.get("regla") == regla and a.get("nivel") in ("CRÍTICA", "ALTA")
        )
        if n_alertas > 0:
            # k_coef es el coeficiente del log (no más "puntos por alerta")
            penalizacion = min(cap, k_coef * math.log(1 + n_alertas))
            score -= penalizacion
            drivers.append({
                "tipo": "alerta",
                "id": regla,
                "n_alertas_7d": n_alertas,
                "impacto": -round(penalizacion, 1),
            })

    return {
        "nombre": nombre,
        "baseline": baseline,
        "score": max(0, min(100, round(score, 1))),
        "penalizacion_total": round(baseline - max(0, score), 1),
        "drivers": sorted(drivers, key=lambda d: d.get("impacto", 0))[:5],
    }


def _score_convergencia(snapshot: dict, intelligence_brief: dict) -> dict:
    """Sub-componente de convergencia: usa el Intelligence Engine."""
    baseline = BASELINE_SUBCOMPONENTES["convergencia_crisis"]
    score = baseline
    drivers = []

    # Penalización por cada convergencia detectada
    convergencias = intelligence_brief.get("convergencias", []) or []
    for c in convergencias:
        if not isinstance(c, dict):
            continue
        n_factores = c.get("n_factores", 0) or 0
        if n_factores >= 5:
            penalizacion = 12
        elif n_factores >= 4:
            penalizacion = 8
        elif n_factores >= 3:
            penalizacion = 5
        else:
            penalizacion = 2
        score -= penalizacion
        drivers.append({
            "tipo": "convergencia",
            "id": c.get("tema", "?"),
            "n_factores": n_factores,
            "direccion": c.get("direccion", "?"),
            "impacto": -penalizacion,
        })

    # Penalización por I&W activos en niveles ALTO/CRÍTICO
    iw = intelligence_brief.get("indicators_warnings", {}) or {}
    n_iw_criticos = 0
    for esc_id, esc_data in iw.items():
        if isinstance(esc_data, dict):
            nivel = esc_data.get("nivel_alerta", "")
            if nivel == "CRÍTICO":
                score -= 4
                n_iw_criticos += 1
            elif nivel == "ALTO":
                score -= 2
                n_iw_criticos += 1
    if n_iw_criticos > 0:
        drivers.append({
            "tipo": "indicators_warnings",
            "id": "I&W",
            "n_iw_activos": n_iw_criticos,
            "impacto": -round((4 * n_iw_criticos), 1),
        })

    return {
        "nombre": "convergencia_crisis",
        "baseline": baseline,
        "score": max(0, min(100, round(score, 1))),
        "penalizacion_total": round(baseline - max(0, score), 1),
        "drivers": sorted(drivers, key=lambda d: d.get("impacto", 0))[:5],
    }


# ==========================================================================
# CÁLCULO EDI PRINCIPAL
# ==========================================================================

def _obtener_alertas_7d(archive, ahora) -> list[dict]:
    """Devuelve alertas ÚNICAS de los últimos 7 días desde el archive.

    DEDUP CRÍTICO: la misma nota puede aparecer cientos de veces (48
    snapshots/día × 14 fuentes × 7 días = miles de filas) pero es un
    solo evento. Agrupamos por (regla, primeros 80 chars de título
    normalizado) y devolvemos una alerta representativa por grupo.

    Esto evita inflación masiva del conteo que distorsionaba el EDI.
    """
    if not archive:
        return []
    cutoff = (ahora - timedelta(days=7)).isoformat()
    try:
        with archive._conn() as c:
            rows = c.execute("""
                SELECT regla, nivel, titulo, timestamp
                FROM alertas
                WHERE timestamp >= ?
            """, (cutoff,)).fetchall()

        # Dedup por (regla, titulo normalizado primeros 80 chars)
        vistos = {}
        for r in rows:
            regla = r["regla"] or ""
            titulo_norm = (r["titulo"] or "").lower().strip()[:80]
            key = f"{regla}::{titulo_norm}"
            if key in vistos:
                continue
            vistos[key] = dict(r)
        return list(vistos.values())
    except Exception:
        return []


def _calcular_banda_confianza(archive, ahora) -> float:
    """Estima la varianza del EDI en últimos 7 días para banda ±.

    Como no podemos recalcular el EDI completo 7 veces (caro),
    aproximamos usando la varianza del score_global de snapshots.
    Es una aproximación: a mayor varianza del score, mayor incertidumbre.
    """
    if not archive:
        return 4.0  # default si no hay archive
    cutoff = (ahora - timedelta(days=7)).isoformat()
    try:
        with archive._conn() as c:
            rows = c.execute("""
                SELECT score_global FROM snapshots
                WHERE generado >= ? AND score_global IS NOT NULL
                ORDER BY generado ASC
            """, (cutoff,)).fetchall()
        scores = [r["score_global"] for r in rows if r["score_global"] is not None]
        if len(scores) < 3:
            return 4.0
        sd = statistics.stdev(scores)
        # Mapear desviación del score (típicamente 5-20 puntos) a banda EDI (2-6)
        return round(min(8.0, max(2.0, sd * 0.4)), 1)
    except Exception:
        return 4.0


def _calcular_tendencia(archive, ahora, score_actual: float) -> dict:
    """Compara EDI actual vs EDI REAL de hace 7 días.

    En lugar de aproximar invirtiendo el score nacional (que era una
    corredera burda), reconstruye el snapshot de hace 7 días desde el
    archive y calcula el EDI con la misma fórmula. Honesto y trazable.
    """
    if not archive:
        return {"delta_7d": 0, "arrow": "→", "etiqueta": "SIN HISTÓRICO"}
    try:
        # Snapshot representativo de hace 7 días: el último snapshot del
        # día calendario que está exactamente 7 días atrás.
        cutoff_7d_atras = (ahora - timedelta(days=7))
        cutoff_8d_atras = (ahora - timedelta(days=8))
        with archive._conn() as c:
            row = c.execute("""
                SELECT id, generado
                FROM snapshots
                WHERE generado >= ? AND generado < ?
                ORDER BY generado DESC
                LIMIT 1
            """, (cutoff_8d_atras.isoformat(), cutoff_7d_atras.isoformat())).fetchone()
            if not row:
                return {"delta_7d": 0, "arrow": "→", "etiqueta": "SIN HISTÓRICO"}

            snap_id_pasado = row["id"]
            generado_pasado = row["generado"]

            # Reconstruir matriz_riesgo de ese snapshot
            matriz_rows = c.execute("""
                SELECT factor_id, nombre, score
                FROM factores WHERE snapshot_id = ?
            """, (snap_id_pasado,)).fetchall()
            matriz_pasada = [
                {"id": m["factor_id"], "nombre": m["nombre"], "score": m["score"]}
                for m in matriz_rows
            ]

            # Alertas únicas en ventana 7 días que TERMINA hace 7 días
            # (es decir, de hace 14 días a hace 7 días)
            ventana_inicio = (cutoff_7d_atras - timedelta(days=7)).isoformat()
            ventana_fin = cutoff_7d_atras.isoformat()
            alertas_rows = c.execute("""
                SELECT regla, nivel, titulo
                FROM alertas
                WHERE timestamp >= ? AND timestamp < ?
            """, (ventana_inicio, ventana_fin)).fetchall()
            # Dedup
            vistos = {}
            for r in alertas_rows:
                key = f"{r['regla'] or ''}::{(r['titulo'] or '').lower().strip()[:80]}"
                if key not in vistos:
                    vistos[key] = dict(r)
            alertas_pasadas = list(vistos.values())

        # Recalcular sub-componentes con los datos de hace 7 días
        snap_pasado = {"matriz_riesgo": matriz_pasada}
        sub_ij = _score_subcomponente("independencia_judicial", snap_pasado, alertas_pasadas)
        sub_cc = _score_subcomponente("capacidad_control", snap_pasado, alertas_pasadas)
        sub_en = _score_subcomponente("estabilidad_normativa", snap_pasado, alertas_pasadas)
        sub_cv = _score_convergencia(snap_pasado, {"convergencias": [], "indicators_warnings": {}})
        edi_pasado = (
            PESOS_SUBCOMPONENTES["independencia_judicial"] * sub_ij["score"] +
            PESOS_SUBCOMPONENTES["capacidad_control"] * sub_cc["score"] +
            PESOS_SUBCOMPONENTES["estabilidad_normativa"] * sub_en["score"] +
            PESOS_SUBCOMPONENTES["convergencia_crisis"] * sub_cv["score"]
        )
        edi_pasado = round(edi_pasado, 1)

        delta = round(score_actual - edi_pasado, 1)
        if delta >= 4:
            arrow, etiqueta = "↑", "MEJORA"
        elif delta <= -4:
            arrow, etiqueta = "↓", "DETERIORO"
        else:
            arrow, etiqueta = "→", "ESTABLE"
        return {
            "delta_7d": delta,
            "arrow": arrow,
            "etiqueta": etiqueta,
            "edi_real_7d_atras": edi_pasado,
            "snapshot_pasado_generado": generado_pasado,
        }
    except Exception as e:
        return {"delta_7d": 0, "arrow": "→", "etiqueta": "ESTABLE",
                "error": str(e)[:120]}


def _etiqueta_edi(score: float) -> tuple[str, str]:
    """Devuelve (etiqueta, color_token)."""
    if score >= 80:
        return "SÓLIDO", "verde"
    if score >= 65:
        return "ESTABLE", "verde-amarillo"
    if score >= 50:
        return "TENSIONADO", "ambar"
    if score >= 35:
        return "FRÁGIL", "naranja"
    return "CRÍTICO", "rojo"


def calcular_edi(snapshot: dict, archive=None,
                  intelligence_brief: dict = None) -> dict:
    """Calcula el EDI actual con todos sus sub-componentes y metadatos.

    Args:
        snapshot: salida del pipeline OSINT
        archive: instancia ApuriskArchive (para histórico)
        intelligence_brief: salida de generar_intelligence_brief() (opcional)

    Returns:
        Dict completo con score EDI, sub-componentes, drivers, banda,
        tendencia, fecha de corte, metadata.
    """
    ahora = datetime.now(timezone(timedelta(hours=-5)))  # PET

    # Alertas de los últimos 7 días desde el archive
    alertas_7d = _obtener_alertas_7d(archive, ahora)

    # Si no hay intelligence_brief, hacemos uno mínimo (sin convergencias)
    if intelligence_brief is None:
        intelligence_brief = {"convergencias": [], "indicators_warnings": {}}

    # Calcular cada sub-componente
    sub_ij = _score_subcomponente("independencia_judicial", snapshot, alertas_7d)
    sub_cc = _score_subcomponente("capacidad_control", snapshot, alertas_7d)
    sub_en = _score_subcomponente("estabilidad_normativa", snapshot, alertas_7d)
    sub_cv = _score_convergencia(snapshot, intelligence_brief)

    # EDI ponderado
    edi_score = (
        PESOS_SUBCOMPONENTES["independencia_judicial"] * sub_ij["score"] +
        PESOS_SUBCOMPONENTES["capacidad_control"] * sub_cc["score"] +
        PESOS_SUBCOMPONENTES["estabilidad_normativa"] * sub_en["score"] +
        PESOS_SUBCOMPONENTES["convergencia_crisis"] * sub_cv["score"]
    )
    edi_score = round(edi_score, 1)
    etiqueta, color = _etiqueta_edi(edi_score)

    # Banda de confianza
    banda = _calcular_banda_confianza(archive, ahora)

    # Tendencia vs 7 días atrás
    tendencia = _calcular_tendencia(archive, ahora, edi_score)

    # Top drivers cruzados (todos los sub-componentes)
    todos_drivers = []
    for sub in [sub_ij, sub_cc, sub_en, sub_cv]:
        for d in sub["drivers"]:
            d_copia = dict(d)
            d_copia["subcomponente"] = sub["nombre"]
            todos_drivers.append(d_copia)
    todos_drivers.sort(key=lambda x: x.get("impacto", 0))  # más negativo primero
    top_drivers = todos_drivers[:5]

    return {
        "edi": edi_score,
        "edi_min": max(0, round(edi_score - banda, 1)),
        "edi_max": min(100, round(edi_score + banda, 1)),
        "banda_confianza": banda,
        "etiqueta": etiqueta,
        "color": color,
        "tendencia": tendencia,
        "fecha_corte": ahora.isoformat(timespec="seconds"),
        "subcomponentes": {
            "independencia_judicial": {
                **sub_ij,
                "peso_pct": PESOS_SUBCOMPONENTES["independencia_judicial"] * 100,
            },
            "capacidad_control": {
                **sub_cc,
                "peso_pct": PESOS_SUBCOMPONENTES["capacidad_control"] * 100,
            },
            "estabilidad_normativa": {
                **sub_en,
                "peso_pct": PESOS_SUBCOMPONENTES["estabilidad_normativa"] * 100,
            },
            "convergencia_crisis": {
                **sub_cv,
                "peso_pct": PESOS_SUBCOMPONENTES["convergencia_crisis"] * 100,
            },
        },
        "top_drivers": top_drivers,
        "metodologia": (
            "EDI = 30%·IJ + 25%·CC + 25%·EN + 20%·CV. "
            "Calculado sobre ventana móvil de últimos 7 días. "
            "Banda ± derivada de varianza del score nacional."
        ),
        "disponibilidad_series": {
            "serie_14d": "disponible",
            "serie_30d": "acumulando — disponible cuando el archive cruce 30 días continuos",
            "serie_90d": "acumulando — disponible cuando el archive cruce 90 días continuos",
        },
    }


# ==========================================================================
# SERIE TEMPORAL: EDI HISTÓRICO POR DÍA
# ==========================================================================

def calcular_edi_serie(archive, dias: int = 14) -> dict:
    """Calcula el EDI diario para los últimos N días.

    Aproximación práctica: para cada día, calcula EDI usando el snapshot
    promedio del día + alertas de ventana móvil 7 días terminando ese día.

    Args:
        archive: instancia ApuriskArchive
        dias: cuántos días hacia atrás (default 14)

    Returns:
        Dict con serie temporal completa.
    """
    if not archive:
        return {"serie": [], "n_dias": 0, "error": "Sin archive"}

    ahora = datetime.now(timezone(timedelta(hours=-5)))

    try:
        with archive._conn() as c:
            # Obtener todos los snapshots de los últimos N días con sus factores
            cutoff = (ahora - timedelta(days=dias)).isoformat()
            rows = c.execute("""
                SELECT id, generado, score_global, nivel
                FROM snapshots
                WHERE generado >= ?
                ORDER BY generado ASC
            """, (cutoff,)).fetchall()

            # Agrupar por día
            snapshots_por_dia = {}
            for r in rows:
                fecha = r["generado"][:10]  # YYYY-MM-DD
                if fecha not in snapshots_por_dia:
                    snapshots_por_dia[fecha] = []
                snapshots_por_dia[fecha].append({
                    "id": r["id"],
                    "generado": r["generado"],
                    "score_global": r["score_global"],
                })

            # Para cada día, computar EDI usando el snapshot más reciente del día
            # + alertas de los 7 días previos
            serie = []
            for fecha in sorted(snapshots_por_dia.keys()):
                snaps_dia = snapshots_por_dia[fecha]
                # Snapshot representativo: el último del día
                snap_repr = snaps_dia[-1]

                # Reconstruir matriz_riesgo de ese snapshot
                snap_id = snap_repr["id"]
                matriz_rows = c.execute("""
                    SELECT factor_id, nombre, score
                    FROM factores
                    WHERE snapshot_id = ?
                """, (snap_id,)).fetchall()
                matriz = [
                    {"id": m["factor_id"], "nombre": m["nombre"],
                     "score": m["score"]}
                    for m in matriz_rows
                ]

                # Alertas 7 días previos a este día
                fin_dia = datetime.fromisoformat(snap_repr["generado"])
                inicio_7d = (fin_dia - timedelta(days=7)).isoformat()
                fin_dia_iso = fin_dia.isoformat()
                rows_a = c.execute("""
                    SELECT regla, nivel
                    FROM alertas
                    WHERE timestamp >= ? AND timestamp < ?
                """, (inicio_7d, fin_dia_iso)).fetchall()
                alertas_dia = [dict(a) for a in rows_a]

                # Calcular sub-componentes (sin convergencias para serie diaria)
                snap_min = {"matriz_riesgo": matriz}
                sub_ij = _score_subcomponente("independencia_judicial", snap_min, alertas_dia)
                sub_cc = _score_subcomponente("capacidad_control", snap_min, alertas_dia)
                sub_en = _score_subcomponente("estabilidad_normativa", snap_min, alertas_dia)
                sub_cv = _score_convergencia(snap_min, {"convergencias": [], "indicators_warnings": {}})

                edi_dia = (
                    PESOS_SUBCOMPONENTES["independencia_judicial"] * sub_ij["score"] +
                    PESOS_SUBCOMPONENTES["capacidad_control"] * sub_cc["score"] +
                    PESOS_SUBCOMPONENTES["estabilidad_normativa"] * sub_en["score"] +
                    PESOS_SUBCOMPONENTES["convergencia_crisis"] * sub_cv["score"]
                )

                serie.append({
                    "fecha": fecha,
                    "edi": round(edi_dia, 1),
                    "independencia_judicial": sub_ij["score"],
                    "capacidad_control": sub_cc["score"],
                    "estabilidad_normativa": sub_en["score"],
                    "convergencia_crisis": sub_cv["score"],
                    "n_snapshots_dia": len(snaps_dia),
                    "score_nacional": round(snap_repr["score_global"], 1) if snap_repr["score_global"] else None,
                })

            return {
                "serie": serie,
                "n_dias": len(serie),
                "rango": {
                    "primer": serie[0]["fecha"] if serie else None,
                    "ultimo": serie[-1]["fecha"] if serie else None,
                },
            }
    except Exception as e:
        import traceback
        return {
            "serie": [],
            "n_dias": 0,
            "error": str(e),
            "traceback": traceback.format_exc().splitlines()[-5:],
        }
