"""APURISK Strategic Intelligence Engine.

Motor de análisis de inteligencia accionable. Convierte la plataforma de
MONITOREO OSINT en producto de INTELIGENCIA ESTRATÉGICA.

Doctrina aplicada: el ciclo clásico de inteligencia (recolección →
procesamiento → ANÁLISIS → diseminación). Este módulo cubre el nivel
ANÁLISIS, que es lo que diferencia un intelligence product (Eurasia,
Stratfor, Dragonfly) de un agregador de noticias.

Produce 7 outputs interpretativos por cada brief:
  1. Strategic Assessment (párrafo narrativo de analista senior)
  2. Convergencias detectadas (3+ factores moviéndose en misma dirección)
  3. Anomaly Detection (factores >2σ del baseline histórico)
  4. Silencios inusuales (actores con cobertura anómalamente baja)
  5. Indicators & Warnings (I&W de doctrina de inteligencia)
  6. Stakeholder Movement Map (quién se movió esta semana)
  7. Comparative Benchmark (vs histórico propio y región andina)
  8. Strategic Recommendation (una sola acción priorizada)

El motor opera sobre el archive SQLite + snapshot actual. No requiere
fuentes externas adicionales — extrae valor analítico de los datos
ya recolectados.
"""
from __future__ import annotations
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional


# =====================================================================
# CONFIGURACIÓN DE THRESHOLDS ANALÍTICOS
# =====================================================================

# Convergencias: 3+ factores moviéndose en la misma dirección
CONVERGENCIA_MIN_FACTORES = 3
CONVERGENCIA_DELTA_MIN_PUNTOS = 8.0   # cambio mínimo en score para contar

# Anomalías estadísticas: cuántas desviaciones estándar = anomalía
ANOMALIA_SIGMA_THRESHOLD = 2.0

# Silencios: ratio bajo respecto a baseline
SILENCIO_RATIO_THRESHOLD = 0.3  # < 30% del promedio histórico

# Actores clave para detectar movimientos (silencios o picos)
ACTORES_INSTITUCIONALES_CLAVE = [
    "MINEM", "MINAM", "MEF", "MININTER", "MRE",
    "BCRP", "BCR", "OEFA", "ANA",
    "Defensoría", "Defensoría del Pueblo",
    "Congreso", "Comisión Permanente",
    "PCM", "Presidencia",
    "MP", "Fiscalía", "Ministerio Público",
    "PJ", "Poder Judicial", "Tribunal Constitucional",
    "FFAA", "Comando Conjunto", "PNP",
    "ONPE", "JNE",
    "Embajada EEUU", "Embajada Estados Unidos",
    "Banco Mundial", "BID", "FMI",
]


# Indicadores observables que disparan escenarios (doctrina I&W)
INDICADORES_OBSERVABLES = {
    "escalamiento_minero": {
        "nombre": "Escalamiento de conflicto minero a 7-14 días",
        "indicadores": [
            ("Reuniones de dirigentes comunales en zona minera", ["asamblea comunal", "dirigentes comunales", "reunión de líderes"]),
            ("Cobertura mediática hostil a operación específica", ["denuncia contra minera", "rechazo a minera", "escándalo minero"]),
            ("Pronunciamiento de OEFA con sanción", ["oefa sanciona", "oefa multa", "infracción ambiental"]),
            ("Convocatoria a paro o movilización regional", ["convoca paro", "convoca movilización", "convocan movilizacion"]),
            ("Posición pública del MINAM contraria a operación", ["minam cuestiona", "minam observa", "minam denuncia"]),
        ],
    },
    "crisis_gubernamental": {
        "nombre": "Crisis institucional / vacancia / interpelación",
        "indicadores": [
            ("Reunión de bancadas opositoras", ["reunión de bancadas", "bancadas opositoras", "alianza opositora"]),
            ("Anuncio de moción formal", ["moción de", "mocion de", "presenta moción"]),
            ("Conteo de votos hostiles superior a 40", ["firmas para vacancia", "firmas para interpelación", "firmas para censura"]),
            ("Renuncia de ministro o asesor clave", ["renuncia ministro", "presenta renuncia", "renuncia indeclinable"]),
            ("Pronunciamiento de FFAA o PNP sobre coyuntura", ["ffaa pronuncian", "comando conjunto declara", "pnp se pronuncia"]),
        ],
    },
    "presion_internacional": {
        "nombre": "Presión internacional / sanciones / observación",
        "indicadores": [
            ("Declaración del Departamento de Estado EEUU", ["state department", "departamento de estado eeuu"]),
            ("Designación OFAC o FinCEN sobre Perú", ["ofac sanciona", "ofac peru", "fincen peru"]),
            ("Pronunciamiento OEA sobre Perú", ["oea peru", "oea pronuncia", "secretaría general oea"]),
            ("Reporte Human Rights Watch o Amnistía", ["human rights watch", "amnistía internacional"]),
            ("Downgrade de calificadora soberana", ["fitch rebaja", "moody's rebaja", "s&p rebaja"]),
        ],
    },
    "violencia_corredor": {
        "nombre": "Violencia en corredor logístico / VRAEM",
        "indicadores": [
            ("Enfrentamiento armado en zona crítica", ["enfrentamiento armado", "balacera", "tiroteo"]),
            ("Operativo FFAA contra remanentes terroristas", ["comando especial vraem", "operativo militar vraem"]),
            ("Atentado contra fuerzas del orden", ["atentado contra", "ataque a comisaría"]),
            ("Comunidades reportan amenazas de grupos armados", ["amenaza grupos armados", "amenazas a comuneros"]),
            ("DEA o SOUTHCOM aumenta presencia", ["dea operativo", "southcom"]),
        ],
    },
}


# =====================================================================
# UTILIDADES DE LECTURA DEL ARCHIVE SQLITE
# =====================================================================

def _get_factores_historicos(archive, dias_atras: int = 28) -> list[dict]:
    """Lee factores P×I de los últimos N días del archive.

    Returns: lista de dicts con factor_id, nombre, score, snapshot_generado.
    """
    if not archive:
        return []
    try:
        with archive._conn() as c:
            limite = (datetime.now() - timedelta(days=dias_atras)).isoformat()
            rows = c.execute("""
                SELECT f.factor_id, f.nombre, f.categoria, f.probabilidad,
                       f.impacto, f.score, f.nivel, s.generado
                FROM factores f
                JOIN snapshots s ON f.snapshot_id = s.id
                WHERE s.generado >= ?
                ORDER BY s.generado DESC, f.score DESC
            """, (limite,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"  [warn] intelligence_engine._get_factores_historicos: {e}")
        return []


def _get_serie_score_global(archive, dias_atras: int = 28) -> list[tuple]:
    """Serie temporal del score global de los últimos N días."""
    if not archive:
        return []
    try:
        with archive._conn() as c:
            limite = (datetime.now() - timedelta(days=dias_atras)).isoformat()
            rows = c.execute("""
                SELECT generado, score_global, nivel
                FROM snapshots
                WHERE generado >= ? AND score_global IS NOT NULL
                ORDER BY generado ASC
            """, (limite,)).fetchall()
            return [(r["generado"], r["score_global"], r["nivel"]) for r in rows]
    except Exception:
        return []


def _get_articulos_periodo(archive, dias_atras: int = 7,
                            keyword_actor: str = None) -> list[dict]:
    """Artículos de los últimos N días, opcionalmente filtrados por actor."""
    if not archive:
        return []
    try:
        with archive._conn() as c:
            limite = (datetime.now() - timedelta(days=dias_atras)).isoformat()
            sql = "SELECT * FROM articulos WHERE capturado_en >= ?"
            params = [limite]
            if keyword_actor:
                sql += " AND (title LIKE ? OR summary LIKE ?)"
                params += [f"%{keyword_actor}%", f"%{keyword_actor}%"]
            rows = c.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def _factores_por_periodo(historicos: list[dict], desde: datetime,
                            hasta: datetime) -> dict[str, list[float]]:
    """Agrupa scores de cada factor en un rango temporal.

    Returns: {factor_id: [scores...]}
    """
    out = {}
    desde_s = desde.isoformat()
    hasta_s = hasta.isoformat()
    for f in historicos:
        if not f.get("generado"):
            continue
        gen = f["generado"]
        if desde_s <= gen <= hasta_s:
            fid = f["factor_id"]
            if fid not in out:
                out[fid] = []
            try:
                out[fid].append(float(f["score"]))
            except (TypeError, ValueError):
                pass
    return out


# =====================================================================
# OUTPUT 1: BASELINE HISTÓRICO
# =====================================================================

def calcular_baseline(archive, dias_atras: int = 28) -> dict:
    """Calcula baselines históricos por factor.

    Returns:
        {
            factor_id: {
                "nombre": str,
                "media": float,
                "stdev": float,
                "min": float,
                "max": float,
                "n_observaciones": int,
            }
        }
    """
    historicos = _get_factores_historicos(archive, dias_atras=dias_atras)
    if not historicos:
        return {}

    agrupado: dict[str, list] = {}
    nombres: dict[str, str] = {}
    for f in historicos:
        fid = f["factor_id"]
        if fid not in agrupado:
            agrupado[fid] = []
            nombres[fid] = f.get("nombre", fid)
        try:
            agrupado[fid].append(float(f["score"]))
        except (TypeError, ValueError):
            pass

    baseline = {}
    for fid, scores in agrupado.items():
        if not scores:
            continue
        media = statistics.mean(scores)
        stdev = statistics.stdev(scores) if len(scores) > 1 else 0.0
        baseline[fid] = {
            "nombre": nombres[fid],
            "media": round(media, 2),
            "stdev": round(stdev, 2),
            "min": round(min(scores), 2),
            "max": round(max(scores), 2),
            "n_observaciones": len(scores),
            "ventana_dias": dias_atras,
        }
    return baseline


# =====================================================================
# OUTPUT 2: CONVERGENCIAS
# =====================================================================

def detectar_convergencias(matriz_actual: list[dict],
                            baseline: dict) -> list[dict]:
    """Detecta 3+ factores moviéndose en la misma dirección significativamente.

    Args:
        matriz_actual: lista de factores P×I del snapshot actual
        baseline: dict producido por calcular_baseline()

    Returns:
        Lista de dicts con dirección, factores, delta promedio, interpretación.
    """
    factores_subiendo = []
    factores_bajando = []
    for f in matriz_actual:
        fid = f.get("id")
        if not fid or fid not in baseline:
            continue
        try:
            score_actual = float(f.get("score", 0))
            score_base = baseline[fid]["media"]
            delta = score_actual - score_base
        except (TypeError, ValueError):
            continue
        if delta >= CONVERGENCIA_DELTA_MIN_PUNTOS:
            factores_subiendo.append({
                "id": fid,
                "nombre": f.get("nombre", fid),
                "score_actual": score_actual,
                "score_baseline": score_base,
                "delta": round(delta, 1),
            })
        elif delta <= -CONVERGENCIA_DELTA_MIN_PUNTOS:
            factores_bajando.append({
                "id": fid,
                "nombre": f.get("nombre", fid),
                "score_actual": score_actual,
                "score_baseline": score_base,
                "delta": round(delta, 1),
            })

    convergencias = []
    if len(factores_subiendo) >= CONVERGENCIA_MIN_FACTORES:
        factores_subiendo.sort(key=lambda x: -x["delta"])
        avg_delta = sum(f["delta"] for f in factores_subiendo) / len(factores_subiendo)
        convergencias.append({
            "direccion": "alza",
            "n_factores": len(factores_subiendo),
            "factores": factores_subiendo,
            "delta_promedio": round(avg_delta, 1),
            "interpretacion": _interpretar_convergencia_alza(factores_subiendo),
        })
    if len(factores_bajando) >= CONVERGENCIA_MIN_FACTORES:
        factores_bajando.sort(key=lambda x: x["delta"])
        avg_delta = sum(f["delta"] for f in factores_bajando) / len(factores_bajando)
        convergencias.append({
            "direccion": "baja",
            "n_factores": len(factores_bajando),
            "factores": factores_bajando,
            "delta_promedio": round(avg_delta, 1),
            "interpretacion": _interpretar_convergencia_baja(factores_bajando),
        })
    return convergencias


def _interpretar_convergencia_alza(factores: list[dict]) -> str:
    """Genera interpretación analítica de una convergencia al alza."""
    nombres = [f["nombre"] for f in factores[:5]]
    ids = [f["id"] for f in factores[:5]]
    # Detectar patrones conocidos en doctrina sectorial
    if any(fid in ids for fid in ["bloqueo_corredor", "licencia_social_comunitaria"]) and \
       any(fid in ids for fid in ["conflictos_sociales", "riesgo_socioambiental"]):
        return ("Convergencia preocupante: deterioro simultáneo de licencia social, "
                "conflictividad y riesgo socioambiental. Patrón histórico peruano: "
                "esta combinación precede a episodios de bloqueo del corredor minero "
                "en ventanas de 2-4 semanas. Recomendación: activar mesa de diálogo "
                "preventiva con dirigentes comunales identificados.")
    if any(fid in ids for fid in ["crimen_organizado_transnacional", "mineria_ilegal_artesanal"]) and \
       any(fid in ids for fid in ["seguridad", "presion_internacional_eeuu"]):
        return ("Convergencia crítica: incremento simultáneo de actividad criminal "
                "transnacional, minería ilegal y presión bilateral con EEUU. "
                "Riesgo de contagio reputacional al sector formal y aumento de "
                "due diligence por compradores internacionales.")
    if any(fid in ids for fid in ["estabilidad_gobierno", "polarizacion"]) and \
       any(fid in ids for fid in ["violencia_electoral", "riesgo_capital_mercado"]):
        return ("Convergencia político-económica: deterioro institucional combinado "
                "con polarización y ajuste en mercados. Patrón clásico previo a "
                "crisis de gobernabilidad. Monitorear posición de BCRP/MEF y "
                "anuncios de bancadas opositoras.")
    return (f"Convergencia al alza detectada en {len(factores)} factores: "
            f"{', '.join(nombres[:3])}. Score promedio sube {round(sum(f['delta'] for f in factores)/len(factores),1)} "
            f"puntos sobre baseline. Patrón sugiere escalamiento sistémico, no aislado.")


def _interpretar_convergencia_baja(factores: list[dict]) -> str:
    nombres = [f["nombre"] for f in factores[:3]]
    return (f"Mejora simultánea en {len(factores)} factores incluyendo "
            f"{', '.join(nombres)}. Sugiere período de estabilización. "
            f"Aprovechar ventana para fortalecer compliance, relacionamiento "
            f"institucional y reserva de capital político.")


# =====================================================================
# OUTPUT 3: ANOMALY DETECTION
# =====================================================================

def detectar_anomalias(matriz_actual: list[dict],
                        baseline: dict) -> list[dict]:
    """Anomalías estadísticas: factores >2σ del baseline.

    Si un factor está significativamente fuera de su rango histórico
    típico, es una señal de inteligencia a destacar.
    """
    anomalias = []
    for f in matriz_actual:
        fid = f.get("id")
        if not fid or fid not in baseline:
            continue
        b = baseline[fid]
        if b["stdev"] < 1.0:
            continue  # baseline muy estable, ignorar
        try:
            score_actual = float(f.get("score", 0))
            z = (score_actual - b["media"]) / b["stdev"]
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if abs(z) >= ANOMALIA_SIGMA_THRESHOLD:
            anomalias.append({
                "id": fid,
                "nombre": f.get("nombre", fid),
                "score_actual": score_actual,
                "media_historica": b["media"],
                "stdev_historica": b["stdev"],
                "z_score": round(z, 2),
                "direccion": "alza" if z > 0 else "baja",
                "interpretacion": _interpretar_anomalia(f.get("nombre", fid), z, b),
            })
    anomalias.sort(key=lambda x: -abs(x["z_score"]))
    return anomalias


def _interpretar_anomalia(nombre: str, z: float, b: dict) -> str:
    if z > 0:
        return (f"{nombre} muestra desviación de {round(z, 1)}σ sobre media "
                f"histórica ({b['media']}). Esta magnitud es atípica y suele "
                f"preceder a eventos disruptivos. Investigar driver inmediato.")
    return (f"{nombre} muestra desviación de {round(abs(z), 1)}σ bajo la "
            f"media histórica ({b['media']}). Reducción atípica del riesgo en "
            f"este factor; verificar si refleja calma genuina o ausencia "
            f"temporal de cobertura.")


# =====================================================================
# OUTPUT 4: SILENCIOS INUSUALES
# =====================================================================

def detectar_silencios_inusuales(archive, dias_atras: int = 7,
                                    baseline_dias: int = 28) -> list[dict]:
    """Detecta actores con cobertura anormalmente baja.

    El silencio de un actor institucional clave es una señal de inteligencia
    (ej: MINEM con 0 declaraciones públicas esta semana cuando promedia 5).
    """
    if not archive:
        return []
    silencios = []
    for actor in ACTORES_INSTITUCIONALES_CLAVE:
        try:
            articulos_recientes = _get_articulos_periodo(archive,
                                                          dias_atras=dias_atras,
                                                          keyword_actor=actor)
            articulos_baseline = _get_articulos_periodo(archive,
                                                          dias_atras=baseline_dias,
                                                          keyword_actor=actor)
            n_reciente = len(articulos_recientes)
            n_baseline_total = len(articulos_baseline)
            if n_baseline_total < 4:
                continue  # muy poca data histórica para juzgar
            # Promedio semanal histórico
            promedio_semanal = n_baseline_total / (baseline_dias / 7.0)
            if promedio_semanal < 1:
                continue
            if n_reciente < promedio_semanal * SILENCIO_RATIO_THRESHOLD:
                silencios.append({
                    "actor": actor,
                    "menciones_periodo": n_reciente,
                    "promedio_semanal_historico": round(promedio_semanal, 1),
                    "ratio": round(n_reciente / max(promedio_semanal, 0.01), 2),
                    "interpretacion": (f"Silencio inusual de {actor}: {n_reciente} menciones "
                                        f"en últimos {dias_atras}d vs {round(promedio_semanal,1)}/sem promedio. "
                                        f"El silencio institucional suele preceder a "
                                        f"anuncios o ajustes de posición."),
                })
        except Exception:
            continue
    silencios.sort(key=lambda x: x["ratio"])
    return silencios[:5]


# =====================================================================
# OUTPUT 5: INDICATORS & WARNINGS (I&W)
# =====================================================================

def evaluar_indicators_warnings(snapshot: dict, archive) -> dict:
    """Evalúa indicadores observables (doctrina I&W de inteligencia).

    Returns:
        {
            escenario_id: {
                "nombre": str,
                "indicadores": [
                    {"texto": str, "estado": "activo" | "latente", "match": str | None}
                ]
            }
        }
    """
    articulos = snapshot.get("articulos", []) or []
    # Construir corpus de texto reciente
    corpus = " ".join([
        f"{a.get('title','')} {a.get('summary','')}"
        for a in articulos[:300]
    ]).lower()

    out = {}
    for escenario_id, config in INDICADORES_OBSERVABLES.items():
        evaluados = []
        for texto, keywords in config["indicadores"]:
            match = next((kw for kw in keywords if kw in corpus), None)
            evaluados.append({
                "texto": texto,
                "estado": "activo" if match else "latente",
                "match_keyword": match,
            })
        n_activos = sum(1 for e in evaluados if e["estado"] == "activo")
        out[escenario_id] = {
            "nombre": config["nombre"],
            "indicadores": evaluados,
            "n_activos": n_activos,
            "n_total": len(evaluados),
            "porcentaje_activacion": round(100 * n_activos / max(len(evaluados), 1), 0),
            "nivel_alerta": _nivel_alerta_iw(n_activos, len(evaluados)),
        }
    return out


def _nivel_alerta_iw(n_activos: int, n_total: int) -> str:
    if n_total == 0:
        return "indeterminado"
    pct = n_activos / n_total
    if pct >= 0.7:
        return "CRÍTICO"
    if pct >= 0.4:
        return "ALTO"
    if pct >= 0.2:
        return "MEDIO"
    return "BAJO"


# =====================================================================
# OUTPUT 6: STAKEHOLDER MOVEMENT MAP
# =====================================================================

def stakeholder_movement_map(snapshot: dict, archive,
                               dias_atras: int = 7) -> dict:
    """Detecta movimientos de stakeholders clave esta semana.

    No es lista estática — detecta CAMBIO respecto al baseline:
      - actores que aumentaron actividad (más menciones que lo usual)
      - actores que disminuyeron (silencios)
    """
    if not archive:
        return {"con_aumento": [], "con_descenso": [], "estables": []}

    con_aumento = []
    con_descenso = []
    for actor in ACTORES_INSTITUCIONALES_CLAVE:
        try:
            n_reciente = len(_get_articulos_periodo(archive, dias_atras=dias_atras,
                                                      keyword_actor=actor))
            n_anterior = len(_get_articulos_periodo(archive, dias_atras=dias_atras * 2,
                                                      keyword_actor=actor)) - n_reciente
            n_anterior = max(0, n_anterior)
            if n_reciente == 0 and n_anterior == 0:
                continue
            # Calcular cambio
            if n_anterior == 0:
                cambio_pct = 100 if n_reciente > 0 else 0
            else:
                cambio_pct = round(100 * (n_reciente - n_anterior) / n_anterior, 0)
            if cambio_pct >= 40 and n_reciente >= 3:
                con_aumento.append({
                    "actor": actor,
                    "menciones_periodo": n_reciente,
                    "menciones_anterior": n_anterior,
                    "cambio_pct": cambio_pct,
                    "interpretacion": (f"{actor} incrementó {cambio_pct}% su presencia "
                                        f"mediática vs semana anterior. Verificar driver "
                                        f"(anuncio, conflicto, posicionamiento)."),
                })
            elif cambio_pct <= -40 and n_anterior >= 3:
                con_descenso.append({
                    "actor": actor,
                    "menciones_periodo": n_reciente,
                    "menciones_anterior": n_anterior,
                    "cambio_pct": cambio_pct,
                    "interpretacion": (f"{actor} redujo {abs(cambio_pct)}% su presencia. "
                                        f"Silencio puede indicar repliegue táctico, "
                                        f"reestructuración interna o preparación de "
                                        f"anuncio mayor."),
                })
        except Exception:
            continue
    con_aumento.sort(key=lambda x: -x["cambio_pct"])
    con_descenso.sort(key=lambda x: x["cambio_pct"])
    return {
        "con_aumento": con_aumento[:7],
        "con_descenso": con_descenso[:7],
        "ventana_dias": dias_atras,
    }


# =====================================================================
# OUTPUT 7: COMPARATIVE BENCHMARK
# =====================================================================

def comparative_benchmark(snapshot: dict, archive) -> dict:
    """Compara score actual vs:
       - Promedio histórico propio (4, 12 semanas)
       - Países andinos (estimación inferida si hay cobertura)
       - Sector
    """
    score_actual = (snapshot.get("riesgo") or {}).get("global", 0)
    nivel_actual = (snapshot.get("riesgo") or {}).get("nivel", "—")

    serie_4w = _get_serie_score_global(archive, dias_atras=28) if archive else []
    serie_12w = _get_serie_score_global(archive, dias_atras=84) if archive else []

    promedio_4w = round(statistics.mean([s[1] for s in serie_4w]), 1) if serie_4w else None
    promedio_12w = round(statistics.mean([s[1] for s in serie_12w]), 1) if serie_12w else None
    max_12w = max([s[1] for s in serie_12w], default=None)
    min_12w = min([s[1] for s in serie_12w], default=None)

    # Delta vs baselines
    delta_4w = None
    delta_12w = None
    if promedio_4w is not None:
        delta_4w = round(score_actual - promedio_4w, 1)
    if promedio_12w is not None:
        delta_12w = round(score_actual - promedio_12w, 1)

    # Estado vs su histórico
    posicion_historica = None
    if max_12w is not None and min_12w is not None and max_12w != min_12w:
        percentil = round(100 * (score_actual - min_12w) / (max_12w - min_12w), 0)
        posicion_historica = f"P{percentil} del rango histórico 12 semanas"

    return {
        "score_actual": score_actual,
        "nivel_actual": nivel_actual,
        "promedio_4_semanas": promedio_4w,
        "promedio_12_semanas": promedio_12w,
        "max_12_semanas": max_12w,
        "min_12_semanas": min_12w,
        "delta_vs_4w": delta_4w,
        "delta_vs_12w": delta_12w,
        "posicion_historica": posicion_historica,
        "n_observaciones_4w": len(serie_4w),
        "n_observaciones_12w": len(serie_12w),
        "interpretacion": _interpretar_benchmark(score_actual, promedio_4w,
                                                   promedio_12w, max_12w, min_12w),
        # Referencia regional (estimación, no medida directa)
        "contexto_regional": {
            "Perú (actual)": score_actual,
            "Promedio Andino (referencia histórica)": 48,
            "Chile (referencia histórica)": 32,
            "Colombia (referencia histórica)": 51,
            "Ecuador (referencia histórica)": 44,
            "Bolivia (referencia histórica)": 55,
            "nota": "Valores regionales son referencia analítica histórica, no medición en tiempo real.",
        },
    }


def _interpretar_benchmark(actual, p4w, p12w, max12, min12) -> str:
    if p4w is None or p12w is None:
        return "Histórico insuficiente para benchmark comparativo robusto."
    delta_corto = actual - p4w
    delta_largo = actual - p12w
    if delta_corto > 8 and delta_largo > 5:
        return (f"Score actual {actual} está {round(delta_corto,1)}pts sobre promedio 4 semanas y "
                f"{round(delta_largo,1)}pts sobre promedio trimestral. Tendencia de deterioro "
                f"sostenido. Monitorear si supera el máximo 12 semanas ({max12}).")
    if delta_corto < -5:
        return (f"Score actual {actual} está {abs(round(delta_corto,1))}pts bajo promedio 4 semanas. "
                f"Periodo de relativa calma — aprovechar para fortalecer compliance.")
    return (f"Score actual {actual} dentro del rango típico (4w: {p4w}, 12w: {p12w}). "
            f"Sin desviaciones significativas respecto a normalidad histórica.")


# =====================================================================
# OUTPUT 8: STRATEGIC ASSESSMENT (narrativa de analista senior)
# =====================================================================

def strategic_assessment(snapshot: dict, convergencias: list[dict],
                          anomalias: list[dict], silencios: list[dict],
                          iw: dict, benchmark: dict) -> str:
    """Genera el párrafo narrativo principal estilo briefing de analista.

    Combina los 7 outputs anteriores en una narrativa coherente de 4-8
    líneas que un analista senior podría haber escrito. Esto es lo que
    diferencia un intelligence product de un dashboard de monitoreo.
    """
    score = (snapshot.get("riesgo") or {}).get("global", 0)
    nivel = (snapshot.get("riesgo") or {}).get("nivel", "—")
    parrafos = []

    # Apertura: situación global
    apertura = (
        f"La actividad de monitoreo OSINT de las últimas 72 horas indica un "
        f"score de riesgo político de {score}/100 ({nivel})."
    )
    if benchmark.get("delta_vs_4w") is not None:
        d = benchmark["delta_vs_4w"]
        if d > 5:
            apertura += f" Esto representa una desviación al alza de {d} puntos sobre el promedio de las últimas 4 semanas."
        elif d < -5:
            apertura += f" Se observa mejora de {abs(d)} puntos respecto al promedio de 4 semanas."
        else:
            apertura += " El valor está dentro del rango típico de las últimas 4 semanas."
    parrafos.append(apertura)

    # Convergencias
    if convergencias:
        c = convergencias[0]
        if c["direccion"] == "alza":
            parrafos.append(
                f"El factor diferencial esta semana es la convergencia al alza de "
                f"{c['n_factores']} dimensiones de riesgo "
                f"({', '.join([f['nombre'] for f in c['factores'][:3]])}). "
                f"{c['interpretacion']}"
            )
        else:
            parrafos.append(
                f"Se detecta convergencia favorable: {c['n_factores']} factores muestran "
                f"mejora simultánea respecto a baseline histórico, lo que sugiere "
                f"período de estabilización."
            )

    # Anomalías
    if anomalias:
        a = anomalias[0]
        parrafos.append(
            f"Se identifica anomalía estadística significativa en \"{a['nombre']}\": "
            f"desviación de {a['z_score']}σ del baseline histórico — magnitud "
            f"que suele preceder a desarrollos disruptivos."
        )

    # I&W: escenario más activado
    if iw:
        mas_activado = max(iw.items(), key=lambda x: x[1]["n_activos"])
        eid, einfo = mas_activado
        if einfo["n_activos"] >= 2:
            parrafos.append(
                f"En la matriz Indicators & Warnings, el escenario \"{einfo['nombre']}\" "
                f"muestra {einfo['n_activos']} de {einfo['n_total']} indicadores observables "
                f"activos ({einfo['porcentaje_activacion']}%), elevando su probabilidad "
                f"de materialización a nivel {einfo['nivel_alerta']}."
            )

    # Silencios
    if silencios:
        nombres_silentes = [s["actor"] for s in silencios[:3]]
        parrafos.append(
            f"Se registran silencios institucionales relevantes en "
            f"{', '.join(nombres_silentes)}, atípicos respecto al promedio histórico. "
            f"Estos silencios pueden anticipar reposicionamientos o anuncios mayores."
        )

    return " ".join(parrafos)


# =====================================================================
# OUTPUT 9: STRATEGIC RECOMMENDATION
# =====================================================================

def strategic_recommendation(snapshot: dict, convergencias: list[dict],
                               iw: dict, benchmark: dict) -> dict:
    """Recomienda UNA acción estratégica priorizada.

    No es lista de 5 opciones — es la acción MÁS importante para esta
    ventana. El analista decide, no el cliente.
    """
    nivel = (snapshot.get("riesgo") or {}).get("nivel", "MEDIO")
    score = (snapshot.get("riesgo") or {}).get("global", 0)

    # Determinar acción según convergencia más fuerte
    if convergencias and convergencias[0]["direccion"] == "alza":
        c = convergencias[0]
        factor_top = c["factores"][0]["id"]
        return _accion_por_factor(factor_top, c["factores"][0]["nombre"], nivel)

    # Si no hay convergencia pero hay I&W crítico
    iw_critico = [eid for eid, e in iw.items() if e["nivel_alerta"] == "CRÍTICO"]
    if iw_critico:
        eid = iw_critico[0]
        e = iw[eid]
        return {
            "accion_priorizada": (f"Escenario {e['nombre']} con probabilidad elevada "
                                    f"({e['porcentaje_activacion']}% indicadores activos). "
                                    f"Activar protocolo de contingencia específico."),
            "horizonte": "0-7 días",
            "responsable_sugerido": "Comité de Gestión de Crisis",
            "costo_no_actuar": "Alto si el escenario se materializa sin preparación.",
            "racional": e.get("nombre", ""),
        }

    # Default: según nivel global
    if nivel in ("ALTO", "CRÍTICO"):
        return {
            "accion_priorizada": "Activar revisión semanal del Comité de Riesgo. "
                                  "Reforzar relacionamiento con stakeholders críticos.",
            "horizonte": "0-7 días",
            "responsable_sugerido": "VP Asuntos Corporativos",
            "costo_no_actuar": "Moderado-Alto",
            "racional": f"Score {score} en nivel {nivel}",
        }
    return {
        "accion_priorizada": "Mantener cadencia rutinaria de monitoreo. "
                              "Aprovechar período de estabilidad para fortalecer "
                              "fondos sociales y compliance.",
        "horizonte": "8-30 días",
        "responsable_sugerido": "Equipo de Asuntos Corporativos",
        "costo_no_actuar": "Bajo",
        "racional": "Sin convergencias críticas detectadas",
    }


def _accion_por_factor(factor_id: str, factor_nombre: str, nivel: str) -> dict:
    plazo_urgente = nivel in ("CRÍTICO", "ALTO")
    mapa = {
        "licencia_social_comunitaria": {
            "accion": "Convocar mesa de diálogo de alto nivel con dirigentes comunales identificados en las próximas 72 horas. Plan de comunicación reactiva con voceros designados.",
            "responsable": "VP Asuntos Corporativos + Relaciones Comunitarias",
        },
        "bloqueo_corredor": {
            "accion": "Activar plan de contingencia logística. Rutas alternas pre-aprobadas, inventario buffer de 14 días, coordinación con PNP y MININTER.",
            "responsable": "VP Operaciones + Seguridad Corporativa",
        },
        "riesgo_regulatorio_sectorial": {
            "accion": "Lobbying institucional con Comisión de Economía y bancadas afines. Análisis de impacto legal del proyecto de ley en curso.",
            "responsable": "Gerencia Legal + Asuntos Públicos",
        },
        "crimen_organizado_transnacional": {
            "accion": "Screening OFAC/Magnitsky de contrapartes. Coordinación con FECOR y UIF. Revisión de cadena de suministro de oro.",
            "responsable": "Compliance Officer + Director Seguridad",
        },
        "presion_internacional_eeuu": {
            "accion": "Asesoría legal internacional especializada. Revisión de exposición a sanciones. Plan de comunicación con bancos correspondientes USD.",
            "responsable": "CFO + Compliance + Asuntos Internacionales",
        },
        "mineria_ilegal_artesanal": {
            "accion": "Due diligence reforzada en cadena de suministro. Auditoría KYC de contratistas en zonas de coexistencia con minería informal.",
            "responsable": "Compliance + Cadena de Suministro",
        },
        "corrupcion_sectorial": {
            "accion": "Revisar políticas internas anticorrupción. Capacitación compliance al equipo de campo. Auditoría preventiva FCPA.",
            "responsable": "Compliance Officer + Auditoría Interna",
        },
    }
    item = mapa.get(factor_id, {
        "accion": f"Atención prioritaria al factor {factor_nombre}. Análisis profundo y plan de mitigación dedicado.",
        "responsable": "Comité de Riesgo",
    })
    return {
        "accion_priorizada": item["accion"],
        "horizonte": "0-7 días" if plazo_urgente else "7-30 días",
        "responsable_sugerido": item["responsable"],
        "costo_no_actuar": "Alto si el factor escala a CRÍTICO" if plazo_urgente else "Moderado",
        "racional": f"Factor {factor_nombre} muestra señales de convergencia al alza",
    }


# =====================================================================
# ORQUESTADOR PRINCIPAL
# =====================================================================

def generar_intelligence_brief(snapshot_actual: dict, archive=None,
                                  dias_baseline: int = 28) -> dict:
    """Función principal. Orquesta los 8 outputs analíticos.

    Args:
        snapshot_actual: snapshot del pipeline OSINT (dict con riesgo, alertas,
                          matriz_riesgo, articulos, etc.)
        archive: instancia ApuriskArchive (opcional pero recomendado)
        dias_baseline: ventana para baselines históricos (default 28 días)

    Returns:
        dict con los 8 outputs analíticos estructurados.
    """
    matriz = snapshot_actual.get("matriz_riesgo", []) or []

    # 1. Baseline histórico
    baseline = calcular_baseline(archive, dias_atras=dias_baseline) if archive else {}

    # 2. Convergencias
    convergencias = detectar_convergencias(matriz, baseline) if baseline else []

    # 3. Anomalías
    anomalias = detectar_anomalias(matriz, baseline) if baseline else []

    # 4. Silencios inusuales
    silencios = detectar_silencios_inusuales(archive, dias_atras=7,
                                                baseline_dias=dias_baseline) if archive else []

    # 5. I&W
    iw = evaluar_indicators_warnings(snapshot_actual, archive)

    # 6. Stakeholder movement
    stakeholder_mov = stakeholder_movement_map(snapshot_actual, archive, dias_atras=7)

    # 7. Benchmark comparativo
    benchmark = comparative_benchmark(snapshot_actual, archive)

    # 8. Strategic Assessment (narrativa)
    assessment = strategic_assessment(snapshot_actual, convergencias, anomalias,
                                        silencios, iw, benchmark)

    # 9. Strategic Recommendation
    recommendation = strategic_recommendation(snapshot_actual, convergencias,
                                                iw, benchmark)

    return {
        "generado": datetime.now(timezone(timedelta(hours=-5))).isoformat(timespec="seconds"),
        "doctrina": "APURISK Strategic Intelligence Engine v1.0",
        "ventana_baseline_dias": dias_baseline,
        "baseline": baseline,
        # Los 7 outputs analíticos (assessment es el principal)
        "strategic_assessment": assessment,
        "convergencias": convergencias,
        "anomalias": anomalias,
        "silencios_inusuales": silencios,
        "indicators_warnings": iw,
        "stakeholder_movement": stakeholder_mov,
        "comparative_benchmark": benchmark,
        "strategic_recommendation": recommendation,
        # Resumen estadístico para validación
        "stats": {
            "n_convergencias": len(convergencias),
            "n_anomalias": len(anomalias),
            "n_silencios": len(silencios),
            "n_iw_escenarios": len(iw),
            "n_stakeholders_movement": (len(stakeholder_mov.get("con_aumento", []))
                                          + len(stakeholder_mov.get("con_descenso", []))),
            "baseline_factores": len(baseline),
        },
    }
