"""Detección de Evento Crítico · Sprint 1.6.

Clasifica si alguno de los eventos del ciclo encaja en una de las 8 categorías
de "evento crítico" que rompen el suavizado temporal del Score Nacional v2.

Cuando se detecta un evento crítico con confianza ≥ 70, el score nacional puede
SALTAR (no se suaviza con el de ayer) — la doctrina es que una vacancia
formalmente presentada o una ruptura diplomática severa NO debe quedar
diluida con el promedio de los días previos.

CATEGORÍAS CANÓNICAS (8) — del brief del usuario:
  1. Vacancia presidencial formalmente presentada
  2. Cierre o intervención de instituciones
  3. Violencia política con muertos
  4. Paro nacional con bloqueos estratégicos
  5. Ruptura diplomática severa
  6. Estado de emergencia nacional
  7. Crisis militar/policial
  8. Crisis constitucional abierta

ESTRATEGIA DE DETECCIÓN:

  1. LLM primero (Claude Haiku 4.5):
       · Recibe lista de eventos con titulares + metadata
       · Devuelve {detectado, tipo, evento_id, confianza, justificacion}
       · Si la API no está disponible o falla, cae a heurística
  2. Heurística como fallback:
       · Match de keywords + actor + ubicación + tipo de evento
       · Confianza máxima 65 (siempre por debajo del umbral LLM de 70)
       · Justificación basada en reglas

  Esto garantiza que el sistema funcione SIN dependencia obligatoria de
  Claude (deploys sin API key siguen produciendo score válido), pero
  cuando Claude está disponible, su clasificación pesa más.
"""
from __future__ import annotations
import json
from typing import Any


# =====================================================================
# CATÁLOGO DE EVENTOS CRÍTICOS · 8 categorías canónicas
# =====================================================================
# Orden: las categorías más específicas van PRIMERO. "Disolución del congreso"
# matchea crisis_constitucional antes que intervencion_institucional.
TIPOS_EVENTOS_CRITICOS: list[dict] = [
    {
        "id": "crisis_constitucional",
        "nombre": "Crisis constitucional abierta",
        "keywords_obligatorias": ["cuestión de confianza denegada",
                                    "disolución del congreso",
                                    "disuelve el congreso",
                                    "ruptura constitucional",
                                    "tc inaplica norma"],
        "actores_relevantes": ["tribunal_constitucional", "presidencia",
                                "congreso"],
    },
    {
        "id": "vacancia_presidencial",
        "nombre": "Vacancia presidencial formalmente presentada",
        "keywords_obligatorias": ["moción de vacancia presentada",
                                    "moción de vacancia formalizada",
                                    "vacancia presentada",
                                    "vacancia ingresada",
                                    "vacancia formalizada"],
        "actores_relevantes": ["presidencia", "congreso"],
    },
    {
        "id": "intervencion_institucional",
        "nombre": "Cierre o intervención de instituciones",
        "keywords_obligatorias": ["cierre del congreso",
                                    "intervención militar",
                                    "intervencion militar",
                                    "toma de instalaciones",
                                    "ocupación militar"],
        "actores_relevantes": ["congreso", "presidencia", "ffaa"],
    },
    {
        "id": "violencia_politica_muertos",
        "nombre": "Violencia política con muertos",
        "keywords_obligatorias": ["muertos en protesta",
                                    "fallecidos en marcha",
                                    "víctimas mortales",
                                    "violencia con muertos",
                                    "muertos tras enfrentamiento"],
        "actores_relevantes": ["pnp", "ffaa", "comunidades_corredor_sur"],
    },
    {
        "id": "paro_nacional_estrategico",
        "nombre": "Paro nacional con bloqueos estratégicos",
        "keywords_obligatorias": ["paro nacional indefinido",
                                    "bloqueo de panamericana",
                                    "bloqueo corredor sur",
                                    "paro general convocado",
                                    "paro nacional convocado"],
        "actores_relevantes": ["transportistas", "comunidades_corredor_sur"],
    },
    {
        "id": "ruptura_diplomatica",
        "nombre": "Ruptura diplomática severa",
        "keywords_obligatorias": ["retiro de embajador",
                                    "expulsión diplomática",
                                    "ruptura relaciones",
                                    "personae non gratae"],
        "actores_relevantes": ["chile", "ecuador", "venezuela", "eeuu"],
    },
    {
        "id": "estado_emergencia_nacional",
        "nombre": "Estado de emergencia nacional",
        "keywords_obligatorias": ["estado de emergencia nacional",
                                    "emergencia ámbito nacional"],
        "actores_relevantes": ["presidencia", "ffaa", "pnp"],
    },
    {
        "id": "crisis_militar_policial",
        "nombre": "Crisis militar/policial",
        "keywords_obligatorias": ["alto mando",        # "renuncia masiva del alto mando", "destitución del alto mando"
                                    "comandante general",
                                    "crisis en ffaa",
                                    "crisis en pnp",
                                    "amotinamiento"],
        "actores_relevantes": ["ffaa", "pnp"],
    },
]

# Palabras condicionales que invalidan la detección de evento crítico.
# Si aparecen en el titular junto a una keyword, NO es evento consumado.
DESCALIFICADORES_CONDICIONAL: tuple[str, ...] = (
    "evaluaría", "evaluaria", "evaluarian", "consideraría", "consideraria",
    "podría", "podrian", "podrían", "estudiaría", "anuncia evaluar",
    "podría presentar", "buscan presentar", "intenta presentar",
    "amenaza con", "advierte que", "ante una eventual",
    "posibilidad de", "barajan", "barajarían",
)


# =====================================================================
# DETECCIÓN HEURÍSTICA (fallback)
# =====================================================================

def detectar_evento_critico_heuristica(eventos: list[dict]) -> dict:
    """Heurística determinística — busca keywords y combinaciones tipo+actor.

    Sin dependencia externa. Confianza máxima 65 (siempre por debajo del
    umbral del LLM de 70) — esto garantiza que cuando Claude está disponible
    su clasificación pese más, pero el sistema sigue funcionando offline.
    """
    if not eventos:
        return _resultado_no_detectado()

    for ev in eventos:
        # Construir blob de búsqueda con titulares + metadata del evento
        titulares = " ".join(ev.get("titulares", []))
        blob = f"{titulares} {ev.get('tipo', '')} {ev.get('ubicacion', '')}".lower()
        actor = ev.get("actor", "")

        # Filtro de descalificación: si el titular indica HECHO EVENTUAL
        # (evaluaría, podría, consideraría), NO es evento consumado.
        es_eventual = any(d in blob for d in DESCALIFICADORES_CONDICIONAL)

        for cat in TIPOS_EVENTOS_CRITICOS:
            # Match 1: una keyword obligatoria presente en el blob
            kw_hit = any(kw in blob for kw in cat["keywords_obligatorias"])
            # Match 2: actor relevante presente
            actor_hit = actor in cat["actores_relevantes"]

            # Si es eventual (rumor/anuncio futuro), no contar como crítico.
            if es_eventual:
                continue

            # Reglas de detección:
            #   · keyword + actor relevante → confianza 65
            #   · keyword sola              → confianza 50
            #   · actor sin keyword         → no detectar
            if kw_hit and actor_hit:
                confianza = 65
            elif kw_hit:
                confianza = 50
            else:
                continue

            return {
                "detectado": True,
                "tipo": cat["id"],
                "nombre": cat["nombre"],
                "evento_id": ev.get("event_id", ""),
                "titular_disparador": (ev.get("titulares") or [""])[0],
                "confianza": confianza,
                "justificacion": (
                    f"Heurística: keyword en titular + "
                    f"{'actor relevante (' + actor + ')' if actor_hit else 'sin actor relevante'}"
                ),
                "fuente_clasificacion": "heuristica",
            }

    return _resultado_no_detectado()


# =====================================================================
# DETECCIÓN LLM (Claude Haiku 4.5)
# =====================================================================

PROMPT_LLM = """Eres un analista senior de inteligencia política especializado en Perú.

Te paso una lista de eventos del día con sus titulares y metadata. Tu tarea es decidir
si ALGUNO encaja claramente en una de estas 8 categorías de evento crítico:

1. vacancia_presidencial · Vacancia presidencial formalmente presentada (no rumor)
2. intervencion_institucional · Cierre o intervención efectiva de instituciones del Estado
3. violencia_politica_muertos · Violencia política con víctimas mortales confirmadas
4. paro_nacional_estrategico · Paro nacional con bloqueos estratégicos (Panamericana, Corredor Sur)
5. ruptura_diplomatica · Ruptura diplomática severa (retiro de embajador, expulsión)
6. estado_emergencia_nacional · Estado de emergencia decretado a nivel nacional
7. crisis_militar_policial · Crisis aguda en FFAA o PNP (renuncia masiva, amotinamiento)
8. crisis_constitucional · Crisis constitucional abierta (disolución, denegación cuestión de confianza)

REGLAS:
· Rumores, amenazas o anuncios futuros NO son críticos. Solo HECHOS CONSUMADOS.
· "Posibilidad de", "evalúan", "considerarían" → NO es crítico.
· Si tienes duda, prefiere "no detectado".
· Confianza 70-100 = muy seguro. Confianza <70 = mejor reportar "no detectado".

DEVUELVE estrictamente JSON con esta estructura:
{
  "detectado": true|false,
  "tipo": "vacancia_presidencial|intervencion_institucional|...|null",
  "evento_id": "<event_id del evento que disparó la detección o null>",
  "titular_disparador": "<titular que justificó la decisión o null>",
  "confianza": 0-100,
  "justificacion": "<1-2 frases en español explicando>"
}
"""


def detectar_evento_critico_llm(eventos: list[dict]) -> dict | None:
    """Llama Claude para clasificar evento crítico. Retorna None si LLM no disponible."""
    if not eventos:
        return _resultado_no_detectado(fuente="llm")

    # Construir contexto compacto: top 8 eventos por severidad
    contexto_eventos = []
    for ev in eventos[:8]:
        contexto_eventos.append({
            "event_id": ev.get("event_id", ""),
            "fecha": ev.get("fecha", ""),
            "actor": ev.get("actor", ""),
            "ubicacion": ev.get("ubicacion", ""),
            "tipo": ev.get("tipo", ""),
            "severidad": ev.get("severidad_base", 0),
            "n_fuentes": ev.get("n_fuentes", 1),
            "titulares": ev.get("titulares", [])[:3],
        })
    contexto_str = json.dumps(contexto_eventos, ensure_ascii=False, indent=2)

    try:
        try:
            from ..utils.llm_client import _llamar_directo, MODEL_DEFAULT, MAX_TOKENS_DEFAULT
        except ImportError:
            from apurisk.utils.llm_client import _llamar_directo, MODEL_DEFAULT, MAX_TOKENS_DEFAULT
        raw = _llamar_directo(
            prompt=PROMPT_LLM,
            contexto=contexto_str,
            max_tokens=MAX_TOKENS_DEFAULT,
            model=MODEL_DEFAULT,
        )
    except Exception:
        return None

    if not raw:
        return None

    # Parsear JSON del LLM (tolerante a wrappers ```json)
    try:
        raw_clean = raw.strip()
        if raw_clean.startswith("```"):
            # Remover fence ```json ... ```
            lines = raw_clean.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_clean = "\n".join(lines)
        data = json.loads(raw_clean)
    except (json.JSONDecodeError, ValueError):
        return None

    # Sanitizar campos
    detectado = bool(data.get("detectado", False))
    tipo = data.get("tipo") if detectado else None
    try:
        confianza = max(0, min(100, int(data.get("confianza", 0))))
    except (ValueError, TypeError):
        confianza = 0

    # Buscar nombre canónico del tipo
    nombre = None
    for cat in TIPOS_EVENTOS_CRITICOS:
        if cat["id"] == tipo:
            nombre = cat["nombre"]
            break

    return {
        "detectado": detectado,
        "tipo": tipo,
        "nombre": nombre,
        "evento_id": data.get("evento_id"),
        "titular_disparador": data.get("titular_disparador"),
        "confianza": confianza,
        "justificacion": data.get("justificacion", ""),
        "fuente_clasificacion": "llm",
    }


# =====================================================================
# ORQUESTADOR · LLM primero, heurística como fallback
# =====================================================================

def detectar_evento_critico(
    eventos: list[dict],
    usar_llm: bool = True,
) -> dict:
    """Detecta si hay un evento crítico en el ciclo.

    Args:
        eventos: lista de eventos dedupeados (output de dedupear_eventos)
        usar_llm: si False, solo usa heurística (útil para tests deterministas)

    Returns:
        dict con:
          · detectado: bool
          · tipo: id de categoría (vacancia_presidencial, etc.) o None
          · nombre: nombre human-readable del tipo
          · evento_id: event_id del evento disparador
          · titular_disparador: titular específico
          · confianza: 0-100
          · justificacion: explicación breve
          · fuente_clasificacion: 'llm' | 'heuristica' | 'sin_eventos'

    Doctrina:
      · Si LLM devuelve detectado con confianza ≥ 70 → se usa esa decisión
      · Si LLM devuelve no detectado → se acepta esa decisión
      · Si LLM no está disponible → se usa heurística (confianza max 65)
      · Si heurística detecta con confianza ≥ 50 → se reporta como tentativo
    """
    if not eventos:
        return _resultado_no_detectado()

    # 1. Intento LLM si está activado
    if usar_llm:
        out_llm = detectar_evento_critico_llm(eventos)
        if out_llm is not None:
            return out_llm

    # 2. Fallback heurístico
    return detectar_evento_critico_heuristica(eventos)


# =====================================================================
# HELPERS
# =====================================================================

def _resultado_no_detectado(fuente: str = "sin_eventos") -> dict:
    return {
        "detectado": False,
        "tipo": None,
        "nombre": None,
        "evento_id": None,
        "titular_disparador": None,
        "confianza": 0,
        "justificacion": "No se detectó evento crítico en el ciclo.",
        "fuente_clasificacion": fuente,
    }


def debe_omitir_suavizado(evento_critico: dict,
                            umbral_confianza: int = 70) -> bool:
    """Devuelve True si la detección de evento crítico debe ROMPER el suavizado.

    Reglas:
      · LLM detecta con confianza ≥ umbral_confianza (default 70) → omite
      · Heurística detecta con confianza ≥ 60 → omite (umbral más bajo
        porque la heurística ya tiene cap 65)
    """
    if not evento_critico or not evento_critico.get("detectado"):
        return False
    fuente = evento_critico.get("fuente_clasificacion", "")
    conf = evento_critico.get("confianza", 0)
    if fuente == "llm" and conf >= umbral_confianza:
        return True
    if fuente == "heuristica" and conf >= 60:
        return True
    return False
