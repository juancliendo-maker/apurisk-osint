"""Matriz de Riesgo: Probabilidad × Impacto.

Sistema de matching estricto en TRES capas para evitar dispersión:
  1. keywords_fuertes: frases muy específicas (multipalabra). Match = peso 3.
  2. keywords_contexto: palabras de respaldo (1-2 palabras). Match = peso 1.
  3. keywords_negacion: si aparece, descarta el match (referencias históricas, etc.).

Para asociar una nota a un factor, debe cumplir AL MENOS UNO:
  - ≥1 keyword fuerte (frase específica)
  - ≥2 keywords de contexto simultáneas

Adicional: filtro temporal (default 7 días) — items más viejos se descartan.

Esto resuelve el problema clásico de matching superficial donde una nota
sobre "ataque a libertad de prensa" se asociaba erróneamente al factor
"deterioro de seguridad" solo por la palabra "ataque".
"""
from __future__ import annotations
import math
import re
from collections import Counter


# ============================================================================
# DEFINICIÓN DE FACTORES (con keywords estructuradas)
# ============================================================================
FACTORES = [
    {
        "id": "vacancia_presidencial",
        "nombre": "Vacancia presidencial",
        "categoria": "Estabilidad gubernamental",
        "impacto_base": 95,
        "keywords_fuertes": [
            "moción de vacancia presidencial",
            "moción de vacancia",
            "mocion de vacancia",
            "vacancia presidencial",
            "firmas para vacancia",
            "destitución del presidente",
            "destitucion del presidente",
            "destituir al presidente",
            "incapacidad moral permanente",
            "vaca al presidente",
            "vacaron al presidente",
            "vacancia contra el presidente",
        ],
        "keywords_contexto": ["vacancia", "destituye", "destituir", "destitución"],
        "keywords_negacion": [
            "vacancia de boluarte", "vacancia de jeri", "vacancia de jerí",
            "vacancia de pedro castillo", "vacancia de vizcarra", "vacancia de pablo kuczynski",
            "vacancia de ppk", "anterior vacancia", "previa vacancia",
            "histórico de vacancias", "vacancias en perú",
        ],
        "descripcion": "Activación actual de moción de vacancia que puede deponer al titular del Ejecutivo.",
    },
    {
        "id": "censura_gabinete",
        "nombre": "Censura / interpelación al Gabinete",
        "categoria": "Estabilidad gubernamental",
        "impacto_base": 75,
        "keywords_fuertes": [
            "moción de interpelación", "mocion de interpelacion",
            "moción de censura", "mocion de censura",
            "cuestión de confianza", "cuestion de confianza",
            "interpelar al ministro", "interpelar al premier",
            "censuran al ministro", "censuran al premier",
            "interpelación al gabinete",
        ],
        "keywords_contexto": ["interpelación", "interpelacion", "censura", "ministro"],
        "keywords_negacion": ["interpelación de prensa", "censura previa", "censura mediática"],
        "descripcion": "Interpelación o censura formal a ministros con efecto de recambio de gabinete.",
    },
    {
        "id": "renuncia_ministro",
        "nombre": "Renuncia de ministro clave",
        "categoria": "Estabilidad gubernamental",
        "impacto_base": 70,
        "keywords_fuertes": [
            "renuncia del ministro", "renuncia el ministro",
            "renuncia irrevocable del ministro",
            "presentó su renuncia el ministro",
            "presento su renuncia el ministro",
            "ministro presenta renuncia",
            "ministro renuncia",
            "deja el cargo el ministro",
            "renuncia del premier", "renuncia el premier",
            "premier renuncia",
        ],
        "keywords_contexto": ["renuncia ministro", "renuncia premier", "deja el cargo"],
        "keywords_negacion": ["renuncia de candidato", "renuncia de congresista"],
        "descripcion": "Salida abrupta de ministros sectoriales que afecte continuidad de políticas.",
    },
    {
        "id": "conflictos_extractivos",
        "nombre": "Bloqueos en zonas extractivas",
        "categoria": "Conflictos sociales",
        "impacto_base": 85,
        "keywords_fuertes": [
            "bloqueo del corredor minero",
            "bloquean corredor minero",
            "paralizan operaciones mineras",
            "comunidades bloquean las bambas",
            "bloqueo en las bambas",
            "paro en antamina", "bloqueo en antamina",
            "bloqueo en tía maría", "bloqueo en tia maria",
            "bloqueo en espinar", "paro en espinar",
            "comunidades de cotabambas bloquean",
            "comunidades campesinas paralizan",
        ],
        "keywords_contexto": ["las bambas", "antamina", "tía maría", "tia maria", "espinar",
                              "corredor minero", "cotabambas", "comunidades campesinas",
                              "operaciones mineras"],
        "keywords_negacion": ["acuerdo en las bambas", "diálogo en las bambas", "fin del bloqueo"],
        "descripcion": "Paralización ACTUAL de operaciones mineras o corredores logísticos clave.",
    },
    {
        "id": "paros_regionales",
        "nombre": "Paros regionales / panamericana",
        "categoria": "Conflictos sociales",
        "impacto_base": 70,
        "keywords_fuertes": [
            "paro regional", "paro indefinido",
            "paro nacional",
            "bloquean panamericana", "bloqueo en la panamericana",
            "bloquean interoceánica", "bloquean interoceanica",
            "bloqueo en la interoceánica",
            "paro de transportistas", "paro agrario",
            "huelga indefinida",
            "frente de defensa convoca paro",
            "convocan paro",
        ],
        "keywords_contexto": ["paro", "panamericana", "interoceánica", "interoceanica",
                              "huelga", "frente de defensa", "movilización"],
        "keywords_negacion": ["paro cardíaco", "paro respiratorio", "paro biológico"],
        "descripcion": "Paros con bloqueo de vías nacionales y disrupción logística.",
    },
    {
        "id": "reforma_electoral",
        "nombre": "Reforma electoral regresiva",
        "categoria": "Riesgo regulatorio",
        "impacto_base": 75,
        "keywords_fuertes": [
            "reforma electoral", "reforma constitucional electoral",
            "elimina la valla electoral", "valla electoral",
            "reforma de bicameralidad", "implementación bicameralidad",
            "implementacion bicameralidad",
            "ley de financiamiento de partidos",
            "reforma del sistema electoral",
        ],
        "keywords_contexto": ["bicameralidad", "valla electoral", "financiamiento de partidos",
                              "reforma electoral", "JNE", "ONPE"],
        "keywords_negacion": ["reforma electoral en chile", "reforma electoral en bolivia",
                              "reforma electoral en méxico"],
        "descripcion": "Reformas que debiliten contrapesos democráticos rumbo a elecciones.",
    },
    {
        "id": "regulacion_sectorial",
        "nombre": "Regulación sectorial restrictiva",
        "categoria": "Riesgo regulatorio",
        "impacto_base": 70,
        "keywords_fuertes": [
            "decreto de urgencia",
            "modifica la ley de", "modifica ley de consulta previa",
            "ley de consulta previa", "convenio 169 oit",
            "regulación sectorial", "regulacion sectorial",
            "nueva regulación restrictiva",
        ],
        "keywords_contexto": ["decreto de urgencia", "consulta previa", "convenio 169"],
        "keywords_negacion": ["decreto educativo", "decreto cultural"],
        "descripcion": "Normas que afecten estabilidad jurídica o reglas de juego sectorial.",
    },
    {
        "id": "investigacion_corrupcion",
        "nombre": "Investigaciones por corrupción",
        "categoria": "Corrupción",
        "impacto_base": 65,
        "keywords_fuertes": [
            "investigación preliminar contra", "investigacion preliminar contra",
            "fiscalía formaliza denuncia", "fiscalia formaliza denuncia",
            "fiscalía denuncia a", "fiscalia denuncia a",
            "denuncia constitucional contra",
            "fiscalía allana", "fiscalia allana", "allanamiento de fiscalía",
            "fiscal acusa a",
            "presunto soborno", "presuntos sobornos",
            "lavado de activos contra", "lavado de activos por",
            "imputación contra", "imputado por",
            "audios revelan",
        ],
        "keywords_contexto": ["fiscalía", "fiscalia", "ministerio público", "ministerio publico",
                              "imputación", "imputado", "soborno", "lava jato", "audios"],
        "keywords_negacion": ["caso archivado", "absuelto", "exonerado",
                              "investigación cerrada", "cierre de investigación"],
        "descripcion": "Casos de corrupción ACTIVOS que comprometan a actores políticos relevantes.",
    },
    {
        "id": "deterioro_seguridad",
        "nombre": "Deterioro de seguridad ciudadana",
        "categoria": "Seguridad",
        "impacto_base": 60,
        "keywords_fuertes": [
            "sicariato", "asesinato a manos de sicarios",
            "extorsión a comerciantes", "extorsion a comerciantes",
            "ola de extorsiones", "casos de extorsión",
            "homicidios en lima", "asesinatos en lima",
            "balacera en", "ataque armado en",
            "estado de emergencia por inseguridad",
            "estado de emergencia ciudadana",
            "crimen organizado en perú", "crimen organizado en peru",
            "ataque a comisaría", "ataque a comisaria",
            "narcotraficantes",
        ],
        "keywords_contexto": ["sicariato", "extorsión", "extorsion",
                              "homicidio doloso", "asesinato dolo",
                              "narcotráfico", "narcotrafico",
                              "estado de emergencia"],
        "keywords_negacion": [
            # CRÍTICO: descartar ataques a libertad de prensa, ataques verbales, etc.
            "ataque a la prensa", "ataque a libertad de prensa",
            "ataque al periodismo", "ataque verbal",
            "ataque informático", "ataque cibernético",
            "ataque cardíaco", "ataque al corazón",
            "ataque a la democracia", "ataque a la constitución",
            "ataque a la oposición", "ataque al gobierno",
        ],
        "descripcion": "Eventos violentos urbanos (sicariato, extorsión, homicidios) que escalen a crisis de seguridad pública.",
    },
    {
        "id": "presion_economica",
        "nombre": "Presión sobre estabilidad económica",
        "categoria": "Económico",
        "impacto_base": 80,
        "keywords_fuertes": [
            "riesgo país sube", "riesgo pais sube",
            "embig sube", "embig peru",
            "calificación soberana", "calificacion soberana",
            "fitch baja", "fitch peru",
            "moody's rebaja", "moodys rebaja", "moody peru",
            "s&p rebaja",
            "sol peruano se deprecia",
            "fuga de capitales",
            "tipo de cambio sube",
        ],
        "keywords_contexto": ["riesgo país", "riesgo pais", "embig",
                              "calificadora", "fitch", "s&p", "moody",
                              "tipo de cambio", "BCR"],
        "keywords_negacion": ["riesgo país de chile", "riesgo país de méxico"],
        "descripcion": "Movimientos en riesgo país, tipo de cambio o calificación soberana.",
    },
    {
        "id": "corrupcion_sistemica",
        "nombre": "Corrupción sistémica de altos cargos",
        "categoria": "Corrupción",
        "impacto_base": 80,
        "keywords_fuertes": [
            "captura del estado", "captura del estado por",
            "lava jato perú", "lava jato peru",
            "caso odebrecht",
            "729 delitos", "67 congresistas",
            "denuncia constitucional contra",
            "investigación a la junta nacional de justicia",
            "investigacion a la jnj", "denuncia a la jnj",
            "captura institucional",
            "organización criminal en el congreso",
            "organizacion criminal en el congreso",
            "investigación a la fiscal",
            "ministerio público investigado",
            "ministerio publico investigado",
        ],
        "keywords_contexto": ["lava jato", "odebrecht", "ministerio público",
                              "ministerio publico", "JNJ",
                              "captura institucional", "denuncia constitucional"],
        "keywords_negacion": ["lava jato brasil", "lava jato argentina",
                              "odebrecht en otros países"],
        "descripcion": "Casos de corrupción sistémica que comprometan poderes del Estado y captura institucional.",
    },
    {
        "id": "intervencion_ffaa",
        "nombre": "Intervención de las FFAA en orden interno",
        "categoria": "Militar / Seguridad",
        "impacto_base": 90,
        "keywords_fuertes": [
            "estado de emergencia decretado",
            "decretan estado de emergencia",
            "ejecutivo decreta estado de emergencia",
            "estado de excepción decretado",
            "comando conjunto despliega",
            "comando conjunto interviene",
            "operativo militar en",
            "patrulla militar dispara",
            "patrulla militar mata",
            "operación antidrogas militar",
            "operacion antidrogas militar",
            "militares disparan a civiles",
            "ejército dispara a civiles",
            "ejercito dispara a civiles",
            "fuerzas armadas en las calles",
            "ffaa en las calles",
            "militarización del orden interno",
            "militarizacion del orden interno",
            "toque de queda decretado",
            "régimen de excepción", "regimen de excepcion",
        ],
        "keywords_contexto": ["fuerzas armadas", "ffaa", "ejército del perú",
                              "ejercito del peru", "comando conjunto",
                              "operación militar", "operacion militar"],
        "keywords_negacion": [
            "desfile militar", "ceremonia militar", "homenaje a las ffaa",
            "ascenso militar", "promoción militar",
            "comando conjunto en 2022", "comando conjunto en 2023",
            "comando conjunto en 2024", "comando conjunto en 2025",
            "anterior comando conjunto", "ex comando conjunto",
            "histórico del comando conjunto",
        ],
        "descripcion": "Despliegue militar en zonas urbanas o intervención ACTUAL en orden interno (potencial regresión democrática).",
    },
    {
        "id": "tensiones_fronterizas",
        "nombre": "Tensiones fronterizas",
        "categoria": "Seguridad nacional",
        "impacto_base": 85,
        "keywords_fuertes": [
            "tensión en la frontera con", "tension en la frontera con",
            "incidente fronterizo en",
            "muro fronterizo",
            "escudo fronterizo",
            "estado de emergencia en frontera",
            "militarización de la frontera",
            "militares peruanos en la frontera",
            "100 agentes en la frontera",
            "blindar la frontera",
            "kast frontera", "kast en la frontera",
        ],
        "keywords_contexto": ["frontera con chile", "frontera con ecuador",
                              "frontera con bolivia", "frontera con brasil",
                              "tacna", "muro fronterizo", "escudo fronterizo"],
        "keywords_negacion": ["frontera digital", "frontera comercial",
                              "frontera del conocimiento"],
        "descripcion": "Incidentes, militarización o disputas en zonas fronterizas con países vecinos.",
    },
    {
        "id": "crisis_migratoria",
        "nombre": "Crisis migratoria",
        "categoria": "Social / Seguridad",
        "impacto_base": 75,
        "keywords_fuertes": [
            "expulsión masiva de migrantes",
            "expulsion masiva de migrantes",
            "deportación masiva",
            "deportacion masiva",
            "ingreso irregular masivo",
            "tren de aragua opera en",
            "tren de aragua en perú", "tren de aragua en peru",
            "regularización migratoria",
            "regularizacion migratoria",
            "venezolanos expulsados",
            "ola migratoria",
        ],
        "keywords_contexto": ["migrantes venezolanos", "migración venezolana",
                              "tren de aragua", "expulsión migrantes"],
        "keywords_negacion": ["aragua venezuela", "estado aragua"],
        "descripcion": "Flujos migratorios masivos, expulsiones y crimen organizado transnacional asociado.",
    },
    {
        "id": "tensiones_diplomaticas",
        "nombre": "Tensiones diplomáticas",
        "categoria": "Diplomacia / Geopolítica",
        "impacto_base": 75,
        "keywords_fuertes": [
            "ruptura diplomática con", "ruptura diplomatica con",
            "expulsa al embajador", "expulsa a la embajadora",
            "retira al embajador", "retira a la embajadora",
            "persona non grata",
            "rompe relaciones con",
            "convoca al embajador", "llama a consultas al embajador",
            "embajada resguardada",
            "asilo a betssy chávez", "asilo a betssy chavez",
            "sheinbaum perú", "sheinbaum peru",
        ],
        "keywords_contexto": ["ruptura diplomática", "ruptura diplomatica",
                              "embajador", "cancillería", "cancilleria",
                              "persona non grata", "asilo"],
        "keywords_negacion": ["embajada en otro país"],
        "descripcion": "Rupturas, congelamientos o crisis con países clave (México, Chile, Venezuela, Bolivia, EE.UU.).",
    },
    {
        "id": "violencia_electoral",
        "nombre": "Violencia electoral",
        "categoria": "Estabilidad gubernamental / Seguridad",
        "impacto_base": 92,
        "keywords_fuertes": [
            "magnicidio",
            "atentado contra el candidato", "atentado al candidato",
            "asesinato del candidato", "asesinato a candidato",
            "asesinaron al candidato",
            "intento de asesinato del candidato",
            "elecciones declaradas nulas",
            "elecciones inválidas", "elecciones invalidas",
            "elecciones cuestionadas", "elecciones anuladas",
            "fraude electoral comprobado",
            "anulación electoral", "anulacion electoral",
            "impugnación electoral",
            "actas falsificadas", "actas adulteradas",
            "ataque a local de votación", "ataque a local de votacion",
            "balacera en mitin", "ataque al mitin",
            "agresión al candidato", "agresion al candidato",
            "amenaza de muerte al candidato",
        ],
        "keywords_contexto": ["candidato presidencial", "balotaje",
                              "fraude electoral", "magnicidio"],
        "keywords_negacion": ["fraude bancario", "fraude tributario",
                              "magnicidio en otro país", "magnicidio en otro pais"],
        "descripcion": "Violencia física contra candidatos, atentados, magnicidios, fraude o impugnación masiva de resultados electorales.",
    },
]


# Configuración global de matching
MATCH_CONFIG = {
    "ventana_dias_max": 7,           # Solo items publicados en los últimos N días
    "score_min_strong": 1,           # Mínimo de keywords fuertes para asociar
    "score_min_contexto": 2,         # O mínimo de keywords de contexto
    "peso_keyword_fuerte": 3,        # Multiplicador para score de relevancia
    "peso_keyword_contexto": 1,
}


def _texto(a) -> str:
    return ((a.title or "") + " " + (a.summary or "")).lower()


def _matchea(text: str, keywords: list[str]) -> int:
    """Cuenta cuántas keywords matchean en el texto, usando word boundaries
    para frases multipalabra y matching exacto.
    """
    n = 0
    for kw in keywords:
        kw_low = kw.lower().strip()
        if not kw_low:
            continue
        # Escape regex y permitir matchear como frase
        pattern = r"\b" + re.escape(kw_low) + r"\b"
        if re.search(pattern, text):
            n += 1
    return n


def _es_relevante(text: str, factor: dict) -> tuple[bool, int]:
    """Decide si una nota es relevante para un factor.
    Retorna (es_relevante, score_relevancia).
    """
    # 1) Si tiene keywords de negación, descartar inmediatamente
    kw_neg = factor.get("keywords_negacion", [])
    if kw_neg and _matchea(text, kw_neg) > 0:
        return False, 0

    # 2) Contar keywords fuertes y de contexto
    kw_fuertes = factor.get("keywords_fuertes", [])
    kw_contexto = factor.get("keywords_contexto", [])

    # Backward compat: si solo tiene "keywords" (formato viejo), usarlas como contexto
    if not kw_fuertes and not kw_contexto and factor.get("keywords"):
        kw_contexto = factor["keywords"]

    n_fuertes = _matchea(text, kw_fuertes)
    n_contexto = _matchea(text, kw_contexto)

    # 3) Regla de decisión
    score = (n_fuertes * MATCH_CONFIG["peso_keyword_fuerte"] +
             n_contexto * MATCH_CONFIG["peso_keyword_contexto"])

    if n_fuertes >= MATCH_CONFIG["score_min_strong"]:
        return True, score
    if n_contexto >= MATCH_CONFIG["score_min_contexto"]:
        return True, score
    return False, 0


def _tendencia(reciente: int, previo: int) -> str:
    if reciente > previo * 1.3 and reciente >= 2:
        return "↑"
    if previo > reciente * 1.3 and previo >= 2:
        return "↓"
    return "→"


def calcular_matriz(articulos: list, conflictos: list) -> list[dict]:
    """Construye la lista de factores de riesgo con prob/impacto/evidencia.

    Aplica matching estricto (3 capas) y filtro temporal de 7 días.
    """
    out: list[dict] = []
    todos = list(articulos) + list(conflictos)

    # FILTRO TEMPORAL: solo considerar items publicados en los últimos 7 días
    horas_max = MATCH_CONFIG["ventana_dias_max"] * 24
    todos_recientes = [a for a in todos if a.hours_ago() <= horas_max]

    for f in FACTORES:
        evidencias = []
        cnt_reciente = 0  # < 24h
        cnt_previo = 0    # 24-72h
        criticidad_max = "media"

        for a in todos_recientes:
            text = _texto(a)
            relevante, _score_rel = _es_relevante(text, f)
            if not relevante:
                continue

            hours = a.hours_ago()
            if hours <= 24:
                cnt_reciente += 1
            elif hours <= 72:
                cnt_previo += 1

            evidencias.append({
                "title": a.title,
                "url": a.url,
                "source": a.source_name,
                "hours_ago": round(hours, 1) if hours != float("inf") else None,
                "criticidad": a.criticidad,
                "score_relevancia": _score_rel,
            })
            if a.criticidad == "alta":
                criticidad_max = "alta"

        # Ordenar evidencias por relevancia (más relevantes primero)
        evidencias.sort(key=lambda e: -e.get("score_relevancia", 0))

        # probabilidad heurística
        prob = 20 + cnt_reciente * 12 + cnt_previo * 5
        if criticidad_max == "alta":
            prob += 20
        prob = min(95, prob)
        if cnt_reciente == 0 and cnt_previo == 0:
            prob = 10

        impacto = f["impacto_base"]
        if criticidad_max == "alta":
            impacto = min(100, impacto + 5)

        score = round(math.sqrt(prob * impacto), 1)
        if score >= 70:
            nivel = "CRÍTICO"
        elif score >= 55:
            nivel = "ALTO"
        elif score >= 35:
            nivel = "MEDIO"
        else:
            nivel = "BAJO"

        out.append({
            "id": f["id"],
            "nombre": f["nombre"],
            "categoria": f["categoria"],
            "descripcion": f["descripcion"],
            "probabilidad": prob,
            "impacto": impacto,
            "score": score,
            "nivel": nivel,
            "tendencia": _tendencia(cnt_reciente, cnt_previo),
            "menciones_24h": cnt_reciente,
            "menciones_72h": cnt_previo,
            "evidencias": evidencias[:6],
        })

    out.sort(key=lambda x: -x["score"])
    return out
