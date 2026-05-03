"""Matriz de Riesgo: Probabilidad × Impacto.

Genera una lista de factores de riesgo, cada uno con:
  - probabilidad (0-100): qué tan probable es que se materialice en el corto plazo
  - impacto (0-100): magnitud del efecto si ocurre
  - score = sqrt(prob * imp)
  - tendencia ↑ ↓ → según volumen reciente vs anterior
  - evidencia: URLs y titulares de soporte

Heurísticas MVP basadas en frecuencia de keywords, severidad de conflictos y
recencia. Reemplazables por modelos bayesianos / forecasting en producción.
"""
from __future__ import annotations
import math
from collections import Counter

# Definición de factores de riesgo y sus señales (keywords / patrones)
FACTORES = [
    {
        "id": "vacancia_presidencial",
        "nombre": "Vacancia presidencial",
        "categoria": "Estabilidad gubernamental",
        "impacto_base": 95,
        "keywords": ["vacancia", "incapacidad moral", "destituye", "destitución"],
        "descripcion": "Activación de moción de vacancia que puede deponer al titular del Ejecutivo.",
    },
    {
        "id": "censura_gabinete",
        "nombre": "Censura / interpelación al Gabinete",
        "categoria": "Estabilidad gubernamental",
        "impacto_base": 75,
        "keywords": ["interpelación", "interpelacion", "censura", "cuestión de confianza", "cuestion de confianza"],
        "descripcion": "Interpelación o censura a ministros con efecto de recambio de gabinete.",
    },
    {
        "id": "renuncia_ministro",
        "nombre": "Renuncia de ministro clave",
        "categoria": "Estabilidad gubernamental",
        "impacto_base": 70,
        "keywords": ["renuncia", "renuncia ministro", "deja el cargo"],
        "descripcion": "Salida abrupta de ministros sectoriales que afecte continuidad de políticas.",
    },
    {
        "id": "conflictos_extractivos",
        "nombre": "Bloqueos en zonas extractivas",
        "categoria": "Conflictos sociales",
        "impacto_base": 85,
        "keywords": ["bloqueo", "corredor minero", "las bambas", "antamina", "tía maría", "tia maria", "espinar"],
        "descripcion": "Paralización de operaciones mineras o corredores logísticos clave.",
    },
    {
        "id": "paros_regionales",
        "nombre": "Paros regionales / panamericana",
        "categoria": "Conflictos sociales",
        "impacto_base": 70,
        "keywords": ["paro", "panamericana", "interoceánica", "interoceanica", "huelga", "frente de defensa"],
        "descripcion": "Paros con bloqueo de vías nacionales y disrupción logística.",
    },
    {
        "id": "reforma_electoral",
        "nombre": "Reforma electoral regresiva",
        "categoria": "Riesgo regulatorio",
        "impacto_base": 75,
        "keywords": ["reforma electoral", "valla electoral", "bicameralidad", "financiamiento de partidos"],
        "descripcion": "Reformas que debiliten contrapesos democráticos rumbo a 2026.",
    },
    {
        "id": "regulacion_sectorial",
        "nombre": "Regulación sectorial restrictiva",
        "categoria": "Riesgo regulatorio",
        "impacto_base": 70,
        "keywords": ["decreto de urgencia", "modifica ley", "consulta previa", "regulación", "regulacion"],
        "descripcion": "Normas que afecten estabilidad jurídica o reglas de juego sectorial.",
    },
    {
        "id": "investigacion_corrupcion",
        "nombre": "Investigaciones por corrupción",
        "categoria": "Corrupción",
        "impacto_base": 65,
        "keywords": ["soborno", "lavado", "fiscalía", "fiscalia", "denuncia", "imputado", "lava jato", "audios"],
        "descripcion": "Casos de corrupción que comprometan a actores políticos relevantes.",
    },
    {
        "id": "deterioro_seguridad",
        "nombre": "Deterioro de seguridad ciudadana",
        "categoria": "Seguridad",
        "impacto_base": 60,
        "keywords": ["sicariato", "extorsión", "extorsion", "homicidio", "asesinato", "narco", "ataque", "estado de emergencia"],
        "descripcion": "Eventos violentos urbanos que escalen a crisis de seguridad pública.",
    },
    {
        "id": "presion_economica",
        "nombre": "Presión sobre estabilidad económica",
        "categoria": "Económico",
        "impacto_base": 80,
        "keywords": ["riesgo país", "riesgo pais", "embig", "calificadora", "fitch", "s&p", "moody", "tipo de cambio", "fuga de capitales"],
        "descripcion": "Movimientos en riesgo país, tipo de cambio o calificación soberana.",
    },
    {
        "id": "corrupcion_sistemica",
        "nombre": "Corrupción sistémica de altos cargos",
        "categoria": "Corrupción",
        "impacto_base": 80,
        "keywords": ["lava jato", "odebrecht", "ministerio público", "ministerio publico", "junta nacional de justicia", "jnj",
                     "729 delitos", "67 congresistas", "denuncia constitucional", "lavado de activos", "organización criminal"],
        "descripcion": "Casos de corrupción sistémica que comprometan poderes del Estado y captura institucional.",
    },
    {
        "id": "intervencion_ffaa",
        "nombre": "Intervención de las FFAA en orden interno",
        "categoria": "Militar / Seguridad",
        "impacto_base": 90,
        "keywords": ["fuerzas armadas", "ffaa", "ejército del perú", "ejercito del peru", "comando conjunto", "vraem",
                     "estado de emergencia", "estado de excepción", "estado de excepcion",
                     "militares en las calles", "militarización", "militarizacion",
                     "presencia armada", "operaciones militares", "toque de queda",
                     "régimen de excepción", "regimen de excepcion", "fuero militar",
                     "ccffaa", "comando operacional", "patrulla militar"],
        "descripcion": "Despliegue militar en zonas urbanas o intervención en orden interno (potencial regresión democrática).",
    },
    {
        "id": "tensiones_fronterizas",
        "nombre": "Tensiones fronterizas",
        "categoria": "Seguridad nacional",
        "impacto_base": 85,
        "keywords": ["frontera con chile", "frontera con ecuador", "frontera con bolivia", "frontera con brasil",
                     "tacna", "puno bolivia", "tumbes ecuador", "muro fronterizo", "escudo fronterizo",
                     "kast", "boric", "estado de emergencia frontera", "incidente fronterizo"],
        "descripcion": "Incidentes, militarización o disputas en zonas fronterizas con países vecinos.",
    },
    {
        "id": "crisis_migratoria",
        "nombre": "Crisis migratoria",
        "categoria": "Social / Seguridad",
        "impacto_base": 75,
        "keywords": ["migrantes venezolanos", "migración venezolana", "migracion venezolana", "tren de aragua",
                     "expulsión migrantes", "expulsion migrantes", "ingreso irregular", "deportación",
                     "deportacion", "regularización migratoria", "regularizacion migratoria",
                     "crimen organizado venezolano"],
        "descripcion": "Flujos migratorios masivos, expulsiones y crimen organizado transnacional asociado.",
    },
    {
        "id": "tensiones_diplomaticas",
        "nombre": "Tensiones diplomáticas",
        "categoria": "Diplomacia / Geopolítica",
        "impacto_base": 75,
        "keywords": ["ruptura diplomática", "ruptura diplomatica", "embajador", "embajada",
                     "cancillería", "cancilleria", "sheinbaum", "asilo betssy chávez", "asilo betssy chavez",
                     "relaciones diplomáticas", "relaciones diplomaticas", "reconciliación bilateral",
                     "reconciliacion bilateral", "diálogo discreto", "dialogo discreto", "persona non grata"],
        "descripcion": "Rupturas, congelamientos o crisis con países clave (México, Chile, Venezuela, Bolivia, EE.UU.).",
    },
    {
        "id": "violencia_electoral",
        "nombre": "Violencia electoral",
        "categoria": "Estabilidad gubernamental / Seguridad",
        "impacto_base": 92,
        "keywords": ["magnicidio", "atentado contra", "atentado al candidato",
                     "asesinato candidato", "asesinato de candidato",
                     "elecciones nulas", "elecciones inválidas", "elecciones invalidas",
                     "elecciones cuestionadas", "elecciones anuladas", "fraude electoral",
                     "anulación electoral", "anulacion electoral", "impugnación electoral",
                     "impugnacion electoral", "votos cuestionados", "actas falsificadas",
                     "violencia en mesas de votación", "violencia en mesas de votacion",
                     "ataque a local de votación", "ataque a local de votacion",
                     "amenaza candidato", "amenaza de muerte candidato",
                     "agresión candidato", "agresion candidato",
                     "balacera mitin", "ataque mitin", "atentado mitin"],
        "descripcion": "Violencia física contra candidatos, atentados, magnicidios, fraude o impugnación masiva de resultados electorales.",
    },
]


def _texto(a) -> str:
    return ((a.title or "") + " " + (a.summary or "")).lower()


def _tendencia(reciente: int, previo: int) -> str:
    if reciente > previo * 1.3 and reciente >= 2:
        return "↑"
    if previo > reciente * 1.3 and previo >= 2:
        return "↓"
    return "→"


def calcular_matriz(articulos: list, conflictos: list) -> list[dict]:
    """Construye la lista de factores de riesgo con prob/impacto/evidencia."""
    out: list[dict] = []
    todos = list(articulos) + list(conflictos)

    # contadores recientes vs previos para tendencia
    for f in FACTORES:
        evidencias = []
        cnt_reciente = 0  # < 24h
        cnt_previo = 0    # 24-72h
        criticidad_max = "media"
        for a in todos:
            text = _texto(a)
            if any(kw in text for kw in f["keywords"]):
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
                })
                if a.criticidad == "alta":
                    criticidad_max = "alta"

        # probabilidad heurística
        # base: 20% + 12 puntos por cada mención reciente, +5 por mención previa, +20 si criticidad alta
        prob = 20 + cnt_reciente * 12 + cnt_previo * 5
        if criticidad_max == "alta":
            prob += 20
        prob = min(95, prob)
        if cnt_reciente == 0 and cnt_previo == 0:
            prob = 10

        impacto = f["impacto_base"]
        # ajuste por criticidad de la evidencia
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
            "evidencias": evidencias[:6],  # top 6
        })

    # ordenar por score desc
    out.sort(key=lambda x: -x["score"])
    return out
