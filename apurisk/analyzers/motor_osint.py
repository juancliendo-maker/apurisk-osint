"""Motor de análisis OSINT con semáforo multiplicativo (Fase C).

Implementa los 4 componentes:
  1. Clasificación de volumen: BRUTO / SOSPECHOSO / SUSTANTIVO / CRÍTICO
  2. Fórmula multiplicativa del semáforo: ∏(factor_i ^ peso_i) con pesos=exponentes
  3. Activadores automáticos de ROJO (absoluto vs condicional)
  4. Reporte de 10 puntos (1-7 señales, 8-10 capa interpretativa marcada)

Modos de ejecución:
  - AUTOMÁTICO: procesa los 10 puntos sin pausa
  - CON_PRECISIONES: devuelve resultado parcial tras punto 3 esperando confirmación

Nota matemática (punto 2):
  Con todos los pesos = 1.0 → ∏(f_i ^ 1.0) = ∏ f_i = VC × PA × CE × IA × V
  Los pesos como exponentes son extensión natural de la multiplicación pura.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Optional

from .entities import extraer_entidades, INSTITUCIONES, PARTIDOS, REGIONES
from .sentiment import analizar_sentimiento
from .topics import detectar_temas

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de clasificación de volumen
# ─────────────────────────────────────────────────────────────────────────────

UMBRAL_SOSPECHOSO = 3   # artículos brutos < umbral → SOSPECHOSO (poco volumen)
UMBRAL_SUSTANTIVO = 10  # >= umbral → SUSTANTIVO
UMBRAL_CRITICO = 25     # >= umbral → CRÍTICO

# Factores sin dato disponible (requieren señales sociales/Twitter — pausadas)
_FACTORES_SIN_DATO = {
    "anonimato",
    "automatizacion_probable",
    "baja_interaccion_autentica",
    "patron_coordinado",
}

# ─────────────────────────────────────────────────────────────────────────────
# Heurística de peso del actor (PA) — origen: "estimado", sin tabla BD aún
# ─────────────────────────────────────────────────────────────────────────────

_ACTORES_PESO: dict[str, float] = {
    # Ejecutivo
    "presidenta": 0.95, "presidente": 0.95, "boluarte": 0.95,
    "premier": 0.80, "primer ministro": 0.80, "pcm": 0.75,
    "consejo de ministros": 0.75, "ministro": 0.65,
    # Legislativo
    "congreso": 0.85, "congresista": 0.60, "mesa directiva": 0.70,
    "pleno": 0.75,
    # Judicial / ministerio público
    "fiscalía": 0.80, "fiscalia": 0.80, "fiscal": 0.70,
    "poder judicial": 0.80, "juez": 0.60, "tribunal constitucional": 0.85,
    "jnj": 0.80, "junta nacional de justicia": 0.80,
    # Electoral
    "jne": 0.75, "onpe": 0.70,
    # Militares
    "fuerzas armadas": 0.80, "ffaa": 0.80, "ejército": 0.70, "ejercito": 0.70,
    "marina": 0.65, "fuerza aérea": 0.65, "fuerza aerea": 0.65, "pnp": 0.65,
    # Reguladores / control
    "contraloría": 0.70, "contraloria": 0.70, "sunat": 0.60,
    "bcr": 0.70, "mef": 0.70,
    # Internacional
    "oea": 0.75, "onu": 0.80, "estados unidos": 0.80, "eeuu": 0.80,
    # Social / conflicto
    "comunidades": 0.50, "ronderos": 0.50, "sindicato": 0.55,
    "frente de defensa": 0.55,
    # Empresas de riesgo
    "las bambas": 0.70, "antamina": 0.65, "petroperú": 0.60, "petroperu": 0.60,
    "tía maría": 0.65, "tia maria": 0.65,
}

_DEFAULT_PA = 0.40  # peso base cuando no se identifica actor


def _estimar_peso_actor(text: str) -> tuple[float, str]:
    """Devuelve (peso 0-1, actor_identificado). Origen siempre 'estimado'."""
    texto_lower = text.lower()
    mejor_peso = _DEFAULT_PA
    mejor_actor = "no_identificado"
    for actor, peso in _ACTORES_PESO.items():
        if actor in texto_lower and peso > mejor_peso:
            mejor_peso = peso
            mejor_actor = actor
    return mejor_peso, mejor_actor


# ─────────────────────────────────────────────────────────────────────────────
# Componente 1 — Clasificación de volumen
# ─────────────────────────────────────────────────────────────────────────────

def clasificar_volumen(n_bruto: int, n_fuentes: int, jaccard_dup: float = 0.0) -> dict:
    """Clasifica el volumen de artículos en 4 bandas.

    n_bruto: artículos totales del ciclo.
    n_fuentes: fuentes distintas que reportan.
    jaccard_dup: índice de duplicación estimado (0-1); si >0.7 → SOSPECHOSO.
    """
    # Determinar clase
    if n_bruto < UMBRAL_SOSPECHOSO or jaccard_dup > 0.70:
        clase = "SOSPECHOSO"
        descripcion = "Volumen bajo o alta duplicación — señal débil"
    elif n_bruto >= UMBRAL_CRITICO and n_fuentes >= 5:
        clase = "CRÍTICO"
        descripcion = "Volumen muy alto en múltiples fuentes — señal potente"
    elif n_bruto >= UMBRAL_SUSTANTIVO:
        clase = "SUSTANTIVO"
        descripcion = "Volumen suficiente para análisis confiable"
    else:
        clase = "BRUTO"
        descripcion = "Artículos sin filtrar — analizar con cautela"

    return {
        "clase": clase,
        "n_bruto": n_bruto,
        "n_fuentes": n_fuentes,
        "jaccard_dup": round(jaccard_dup, 3),
        "descripcion": descripcion,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Componente 2 — Factores del semáforo (VC, PA, CE, IA, V)
# ─────────────────────────────────────────────────────────────────────────────

def _calcular_vc(articles: list, riesgo_nacional: float = 0.0) -> tuple[float, str]:
    """Volatilidad del Contexto (VC): riesgo nacional normalizado 0-1.

    Usa el score de riesgo nacional (0-100) escalado a 0-1.
    Origen: 'real'.
    """
    vc = max(0.0, min(1.0, riesgo_nacional / 100.0))
    if vc == 0.0 and articles:
        # Proxy: ratio de artículos con sentimiento negativo
        negs = sum(1 for a in articles
                   if analizar_sentimiento((a.title or "") + " " + (a.summary or ""))["label"] == "negativo")
        vc = round(negs / len(articles), 3)
    return round(vc, 4), "proxy" if riesgo_nacional == 0.0 else "real"


def _calcular_ce(articles: list, n_fuentes: int) -> tuple[float, str]:
    """Capacidad de Escalamiento (CE): criticidad + diversidad de fuentes.

    Origen: 'proxy'.
    """
    if not articles:
        return 0.0, "proxy"
    n = len(articles)
    alta = sum(1 for a in articles if getattr(a, "criticidad", "media") == "alta")
    media = sum(1 for a in articles if getattr(a, "criticidad", "media") == "media")
    # Puntuación base por criticidad (0-1)
    score_crit = min(1.0, (alta * 1.0 + media * 0.5) / n)
    # Amplificador por diversidad de fuentes (log normalizado)
    amp_fuentes = min(1.0, math.log1p(n_fuentes) / math.log1p(15))
    ce = round((score_crit * 0.7 + amp_fuentes * 0.3), 4)
    return ce, "proxy"


def _calcular_ia(articles: list, n_fuentes: int) -> tuple[float, str]:
    """Intensidad Acumulada (IA): sentimiento negativo × cobertura.

    Origen: 'proxy'.
    """
    if not articles:
        return 0.0, "proxy"
    total_neg = 0.0
    for a in articles:
        sent = analizar_sentimiento((a.title or "") + " " + (a.summary or ""))
        if sent["label"] == "negativo":
            total_neg += abs(sent["score"])
        elif sent["label"] == "neutral":
            total_neg += 0.1
    # Normalizar: máx teórico = n_articulos × 1.0
    base = round(min(1.0, total_neg / max(1, len(articles))), 4)
    # Amplificador por número de fuentes
    amp = min(1.0, math.log1p(n_fuentes) / math.log1p(10))
    ia = round(min(1.0, base * 0.6 + amp * 0.4), 4)
    return ia, "proxy"


def _calcular_v(articles: list) -> tuple[float, str]:
    """Volatilidad temporal (V): dispersión de publicaciones en ventana 7 días.

    Artículos publicados en múltiples días distintos → mayor volatilidad.
    Origen: 'real'.
    """
    if not articles:
        return 0.0, "real"
    fechas: set[str] = set()
    for a in articles:
        pub = getattr(a, "published", None)
        if pub:
            try:
                # Extraer solo la fecha (YYYY-MM-DD)
                fechas.add(pub[:10])
            except Exception:
                pass
    n_dias = max(1, len(fechas))
    # 1 día → baja volatilidad, 7+ días → máxima
    v = round(min(1.0, (n_dias - 1) / 6.0), 4)
    return v, "real"


def calcular_factores_semaforo(articles: list, formula_config: list,
                                riesgo_nacional: float = 0.0) -> dict:
    """Calcula los 5 factores del semáforo (VC, PA, CE, IA, V).

    formula_config: lista de {factor, nombre, peso} desde BD.
    Devuelve {factor: {valor, peso, origen, nombre}}.
    """
    n_fuentes = len({getattr(a, "source_name", "") for a in articles})
    text_total = " ".join((a.title or "") + " " + (a.summary or "") for a in articles)

    # Calcular cada factor
    vc_val, vc_ori = _calcular_vc(articles, riesgo_nacional)
    pa_val, pa_actor = _estimar_peso_actor(text_total)
    ce_val, ce_ori = _calcular_ce(articles, n_fuentes)
    ia_val, ia_ori = _calcular_ia(articles, n_fuentes)
    v_val, v_ori = _calcular_v(articles)

    raw = {
        "VC": (vc_val, "real" if riesgo_nacional > 0 else "proxy"),
        "PA": (pa_val, "estimado"),
        "CE": (ce_val, "proxy"),
        "IA": (ia_val, "proxy"),
        "V": (v_val, "real"),
    }

    # Obtener pesos desde config (fallback = 1.0)
    pesos_config = {r["factor"]: r for r in formula_config}

    factores: dict[str, dict] = {}
    for factor_key, (valor, origen) in raw.items():
        cfg = pesos_config.get(factor_key, {})
        nombre = cfg.get("nombre", factor_key)
        peso = float(cfg.get("peso", 1.0))
        factores[factor_key] = {
            "valor": valor,
            "peso": peso,
            "origen": origen,
            "nombre": nombre,
        }

    # Agregar metadata del actor PA
    factores["PA"]["actor_identificado"] = pa_actor

    return factores


def calcular_semaforo(factores: dict, umbrales: list) -> dict:
    """Aplica la fórmula multiplicativa y determina nivel del semáforo.

    Fórmula: score = ∏(factor_i ^ peso_i)
    Con peso_i=1.0: idéntico a VC × PA × CE × IA × V.
    score ∈ [0, 1].
    """
    score = 1.0
    for fk, fd in factores.items():
        valor = max(0.0, min(1.0, float(fd["valor"])))
        peso = float(fd["peso"])
        if valor == 0.0:
            # Factor 0 colapsa el resultado (intencional)
            score = 0.0
            break
        score *= valor ** peso

    score = round(score, 6)

    # Determinar nivel desde umbrales de BD (fallback hardcodeado)
    nivel = _resolver_nivel(score, umbrales)

    return {"score": score, "nivel": nivel["nivel_sugerido"],
            "color_hex": nivel.get("color_hex"), "umbral_banda": nivel}


def _resolver_nivel(score: float, umbrales: list) -> dict:
    """Busca el umbral que contiene el score (0-1, las bandas usan %)."""
    score_pct = score * 100.0  # convertir a % para comparar con rango_min/rango_max
    for u in umbrales:
        if u["rango_min"] <= score_pct <= u["rango_max"]:
            return u
    # Fallback hardcodeado si BD no tiene umbrales
    if score_pct <= 3:
        return {"rango_min": 0, "rango_max": 3, "nivel_sugerido": "VERDE", "color_hex": "#2D9E56",
                "nivel_secundario": "AMARILLO", "color_secundario_hex": "#F5A623"}
    if score_pct <= 9:
        return {"rango_min": 3, "rango_max": 9, "nivel_sugerido": "AMARILLO", "color_hex": "#F5A623",
                "nivel_secundario": "NARANJA", "color_secundario_hex": "#E06000"}
    if score_pct <= 19:
        return {"rango_min": 10, "rango_max": 19, "nivel_sugerido": "NARANJA ALTO", "color_hex": "#E06000",
                "nivel_secundario": None, "color_secundario_hex": None}
    if score_pct <= 30:
        return {"rango_min": 20, "rango_max": 30, "nivel_sugerido": "ROJO PROBABLE", "color_hex": "#C0392B",
                "nivel_secundario": None, "color_secundario_hex": None}
    return {"rango_min": 30, "rango_max": 100, "nivel_sugerido": "ROJO", "color_hex": "#7B0000",
            "nivel_secundario": None, "color_secundario_hex": None}


# ─────────────────────────────────────────────────────────────────────────────
# Componente 3 — Activadores de ROJO
# ─────────────────────────────────────────────────────────────────────────────

# Palabras vacías que no aportan señal de activación (no deben contar como match)
_STOPWORDS_ACT = {
    "o", "u", "y", "e", "de", "del", "la", "el", "los", "las", "en", "por",
    "a", "al", "un", "una", "como", "que", "se", "su", "sus", "para", "con",
    "públic", "público", "pública", "formal", "activada", "anunciada",
    "documentado", "directa", "instalando", "abierta",
}

# Umbral de solapamiento: fracción de tokens significativos del activador que
# deben aparecer en el corpus para considerarlo disparado (heurístico, conservador).
_UMBRAL_ACTIVADOR = 0.40


def _tokens_significativos(texto: str) -> set[str]:
    toks = re.findall(r"[a-záéíóúñ]{4,}", texto.lower())
    return {t for t in toks if t not in _STOPWORDS_ACT}


def verificar_activadores(articles: list, activadores_config: list) -> dict:
    """Detecta activadores de ROJO en el corpus de artículos.

    Heurística de solapamiento de tokens: el activador se dispara si al menos
    _UMBRAL_ACTIVADOR de sus tokens significativos aparecen en el corpus. Es
    deliberadamente conservador (origen 'proxy') — un activador disparado eleva
    el nivel pero un absoluto siempre fuerza ROJO (la fórmula no lo baja).

    Retorna {disparado, tipo: 'absoluto'|'condicional'|None,
             activadores_detectados: [{descripcion, tipo, solapamiento}]}.
    """
    if not articles:
        return {"disparado": False, "tipo": None, "activadores_detectados": []}

    corpus_tokens = _tokens_significativos(
        " ".join((a.title or "") + " " + (a.summary or "") for a in articles)
    )

    detectados = []
    tipo_mas_grave = None

    for act in activadores_config:
        desc_tokens = _tokens_significativos(act["descripcion"])
        if not desc_tokens:
            continue
        solape = len(desc_tokens & corpus_tokens) / len(desc_tokens)
        if solape >= _UMBRAL_ACTIVADOR:
            tipo = act["tipo"]
            detectados.append({
                "descripcion": act["descripcion"],
                "tipo": tipo,
                "solapamiento": round(solape, 2),
            })
            if tipo == "absoluto":
                tipo_mas_grave = "absoluto"
            elif tipo_mas_grave != "absoluto":
                tipo_mas_grave = "condicional"

    return {
        "disparado": len(detectados) > 0,
        "tipo": tipo_mas_grave,
        "activadores_detectados": detectados,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Componente 4 — Reporte de 10 puntos
# ─────────────────────────────────────────────────────────────────────────────

def _punto_1_volumen(vol: dict) -> dict:
    return {
        "punto": 1,
        "titulo": "Volumen y calidad del corpus",
        "capa": "señal",
        "resultado": vol["clase"],
        "detalle": {
            "n_bruto": vol["n_bruto"],
            "n_fuentes": vol["n_fuentes"],
            "jaccard_dup": vol["jaccard_dup"],
            "descripcion": vol["descripcion"],
        },
    }


def _punto_2_temas(articles: list) -> dict:
    temas = detectar_temas(articles)
    conteos = temas.get("conteos", {})
    top3 = sorted(conteos.items(), key=lambda x: x[1], reverse=True)[:3]
    return {
        "punto": 2,
        "titulo": "Temas dominantes detectados",
        "capa": "señal",
        "resultado": [{"tema": t, "menciones": n} for t, n in top3],
        "detalle": conteos,
    }


def _punto_3_entidades(articles: list) -> dict:
    ent = extraer_entidades(articles)
    return {
        "punto": 3,
        "titulo": "Entidades clave mencionadas",
        "capa": "señal",
        "resultado": {
            "instituciones": ent["instituciones"][:5],
            "partidos": ent["partidos"][:3],
            "regiones": ent["regiones"][:3],
        },
        "detalle": ent,
    }


def _punto_4_sentimiento(articles: list) -> dict:
    if not articles:
        return {"punto": 4, "titulo": "Sentimiento agregado", "capa": "señal",
                "resultado": "neutral", "detalle": {}}
    scores = []
    dist = {"positivo": 0, "negativo": 0, "neutral": 0}
    for a in articles:
        s = analizar_sentimiento((a.title or "") + " " + (a.summary or ""))
        scores.append(s["score"])
        dist[s["label"]] += 1
    avg = round(sum(scores) / len(scores), 3)
    etiqueta = "positivo" if avg > 0.2 else ("negativo" if avg < -0.2 else "neutral")
    return {
        "punto": 4,
        "titulo": "Sentimiento agregado",
        "capa": "señal",
        "resultado": etiqueta,
        "detalle": {"score_promedio": avg, "distribucion": dist},
    }


def _punto_5_activadores(act_result: dict) -> dict:
    return {
        "punto": 5,
        "titulo": "Activadores automáticos de ROJO",
        "capa": "señal",
        "resultado": "DISPARADO" if act_result["disparado"] else "ninguno",
        "detalle": act_result,
    }


def _punto_6_factores(factores: dict, semaforo: dict) -> dict:
    cobertura_total = len(factores)
    disponibles = cobertura_total  # todos calculados (sin dato → excluidos antes de llamar)
    return {
        "punto": 6,
        "titulo": "Factores del semáforo",
        "capa": "señal",
        "resultado": {
            "score_semaforo": semaforo["score"],
            "nivel": semaforo["nivel"],
            "color_hex": semaforo["color_hex"],
        },
        "detalle": {
            "cobertura": f"{disponibles} de {cobertura_total} factores disponibles",
            "factores": {k: {
                "valor": v["valor"],
                "peso": v["peso"],
                "origen": v["origen"],
                "nombre": v["nombre"],
            } for k, v in factores.items()},
            "formula": "∏(factor_i ^ peso_i)",
            "nota_equivalencia": "Con pesos=1.0: idéntico a VC×PA×CE×IA×V",
        },
    }


def _punto_7_cobertura_geografica(articles: list) -> dict:
    regiones_mencionadas: dict[str, int] = {}
    from .entities import REGIONES, _find_all
    for a in articles:
        text = (a.title or "") + " " + (a.summary or "")
        for r in _find_all(text, REGIONES):
            regiones_mencionadas[r] = regiones_mencionadas.get(r, 0) + 1
    top_reg = sorted(regiones_mencionadas.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "punto": 7,
        "titulo": "Cobertura geográfica",
        "capa": "señal",
        "resultado": "nacional" if len(regiones_mencionadas) >= 3 else ("regional" if regiones_mencionadas else "sin_datos"),
        "detalle": {"regiones": top_reg},
    }


# Puntos 8-10: capa interpretativa — marcada explícitamente

def _punto_8_nivel_riesgo_interpretado(semaforo: dict, act_result: dict,
                                        vol: dict) -> dict:
    nivel_base = semaforo["nivel"]
    # Elevar a ROJO si activador absoluto
    if act_result["tipo"] == "absoluto":
        nivel_final = "ROJO"
        razon = "Activador absoluto disparado — eleva a ROJO independientemente del score"
    elif act_result["tipo"] == "condicional":
        nivel_final = nivel_base  # fórmula aún modula
        razon = f"Activador condicional detectado — score semáforo modula el nivel ({nivel_base})"
    else:
        nivel_final = nivel_base
        razon = "Sin activadores — nivel determinado por score semáforo"

    if vol["clase"] == "SOSPECHOSO":
        razon += " · Corpus SOSPECHOSO: reducir confianza del nivel"

    return {
        "punto": 8,
        "titulo": "Nivel de riesgo interpretado",
        "capa": "interpretativa",
        "advertencia": "CAPA INTERPRETATIVA — requiere validación analista",
        "resultado": nivel_final,
        "detalle": {"nivel_formula": nivel_base, "razon": razon},
    }


def _punto_9_factores_agravantes(factores: dict, articles: list) -> dict:
    agravantes = []
    atenuantes = []

    # Agravantes: factores con valor > 0.7
    for fk, fd in factores.items():
        if fd["valor"] >= 0.70:
            agravantes.append(f"{fd['nombre']} ({fk}): {fd['valor']:.2f} [{fd['origen']}]")
        elif fd["valor"] <= 0.25:
            atenuantes.append(f"{fd['nombre']} ({fk}): {fd['valor']:.2f} [{fd['origen']}]")

    # Agravante por volumen crítico
    if hasattr(articles, "__len__") and len(articles) >= UMBRAL_CRITICO:
        agravantes.append(f"Volumen crítico: {len(articles)} artículos")

    return {
        "punto": 9,
        "titulo": "Factores agravantes y atenuantes",
        "capa": "interpretativa",
        "advertencia": "CAPA INTERPRETATIVA — estimaciones proxy, no señales verificadas",
        "resultado": {"agravantes": agravantes, "atenuantes": atenuantes},
        "detalle": {
            "nota_PA": "Peso del Actor (PA) calculado por heurística — origen 'estimado'",
            "nota_CE_IA": "CE e IA son proxies basados en criticidad y sentimiento",
        },
    }


def _punto_10_recomendaciones(nivel_final: str, act_result: dict,
                               vol: dict, factores: dict) -> dict:
    recomendaciones = []

    if nivel_final == "ROJO":
        recomendaciones.append("Escalar inmediatamente al analista senior")
        recomendaciones.append("Verificar activadores disparados con fuentes primarias")
    elif nivel_final in ("ROJO PROBABLE", "NARANJA ALTO"):
        recomendaciones.append("Ampliar monitoreo de fuentes en próximas 6h")
        recomendaciones.append("Confirmar manualmente los activadores condicionales detectados")
    elif nivel_final == "AMARILLO":
        recomendaciones.append("Seguimiento ordinario con frecuencia aumentada")
    else:
        recomendaciones.append("Monitoreo estándar — sin acción inmediata requerida")

    if vol["clase"] == "SOSPECHOSO":
        recomendaciones.append("Ampliar fuentes: corpus actual es insuficiente")

    # Factores con origen estimado que deberían validarse
    estimados = [fk for fk, fd in factores.items() if fd["origen"] == "estimado"]
    if estimados:
        recomendaciones.append(
            f"Validar manualmente: {', '.join(estimados)} (origen estimado/heurístico)"
        )

    return {
        "punto": 10,
        "titulo": "Recomendaciones de seguimiento",
        "capa": "interpretativa",
        "advertencia": "CAPA INTERPRETATIVA — sin plantillas sectoriales aún",
        "resultado": recomendaciones,
        "detalle": {
            "nivel_disparador": nivel_final,
            "activadores_detectados": len(act_result["activadores_detectados"]),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Función principal del motor
# ─────────────────────────────────────────────────────────────────────────────

def analizar_osint(
    articles: list,
    db_path: str,
    pais: str = "PE",
    riesgo_nacional: float = 0.0,
    jaccard_dup: float = 0.0,
    modo: str = "AUTOMATICO",  # "AUTOMATICO" | "CON_PRECISIONES"
    articulo_id: Optional[int] = None,
    persistir: bool = True,
) -> dict:
    """Ejecuta el motor OSINT completo sobre un corpus de artículos.

    Parámetros:
        articles: lista de Article del ciclo de análisis.
        db_path: ruta a la BD SQLite (para leer config y guardar resultado).
        pais: código de país para filtrar config (default 'PE').
        riesgo_nacional: score nacional 0-100 para VC (0 → usa proxy).
        jaccard_dup: índice de duplicación del corpus (0-1).
        modo: 'AUTOMATICO' → 10 puntos completos; 'CON_PRECISIONES' → parcial hasta punto 3.
        articulo_id: id del artículo en BD si aplica (para persistir resultado).
        persistir: si True, guarda en resultados_analisis.

    Devuelve resultado_json con todos los componentes.
    """
    from apurisk.storage.config_loader import (
        cargar_formula_semaforo,
        cargar_umbrales_semaforo,
        cargar_activadores_rojo,
        guardar_resultado_analisis,
    )

    # Cargar config desde BD
    formula_config = cargar_formula_semaforo(db_path, pais=pais)
    umbrales = cargar_umbrales_semaforo(db_path, pais=pais)
    activadores_config = cargar_activadores_rojo(db_path, pais=pais)

    # ── Componente 1: Volumen ─────────────────────────────────────────────
    n_fuentes = len({getattr(a, "source_name", "") for a in articles})
    vol = clasificar_volumen(len(articles), n_fuentes, jaccard_dup)

    # ── Puntos 1-3 (siempre) ─────────────────────────────────────────────
    p1 = _punto_1_volumen(vol)
    p2 = _punto_2_temas(articles)
    p3 = _punto_3_entidades(articles)

    if modo == "CON_PRECISIONES":
        return {
            "motor": "osint",
            "pais": pais,
            "modo": modo,
            "estado": "PARCIAL — esperando confirmación analista (punto 3 completado)",
            "procesado_en": datetime.now(timezone.utc).isoformat(),
            "puntos": [p1, p2, p3],
            "siguiente_paso": "Confirmar o corregir entidades/temas antes de continuar",
        }

    # ── Componentes 2, 3 (modo AUTOMATICO) ───────────────────────────────
    factores = calcular_factores_semaforo(articles, formula_config, riesgo_nacional)
    semaforo = calcular_semaforo(factores, umbrales)
    act_result = verificar_activadores(articles, activadores_config)

    # ── Puntos 4-7 ───────────────────────────────────────────────────────
    p4 = _punto_4_sentimiento(articles)
    p5 = _punto_5_activadores(act_result)
    p6 = _punto_6_factores(factores, semaforo)
    p7 = _punto_7_cobertura_geografica(articles)

    # ── Puntos 8-10 (capa interpretativa) ────────────────────────────────
    p8 = _punto_8_nivel_riesgo_interpretado(semaforo, act_result, vol)
    p9 = _punto_9_factores_agravantes(factores, articles)
    p10 = _punto_10_recomendaciones(p8["resultado"], act_result, vol, factores)

    # ── Score sustancia (suma ponderada de factores disponibles) ─────────
    _score_sustancia = round(
        sum(fd["valor"] * fd["peso"] for fd in factores.values()) /
        max(1, sum(fd["peso"] for fd in factores.values())),
        4
    )

    # ── Score ruido (factores sin dato excluidos) ─────────────────────────
    # VC y V son señales de "ruido" en sentido de amplificación; CE, IA son proxy ruido
    _score_ruido = round(
        (factores.get("CE", {}).get("valor", 0) +
         factores.get("IA", {}).get("valor", 0)) / 2.0,
        4
    )

    resultado = {
        "motor": "osint",
        "pais": pais,
        "modo": modo,
        "estado": "COMPLETO",
        "procesado_en": datetime.now(timezone.utc).isoformat(),
        "volumen": vol,
        "semaforo": {
            "score": semaforo["score"],
            "nivel": semaforo["nivel"],
            "nivel_interpretado": p8["resultado"],
            "color_hex": semaforo["color_hex"],
            "activador_disparado": act_result["disparado"],
            "activador_tipo": act_result["tipo"],
        },
        "factores_semaforo": {k: {
            "valor": v["valor"],
            "peso": v["peso"],
            "origen": v["origen"],
            "nombre": v["nombre"],
        } for k, v in factores.items()},
        "cobertura_factores": f"{len(factores)} de 5 factores calculados",
        "factores_sin_dato": list(_FACTORES_SIN_DATO),
        "puntos": [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10],
        "score_sustancia": _score_sustancia,
        "score_ruido": _score_ruido,
    }

    # ── Persistir en BD si aplica ─────────────────────────────────────────
    if persistir and articulo_id is not None:
        try:
            guardar_resultado_analisis(
                db_path=db_path,
                articulo_id=articulo_id,
                motor="osint",
                score_sustancia=_score_sustancia,
                score_ruido=_score_ruido,
                score_semaforo=semaforo["score"],
                nivel_semaforo=p8["resultado"],
                activador_rojo=act_result["disparado"],
                resultado_json=json.dumps(resultado, ensure_ascii=False),
                pais=pais,
            )
        except Exception as e:
            resultado["_persistencia_error"] = str(e)

    return resultado
