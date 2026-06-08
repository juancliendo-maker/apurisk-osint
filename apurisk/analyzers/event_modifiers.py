"""Modificadores de eventos · Sprint 1.5.

Convierte la severidad bruta de un evento en una "severidad modificada" que
incorpora cuatro factores no-volumétricos:

  · factor_fuente:       castiga rumores, premia oficial
  · factor_actor:        premia mención de actor institucional relevante
  · factor_escalamiento: premia eventos con potencial de escalada
  · factor_persistencia: premia eventos que han persistido en el tiempo

DOCTRINA:

  Un rumor anónimo sobre golpe militar no debe pesar igual que un fallo del
  Tribunal Constitucional, aunque ambos tengan severidad subjetiva alta.
  La calidad de la fuente, la relevancia del actor, el potencial de escalada
  y la persistencia temporal son CORRECCIONES OBLIGATORIAS al volumen
  observado en prensa.

FÓRMULA:

  severidad_modificada = severidad_base
                       × factor_fuente       (0.20 → 1.20)
                       × factor_actor        (0.70 → 1.30)
                       × factor_escalamiento (0.80 → 1.40)
                       × factor_persistencia (1.00 → 1.25)
                       × factor_confirmacion (de event_clustering: 1.00 → 1.50)

  Cap final en 100 (severidad no puede exceder 100).

NOTAS:

  · Sprint 1.5 implementa los 4 factores con heurísticas razonables.
  · Sprint 1.6 (LLM evento crítico) puede refinar factor_escalamiento.
  · Sprint 1.7 (confidence_score) consume el detalle de factor_fuente.
"""
from __future__ import annotations
import math
from typing import Any
from datetime import datetime, timedelta, timezone


# =====================================================================
# 1. CLASIFICACIÓN DE FUENTES OSINT
# =====================================================================

PESO_FUENTE: dict[str, float] = {
    "oficial_primaria":         1.20,   # PCM, Presidencia, Congreso, MP, PJ, TC, JNE, ONPE, BCR, INEI, Defensoría
    "agencia_internacional":    1.10,   # Reuters, AP, AFP, Bloomberg, DW
    "medio_nacional":           1.00,   # medios peruanos consolidados
    "medio_regional":           0.95,   # correo regionales, losandes
    "think_tank":               1.00,   # transparency, GAN, BM, FMI, OCDE, HRW
    "red_social_verificada":    0.85,   # cuentas oficiales verificadas
    "red_social_no_verificada": 0.40,   # tweets sin verificación
    "rumor_anonimo":            0.20,   # blogs, threads anónimos, cuentas burner
}


# Mapeo de identificadores del config.yaml → tipo de fuente.
# Cuando una noticia trae `medio` o `source` matcheable a estos IDs, se
# clasifica directamente. Para nombres libres (ej. "Reuters", "RPP")
# usamos heurística por nombre debajo.
MAPEO_ID_TIPO: dict[str, str] = {
    # OFICIALES PRIMARIOS — Estado peruano
    "andina_politica":        "oficial_primaria",   # agencia oficial
    "mp_fiscalia":            "oficial_primaria",
    "sunat_noticias":         "oficial_primaria",
    "sunafil_noticias":       "oficial_primaria",
    "mininter_oficial":       "oficial_primaria",
    "mininter_seguridad":     "oficial_primaria",
    "mre_oficial":            "oficial_primaria",
    "bcrp_notas":             "oficial_primaria",
    "jne_noticias":           "oficial_primaria",
    "onpe_noticias":          "oficial_primaria",
    "inei_estadisticas":      "oficial_primaria",
    "inei_seguridad":         "oficial_primaria",
    "contraloria_noticias":   "oficial_primaria",
    "pj_noticias":            "oficial_primaria",
    "tc_jurisprudencia":      "oficial_primaria",
    "ccffaa_oficial":         "oficial_primaria",
    "mindef_oficial":         "oficial_primaria",
    "el_peruano":             "oficial_primaria",   # diario oficial

    # AGENCIAS INTERNACIONALES
    "reuters_peru":           "agencia_internacional",
    "reuters_latam":          "agencia_internacional",
    "ap_peru":                "agencia_internacional",
    "ap_latam":               "agencia_internacional",
    "dw_espanol":             "agencia_internacional",
    "dw_espanol_directo":     "agencia_internacional",
    "bbc_americalatina":      "agencia_internacional",
    "el_pais_peru":           "agencia_internacional",
    "bloomberg_latam":        "agencia_internacional",
    "ft_emerging":            "agencia_internacional",
    "economist_americas":     "agencia_internacional",
    "cnn_espanol":            "agencia_internacional",

    # MEDIOS NACIONALES PERUANOS
    "rpp_politica":           "medio_nacional",
    "larepublica_politica":   "medio_nacional",
    "elcomercio_politica":    "medio_nacional",
    "gestion_politica":       "medio_nacional",
    "idl_reporteros":         "medio_nacional",
    "ojo_publico":            "medio_nacional",
    "convoca_pe":             "medio_nacional",
    "peru21_politica":        "medio_nacional",
    "infobae_peru":           "medio_nacional",
    "infobae_america":        "medio_nacional",
    "infobae_politica":       "medio_nacional",
    "willax_politica":        "medio_nacional",
    "willax_directo":         "medio_nacional",
    "expreso_politica":       "medio_nacional",
    "expreso_directo":        "medio_nacional",
    "caretas":                "medio_nacional",
    "hildebrandt_trece":      "medio_nacional",
    "cuartopoder_gnews":      "medio_nacional",
    "sin_medias_tintas":      "medio_nacional",
    "youtube_panorama_gnews": "medio_nacional",
    "youtube_puntofinal_gnews": "medio_nacional",

    # MEDIOS REGIONALES
    "correo_arequipa":        "medio_regional",
    "correo_cusco":           "medio_regional",
    "correo_puno":            "medio_regional",
    "correo_huancayo":        "medio_regional",
    "losandes_puno":          "medio_regional",

    # THINK TANKS / ORGANISMOS INTERNACIONALES
    "transparency_minero":    "think_tank",
    "gan_integrity":          "think_tank",
    "bm_mining_diagnostic":   "think_tank",
    "bm_peru":                "think_tank",
    "bid_peru":               "think_tank",
    "fmi_peru":               "think_tank",
    "ocde_peru":              "think_tank",
    "hrw_peru":               "think_tank",
    "amnesty_peru":           "think_tank",
    "iep_encuestas":          "think_tank",
    "ipsos_peru":             "think_tank",
    "cpi_encuestas":          "think_tank",
    "datum_internacional":    "think_tank",
    "snmpe_minero":           "medio_nacional",
    "discovery_alert":        "think_tank",
    "ofac_treasury_peru":     "oficial_primaria",   # gobierno EEUU
    "dea_southcom_peru":      "oficial_primaria",
    "mining_minero":          "medio_nacional",
    "bnamericas_minero":      "medio_nacional",
    "mineria_ilegal_peru":    "medio_nacional",
    "narco_mineria_peru":     "medio_nacional",
    "encuestas_peru_gnews":   "think_tank",

    # REDES SOCIALES
    "reddit_peru":            "red_social_no_verificada",
    "reddit_lima":            "red_social_no_verificada",
    # twitter queries individuales — Sprint 1.5 las trata como red_social_verificada
    # (porque los queries del config son de cuentas seguidas o keywords políticas)
}


# Heurísticas por nombre de medio cuando no hay ID exacto
HEURISTICAS_NOMBRE: list[tuple[str, str]] = [
    # Oficiales (con dominios o palabras clave)
    ("gob.pe",            "oficial_primaria"),
    ("congreso",          "oficial_primaria"),
    ("presidencia",       "oficial_primaria"),
    ("treasury",          "oficial_primaria"),
    ("worldbank",         "think_tank"),
    ("imf.org",           "think_tank"),
    ("iadb.org",          "think_tank"),
    # Agencias internacionales
    ("reuters",           "agencia_internacional"),
    ("ap news",           "agencia_internacional"),
    ("associated press",  "agencia_internacional"),
    ("bloomberg",         "agencia_internacional"),
    ("financial times",   "agencia_internacional"),
    ("economist",         "agencia_internacional"),
    ("bbc",               "agencia_internacional"),
    ("dw.com",            "agencia_internacional"),
    ("deutsche welle",    "agencia_internacional"),
    ("cnn",               "agencia_internacional"),
    ("el país",           "agencia_internacional"),
    # Medios nacionales conocidos
    ("rpp",               "medio_nacional"),
    ("la república",      "medio_nacional"),
    ("el comercio",       "medio_nacional"),
    ("gestión",           "medio_nacional"),
    ("perú 21",           "medio_nacional"),
    ("peru21",            "medio_nacional"),
    ("idl",               "medio_nacional"),
    ("ojo público",       "medio_nacional"),
    ("ojo-publico",       "medio_nacional"),
    ("convoca",           "medio_nacional"),
    ("caretas",           "medio_nacional"),
    ("hildebrandt",       "medio_nacional"),
    ("infobae",           "medio_nacional"),
    ("willax",            "medio_nacional"),
    ("andina",            "oficial_primaria"),
    ("el peruano",        "oficial_primaria"),
    # Think tanks
    ("transparency",      "think_tank"),
    ("gan integrity",     "think_tank"),
    ("oecd",              "think_tank"),
    ("ocde",              "think_tank"),
    ("hrw",               "think_tank"),
    ("human rights",      "think_tank"),
    ("amnesty",           "think_tank"),
    ("ipsos",             "think_tank"),
    ("datum",             "think_tank"),
    ("iep",               "think_tank"),
    # Redes
    ("twitter",           "red_social_verificada"),
    ("x.com",             "red_social_verificada"),
    ("reddit",            "red_social_no_verificada"),
    ("youtube",           "red_social_no_verificada"),
    # Rumor / blog
    ("blog",              "rumor_anonimo"),
    ("anonimo",           "rumor_anonimo"),
    ("anónimo",           "rumor_anonimo"),
    ("foro",              "red_social_no_verificada"),
]


def clasificar_fuente(medio: str | None) -> str:
    """Mapea un medio / source a un tipo de fuente canónico.

    Estrategia:
      1. Match exacto por ID del config.yaml
      2. Override por keyword de descalificación (anónimo, no verificado, rumor)
      3. Match heurístico por nombre
      4. Default: medio_nacional (asume calidad media)
    """
    if not medio:
        return "medio_nacional"
    m = str(medio).strip().lower()
    # Match exacto por ID
    if m in MAPEO_ID_TIPO:
        return MAPEO_ID_TIPO[m]
    # OVERRIDE de descalificación: si el nombre contiene palabras que
    # explícitamente denotan baja credibilidad, gana sobre cualquier otra
    # heurística. Esto evita que "foro twitter no verificado" se clasifique
    # como red_social_verificada por matchear "twitter".
    DESCALIFICADORES_RUMOR: tuple[str, ...] = (
        "anónim", "anonim", "rumor", "blog ", "blog\t",
        "burner", "trol",
    )
    DESCALIFICADORES_NO_VERIFICADO: tuple[str, ...] = (
        "no verificad", "no oficial", "sin confirmar",
        "no confirmad", "foro ",
    )
    if any(d in m for d in DESCALIFICADORES_RUMOR):
        return "rumor_anonimo"
    if any(d in m for d in DESCALIFICADORES_NO_VERIFICADO):
        return "red_social_no_verificada"
    # Match heurístico por keyword en el nombre
    for keyword, tipo in HEURISTICAS_NOMBRE:
        if keyword in m:
            return tipo
    return "medio_nacional"


def factor_fuente(medio: str | None) -> float:
    """Devuelve el peso multiplicativo del tipo de fuente (0.20 → 1.20)."""
    tipo = clasificar_fuente(medio)
    return PESO_FUENTE.get(tipo, 1.00)


def factor_fuente_cluster(noticias_del_cluster: list[dict]) -> float:
    """Cuando un evento tiene varias noticias (fuentes), el factor_fuente
    final es el MÁXIMO entre las fuentes del cluster.

    Justificación: si un mismo hecho es reportado por Reuters y un rumor
    anónimo, vale tanto como Reuters — la peor fuente no degrada al evento.
    Si todas las fuentes son rumores, el evento queda en 0.20.
    """
    if not noticias_del_cluster:
        return 1.00
    factores = [
        factor_fuente(n.get("medio") or n.get("source") or "")
        for n in noticias_del_cluster
    ]
    return max(factores) if factores else 1.00


# =====================================================================
# 2. FACTOR ACTOR
# =====================================================================

PESO_ACTOR: dict[str, float] = {
    # Institucional alto (oficial constitucional)
    "presidencia":              1.30,
    "premier":                  1.30,
    "presidente_congreso":      1.30,
    "tribunal_constitucional":  1.30,
    "fiscalia":                 1.30,
    "poder_judicial":           1.30,
    "jnj":                      1.30,
    "jne":                      1.30,
    "onpe":                     1.25,
    # Ministerios sectoriales clave
    "minister_interior":        1.25,
    "minister_economia":        1.25,
    "minister_energia_minas":   1.25,
    # Congreso colectivo
    "congreso":                 1.20,
    # Seguridad institucional
    "pnp":                      1.15,
    "ffaa":                     1.20,
    # Actores sociales movilizadores
    "comunidades_corredor_sur": 1.15,
    "transportistas":           1.10,
    # Actores delictivos (relevancia para riesgo aunque no institucionales)
    "crimen_organizado":        1.20,
    "mineria_ilegal":           1.20,
    # Actores internacionales con impacto sobre Perú
    "chile":                    1.10,
    "ecuador":                  1.05,
    "venezuela":                1.05,
    "eeuu":                     1.15,
    "oea":                      1.10,
    # Default — evento sin actor identificable (rumor genérico)
    "sin_actor":                0.70,
}


def factor_actor(actor_canonico: str) -> float:
    """Peso multiplicativo del actor en el score (0.70 → 1.30)."""
    if not actor_canonico:
        return 0.70
    return PESO_ACTOR.get(actor_canonico, 1.00)


# =====================================================================
# 3. FACTOR ESCALAMIENTO (potencial de escalada)
# =====================================================================

# Tipos de evento con potencial de escalada estructural
TIPOS_ESCALABLES: set[str] = {
    "moción", "denuncia", "asesinato", "estado_emergencia",
    "bloqueo", "paro", "operativo", "renuncia",
}

# Ubicaciones con alta sensibilidad económica/política
UBICACIONES_CRITICAS: set[str] = {
    "apurimac",       # corredor minero sur
    "cusco",          # corredor minero sur
    "puno",           # zona sensible electoral
    "la_libertad",    # crimen organizado urbano
    "tumbes",         # frontera norte
    "tacna",          # frontera sur
    "frontera_norte",
    "frontera_sur",
    "vraem",          # narco / ffaa
    "lima",           # centro del poder político
    "nacional",       # afecta a todo el país
}

# Actores con capacidad de escalar al ciclo nacional rápidamente
ACTORES_ESCALABLES: set[str] = {
    "presidencia", "premier", "congreso", "tribunal_constitucional",
    "fiscalia", "jnj", "poder_judicial",
    "comunidades_corredor_sur", "transportistas",
    "crimen_organizado", "mineria_ilegal",
    "chile", "ecuador", "venezuela", "eeuu", "oea",
}


def factor_escalamiento(evento: dict) -> float:
    """Heurística multiplicativa del potencial de escalada (0.80 → 1.40).

    Combinaciones que activan:
      · tipo escalable           ×1.15
      · ubicación crítica        ×1.10
      · actor escalable          ×1.10

    Capped en 1.40 (mismo techo que tu brief).
    Floor en 0.80 (eventos triviales).
    """
    if not evento:
        return 1.00
    f = 1.00
    if evento.get("tipo") in TIPOS_ESCALABLES:
        f *= 1.15
    if evento.get("ubicacion") in UBICACIONES_CRITICAS:
        f *= 1.10
    if evento.get("actor") in ACTORES_ESCALABLES:
        f *= 1.10
    # Si el evento es declaracion + sin actor + nacional → trivial
    if (evento.get("tipo") == "declaracion"
        and evento.get("actor") in (None, "sin_actor")):
        f = 0.80
    return min(1.40, max(0.80, f))


# =====================================================================
# 4. FACTOR PERSISTENCIA (Sprint 1.5 versión mínima)
# =====================================================================

def factor_persistencia(event_id: str,
                          archive: Any = None,
                          dias_ventana: int = 14) -> float:
    """Detecta si el mismo evento (o muy similar) ha persistido en días previos.

    Sprint 1.5 versión mínima:
      · Sin archive (o sin tabla `eventos_historicos`): devuelve 1.00
      · Con archive: cuenta cuántos días previos (en la ventana) el mismo
        event_id apareció. Más días persistentes → mayor factor.

    Rango: 1.00 (un día) → 1.25 (≥7 días persistente).

    Justificación doctrinaria: un evento aislado de un día puede ser ruido.
    Un evento que persiste 7+ días es señal estructural.
    """
    if not archive or not event_id:
        return 1.00
    try:
        with archive._conn() as c:
            # Sprint 1.5 versión mínima: no tenemos tabla eventos_historicos
            # todavía. Cuando se implemente (Sprint futuro), aquí va una
            # query del tipo:
            # SELECT COUNT(DISTINCT fecha) FROM eventos_historicos
            # WHERE event_id = ? AND fecha >= date('now', '-{N} days')
            #
            # Por ahora devolvemos 1.0 — la persistencia se implementará cuando
            # el archive guarde eventos dedupeados día a día. Sprint 1.8
            # (validación paralela) introduce la tabla.
            return 1.00
    except Exception:
        return 1.00


# =====================================================================
# APLICACIÓN INTEGRAL · enriquece evento con los 4 factores
# =====================================================================

def aplicar_modificadores(
    evento: dict,
    archive: Any = None,
) -> dict:
    """Devuelve una copia del evento enriquecida con los 4 factores
    y la `severidad_modificada` lista para usar en el cálculo de dimensión.

    Args:
        evento: dict del output de dedupear_eventos
        archive: ApuriskArchive para factor_persistencia (opcional)

    Returns:
        evento + campos:
          · factor_fuente, factor_actor, factor_escalamiento, factor_persistencia
          · severidad_modificada: severidad_base × producto de los 4 factores
                                 (cap 100)
    """
    if not evento:
        return evento

    # 1. Factor fuente (max de las fuentes del cluster)
    noticias = evento.get("origen_noticias") or []
    f_fuente = factor_fuente_cluster(noticias)

    # 2. Factor actor
    f_actor = factor_actor(evento.get("actor", "sin_actor"))

    # 3. Factor escalamiento
    f_escal = factor_escalamiento(evento)

    # 4. Factor persistencia
    f_pers = factor_persistencia(
        evento.get("event_id", ""),
        archive=archive,
    )

    # factor_confirmacion ya viene del clustering (log(1+n_fuentes))
    f_conf = float(evento.get("factor_confirmacion", 1.00))

    sev_base = float(evento.get("severidad_base", 40))
    sev_modificada = sev_base * f_fuente * f_actor * f_escal * f_pers * f_conf
    sev_modificada = min(100.0, sev_modificada)

    out = dict(evento)
    out["factor_fuente"] = round(f_fuente, 3)
    out["factor_actor"] = round(f_actor, 3)
    out["factor_escalamiento"] = round(f_escal, 3)
    out["factor_persistencia"] = round(f_pers, 3)
    out["factor_confirmacion"] = round(f_conf, 3)
    out["severidad_modificada"] = round(sev_modificada, 2)
    return out


def aplicar_modificadores_lista(
    eventos: list[dict],
    archive: Any = None,
) -> list[dict]:
    """Helper para enriquecer una lista completa."""
    return [aplicar_modificadores(ev, archive=archive) for ev in eventos]
