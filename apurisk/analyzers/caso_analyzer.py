"""Analizador de Casos OSINT — APURISK 1.0 (Opción A: algorítmico, sin LLM).

Recibe un INPUT del analista con descripción del caso y produce un análisis
estructurado en 14 dimensiones para alimentar el reporte PDF analítico.

Fuentes consultadas:
  1. Base SQLite interna (snapshots históricos, artículos, alertas archivados)
  2. URLs específicas proporcionadas por el analista (fetch directo)
  3. Últimos snapshots JSON en output/
  4. Configuración de fuentes (config.yaml)

Clasificación basada en:
  - Conteo de menciones por keyword
  - Scoring por tipo de fuente (oficial > medios > redes)
  - Tendencia temporal (volumen últimas 24h vs 48-72h)
  - Cruce con factores de riesgo conocidos (FACTORES en risk_matrix.py)
  - Reglas para clasificar nivel (BAJO/MODERADO/ALTO/CRÍTICO)

Cuando se active la Opción B (Claude API), este módulo seguirá siendo la
base de datos: pasará sus hallazgos al LLM para interpretación narrativa.
"""
from __future__ import annotations
import json
import re
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter, defaultdict


PET = timezone(timedelta(hours=-5))


# =============================================================================
# UTILIDADES DE EXTRACCIÓN
# =============================================================================
_STOPWORDS = set("""
de la el los las que en y a un una para por con sin sobre tras como del al lo
se es son está están era fue ha haber este esta estos estas su sus me te nos
qué quién cómo cuándo dónde porque pues más muy ya yo tú él ella ellos ellas
desde hasta entre durante mientras así también solo sólo todo todos toda todas
ningún ninguna alguno alguna algunos algunas otro otra otros otras mismo misma
""".split())


def _tokens(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"[a-záéíóúñü]{4,}", text.lower())


def _keywords_del_caso(descripcion: str, comentario: str = "", min_n: int = 5) -> list[str]:
    """Extrae las palabras más frecuentes del input del analista."""
    text = f"{descripcion} {comentario}"
    toks = [t for t in _tokens(text) if t not in _STOPWORDS]
    freq = Counter(toks)
    # También incluimos sustantivos propios capitalizados del texto original
    propios = re.findall(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?", descripcion + " " + comentario)
    for p in propios:
        freq[p.lower()] += 5  # más peso a propios
    return [k for k, _ in freq.most_common(max(min_n, 10))]


# =============================================================================
# BÚSQUEDA EN BASE INTERNA
# =============================================================================
def _buscar_en_archivo(archive, keywords: list[str], dias: int = 30) -> dict:
    """Busca en SQLite por cada keyword. Retorna artículos + alertas matched."""
    desde = (datetime.now(PET) - timedelta(days=dias)).isoformat()
    art_total = []
    alert_total = []
    art_ids = set()
    alert_titles = set()
    for kw in keywords:
        try:
            arts = archive.search_articulos(keyword=kw, desde=desde, limit=50)
            for a in arts:
                aid = a.get("url") or a.get("title")
                if aid not in art_ids:
                    art_ids.add(aid)
                    art_total.append(a)
            als = archive.search_alertas(keyword=kw, desde=desde, limit=20)
            for al in als:
                t = al.get("titulo")
                if t and t not in alert_titles:
                    alert_titles.add(t)
                    alert_total.append(al)
        except Exception:
            continue
    return {"articulos": art_total[:100], "alertas": alert_total[:30]}


# =============================================================================
# IDENTIFICACIÓN DE ACTORES, REGIONES, SECTORES
# =============================================================================
ACTORES_PERU = {
    "Ejecutivo": ["presidente", "presidenta", "ejecutivo", "balcázar", "balcazar",
                  "consejo de ministros", "premier", "primer ministro", "gabinete"],
    "Congreso": ["congreso", "congresista", "congresistas", "pleno", "mesa directiva",
                 "moción de orden del día", "moción de vacancia", "comisión"],
    "Poder Judicial": ["poder judicial", "juzgado", "sala penal", "corte suprema",
                       "juez", "jueza", "fiscal supremo"],
    "Fiscalía / MP": ["fiscalía", "fiscalia", "ministerio público", "ministerio publico",
                      "fiscal de la nación", "fiscal de la nacion", "fiscal anticorrupción"],
    "Tribunal Constitucional": ["tribunal constitucional", "tc", "magistrado"],
    "JNE": ["jne", "jurado nacional de elecciones", "jurado electoral especial",
            "jee", "resolución del jne", "resolucion del jne"],
    "ONPE": ["onpe", "oficina nacional de procesos electorales", "actas observadas",
             "conteo onpe"],
    "FFAA / Comando Conjunto": ["fuerzas armadas", "ffaa", "comando conjunto",
                                 "ejército", "ejercito", "marina", "fuerza aérea",
                                 "vraem", "ccffaa"],
    "PNP / Mininter": ["policía nacional", "policia nacional", "pnp", "mininter",
                       "ministerio del interior", "ministro del interior"],
    "Defensoría del Pueblo": ["defensoría", "defensoria", "defensor del pueblo"],
    "BCR": ["bcr", "bcrp", "banco central", "velarde"],
    "MEF": ["mef", "ministerio de economía", "ministerio de economia"],
    "Cancillería": ["cancillería", "cancilleria", "mre", "canciller"],
    "Contraloría": ["contraloría", "contraloria", "contralor"],
    "Comunidades campesinas": ["comunidad campesina", "comunidades campesinas",
                                "ronda campesina", "rondas campesinas", "comuneros",
                                "frente de defensa"],
    "Sindicatos / Gremios": ["cgtp", "sindicato", "sindicatos", "gremio", "fdta",
                              "confiep", "sociedad nacional de minería"],
    "Partidos políticos": ["fuerza popular", "renovación popular", "renovacion popular",
                            "perú libre", "peru libre", "juntos por el perú",
                            "juntos por el peru", "acción popular", "accion popular",
                            "podemos perú", "podemos peru", "avanza país", "avanza pais"],
    "Empresas extractivas": ["las bambas", "mmg", "antamina", "tía maría", "tia maria",
                              "southern", "glencore", "newmont", "buenaventura",
                              "petroperú", "petroperu", "camisea"],
    "Medios": ["el comercio", "la república", "la republica", "gestión", "gestion",
               "rpp", "infobae", "idl reporteros", "ojo público", "ojo publico",
               "convoca", "caretas", "willax"],
}

REGIONES_PERU = [
    "Amazonas", "Áncash", "Ancash", "Apurímac", "Apurimac", "Arequipa",
    "Ayacucho", "Cajamarca", "Callao", "Cusco", "Huancavelica", "Huánuco",
    "Huanuco", "Ica", "Junín", "Junin", "La Libertad", "Lambayeque", "Lima",
    "Loreto", "Madre de Dios", "Moquegua", "Pasco", "Piura", "Puno",
    "San Martín", "San Martin", "Tacna", "Tumbes", "Ucayali",
]

SECTORES_ECONOMICOS = {
    "Minería": ["mina", "minero", "minería", "mineria", "extractivo", "concentrado",
                 "cobre", "oro", "plata", "zinc"],
    "Hidrocarburos": ["petróleo", "petroleo", "gas natural", "lote", "perúpetro",
                       "perupetro", "hidrocarburos"],
    "Pesca": ["pesca", "pesquero", "anchoveta", "harina de pescado"],
    "Agro": ["agrícola", "agricola", "agro", "agroexportación", "agroexportacion",
              "cultivo", "uva", "palta", "espárragos"],
    "Construcción": ["construcción", "construccion", "obra pública", "obras públicas",
                      "infraestructura", "carretera", "puente"],
    "Turismo": ["turismo", "machu picchu", "cusco turístico", "cusco turistico"],
    "Financiero": ["banco", "financiera", "bvl", "bolsa de valores", "sol", "tipo de cambio",
                    "fitch", "moody", "s&p", "embig"],
    "Comercio": ["comercio", "mercado", "exportación", "exportacion"],
}


def _identificar_actores(textos: list[str]) -> dict[str, int]:
    """Cuenta menciones de cada actor en una lista de textos."""
    blob = " ".join(textos).lower()
    out = Counter()
    for actor, kws in ACTORES_PERU.items():
        for kw in kws:
            n = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", blob))
            if n:
                out[actor] += n
    return dict(out.most_common())


def _identificar_regiones(textos: list[str]) -> dict[str, int]:
    blob = " ".join(textos).lower()
    out = Counter()
    for r in REGIONES_PERU:
        n = len(re.findall(r"\b" + re.escape(r.lower()) + r"\b", blob))
        if n:
            out[r] += n
    return dict(out.most_common())


def _identificar_sectores(textos: list[str]) -> dict[str, int]:
    blob = " ".join(textos).lower()
    out = Counter()
    for s, kws in SECTORES_ECONOMICOS.items():
        for kw in kws:
            n = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", blob))
            if n:
                out[s] += n
    return dict(out.most_common())


# =============================================================================
# CLASIFICACIÓN DE RIESGO Y TENDENCIA
# =============================================================================
def _clasificar_nivel(score: float) -> str:
    if score >= 75:
        return "CRÍTICO"
    if score >= 55:
        return "ALTO"
    if score >= 35:
        return "MODERADO"
    return "BAJO"


def _clasificar_tendencia(menciones_recientes_24h: int, menciones_24_72h: int,
                          menciones_3_7d: int) -> tuple[str, str]:
    """Devuelve (clasificacion, razon)."""
    if menciones_recientes_24h == 0 and menciones_24_72h == 0 and menciones_3_7d > 0:
        return ("Latente", "Ya no aparece en cobertura reciente pero persiste el tema en el período.")
    if menciones_recientes_24h == 0 and menciones_24_72h == 0:
        return ("Estancado",
                "Sin cobertura reciente en las últimas 72 horas.")
    if menciones_recientes_24h > max(menciones_24_72h, menciones_3_7d) * 1.5 and menciones_recientes_24h >= 3:
        return ("Escalada",
                f"Pico de cobertura en últimas 24h ({menciones_recientes_24h} menciones) "
                f"comparado con períodos previos.")
    if menciones_3_7d > menciones_recientes_24h * 1.5 and menciones_3_7d >= 3:
        return ("Desescalada",
                f"La cobertura disminuye: {menciones_3_7d} menciones hace 3-7 días vs "
                f"{menciones_recientes_24h} en las últimas 24h.")
    if menciones_recientes_24h >= 2 and menciones_24_72h >= 2:
        return ("En desarrollo",
                "Cobertura sostenida en los últimos días con flujo continuo.")
    if menciones_3_7d >= 2 and menciones_recientes_24h >= 1:
        return ("Recurrente",
                "El tema reaparece intermitentemente a lo largo del período.")
    return ("En desarrollo", "Cobertura limitada pero con presencia activa.")


def _evaluar_dimensiones_riesgo(actores: dict, regiones: dict,
                                  sectores: dict, alertas: list,
                                  factor_riesgo_top: dict | None) -> dict:
    """Evalúa el riesgo en 6 dimensiones (institucional, social, electoral,
    económico, mediático, seguridad)."""
    score_institucional = 0
    score_social = 0
    score_electoral = 0
    score_economico = 0
    score_mediatico = 0
    score_seguridad = 0

    # Institucional: peso de Ejecutivo, Congreso, PJ, MP
    for actor_key in ("Ejecutivo", "Congreso", "Poder Judicial", "Fiscalía / MP",
                       "Tribunal Constitucional", "Defensoría del Pueblo"):
        if actor_key in actores:
            score_institucional += min(30, actores[actor_key] * 3)

    # Social: comunidades, sindicatos, partidos
    for actor_key in ("Comunidades campesinas", "Sindicatos / Gremios", "Partidos políticos"):
        if actor_key in actores:
            score_social += min(30, actores[actor_key] * 4)

    # Electoral: JNE, ONPE
    for actor_key in ("JNE", "ONPE"):
        if actor_key in actores:
            score_electoral += min(40, actores[actor_key] * 6)

    # Económico: BCR, MEF, sectores económicos
    for actor_key in ("BCR", "MEF", "Empresas extractivas"):
        if actor_key in actores:
            score_economico += min(25, actores[actor_key] * 3)
    score_economico += min(30, sum(sectores.values()) * 2)

    # Mediático: número de medios + cobertura
    if "Medios" in actores:
        score_mediatico = min(70, actores["Medios"] * 5)
    score_mediatico += min(30, len(regiones) * 5)  # diversidad geográfica

    # Seguridad: FFAA, PNP, alertas críticas
    for actor_key in ("FFAA / Comando Conjunto", "PNP / Mininter"):
        if actor_key in actores:
            score_seguridad += min(35, actores[actor_key] * 4)
    crit = len([a for a in alertas if a.get("nivel") == "CRÍTICA"])
    score_seguridad += min(35, crit * 8)

    # Bonus si el caso encaja con un factor de riesgo conocido top
    if factor_riesgo_top:
        nivel_factor = factor_riesgo_top.get("nivel", "")
        if nivel_factor == "CRÍTICO":
            score_institucional += 15
            score_seguridad += 10

    return {
        "institucional": min(100, score_institucional),
        "social": min(100, score_social),
        "electoral": min(100, score_electoral),
        "economico": min(100, score_economico),
        "mediatico": min(100, score_mediatico),
        "seguridad": min(100, score_seguridad),
    }


# =============================================================================
# PROYECCIÓN MEDIÁTICA
# =============================================================================
def _proyeccion_mediatica(menciones_24h: int, menciones_72h: int, n_medios: int,
                            tendencia: str) -> dict:
    """Estima probabilidad de crecimiento mediático en próximas ventanas."""
    base = menciones_24h * 5 + menciones_72h * 2 + n_medios * 3
    factor_tend = {"Escalada": 1.5, "En desarrollo": 1.1, "Recurrente": 1.0,
                    "Desescalada": 0.5, "Estancado": 0.3, "Latente": 0.4}.get(tendencia, 1.0)

    def clas(p):
        if p >= 70: return "Muy alta"
        if p >= 50: return "Alta"
        if p >= 30: return "Media"
        return "Baja"

    p24 = min(95, int(base * factor_tend))
    p48 = min(95, int(p24 * (0.95 if "Desescalada" in tendencia else 1.05)))
    p72 = min(95, int(p48 * (0.9 if "Desescalada" in tendencia else 1.0)))
    psem = min(95, int(p72 * 0.85))

    return {
        "24h": {"prob": p24, "clase": clas(p24)},
        "48h": {"prob": p48, "clase": clas(p48)},
        "72h": {"prob": p72, "clase": clas(p72)},
        "semana": {"prob": psem, "clase": clas(psem)},
        "pasa_a_medios_tradicionales": "Alta" if n_medios >= 3 else "Media",
        "internacionalizacion": "Alta" if menciones_24h >= 5 and n_medios >= 4 else "Baja",
    }


# =============================================================================
# ESCENARIOS PROSPECTIVOS
# =============================================================================
def _escenarios_prospectivos(tendencia: str, nivel: str,
                              menciones_24h: int) -> list[dict]:
    """Devuelve 3 escenarios: Desescalada, Continuidad, Escalada."""
    if tendencia == "Escalada":
        prob_esc, prob_cont, prob_des = 55, 30, 15
    elif tendencia == "Desescalada":
        prob_esc, prob_cont, prob_des = 15, 30, 55
    elif tendencia == "Latente":
        prob_esc, prob_cont, prob_des = 20, 30, 50
    elif tendencia == "Estancado":
        prob_esc, prob_cont, prob_des = 15, 50, 35
    else:
        prob_esc, prob_cont, prob_des = 35, 40, 25

    return [
        {
            "nombre": "Escalada",
            "descripcion": "El caso gana mayor cobertura mediática, atrae a actores políticos "
                            "y deviene en presión institucional / movilizaciones.",
            "probabilidad": f"{prob_esc}%",
            "detonantes": "Nuevas revelaciones, declaraciones polarizantes, intervención de "
                           "actores políticos, viralización en redes.",
            "impacto_politico": "Alto: erosión de credibilidad institucional, presión sobre "
                                  "autoridades, posible activación de mecanismos de control político.",
        },
        {
            "nombre": "Continuidad",
            "descripcion": "El caso mantiene su trayectoria actual: cobertura estable y disputa "
                            "narrativa en curso.",
            "probabilidad": f"{prob_cont}%",
            "detonantes": "Sin novedad relevante. El caso queda en agenda secundaria.",
            "impacto_politico": "Moderado: persiste como tema de debate pero sin movilizar "
                                  "decisiones políticas inmediatas.",
        },
        {
            "nombre": "Desescalada",
            "descripcion": "El caso pierde tracción mediática y es desplazado por otros temas "
                            "de agenda.",
            "probabilidad": f"{prob_des}%",
            "detonantes": "Reemplazo en agenda por evento de mayor impacto, declaraciones "
                            "conciliadoras, acuerdos institucionales.",
            "impacto_politico": "Bajo: el caso queda como antecedente sin consecuencias "
                                  "inmediatas.",
        },
    ]


# =============================================================================
# ALERTAS TEMPRANAS
# =============================================================================
def _alertas_tempranas(tendencia: str, actores: dict, regiones: dict,
                         dimensiones: dict) -> list[str]:
    out = []
    if tendencia in ("Escalada", "En desarrollo"):
        out.append("Monitorear declaraciones públicas en las próximas 24-48h.")
    if "Comunidades campesinas" in actores or "Sindicatos / Gremios" in actores:
        out.append("Vigilar convocatorias a movilización o paro en redes sociales y comunicados.")
    if "Congreso" in actores:
        out.append("Seguir mociones, citaciones y declaraciones de bancadas.")
    if "Fiscalía / MP" in actores:
        out.append("Monitorear acciones del Ministerio Público y posibles allanamientos.")
    if "FFAA / Comando Conjunto" in actores:
        out.append("Atender comunicados oficiales del CCFFAA y disposiciones del Ejecutivo.")
    if dimensiones.get("electoral", 0) >= 50:
        out.append("Revisar resoluciones del JNE y actos de campaña/balotaje.")
    if dimensiones.get("seguridad", 0) >= 60:
        out.append("Cruce con Mapa del Delito MININTER para identificar zonas de riesgo.")
    if any(r in ("Apurímac", "Apurimac", "Cusco", "Puno", "Cajamarca") for r in regiones):
        out.append("Reforzar monitoreo de conflictos socioambientales en sur andino.")
    if not out:
        out.append("Mantener monitoreo pasivo. No se detectan señales de escalamiento inmediato.")
    return out


# =============================================================================
# CONFIABILIDAD
# =============================================================================
def _evaluar_confiabilidad(articulos: list, alertas: list) -> dict:
    """Clasifica el material por confiabilidad."""
    fuentes_oficiales = 0
    fuentes_medios = 0
    fuentes_redes = 0
    for a in articulos:
        sid = (a.get("source_id") or "").lower()
        if any(k in sid for k in ["jne", "onpe", "inei", "bcr", "sunat", "contraloria",
                                    "mininter", "mre", "ccffaa", "mp_", "pj_", "tc_",
                                    "defensoria", "congreso", "andina"]):
            fuentes_oficiales += 1
        elif any(k in sid for k in ["twitter", "reddit", "youtube"]):
            fuentes_redes += 1
        else:
            fuentes_medios += 1

    total = max(1, fuentes_oficiales + fuentes_medios + fuentes_redes)
    return {
        "n_articulos": len(articulos),
        "n_alertas": len(alertas),
        "fuentes_oficiales": fuentes_oficiales,
        "fuentes_medios": fuentes_medios,
        "fuentes_redes": fuentes_redes,
        "confirmada": fuentes_oficiales,
        "probable": fuentes_medios,
        "no_confirmada": fuentes_redes,
        "%_oficial": round(100 * fuentes_oficiales / total, 1),
        "%_medios": round(100 * fuentes_medios / total, 1),
        "%_redes": round(100 * fuentes_redes / total, 1),
    }


# =============================================================================
# COBERTURA MEDIÁTICA
# =============================================================================
def _analizar_cobertura(articulos: list) -> dict:
    medios_count = Counter()
    categoria_count = Counter()
    for a in articulos:
        nombre = a.get("source_name", "")
        cat = a.get("category", "medios")
        if nombre:
            medios_count[nombre] += 1
        categoria_count[cat] += 1

    n_nacional = categoria_count.get("medios", 0) + categoria_count.get("estado", 0)
    n_internacional = categoria_count.get("internacional", 0)
    n_redes = categoria_count.get("redes", 0)
    n_encuestas = categoria_count.get("encuestas", 0)

    return {
        "top_medios": medios_count.most_common(10),
        "n_medios_distintos": len(medios_count),
        "n_nacional": n_nacional,
        "n_internacional": n_internacional,
        "n_redes": n_redes,
        "n_encuestas": n_encuestas,
        "diversidad": "Alta" if len(medios_count) >= 5 else "Media" if len(medios_count) >= 3 else "Baja",
    }


# =============================================================================
# RECOMENDACIÓN PARA EL ANALISTA
# =============================================================================
def _recomendacion_analista(nivel: str, tendencia: str, dimensiones: dict) -> list[str]:
    out = []
    if nivel in ("CRÍTICO", "ALTO"):
        if tendencia == "Escalada":
            out.append("ACTIVAR monitoreo intensivo del caso.")
            out.append("EMITIR alerta temprana a stakeholders.")
            out.append("PREPARAR informe especial en 24-48h.")
            out.append("ESCALAR el caso a supervisión superior.")
        else:
            out.append("Mantener monitoreo activo del caso.")
            out.append("Preparar informe analítico para reporte ejecutivo del día.")
    elif nivel == "MODERADO":
        out.append("Continuar monitoreo regular del caso.")
        if tendencia in ("Escalada", "Recurrente"):
            out.append("Revisar en las próximas 24h si hay evolución.")
    else:
        out.append("Mantener monitoreo pasivo.")
        out.append("Documentar el caso para futuras referencias.")

    if dimensiones.get("electoral", 0) >= 50:
        out.append("Cruce con bases del JNE/ONPE para verificar contexto electoral.")
    if dimensiones.get("seguridad", 0) >= 60:
        out.append("Cruce con MININTER y Defensoría del Pueblo.")
    if dimensiones.get("mediatico", 0) >= 70:
        out.append("Monitorear actores específicos identificados como impulsores del caso.")

    return out


# =============================================================================
# ANÁLISIS PRINCIPAL
# =============================================================================
def analizar_caso(input_analista: dict, archive=None, snapshot_actual: dict | None = None,
                   url_fetcher=None) -> dict:
    """Analiza un caso a partir del INPUT del analista.

    Args:
      input_analista: dict con campos:
        - caso: descripción del caso
        - comentario: comentario/hipótesis del analista
        - urls: lista de URLs proporcionadas
        - periodo: periodo de monitoreo (texto libre)
        - profundidad: "BREVE" | "ESTANDAR" | "PROFUNDO"
        - regiones_actores: regiones/actores/sectores de interés
        - solicitante: nombre o ID del analista (opcional)
      archive: instancia ApuriskArchive (opcional)
      snapshot_actual: dict del snapshot más reciente (opcional)
      url_fetcher: función callable(url) -> str con texto del HTML (opcional)

    Returns:
      dict con TODOS los datos estructurados para alimentar el reporte PDF.
    """
    caso = input_analista.get("caso", "")
    comentario = input_analista.get("comentario", "")
    urls = input_analista.get("urls", []) or []
    periodo = input_analista.get("periodo", "últimos 7 días")
    profundidad = input_analista.get("profundidad", "ESTÁNDAR").upper()
    regiones_actores = input_analista.get("regiones_actores", "")
    solicitante = input_analista.get("solicitante", "")

    # 1) Extraer keywords del input
    kws = _keywords_del_caso(caso, comentario + " " + regiones_actores)

    # 2) Buscar en archivo SQLite si está disponible
    matches = {"articulos": [], "alertas": []}
    if archive:
        matches = _buscar_en_archivo(archive, kws, dias=30)

    # 3) Combinar con snapshot actual
    if snapshot_actual:
        for a in snapshot_actual.get("articulos", []):
            text = ((a.get("title", "") + " " + a.get("summary", "")).lower())
            if any(re.search(r"\b" + re.escape(k.lower()) + r"\b", text) for k in kws):
                matches["articulos"].append({
                    "title": a.get("title"),
                    "summary": a.get("summary"),
                    "url": a.get("url"),
                    "published": a.get("published"),
                    "source_id": a.get("source_id"),
                    "source_name": a.get("source_name"),
                    "category": a.get("category", "medios"),
                    "region": a.get("region"),
                })
        for al in snapshot_actual.get("alertas", []):
            text = (al.get("titulo", "") + " " + al.get("resumen", "")).lower()
            if any(re.search(r"\b" + re.escape(k.lower()) + r"\b", text) for k in kws):
                matches["alertas"].append(al)

    # 4) Fetch URLs proporcionadas (si hay fetcher disponible)
    urls_analizadas = []
    if url_fetcher:
        for u in urls[:5]:
            try:
                html = url_fetcher(u)
                if html:
                    snippet = re.sub(r"<[^>]+>", " ", html)
                    snippet = re.sub(r"\s+", " ", snippet)[:500]
                    urls_analizadas.append({"url": u, "extracto": snippet})
            except Exception:
                continue
    else:
        urls_analizadas = [{"url": u, "extracto": "(no procesado)"} for u in urls]

    # 5) Identificar actores/regiones/sectores
    textos = [m.get("title", "") + " " + m.get("summary", "") for m in matches["articulos"]]
    textos += [u["extracto"] for u in urls_analizadas if u.get("extracto")]
    textos.append(caso + " " + comentario + " " + regiones_actores)

    actores = _identificar_actores(textos)
    regiones = _identificar_regiones(textos)
    sectores = _identificar_sectores(textos)

    # 6) Tendencia temporal
    now = datetime.now(PET)
    cnt_24h = cnt_24_72h = cnt_3_7d = 0
    for a in matches["articulos"]:
        try:
            pub = a.get("published", "")
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=PET)
            h = (now - dt).total_seconds() / 3600
            if h <= 24:
                cnt_24h += 1
            elif h <= 72:
                cnt_24_72h += 1
            elif h <= 168:
                cnt_3_7d += 1
        except Exception:
            continue

    tendencia, razon_tendencia = _clasificar_tendencia(cnt_24h, cnt_24_72h, cnt_3_7d)

    # 7) Cobertura
    cobertura = _analizar_cobertura(matches["articulos"])

    # 8) Encontrar factor de riesgo top (cruce con la matriz P×I del snapshot)
    factor_riesgo_top = None
    if snapshot_actual and snapshot_actual.get("matriz_riesgo"):
        for f in snapshot_actual["matriz_riesgo"]:
            f_kws = (f.get("keywords_fuertes", []) or []) + \
                     (f.get("keywords_contexto", []) or []) + \
                     (f.get("keywords", []) or [])
            blob = (caso + " " + comentario).lower()
            if any(kw.lower() in blob for kw in f_kws):
                factor_riesgo_top = f
                break

    # 9) Dimensiones de riesgo
    dimensiones = _evaluar_dimensiones_riesgo(actores, regiones, sectores,
                                                matches["alertas"], factor_riesgo_top)

    # 10) Score global y nivel
    score_global = round(sum(dimensiones.values()) / 6, 1)
    nivel_riesgo = _clasificar_nivel(score_global)

    # 11) Proyección, escenarios, alertas tempranas, recomendaciones
    proyeccion = _proyeccion_mediatica(cnt_24h, cnt_24h + cnt_24_72h,
                                          cobertura["n_medios_distintos"], tendencia)
    escenarios = _escenarios_prospectivos(tendencia, nivel_riesgo, cnt_24h)
    alertas_temp = _alertas_tempranas(tendencia, actores, regiones, dimensiones)
    recomendaciones = _recomendacion_analista(nivel_riesgo, tendencia, dimensiones)

    # 12) Confiabilidad
    confiabilidad = _evaluar_confiabilidad(matches["articulos"], matches["alertas"])

    # 13) Determinar naturaleza del evento (heurística)
    naturaleza = "Político-institucional"
    if any(r in ("Apurímac", "Apurimac", "Cusco", "Puno", "Cajamarca") for r in regiones):
        if "Comunidades campesinas" in actores or "Empresas extractivas" in actores:
            naturaleza = "Conflicto socioambiental"
    if "JNE" in actores or "ONPE" in actores:
        naturaleza = "Electoral"
    if "FFAA / Comando Conjunto" in actores or "PNP / Mininter" in actores:
        naturaleza = "Seguridad / Militar"
    if "Fiscalía / MP" in actores or "Poder Judicial" in actores:
        naturaleza = "Judicial / Anticorrupción"
    if dimensiones.get("economico", 0) >= 60:
        naturaleza = "Económico-financiero"

    return {
        "input": {
            "caso": caso,
            "comentario": comentario,
            "urls": urls,
            "periodo": periodo,
            "profundidad": profundidad,
            "regiones_actores": regiones_actores,
            "solicitante": solicitante,
        },
        "generado_en": now.isoformat(timespec="seconds"),
        "keywords_extraidas": kws,
        "naturaleza": naturaleza,
        "score_global": score_global,
        "nivel_riesgo": nivel_riesgo,
        "tendencia": tendencia,
        "razon_tendencia": razon_tendencia,
        "actores": actores,
        "regiones": regiones,
        "sectores": sectores,
        "dimensiones": dimensiones,
        "factor_riesgo_top": factor_riesgo_top,
        "cobertura": cobertura,
        "proyeccion_mediatica": proyeccion,
        "escenarios": escenarios,
        "alertas_tempranas": alertas_temp,
        "confiabilidad": confiabilidad,
        "recomendaciones": recomendaciones,
        "articulos_relacionados": matches["articulos"][:30],
        "alertas_relacionadas": matches["alertas"][:15],
        "urls_analizadas": urls_analizadas,
        "menciones_24h": cnt_24h,
        "menciones_72h": cnt_24h + cnt_24_72h,
        "menciones_7d": cnt_24h + cnt_24_72h + cnt_3_7d,
    }
