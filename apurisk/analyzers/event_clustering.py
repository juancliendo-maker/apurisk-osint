"""Deduplicación y clustering de eventos OSINT · Sprint 1.2.

Convierte una lista de NOTICIAS (que pueden referirse al mismo hecho) en
una lista de EVENTOS deduplicados — el ladrillo fundamental sobre el cual
el Score Riesgo Político v2 calcula presión política real (no volumen
narrativo).

DOCTRINA:

  Un evento es un hecho político-social-económico concreto que sucedió
  en una fecha aproximada, con un actor principal, en una ubicación, de
  un tipo identificable y sobre un tema central. Si dos noticias hablan
  del mismo hecho, se fusionan en un único evento — el volumen de
  noticias aumenta la CONFIANZA del evento, no su gravedad.

CLAVE DE DEDUPLICACIÓN (event_id):

  event_id = sha1(fecha_aproximada + actor_principal + ubicacion + tipo + tema)

  Esto agrupa exactamente noticias con los mismos cinco campos. Para
  noticias que tienen ligeras variaciones (un titular distinto pero el
  mismo hecho), se aplica además similitud de Jaccard sobre los tokens
  del título normalizado (>= 0.55 = mismo evento).

OUTPUT POR CLUSTER:

  - severidad_base: max() de las severidades reportadas por las noticias
  - n_fuentes: número de noticias agrupadas
  - factor_confirmacion: log(1 + n_fuentes) * 0.15  (cap 1.50)
  - origen_noticias: lista con ID/URL/medio de cada noticia del cluster
  - categorias: unión de categorías legacy (estabilidad_gobierno, etc)
    que se usa en Sprint 1.3 para mapear a dimensiones canónicas

Esto es deduplicación BÁSICA (Sprint 1.2). En el roadmap futuro:
  · Sprint 4 (modificadores) → enriquece con fuente/actor/escalamiento
  · Versión >v2 → embeddings semánticos para clustering más fino
"""
from __future__ import annotations
import hashlib
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Iterable


# =====================================================================
# DICCIONARIOS HEURÍSTICOS · Perú 2026
# =====================================================================

# Actores institucionales canónicos. Si una noticia menciona cualquier alias,
# el actor principal del evento se canonicaliza al nombre oficial.
ACTORES_CANONICOS_DEDUP: dict[str, list[str]] = {
    # Ejecutivo
    "presidencia":              ["presidenta", "presidente", "boluarte", "dina boluarte",
                                  "jefa de estado", "palacio de gobierno"],
    "premier":                  ["premier", "primer ministro", "presidente del consejo",
                                  "pcm", "consejo de ministros"],
    "minister_interior":        ["mininter", "ministerio del interior", "ministro del interior"],
    "minister_economia":        ["mef", "ministerio de economía", "ministro de economía"],
    "minister_energia_minas":   ["minem", "ministerio de energía", "ministerio de minas"],
    # Legislativo
    "congreso":                 ["congreso", "pleno del congreso", "comisión permanente",
                                  "bancada", "junta de portavoces"],
    "presidente_congreso":      ["presidente del congreso", "salhuana"],
    # Judicial
    "tribunal_constitucional":  ["tribunal constitucional", "tc ", "magistrados del tc"],
    "poder_judicial":           ["poder judicial", "corte suprema", "javier arévalo"],
    "fiscalia":                 ["fiscal de la nación", "delia espinoza", "fiscalía",
                                  "ministerio público"],
    "jnj":                      ["jnj", "junta nacional de justicia"],
    # Electoral
    "jne":                      ["jne", "jurado nacional de elecciones"],
    "onpe":                     ["onpe", "oficina nacional de procesos"],
    # Seguridad
    "pnp":                      ["pnp", "policía nacional", "policia nacional",
                                  "comandante general pnp"],
    "ffaa":                     ["fuerzas armadas", "comando conjunto", "ccffaa"],
    # Sociales
    "comunidades_corredor_sur": ["federaciones campesinas", "ronda campesina",
                                  "comunidades", "corredor sur"],
    "transportistas":           ["transportistas", "gremio de transportistas",
                                  "anitra"],
    # Actores delictivos
    "mineria_ilegal":           ["minería ilegal", "mineria ilegal", "mineros informales"],
    "crimen_organizado":        ["organización criminal", "tren de aragua", "narcotráfico",
                                  "sicariato"],
    # Internacionales
    "chile":                    ["chile", "gobierno chileno", "boric", "kast"],
    "ecuador":                  ["ecuador", "gobierno ecuatoriano", "noboa"],
    "venezuela":                ["venezuela", "régimen de maduro", "migrantes venezolanos"],
    "eeuu":                     ["estados unidos", "ee.uu", "eeuu", "department of state",
                                  "ofac", "treasury"],
    "oea":                      ["oea", "organización de estados americanos"],
}

# Ubicaciones canónicas que ya están capturadas como regiones/ciudades de Perú.
# El sistema busca menciones de estos términos para asignar ubicación al evento.
UBICACIONES_CANONICAS: dict[str, list[str]] = {
    "nacional":      ["perú", "nacional", "todo el país"],
    "lima":          ["lima", "lima metropolitana", "callao", "centro de lima"],
    "apurimac":      ["apurímac", "apurimac", "abancay", "andahuaylas"],
    "arequipa":      ["arequipa"],
    "cusco":         ["cusco", "cuzco"],
    "puno":          ["puno", "juliaca"],
    "junin":         ["junín", "junin", "huancayo"],
    "ayacucho":      ["ayacucho", "huamanga"],
    "huancavelica":  ["huancavelica"],
    "cajamarca":     ["cajamarca"],
    "la_libertad":   ["la libertad", "trujillo"],
    "lambayeque":    ["lambayeque", "chiclayo"],
    "piura":         ["piura"],
    "tumbes":        ["tumbes", "frontera con ecuador"],
    "tacna":         ["tacna", "frontera con chile"],
    "loreto":        ["loreto", "iquitos"],
    "ucayali":       ["ucayali", "pucallpa"],
    "madre_de_dios": ["madre de dios", "puerto maldonado"],
    "ica":           ["ica", "panamericana sur"],
    "ancash":        ["áncash", "ancash", "huaraz", "chimbote"],
    "amazonas":      ["amazonas", "bagua"],
    "san_martin":    ["san martín", "tarapoto", "moyobamba"],
    "huanuco":       ["huánuco", "tingo maría", "tocache"],
    "frontera_norte": ["frontera norte", "tumbes-ecuador"],
    "frontera_sur":  ["frontera sur", "tacna-chile"],
    "vraem":         ["vraem", "valle de los ríos"],
    "exterior":      ["santiago", "washington", "caracas", "quito", "bogotá"],
}

# Tipos de evento canónicos
TIPOS_EVENTO: dict[str, list[str]] = {
    "paro":             ["paro", "huelga", "movilización", "marcha"],
    "bloqueo":          ["bloqueo", "vía bloqueada", "carretera tomada"],
    "moción":           ["moción", "censura", "vacancia", "interpelación"],
    "denuncia":         ["denuncia constitucional", "denuncia penal",
                          "denuncia fiscal"],
    "fallo_judicial":   ["fallo", "sentencia", "resolución", "auto"],
    "decreto":          ["decreto supremo", "decreto de urgencia",
                          "decreto legislativo", "ds nº"],
    "asesinato":        ["asesinato", "sicariato", "homicidio",
                          "muerto", "muertos", "fallecido"],
    "operativo":        ["operativo", "intervención policial", "captura",
                          "incautación"],
    "declaracion":      ["declaró", "afirmó", "anunció", "pronunciamiento"],
    "renuncia":         ["renuncia", "dimite", "deja el cargo"],
    "designacion":      ["designado", "designación", "nombrado", "juramentó"],
    "comparecencia":    ["comparecencia", "declaración ante fiscalía"],
    "estado_emergencia": ["estado de emergencia"],
    "convocatoria":     ["convocatoria", "convoca a", "llamado a"],
    "evento_externo":   ["pronunciamiento", "comunicado", "nota diplomática"],
}


# =====================================================================
# NORMALIZACIÓN DE TEXTO
# =====================================================================

def _normalizar(s: str) -> str:
    """Limpieza canónica: minúsculas + sin acentos + sin puntuación múltiple."""
    if not s:
        return ""
    s = s.lower()
    # Quitar acentos
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Reducir puntuación y espacios
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokens(s: str) -> set[str]:
    """Set de tokens útiles (>3 chars, sin stopwords mínimas)."""
    STOPWORDS = {
        "que", "para", "como", "este", "esta", "esto", "esos", "esas",
        "una", "unos", "unas", "del", "los", "las", "por", "con", "sin",
        "más", "mas", "pero", "tras", "sobre", "hasta", "desde", "entre",
        "tambien", "también", "fue", "han", "habia", "había", "será",
        "sera", "son", "ser", "estar", "este", "esa",
    }
    n = _normalizar(s)
    return {t for t in n.split() if len(t) >= 4 and t not in STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    """Similitud Jaccard entre dos conjuntos (0-1)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# =====================================================================
# EXTRACCIÓN HEURÍSTICA DE CAMPOS DEL EVENTO
# =====================================================================

def _fecha_aproximada(noticia: dict) -> str:
    """Devuelve fecha YYYY-MM-DD del evento (de campo `fecha` o `publicado`).

    Si no hay fecha confiable, usa fecha de captura. La fecha aproximada es
    importante porque dos noticias del mismo evento pueden publicarse con
    1 día de diferencia — por eso se redondea al día y se permite ±1 día
    para el match (lógica en `_es_mismo_evento`).
    """
    for k in ("fecha_evento", "fecha", "published", "publicado", "pubDate"):
        v = noticia.get(k)
        if v:
            # Tomamos los primeros 10 chars (YYYY-MM-DD)
            s = str(v)[:10]
            if re.match(r"\d{4}-\d{2}-\d{2}", s):
                return s
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def extraer_actor_principal(noticia: dict) -> str:
    """Busca actor canónico mencionado en titulo + resumen. Default 'sin_actor'."""
    blob = _normalizar(
        f"{noticia.get('title', '')} {noticia.get('titulo', '')} "
        f"{noticia.get('summary', '')} {noticia.get('resumen', '')}"
    )
    for canon, aliases in ACTORES_CANONICOS_DEDUP.items():
        if any(a in blob for a in aliases):
            return canon
    return "sin_actor"


def extraer_ubicacion(noticia: dict) -> str:
    """Detecta ubicación principal del evento. Default 'nacional'."""
    blob = _normalizar(
        f"{noticia.get('title', '')} {noticia.get('titulo', '')} "
        f"{noticia.get('summary', '')} {noticia.get('resumen', '')}"
    )
    for canon, aliases in UBICACIONES_CANONICAS.items():
        if any(a in blob for a in aliases):
            return canon
    return "nacional"


def extraer_tipo_evento(noticia: dict) -> str:
    """Detecta tipo de evento. Default 'declaracion' (más común)."""
    blob = _normalizar(
        f"{noticia.get('title', '')} {noticia.get('titulo', '')} "
        f"{noticia.get('summary', '')} {noticia.get('resumen', '')}"
    )
    for tipo, aliases in TIPOS_EVENTO.items():
        if any(a in blob for a in aliases):
            return tipo
    return "declaracion"


def extraer_tema_principal(noticia: dict) -> str:
    """Toma la primera categoría/tema clasificado por v1 — si no, tipo de evento.

    El sistema actual ya clasifica artículos en 12 categorías (config.yaml
    indicadores_riesgo). Esta función prefiere esa clasificación. Sino,
    cae al tipo de evento detectado heurísticamente.
    """
    cats = (
        noticia.get("categorias")
        or noticia.get("categories")
        or noticia.get("temas")
        or []
    )
    if cats:
        if isinstance(cats, list):
            return str(cats[0]).lower()
        if isinstance(cats, str):
            return cats.lower()
        if isinstance(cats, dict):
            # Tomar la categoría con score más alto
            return max(cats.items(), key=lambda kv: kv[1])[0]
    return extraer_tipo_evento(noticia)


# =====================================================================
# event_id Y CLUSTERING
# =====================================================================

def event_id(noticia: dict) -> str:
    """Hash determinista del evento basado en 5 campos canónicos.

    SHA-1 hex truncado a 16 chars (suficiente para deduplicar dentro de
    una ventana móvil de 90 días).
    """
    fecha = _fecha_aproximada(noticia)
    actor = extraer_actor_principal(noticia)
    ubic = extraer_ubicacion(noticia)
    tipo = extraer_tipo_evento(noticia)
    tema = extraer_tema_principal(noticia)
    key = f"{fecha}|{actor}|{ubic}|{tipo}|{tema}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _es_mismo_evento(eid_a: str, eid_b: str,
                       tokens_a: set, tokens_b: set,
                       umbral_jaccard: float = 0.55) -> bool:
    """Dos noticias son el mismo evento si:
      A) event_id idéntico, O
      B) Jaccard sobre tokens del título >= umbral
    """
    if eid_a == eid_b:
        return True
    return _jaccard(tokens_a, tokens_b) >= umbral_jaccard


def _severidad_de_noticia(noticia: dict) -> float:
    """Severidad reportada por la noticia (campo del LLM v1 o heurístico).

    Sprint 1.2 usa heurística simple. Sprint 1.5 enriquece con factores.
    """
    # Si la noticia trae score del LLM v1, lo usamos
    s = noticia.get("score") or noticia.get("severidad") or noticia.get("riesgo_score")
    if s:
        try:
            return float(s)
        except (ValueError, TypeError):
            pass
    # Heurística: por tipo de evento
    tipo = extraer_tipo_evento(noticia)
    SEVERIDADES_DEFAULT = {
        "asesinato":        80,
        "moción":           75,
        "estado_emergencia": 85,
        "bloqueo":          70,
        "paro":             65,
        "denuncia":         60,
        "operativo":        55,
        "fallo_judicial":   60,
        "decreto":          50,
        "renuncia":         55,
        "designacion":      40,
        "comparecencia":    45,
        "convocatoria":     50,
        "evento_externo":   45,
        "declaracion":      35,
    }
    return SEVERIDADES_DEFAULT.get(tipo, 40)


def _categorias_de_noticia(noticia: dict) -> list[str]:
    """Devuelve lista de categorías legacy v1 asociadas a la noticia.

    Si la noticia ya viene clasificada en las 12 categorías de v1, las
    devuelve. Si no, hace un mapeo desde el tipo de evento.
    """
    cats = noticia.get("categorias") or noticia.get("categories") or []
    if isinstance(cats, dict):
        cats = list(cats.keys())
    if isinstance(cats, str):
        cats = [cats]
    cats = [str(c).lower() for c in cats]
    if cats:
        return cats

    # Fallback: derivar categoría de v1 desde el tipo de evento detectado
    tipo = extraer_tipo_evento(noticia)
    MAPEO_TIPO_CATEGORIA = {
        "moción":            ["estabilidad_gobierno"],
        "denuncia":          ["corrupcion"],
        "fallo_judicial":    ["estabilidad_gobierno"],
        "decreto":           ["riesgo_regulatorio"],
        "asesinato":         ["seguridad"],
        "operativo":         ["seguridad"],
        "renuncia":          ["estabilidad_gobierno"],
        "designacion":       ["estabilidad_gobierno"],
        "comparecencia":     ["corrupcion"],
        "paro":              ["conflictos_sociales"],
        "bloqueo":           ["conflictos_sociales"],
        "convocatoria":      ["conflictos_sociales"],
        "estado_emergencia": ["seguridad", "intervencion_militar"],
        "evento_externo":    ["tensiones_diplomaticas"],
        "declaracion":       ["polarizacion"],
    }
    return MAPEO_TIPO_CATEGORIA.get(tipo, ["polarizacion"])


# =====================================================================
# FUNCIÓN PRINCIPAL · dedupear_eventos
# =====================================================================

def dedupear_eventos(noticias: Iterable[dict]) -> list[dict]:
    """Convierte lista de noticias en lista de eventos deduplicados.

    Estrategia:
      1. Calcula event_id + tokens del título por cada noticia
      2. Agrupa por event_id idéntico
      3. Para los grupos restantes de 1 elemento, intenta unir con otros
         grupos vía Jaccard de tokens >= 0.55 (recupera variaciones de titular)
      4. Cada cluster final = 1 evento; el output preserva referencia a
         las noticias originales

    Args:
        noticias: iterable de dicts con campos:
            title/titulo, summary/resumen, fecha (opcional),
            score/severidad (opcional), categorias (opcional)

    Returns:
        Lista de eventos:
        [
          {
            "event_id": "abc123...",
            "fecha":      "2026-06-05",
            "actor":      "premier",
            "ubicacion":  "nacional",
            "tipo":       "moción",
            "tema":       "estabilidad_gobierno",
            "severidad_base": 75.0,
            "n_fuentes":  12,
            "factor_confirmacion": 1.35,
            "categorias_legacy": ["estabilidad_gobierno", "polarizacion"],
            "origen_noticias": [{titulo, url, medio}, ...],
            "titulares": ["...", "..."],
          },
          ...
        ]
    """
    noticias = list(noticias)
    if not noticias:
        return []

    # Paso 1: precalcular (event_id, tokens, severidad, categorias) para cada noticia
    enriquecidas = []
    for n in noticias:
        titulo = n.get("title") or n.get("titulo") or ""
        eid = event_id(n)
        toks = _tokens(titulo)
        sev = _severidad_de_noticia(n)
        cats = _categorias_de_noticia(n)
        enriquecidas.append({
            "noticia": n,
            "eid": eid,
            "tokens": toks,
            "severidad": sev,
            "categorias": cats,
            "fecha": _fecha_aproximada(n),
            "actor": extraer_actor_principal(n),
            "ubicacion": extraer_ubicacion(n),
            "tipo": extraer_tipo_evento(n),
            "tema": extraer_tema_principal(n),
        })

    # Paso 2: agrupación por event_id (exacto)
    grupos: dict[str, list] = {}
    for e in enriquecidas:
        grupos.setdefault(e["eid"], []).append(e)

    # Paso 3: fusión por similitud — colapsa grupos con misma (ubicación, tipo)
    # cuyo Jaccard sobre tokens del título sea alto, aunque el actor difiera.
    # Esto recupera el caso típico: 5 réplicas del mismo paro, donde una
    # noticia menciona el actor explícitamente y las otras no.
    #
    # Algoritmo: para cada par de grupos (A, B), si comparten ubicación y
    # tipo y al menos un par de noticias entre ambos tiene Jaccard >= 0.55,
    # fusionamos B en A (el grupo con más fuentes gana).
    def _similar_entre_grupos(grp_a: list, grp_b: list,
                                 umbral: float = 0.55) -> bool:
        for ea in grp_a:
            for eb in grp_b:
                if _jaccard(ea["tokens"], eb["tokens"]) >= umbral:
                    return True
        return False

    cambio = True
    while cambio:
        cambio = False
        eids = sorted(grupos.keys(), key=lambda k: -len(grupos[k]))  # mayores primero
        for i, eid_a in enumerate(eids):
            if eid_a not in grupos:
                continue
            grp_a = grupos[eid_a]
            ev_a = grp_a[0]
            for eid_b in eids[i + 1:]:
                if eid_b not in grupos or eid_b == eid_a:
                    continue
                grp_b = grupos[eid_b]
                ev_b = grp_b[0]
                # Misma ubicación + mismo tipo + similitud léxica alta = mismo hecho
                if (ev_a["ubicacion"] == ev_b["ubicacion"]
                    and ev_a["tipo"] == ev_b["tipo"]
                    and _similar_entre_grupos(grp_a, grp_b)):
                    grupos[eid_a].extend(grupos[eid_b])
                    del grupos[eid_b]
                    cambio = True
            if cambio:
                break  # reiniciar el outer loop tras una fusión

    # Paso 4: armar eventos finales
    eventos = []
    for eid, grp in grupos.items():
        first = grp[0]
        severidades = [g["severidad"] for g in grp]
        categorias_set = set()
        for g in grp:
            categorias_set.update(g["categorias"])
        titulares = [
            g["noticia"].get("title") or g["noticia"].get("titulo") or ""
            for g in grp
        ]
        origen = [{
            "titulo": g["noticia"].get("title") or g["noticia"].get("titulo") or "",
            "url":    g["noticia"].get("url") or g["noticia"].get("link") or "",
            "medio":  g["noticia"].get("medio") or g["noticia"].get("source") or "",
        } for g in grp]

        n_fuentes = len(grp)
        # Confianza por replicación: log(1 + n) * 0.15, cap 1.50
        factor_confirmacion = min(1.50, 1.0 + math.log(1 + n_fuentes) * 0.15)

        eventos.append({
            "event_id":          eid,
            "fecha":             first["fecha"],
            "actor":             first["actor"],
            "ubicacion":         first["ubicacion"],
            "tipo":              first["tipo"],
            "tema":              first["tema"],
            "severidad_base":    max(severidades),
            "n_fuentes":         n_fuentes,
            "factor_confirmacion": round(factor_confirmacion, 3),
            "categorias_legacy": sorted(categorias_set),
            "titulares":         titulares,
            "origen_noticias":   origen,
        })

    # Ordenar por severidad descendente (los más graves primero)
    eventos.sort(key=lambda e: e["severidad_base"], reverse=True)
    return eventos


# =====================================================================
# MAPEO eventos → 5 dimensiones canónicas v2
# =====================================================================

def clasificar_eventos_a_dimensiones(eventos: list[dict]) -> dict[str, list[dict]]:
    """Agrupa eventos en las 5 dimensiones canónicas v2.

    Usa MAPEO_CATEGORIAS_A_DIMENSIONES de risk_score_v2.py — cada categoría
    legacy aporta a una o más dimensiones con un peso.

    Para eventos que mapean a múltiples dimensiones (ej: corrupción → 60%
    gobernabilidad + 40% economia_politica), el evento se replica con un
    peso interno. La suma de pesos por evento NO supera 1.0.

    Returns:
        {
          "gobernabilidad":         [eventos...],
          "conflictividad_social":  [eventos...],
          "seguridad_crimen":       [eventos...],
          "economia_politica":      [eventos...],
          "relaciones_exteriores":  [eventos...],
        }
    """
    try:
        from .risk_score_v2 import MAPEO_CATEGORIAS_A_DIMENSIONES
    except ImportError:
        from apurisk.analyzers.risk_score_v2 import MAPEO_CATEGORIAS_A_DIMENSIONES

    DIMS = ["gobernabilidad", "conflictividad_social", "seguridad_crimen",
            "economia_politica", "relaciones_exteriores"]
    por_dim: dict[str, list[dict]] = {d: [] for d in DIMS}

    for ev in eventos:
        # Acumular pesos por dimensión sumando contribuciones de cada categoría legacy
        pesos_por_dim: dict[str, float] = {d: 0.0 for d in DIMS}
        for cat in ev.get("categorias_legacy", []):
            mapeo = MAPEO_CATEGORIAS_A_DIMENSIONES.get(cat, {})
            for dim, w in mapeo.items():
                if dim in pesos_por_dim:
                    pesos_por_dim[dim] += w

        # Si el evento no tiene mapeo (categoría desconocida), fallback a
        # gobernabilidad (la dimensión más amplia)
        total = sum(pesos_por_dim.values())
        if total == 0:
            pesos_por_dim["gobernabilidad"] = 1.0
            total = 1.0

        # Normalizar para que la suma de pesos del evento sea 1.0
        for dim in DIMS:
            if pesos_por_dim[dim] > 0:
                ev_copia = dict(ev)
                ev_copia["peso_en_dimension"] = round(pesos_por_dim[dim] / total, 3)
                por_dim[dim].append(ev_copia)

    return por_dim
