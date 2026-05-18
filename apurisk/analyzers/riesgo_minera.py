"""Motor de análisis de Riesgo Político para empresas mineras.

Plantilla genérica nacional configurable por:
  - departamentos de operación (filtro geográfico)
  - empresa específica (opcional, para precarga de datos)
  - horizonte temporal (semanal por default)

Produce 8 factores propietarios de riesgo minero P×I + análisis multidimensional
basado en los datos OSINT archivados en SQLite y el snapshot actual.

Salida: dict estructurado consumible por pdf_minera.py para generar el reporte.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from typing import Optional

try:
    from ..utils.timezone_pe import now_pe, now_pe_iso, fmt_pe, parse_to_pe
except ImportError:
    from apurisk.utils.timezone_pe import now_pe, now_pe_iso, fmt_pe, parse_to_pe


# =====================================================================
# 8 FACTORES DE RIESGO PROPIETARIOS MINEROS (P×I)
# =====================================================================

FACTORES_MINEROS = {
    "licencia_social_comunitaria": {
        "nombre": "Licencia social comunitaria",
        "descripcion": "Apoyo o rechazo de comunidades campesinas en zona de operación",
        "keywords_alta": ["comunidad rechaza", "rompe diálogo", "asamblea comunal",
                          "comuneros se oponen", "exigen consulta previa",
                          "movilización comunal", "frente de defensa"],
        "keywords_media": ["comunidad", "consulta previa", "diálogo", "convenio marco",
                            "responsabilidad social"],
    },
    "bloqueo_corredor": {
        "nombre": "Riesgo de bloqueo de corredor logístico",
        "descripcion": "Bloqueos de carreteras críticas (Las Bambas, Panamericana)",
        "keywords_alta": ["bloqueo corredor minero", "toma de carretera",
                          "paro indefinido", "panamericana bloqueada",
                          "carretera tomada", "vehículos varados",
                          "convoy minero detenido"],
        "keywords_media": ["bloqueo", "paro", "tránsito interrumpido",
                            "marcha de protesta"],
    },
    "riesgo_regulatorio_sectorial": {
        "nombre": "Riesgo regulatorio sectorial minero",
        "descripcion": "Proyectos de ley, decretos y normas que afectan minería",
        "keywords_alta": ["proyecto de ley minería", "reforma minera",
                          "nacionalizar minas", "elevación de regalías",
                          "moratoria minera", "consulta previa obligatoria",
                          "endurecer fiscalización ambiental"],
        "keywords_media": ["minería", "minam", "minem", "regalías", "canon minero",
                            "fiscalización ambiental", "oefa"],
    },
    "riesgo_tributario": {
        "nombre": "Riesgo tributario y de regalías",
        "descripcion": "Cambios en regalías, impuestos especiales, redistribución",
        "keywords_alta": ["sobreganancias minera", "impuesto extraordinario minería",
                          "modificar régimen tributario minero", "subir regalías",
                          "windfall tax"],
        "keywords_media": ["regalías", "canon", "tributación minera",
                            "carga tributaria", "sunat fiscaliza"],
    },
    "riesgo_socioambiental": {
        "nombre": "Riesgo socioambiental",
        "descripcion": "Contaminación, pasivos, certificación ANA, conflictos hídricos",
        "keywords_alta": ["derrame minero", "contaminación mina",
                          "agua envenenada", "pasivo ambiental",
                          "denuncia oefa", "cancelación certificación",
                          "mercurio minería", "rio contaminado"],
        "keywords_media": ["ambiental", "agua", "remediación", "ana",
                            "certificación", "monitoreo ambiental"],
    },
    "riesgo_seguridad_operativa": {
        "nombre": "Riesgo de seguridad operativa",
        "descripcion": "Atentados, robo de explosivos, infiltración, sabotaje",
        "keywords_alta": ["atentado mina", "robo explosivos", "sabotaje mina",
                          "incendio mina", "asalto convoy", "secuestro empleados",
                          "extorsión minera"],
        "keywords_media": ["seguridad mina", "vigilancia minera", "patrullaje"],
    },
    "riesgo_imagen_mediatica": {
        "nombre": "Riesgo de imagen y reputación mediática",
        "descripcion": "Cobertura mediática hostil, virales negativos",
        "keywords_alta": ["escándalo minero", "denuncia contra minera",
                          "video viral minería", "minería cuestionada",
                          "imagen minera deteriorada"],
        "keywords_media": ["minera", "minería", "investigación periodística",
                            "reportaje"],
    },
    "riesgo_electoral_cambio_politica": {
        "nombre": "Riesgo electoral y cambio de política minera",
        "descripcion": "Cambios de gobierno con potencial efecto sobre minería",
        "keywords_alta": ["candidato anti minería", "nuevo gobierno",
                          "cambio política minera", "estatización",
                          "revisar concesiones"],
        "keywords_media": ["elecciones", "candidato", "campaña", "balotaje",
                            "segunda vuelta", "presidente electo"],
    },
    # ===== NUEVOS FACTORES AVANZADOS (mayo 2026) =====
    "mineria_ilegal_artesanal": {
        "nombre": "Minería ilegal y artesanal informal",
        "descripcion": "La Pampa, dragado ilegal, oro ilegal, REINFO, minería informal aurífera",
        "keywords_alta": ["minería ilegal", "mineria ilegal", "mineros ilegales",
                          "dragado ilegal", "extracción ilegal de oro",
                          "operativo mercurio", "interdicción minera",
                          "la pampa madre de dios", "balsa ilegal",
                          "deforestación minería ilegal",
                          "asesinato defensor ambiental",
                          "mineros informales protestan",
                          "minería ilegal supera narcotráfico"],
        "keywords_media": ["minería informal", "mineria informal", "reinfo",
                            "registro integral formalización minera",
                            "madre de dios", "puerto maldonado",
                            "balsa de extracción", "concesión informal",
                            "amazonía minería"],
    },
    "crimen_organizado_transnacional": {
        "nombre": "Crimen organizado transnacional y narco-minería",
        "descripcion": "Cárteles, lavado de activos, rutas oro Bolivia/Colombia/Brasil, narco-minería",
        "keywords_alta": ["narco-minería", "narcominería",
                          "lavado de activos minería", "lavado oro",
                          "cártel oro", "ruta del oro ilegal",
                          "frontera bolivia oro", "frontera colombia oro",
                          "tren de aragua oro",
                          "extorsión minera por bandas",
                          "sicariato minería ilegal",
                          "primer comando capital", "pcc oro",
                          "minería ilegal supera narcotráfico",
                          "carteles colombianos oro perú",
                          "comando vermelho",
                          "crimen organizado oro"],
        "keywords_media": ["lavado de activos", "operación oro",
                            "incauta oro ilegal", "decomisa oro",
                            "rutas ilícitas", "interdicción aurífera",
                            "santa rosa", "iñapari frontera"],
    },
    "presion_internacional_eeuu": {
        "nombre": "Presión internacional / EEUU y sanciones",
        "descripcion": "OFAC, FinCEN, Magnitsky, DEA, SOUTHCOM, política bilateral Perú-EEUU sobre lavado y narco",
        "keywords_alta": ["sanciones ofac", "ofac sanciona",
                          "treasury sanctions peru",
                          "magnitsky perú", "magnitsky peru",
                          "fincen perú", "fincen peru",
                          "lista clinton peru", "lista negra eeuu",
                          "dea operativo peru", "southcom peru",
                          "us state department peru",
                          "ambassador peru sanctions",
                          "department of treasury peru",
                          "embajada eeuu sanciones",
                          "designación criminal eeuu"],
        "keywords_media": ["estados unidos", "departamento de estado",
                            "embajada estadounidense", "kennedy peru",
                            "ayuda bilateral", "cooperación dea",
                            "narcotráfico transfronterizo",
                            "tier 3 trafficking", "drug certification"],
    },
    "corrupcion_sectorial": {
        "nombre": "Corrupción sectorial y captura institucional",
        "descripcion": "Sobornos, conflictos de interés, captura del MINEM/OEFA/ANA, transparencia",
        "keywords_alta": ["soborno minería", "coima minería",
                          "captura institucional minería",
                          "exministro investigado",
                          "conflicto de interés ministro",
                          "puerta giratoria minería",
                          "transparency peru mining",
                          "transparency international perú minería",
                          "lava jato minería", "odebrecht minería",
                          "operación car wash perú",
                          "denuncia constitucional ministro minero",
                          "fiscalía investiga minera",
                          "lobby ilegal minería"],
        "keywords_media": ["corrupción", "transparencia",
                            "denuncia anticorrupción",
                            "comisión ad hoc", "fiscalía especial",
                            "procurador anticorrupción",
                            "compliance minero", "due diligence sectorial",
                            "integridad pública"],
    },
    "riesgo_capital_mercado": {
        "nombre": "Riesgo de capital, mercados e inversión",
        "descripcion": "Sentimiento inversor, riesgo país, downgrade, retiro de capital, mining capital allocation",
        "keywords_alta": ["inversores nerviosos minería",
                          "mining investors jittery",
                          "retiro inversión minera", "ralentización inversión minera",
                          "cae inversión minera",
                          "downgrade calificación perú",
                          "fitch rebaja perú", "s&p rebaja peru",
                          "moody's rebaja peru",
                          "riesgo país perú sube",
                          "fuga de capitales",
                          "mining capital allocation peru",
                          "embi+ perú", "cds perú",
                          "mineras suspenden inversión"],
        "keywords_media": ["inversión privada", "ciec minero", "cartera minera",
                            "proyectos en pausa", "stand-by",
                            "presupuesto exploración", "capex minero",
                            "calificadora", "rating soberano",
                            "sentimiento de mercado", "expectativas empresariales"],
    },
}


# Departamentos con presencia minera relevante (priorizamos los de mayor producción)
DEPARTAMENTOS_MINEROS = {
    "Apurímac": ["Las Bambas (MMG)", "Antabamba", "Cotabambas", "Grau", "Antilla"],
    "Áncash": ["Antamina (BHP/Glencore/Teck)", "Pierina (Barrick)", "Huarmey"],
    "Arequipa": ["Cerro Verde (Freeport)", "Tía María (Southern)", "Caylloma"],
    "Cajamarca": ["Yanacocha (Newmont)", "Conga (suspendido)", "Tantahuatay"],
    "Cusco": ["Antapaccay (Glencore)", "Constancia (Hudbay)"],
    "Junín": ["Toromocho (Chinalco)", "Cobriza", "Yauli"],
    "La Libertad": ["Lagunas Norte (Barrick)", "La Arena", "Comarsa"],
    "Madre de Dios": ["Minería ilegal La Pampa", "Reinfo en proceso"],
    "Moquegua": ["Cuajone (Southern)", "Quellaveco (AngloAmerican)"],
    "Pasco": ["Cerro de Pasco (Volcan)", "Atacocha", "Milpo"],
    "Piura": ["Tambogrande (proyecto)", "Río Blanco"],
    "Puno": ["San Rafael (Minsur)", "Macusani Yellowcake (uranio)"],
    "Tacna": ["Toquepala (Southern)"],
}


# ONGs ambientales y defensores activos en Perú (mapeo OSINT)
ONGS_RELEVANTES = [
    "CooperAcción", "Red Muqui", "Forum Solidaridad",
    "Comisión de Justicia Social", "AIDESEP",
    "Coordinadora Nacional de Comunidades Afectadas por la Minería (CONACAMI)",
    "Grufides", "DAR Perú",
    "Sociedad Peruana de Derecho Ambiental",
]


# =====================================================================
# MOTOR DE ANÁLISIS PRINCIPAL
# =====================================================================

def analizar_riesgo_minera(
    parametros: dict,
    archive=None,
    snapshot_actual: Optional[dict] = None,
) -> dict:
    """Genera el análisis estructurado de riesgo político para sector minero.

    Args:
        parametros: dict con configuración del caso:
            - empresa (str, opcional): nombre de la minera
            - departamentos (list[str]): departamentos de operación
            - alcance (str): "nacional" o "regional"
            - solicitante (str, opcional)
            - periodo_dias (int): ventana de análisis (default 7)
        archive: instancia de ApuriskArchive para datos históricos
        snapshot_actual: dict del último snapshot del pipeline

    Returns:
        dict estructurado con 12 secciones del reporte minero
    """
    empresa = parametros.get("empresa", "Sector minero peruano (genérico)")
    departamentos = parametros.get("departamentos") or list(DEPARTAMENTOS_MINEROS.keys())
    alcance = parametros.get("alcance", "nacional")
    solicitante = parametros.get("solicitante", "Cliente piloto")
    periodo_dias = int(parametros.get("periodo_dias", 7))
    ahora = now_pe()

    # --- Acceso a datos ---
    articulos = []
    alertas = []
    conflictos = []
    crimen_items = []
    if snapshot_actual:
        articulos = snapshot_actual.get("articulos", []) or []
        alertas = snapshot_actual.get("alertas", []) or []
        conflictos = snapshot_actual.get("conflictos", []) or []
        crimen_items = snapshot_actual.get("crimen_items", []) or []

    # --- Filtrar por ventana temporal ---
    desde = ahora - timedelta(days=periodo_dias)
    arts_ventana = _filtrar_por_fecha(articulos, desde)
    alertas_ventana = _filtrar_por_fecha(alertas, desde, campo_fecha="timestamp")
    conf_ventana = _filtrar_por_fecha(conflictos, desde)
    crimen_ventana = _filtrar_por_fecha(crimen_items, desde)

    # --- Filtrar por relevancia minera ---
    arts_mineros = _filtrar_por_relevancia_minera(arts_ventana, departamentos)
    alertas_mineras = _filtrar_alertas_minera(alertas_ventana, departamentos)
    conf_mineros = _filtrar_conflictos_minera(conf_ventana, departamentos)

    # --- Calcular 8 factores P×I propietarios ---
    factores_pxi = _calcular_factores_mineros(arts_mineros + arts_ventana[:50])

    # --- Score global del sector ---
    score_global, nivel = _score_global_minera(factores_pxi, alertas_mineras, conf_mineros)

    # --- Construir las 12 secciones del reporte ---
    return {
        "metadata": {
            "tipo": "riesgo_minera_semanal",
            "generado": ahora.isoformat(timespec="seconds"),
            "periodo": f"{desde.strftime('%d/%m/%Y')} — {ahora.strftime('%d/%m/%Y')}",
            "empresa": empresa,
            "departamentos": departamentos,
            "alcance": alcance,
            "solicitante": solicitante,
            "periodo_dias": periodo_dias,
            "semana_iso": ahora.isocalendar()[1],
            "año": ahora.year,
            "mes": ahora.month,
        },

        # SECCIÓN 1: RESUMEN EJECUTIVO
        "seccion_1_resumen_ejecutivo": {
            "score_global": score_global,
            "nivel": nivel,
            "semaforo": _construir_semaforo(factores_pxi),
            "alerta_principal_semana": _alerta_principal(alertas_mineras, conf_mineros),
            "headline": _generar_headline(score_global, nivel, factores_pxi),
        },

        # SECCIÓN 2: PERFIL DEL CASO MONITOREADO
        "seccion_2_perfil_operacion": {
            "empresa": empresa,
            "departamentos_operacion": departamentos,
            "unidades_mineras_zona": _listar_unidades(departamentos),
            "alcance_geografico": alcance,
            "fuentes_monitoreadas": ["RPP", "La República", "El Comercio", "Caretas",
                                      "Hildebrandt en sus Trece", "Diario Correo regionales",
                                      "Andina", "Infobae", "Reuters", "AP", "DW",
                                      "Twitter/X", "Reddit r/peru"],
        },

        # SECCIÓN 3: PULSO COMUNITARIO
        "seccion_3_pulso_comunitario": _analizar_pulso_comunitario(
            arts_mineros, conf_mineros, departamentos
        ),

        # SECCIÓN 4: BLOQUEOS Y MOVILIZACIONES
        "seccion_4_bloqueos_movilizaciones": _analizar_bloqueos(
            conf_mineros, arts_mineros, departamentos
        ),

        # SECCIÓN 5: RIESGO REGULATORIO
        "seccion_5_riesgo_regulatorio": _analizar_riesgo_regulatorio(
            arts_ventana, snapshot_actual
        ),

        # SECCIÓN 6: POSICIÓN POLÍTICA NACIONAL SOBRE MINERÍA
        "seccion_6_posicion_politica": _analizar_posicion_politica(arts_ventana),

        # SECCIÓN 7: RIESGO SOCIOAMBIENTAL
        "seccion_7_riesgo_socioambiental": _analizar_riesgo_socioambiental(
            arts_mineros, departamentos
        ),

        # SECCIÓN 8: INTELIGENCIA REGIONAL ESPECÍFICA
        "seccion_8_inteligencia_regional": _analizar_inteligencia_regional(
            arts_ventana, departamentos
        ),

        # SECCIÓN 9: STAKEHOLDERS RELEVANTES
        "seccion_9_stakeholders": _mapear_stakeholders(
            arts_ventana, departamentos, snapshot_actual
        ),

        # SECCIÓN 10: SENTIMIENTO MEDIÁTICO
        "seccion_10_sentimiento_mediatico": _analizar_sentimiento(
            arts_mineros, snapshot_actual
        ),

        # SECCIÓN 11: MINERÍA ILEGAL Y CRIMEN ORGANIZADO TRANSNACIONAL
        "seccion_11_mineria_ilegal_crimen": _analizar_mineria_ilegal_crimen(
            arts_ventana, crimen_ventana
        ),

        # SECCIÓN 12: PRESIÓN INTERNACIONAL EEUU
        "seccion_12_presion_eeuu": _analizar_presion_eeuu(arts_ventana),

        # SECCIÓN 13: CAPITAL MARKETS Y SENTIMIENTO INVERSOR
        "seccion_13_capital_markets": _analizar_capital_markets(arts_ventana),

        # SECCIÓN 14: CORRUPCIÓN SECTORIAL
        "seccion_14_corrupcion": _analizar_corrupcion_sectorial(arts_ventana),

        # SECCIÓN 15: ESCENARIOS PROSPECTIVOS (renombrada de 11)
        "seccion_15_escenarios": _generar_escenarios(
            factores_pxi, alertas_mineras, conf_mineros
        ),

        # SECCIÓN 16: RECOMENDACIONES OPERATIVAS (renombrada de 12)
        "seccion_16_recomendaciones": _generar_recomendaciones(
            factores_pxi, nivel, alertas_mineras
        ),

        # Aliases hacia atrás para compatibilidad con el PDF actual
        "seccion_11_escenarios": _generar_escenarios(
            factores_pxi, alertas_mineras, conf_mineros
        ),
        "seccion_12_recomendaciones": _generar_recomendaciones(
            factores_pxi, nivel, alertas_mineras
        ),

        # Factores P×I propietarios (datos crudos)
        "factores_pxi": factores_pxi,

        # Conteos para validación
        "stats": {
            "articulos_periodo": len(arts_ventana),
            "articulos_relevantes_mineria": len(arts_mineros),
            "alertas_mineras": len(alertas_mineras),
            "conflictos_relevantes": len(conf_mineros),
            "crimen_items": len(crimen_ventana),
        },
    }


# =====================================================================
# FUNCIONES DE FILTRADO
# =====================================================================

def _filtrar_por_fecha(items, desde, campo_fecha="published"):
    out = []
    for it in items:
        if isinstance(it, dict):
            fecha = it.get(campo_fecha) or it.get("timestamp") or it.get("published")
        else:
            fecha = getattr(it, "published", "")
        if not fecha:
            continue
        try:
            dt = parse_to_pe(fecha)
            if dt and dt >= desde:
                out.append(it)
        except Exception:
            continue
    return out


def _filtrar_por_relevancia_minera(articulos, departamentos):
    """Filtra artículos relevantes para sector minero según keywords y geografía.

    Aplica filtro defensivo de contenido deportivo/espectáculos primero.
    """
    try:
        from ..utils.content_filter import es_contenido_irrelevante
    except ImportError:
        from apurisk.utils.content_filter import es_contenido_irrelevante

    KEYWORDS_MINERAS = [
        "minería", "mineria", "minera", "minero", "minerí­a", "extracción minera",
        "concesión minera", "explotación minera", "exploración minera",
        "regalías mineras", "canon minero", "fiscalización ambiental",
        "minam", "minem", "oefa", "ana ", "geocatmin",
        "comunidad campesina", "comunidades indígenas", "consulta previa",
        "convenio marco", "fondo social", "responsabilidad social minera",
    ]
    # Agregar nombres de unidades mineras conocidas
    UNIDADES = []
    for dep, lista in DEPARTAMENTOS_MINEROS.items():
        UNIDADES.extend([u.split("(")[0].strip().lower() for u in lista])

    out = []
    for a in articulos:
        # DEFENSA: rechazar deportes/espectáculos antes de matching minero
        if es_contenido_irrelevante(a):
            continue
        texto = _texto(a).lower()
        # Match por keyword minera
        if any(kw in texto for kw in KEYWORDS_MINERAS):
            out.append(a)
            continue
        # Match por unidad minera
        if any(u in texto for u in UNIDADES if u):
            out.append(a)
            continue
        # Match por departamento + palabra contexto
        for dep in departamentos:
            if dep.lower() in texto and any(k in texto for k in ["mina", "minera", "conflicto", "comunidad"]):
                out.append(a)
                break
    return out


def _filtrar_alertas_minera(alertas, departamentos):
    """Alertas relacionadas con sector minero."""
    out = []
    for a in alertas:
        if not isinstance(a, dict):
            continue
        texto = (a.get("titulo", "") + " " + a.get("resumen", "")).lower()
        region = a.get("region", "")
        if any(d.lower() in texto or d == region for d in departamentos):
            out.append(a)
        elif any(k in texto for k in ["miner", "comunidad", "consulta previa", "regalías"]):
            out.append(a)
    return out


def _filtrar_conflictos_minera(conflictos, departamentos):
    """Conflictos relevantes para sector minero."""
    out = []
    for c in conflictos:
        if isinstance(c, dict):
            tipo = c.get("tipo", "")
            region = c.get("region", "")
            texto = (c.get("titulo", "") + " " + c.get("descripcion", "")).lower()
        else:
            raw = c.raw or {}
            tipo = raw.get("tipo", "")
            region = raw.get("region", "") or c.region or ""
            texto = (c.title + " " + (c.summary or "")).lower()
        if tipo == "socioambiental":
            out.append(c)
            continue
        if any(d in region for d in departamentos):
            out.append(c)
            continue
        if any(k in texto for k in ["miner", "comunidad", "corredor", "las bambas"]):
            out.append(c)
    return out


def _texto(art) -> str:
    """Extrae texto de un artículo (dict o Article)."""
    if isinstance(art, dict):
        return f"{art.get('title','')} {art.get('summary','')}"
    return f"{getattr(art, 'title', '')} {getattr(art, 'summary', '')}"


# =====================================================================
# CÁLCULO DE FACTORES P×I MINEROS
# =====================================================================

def _calcular_factores_mineros(articulos) -> list[dict]:
    """Calcula los 8 factores propietarios de riesgo minero con scoring P×I."""
    factores_resultado = []
    todo_texto = " ".join(_texto(a).lower() for a in articulos)

    for factor_id, config in FACTORES_MINEROS.items():
        # Contar matches de keywords fuertes y medias
        n_alta = sum(1 for kw in config["keywords_alta"] if kw in todo_texto)
        n_media = sum(1 for kw in config["keywords_media"] if kw in todo_texto)

        # Probabilidad: 0-100 basado en frecuencia normalizada
        prob_raw = (n_alta * 3 + n_media * 1) * 8
        probabilidad = min(95, max(15, prob_raw))

        # Impacto: pesos sectoriales propietarios (calibrados por experiencia)
        impactos_base = {
            "licencia_social_comunitaria": 85,
            "bloqueo_corredor": 92,
            "riesgo_regulatorio_sectorial": 78,
            "riesgo_tributario": 75,
            "riesgo_socioambiental": 88,
            "riesgo_seguridad_operativa": 80,
            "riesgo_imagen_mediatica": 65,
            "riesgo_electoral_cambio_politica": 70,
            # Nuevos factores avanzados
            "mineria_ilegal_artesanal": 82,        # alto - distorsiona mercado y reputación país
            "crimen_organizado_transnacional": 90, # crítico - amenaza directa de violencia
            "presion_internacional_eeuu": 86,      # crítico - sanciones afectan financiamiento
            "corrupcion_sectorial": 79,             # alto - afecta legitimidad y due diligence
            "riesgo_capital_mercado": 84,           # alto - afecta CAPEX y exploración
        }
        impacto = impactos_base.get(factor_id, 70)

        # Score combinado P×I (normalizado 0-100)
        score = round((probabilidad * 0.5 + impacto * 0.5), 1)
        nivel = "CRÍTICO" if score >= 75 else "ALTO" if score >= 55 else "MEDIO" if score >= 35 else "BAJO"

        factores_resultado.append({
            "id": factor_id,
            "nombre": config["nombre"],
            "descripcion": config["descripcion"],
            "probabilidad": probabilidad,
            "impacto": impacto,
            "score": score,
            "nivel": nivel,
            "matches_alta": n_alta,
            "matches_media": n_media,
        })

    # Ordenar por score descendente
    factores_resultado.sort(key=lambda f: -f["score"])
    return factores_resultado


def _score_global_minera(factores, alertas_mineras, conf_mineros):
    """Score global ponderado del sector minero (0-100)."""
    if not factores:
        return 50, "MEDIO"
    # Promedio top 4 factores + bonificación por alertas/conflictos
    top4 = factores[:4]
    avg_top4 = sum(f["score"] for f in top4) / len(top4)
    bonus_alertas = min(15, len([a for a in alertas_mineras if a.get("nivel") == "CRÍTICA"]) * 2)
    bonus_conflictos = min(10, len(conf_mineros) * 0.8)
    score = min(100, avg_top4 + bonus_alertas + bonus_conflictos)
    if score >= 75:
        nivel = "CRÍTICO"
    elif score >= 55:
        nivel = "ALTO"
    elif score >= 35:
        nivel = "MEDIO"
    else:
        nivel = "BAJO"
    return round(score, 1), nivel


def _construir_semaforo(factores) -> dict:
    """Semáforo por dimensión: verde/amarillo/rojo."""
    semaforo = {}
    for f in factores:
        nivel = f["nivel"]
        color = "🟢 verde" if nivel == "BAJO" else "🟡 amarillo" if nivel == "MEDIO" else "🟠 naranja" if nivel == "ALTO" else "🔴 rojo"
        semaforo[f["id"]] = {
            "nombre": f["nombre"],
            "score": f["score"],
            "nivel": nivel,
            "color": color,
        }
    return semaforo


# =====================================================================
# ANÁLISIS DE SECCIONES
# =====================================================================

def _alerta_principal(alertas, conflictos):
    """La alerta de mayor criticidad de la semana."""
    if alertas:
        criticas = [a for a in alertas if a.get("nivel") == "CRÍTICA"]
        if criticas:
            return {
                "titulo": criticas[0].get("titulo", "Alerta crítica"),
                "resumen": criticas[0].get("resumen", "")[:300],
                "region": criticas[0].get("region", ""),
                "fuente": criticas[0].get("fuente", ""),
                "url": criticas[0].get("url", ""),
            }
        return {
            "titulo": alertas[0].get("titulo", "Alerta"),
            "resumen": alertas[0].get("resumen", "")[:300],
            "region": alertas[0].get("region", ""),
            "fuente": alertas[0].get("fuente", ""),
            "url": alertas[0].get("url", ""),
        }
    if conflictos:
        c = conflictos[0]
        if isinstance(c, dict):
            return {
                "titulo": c.get("titulo", "Conflicto activo"),
                "resumen": c.get("descripcion", "")[:300],
                "region": c.get("region", ""),
                "fuente": "Conflictos sociales",
                "url": c.get("url", ""),
            }
        return {
            "titulo": c.title,
            "resumen": (c.summary or "")[:300],
            "region": c.region or "",
            "fuente": c.source_name,
            "url": c.url,
        }
    return {
        "titulo": "Sin alertas críticas en la semana",
        "resumen": "El sector minero presenta actividad de monitoreo en niveles estables.",
        "region": "Nacional",
        "fuente": "APURISK",
        "url": "",
    }


def _generar_headline(score, nivel, factores):
    """Frase resumen de portada."""
    factor_top = factores[0] if factores else None
    if not factor_top:
        return f"Sector minero peruano con score {score} (nivel {nivel})."
    return (f"Sector minero peruano con score global {score}/100 ({nivel}). "
            f"Factor de mayor criticidad esta semana: {factor_top['nombre']} "
            f"(score {factor_top['score']}).")


def _listar_unidades(departamentos):
    """Unidades mineras activas en los departamentos seleccionados."""
    unidades = []
    for dep in departamentos:
        for u in DEPARTAMENTOS_MINEROS.get(dep, []):
            unidades.append({"departamento": dep, "unidad": u})
    return unidades


def _analizar_pulso_comunitario(arts_mineros, conf_mineros, departamentos):
    """Sección 3: comunidades, dirigentes, demandas activas."""
    comunidades_mencionadas = []
    demandas = []
    dirigentes = []

    KEYWORDS_COMUNIDAD = ["comunidad campesina", "comunidad indígena",
                          "ronderos", "frente de defensa",
                          "asamblea comunal", "dirigentes comunales"]
    KEYWORDS_DEMANDA = ["exigen", "demandan", "reclaman", "rechazan",
                        "se oponen", "denuncian"]

    for a in arts_mineros[:30]:
        texto = _texto(a).lower()
        for kw in KEYWORDS_COMUNIDAD:
            if kw in texto:
                comunidades_mencionadas.append({
                    "match": kw,
                    "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                    "titulo": getattr(a, "title", None) or a.get("title", ""),
                    "url": getattr(a, "url", None) or a.get("url", ""),
                })
                break
        for kw in KEYWORDS_DEMANDA:
            if kw in texto:
                demandas.append({
                    "verbo": kw,
                    "titulo": getattr(a, "title", None) or a.get("title", ""),
                    "url": getattr(a, "url", None) or a.get("url", ""),
                })
                break

    return {
        "n_comunidades_activas": len(comunidades_mencionadas),
        "comunidades": comunidades_mencionadas[:10],
        "n_demandas_detectadas": len(demandas),
        "demandas_top": demandas[:10],
        "conflictos_comunales": [
            {
                "titulo": (c.get("titulo", "") if isinstance(c, dict) else c.title),
                "region": (c.get("region", "") if isinstance(c, dict) else (c.region or "")),
                "tipo": (c.get("tipo", "") if isinstance(c, dict) else (c.raw or {}).get("tipo", "")),
                "url": (c.get("url", "") if isinstance(c, dict) else c.url),
            } for c in conf_mineros[:8]
        ],
        "diagnostico": _diagnostico_comunitario(len(comunidades_mencionadas), len(demandas)),
    }


def _diagnostico_comunitario(n_comunidades, n_demandas):
    if n_demandas >= 8:
        return "Alto nivel de conflictividad comunitaria en la semana. Sugerir intensificar mesas de diálogo y monitoreo de dirigentes."
    elif n_demandas >= 4:
        return "Actividad moderada de demandas comunales. Mantener canales abiertos con frentes de defensa."
    elif n_comunidades >= 3:
        return "Comunidades en monitoreo pero sin demandas formales detectadas. Período de relativa estabilidad."
    return "Sin actividad relevante de demandas comunitarias detectada en la semana."


def _analizar_bloqueos(conf_mineros, arts_mineros, departamentos):
    """Sección 4: bloqueos del corredor minero y movilizaciones."""
    bloqueos = []
    movilizaciones = []
    KEYWORDS_BLOQUEO = ["bloqueo de carretera", "toma de carretera",
                        "panamericana bloqueada", "corredor minero bloqueado",
                        "vehículos varados", "convoy detenido"]
    KEYWORDS_MOVILIZACION = ["marcha", "movilización", "paro", "huelga",
                             "manifestación", "plantón"]

    for a in arts_mineros[:50]:
        texto = _texto(a).lower()
        if any(kw in texto for kw in KEYWORDS_BLOQUEO):
            bloqueos.append({
                "titulo": getattr(a, "title", None) or a.get("title", ""),
                "url": getattr(a, "url", None) or a.get("url", ""),
                "fecha": getattr(a, "published", None) or a.get("published", ""),
                "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
            })
        elif any(kw in texto for kw in KEYWORDS_MOVILIZACION):
            movilizaciones.append({
                "titulo": getattr(a, "title", None) or a.get("title", ""),
                "url": getattr(a, "url", None) or a.get("url", ""),
            })

    return {
        "bloqueos_semana": len(bloqueos),
        "movilizaciones_semana": len(movilizaciones),
        "bloqueos_detallados": bloqueos[:10],
        "movilizaciones_detalladas": movilizaciones[:10],
        "tendencia": _tendencia_bloqueos(len(bloqueos)),
    }


def _tendencia_bloqueos(n):
    if n >= 5:
        return "ALTA: múltiples bloqueos esta semana. Riesgo logístico significativo."
    elif n >= 2:
        return "MEDIA: bloqueos detectados en la semana, monitorear evolución."
    elif n >= 1:
        return "BAJA-MEDIA: un bloqueo reportado, evento aislado."
    return "ESTABLE: sin bloqueos detectados en la semana."


def _analizar_riesgo_regulatorio(arts, snapshot):
    """Sección 5: proyectos de ley y normas que afectan minería."""
    KEYWORDS_REG = ["proyecto de ley", "reforma minera", "regalías mineras",
                    "consulta previa", "concesión minera", "fiscalización minera",
                    "moratoria minera"]
    items = []
    for a in arts[:200]:
        texto = _texto(a).lower()
        if any(kw in texto for kw in KEYWORDS_REG):
            items.append({
                "titulo": getattr(a, "title", None) or a.get("title", ""),
                "url": getattr(a, "url", None) or a.get("url", ""),
                "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                "fecha": getattr(a, "published", None) or a.get("published", ""),
            })

    # También extraer proyectos de ley del snapshot si están disponibles
    proyectos_relevantes = []
    if snapshot:
        proyectos = snapshot.get("proyectos", []) or []
        for p in proyectos[:50]:
            texto = ((p.get("title", "") if isinstance(p, dict) else p.title) + " " +
                     (p.get("summary", "") if isinstance(p, dict) else (p.summary or ""))).lower()
            if any(kw in texto for kw in ["miner", "regalías", "canon", "concesión", "consulta previa"]):
                proyectos_relevantes.append(p if isinstance(p, dict) else p.to_dict())

    return {
        "noticias_regulatorias": items[:15],
        "proyectos_ley_relevantes": proyectos_relevantes[:10],
        "diagnostico": "Mantener seguimiento estrecho a Comisión de Economía y a la Comisión de Pueblos Andinos del Congreso." if proyectos_relevantes else "Sin actividad legislativa específica detectada esta semana sobre el sector minero.",
    }


def _analizar_posicion_politica(arts):
    """Sección 6: posición de figuras políticas sobre minería."""
    ACTORES_CLAVE = ["presidente", "primer ministro", "ministro de energía",
                     "ministro de ambiente", "ministro de cultura",
                     "ministro de economía", "minem", "minam"]
    declaraciones = []
    for a in arts[:100]:
        texto = _texto(a).lower()
        if any(actor in texto for actor in ACTORES_CLAVE) and any(k in texto for k in ["miner", "regalí", "concesi"]):
            declaraciones.append({
                "titulo": getattr(a, "title", None) or a.get("title", ""),
                "url": getattr(a, "url", None) or a.get("url", ""),
                "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
            })
    return {
        "declaraciones_oficiales": declaraciones[:10],
        "diagnostico": _diagnostico_posicion_politica(declaraciones),
    }


def _diagnostico_posicion_politica(declaraciones):
    if len(declaraciones) >= 5:
        return "Alta actividad política sobre el sector. Cumple agenda mediática del ejecutivo."
    elif len(declaraciones) >= 2:
        return "Actividad moderada del Ejecutivo sobre minería."
    return "Posición gubernamental discreta esta semana sobre el sector."


def _analizar_riesgo_socioambiental(arts_mineros, departamentos):
    """Sección 7: contaminación, pasivos, denuncias ambientales."""
    KEYWORDS_AMBIENTAL = ["contaminación", "derrame", "pasivo ambiental",
                          "denuncia ambiental", "agua contaminada",
                          "mercurio", "cianuro", "relave", "oefa",
                          "remediación ambiental"]
    items = []
    for a in arts_mineros[:80]:
        texto = _texto(a).lower()
        kws_found = [kw for kw in KEYWORDS_AMBIENTAL if kw in texto]
        if kws_found:
            items.append({
                "titulo": getattr(a, "title", None) or a.get("title", ""),
                "url": getattr(a, "url", None) or a.get("url", ""),
                "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                "indicadores": kws_found,
            })
    return {
        "incidentes_ambientales": items[:15],
        "n_incidentes": len(items),
        "diagnostico": ("Múltiples incidentes ambientales reportados. Riesgo reputacional elevado."
                        if len(items) >= 5 else
                        "Actividad ambiental rutinaria sin denuncias críticas significativas.")
    }


def _analizar_inteligencia_regional(arts, departamentos):
    """Sección 8: cobertura de medios regionales sobre la operación."""
    FUENTES_REGIONALES = ["correo", "los andes", "la republica", "andina"]
    items = []
    for a in arts[:200]:
        fuente = (getattr(a, "source_name", None) or a.get("source_name", "")).lower()
        texto = _texto(a).lower()
        if any(f in fuente for f in FUENTES_REGIONALES):
            for dep in departamentos:
                if dep.lower() in texto or dep.lower() in fuente:
                    items.append({
                        "departamento_match": dep,
                        "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                        "titulo": getattr(a, "title", None) or a.get("title", ""),
                        "url": getattr(a, "url", None) or a.get("url", ""),
                    })
                    break
    return {
        "cobertura_regional": items[:15],
        "n_items_regionales": len(items),
        "diagnostico": "Cobertura regional activa en las zonas monitoreadas." if items else "Baja densidad de cobertura regional esta semana."
    }


def _mapear_stakeholders(arts, departamentos, snapshot):
    """Sección 9: actores políticos, comunales, gremiales relevantes."""
    entidades = snapshot.get("entidades", {}) if snapshot else {}
    return {
        "instituciones_top": (entidades.get("instituciones") or [])[:8],
        "partidos_top": (entidades.get("partidos") or [])[:5],
        "regiones_top": (entidades.get("regiones") or [])[:8],
        "ongs_activas": ONGS_RELEVANTES,
        "diagnostico": "Stakeholders extraídos del archivo OSINT de la semana. Cruzar con relacionamiento institucional propio.",
    }


def _analizar_sentimiento(arts_mineros, snapshot):
    """Sección 10: análisis de tono mediático."""
    NEGATIVO = ["denuncia", "rechazo", "escándalo", "contamina", "crisis",
                "conflicto", "bloqueo", "atentado", "víctimas", "criminal",
                "ilegal", "muerto"]
    POSITIVO = ["acuerdo", "diálogo", "convenio", "inversión", "empleo",
                "desarrollo", "responsabilidad social", "consulta"]
    pos = neg = neu = 0
    for a in arts_mineros:
        texto = _texto(a).lower()
        n = sum(1 for kw in NEGATIVO if kw in texto)
        p = sum(1 for kw in POSITIVO if kw in texto)
        if n > p:
            neg += 1
        elif p > n:
            pos += 1
        else:
            neu += 1
    total = max(1, pos + neg + neu)
    return {
        "positivo": pos,
        "negativo": neg,
        "neutral": neu,
        "ratio_neg_pos": round(neg / max(1, pos), 2),
        "polaridad_neta": round((pos - neg) / total, 2),
        "diagnostico": _diagnostico_sentimiento(pos, neg, neu),
    }


def _diagnostico_sentimiento(pos, neg, neu):
    if neg > pos * 2:
        return "Cobertura predominantemente negativa. Riesgo reputacional sectorial."
    elif neg > pos:
        return "Tono mediático mixto con sesgo crítico."
    elif pos > neg * 1.5:
        return "Cobertura mayoritariamente positiva o neutral."
    return "Balance equilibrado entre cobertura crítica y favorable."


def _generar_escenarios(factores, alertas, conflictos):
    """Sección 11: 3 escenarios prospectivos a 30/90 días."""
    score_top3 = sum(f["score"] for f in factores[:3]) / max(1, len(factores[:3]))
    return {
        "escenario_base": {
            "titulo": "Continuidad operativa con tensión normal",
            "probabilidad": 55,
            "descripcion": (f"El sector mantiene operaciones con nivel de riesgo {factores[0]['nivel'] if factores else 'MEDIO'}. "
                            f"Persisten tensiones puntuales con comunidades pero sin escalamiento. "
                            f"Indicadores principales: {factores[0]['nombre'] if factores else 'N/A'} "
                            f"(score {factores[0]['score'] if factores else 'N/A'})."),
            "disparadores_observables": ["Mantenimiento del diálogo en mesas activas",
                                          "Sin nuevos bloqueos de corredor",
                                          "Cobertura mediática estable"],
        },
        "escenario_deterioro": {
            "titulo": "Escalamiento gradual de conflictos comunales",
            "probabilidad": 30,
            "descripcion": ("Aumento de movilizaciones, ingreso de ONGs ambientales con mayor activismo, "
                            "tensión política sobre regalías. Posibilidad de paros sectoriales focalizados."),
            "disparadores_observables": ["2+ bloqueos en un mes",
                                          "Declaraciones presidenciales contra minería",
                                          "Proyecto de ley sobre regalías avanza en Congreso",
                                          "Denuncia OEFA pública"],
        },
        "escenario_crisis": {
            "titulo": "Crisis sectorial con paralización temporal",
            "probabilidad": 15,
            "descripcion": ("Bloqueo prolongado del corredor minero, paro indefinido de comunidades, "
                            "intervención del Ejecutivo con medidas legales o fiscales. "
                            "Impacto material en producción y cotización bursátil."),
            "disparadores_observables": ["Paro indefinido decretado",
                                          "Intervención policial con víctimas",
                                          "Decreto de urgencia tributario",
                                          "Inversores extranjeros se retiran"],
        },
        "indicador_clave_a_monitorear": (factores[0]["nombre"] if factores else "Conflictos socioambientales"),
    }


def _generar_recomendaciones(factores, nivel_global, alertas):
    """Sección 12: recomendaciones operativas accionables."""
    base = []

    if nivel_global == "CRÍTICO":
        base.append("ACTIVAR plan de contingencia operativa. Reunión semanal del Comité de Gestión de Crisis.")
        base.append("Convocar mesas de diálogo con comunidades en zona de mayor tensión en las próximas 72h.")
        base.append("Coordinar con MININTER y MINEM presencia preventiva en corredor logístico.")
    elif nivel_global == "ALTO":
        base.append("Reforzar canales de relacionamiento institucional con dirigentes comunales.")
        base.append("Revisar cumplimiento de convenios marco con comunidades en zona de operación.")
        base.append("Plan de comunicación reactiva: vocero designado, mensajes clave, respuestas a denuncias.")
    elif nivel_global == "MEDIO":
        base.append("Mantener cadencia normal de relacionamiento social.")
        base.append("Monitorear evolución semanal de los 3 factores top.")
        base.append("Reuniones quincenales con la Defensoría del Pueblo y gobierno regional.")
    else:
        base.append("Mantener prácticas de cumplimiento social y ambiental como rutina preventiva.")
        base.append("Aprovechar período de estabilidad para fortalecer fondos sociales.")

    # Recomendaciones específicas por factor crítico
    top_factor = factores[0] if factores else None
    if top_factor and top_factor["nivel"] in ("ALTO", "CRÍTICO"):
        recom_factor = {
            "licencia_social_comunitaria": "Activar mesa de diálogo de alto nivel con dirigentes comunales identificados.",
            "bloqueo_corredor": "Plan de contingencia logística: rutas alternas, inventario, coordinación PNP.",
            "riesgo_regulatorio_sectorial": "Lobbying institucional con Comisión de Economía y bancadas afines en Congreso.",
            "riesgo_tributario": "Análisis de impacto fiscal de iniciativas en curso. Coordinación con SNMPE.",
            "riesgo_socioambiental": "Auditoría ambiental preventiva. Refuerzo de monitoreo participativo con comunidades.",
            "riesgo_seguridad_operativa": "Coordinación con FFAA/PNP. Revisión de protocolos de seguridad en operación.",
            "riesgo_imagen_mediatica": "Campaña reputacional. Activación de voceros corporativos.",
            "riesgo_electoral_cambio_politica": "Monitoreo de candidatos y posicionamientos sectoriales. Plan de relacionamiento post-elecciones.",
        }
        base.append(f"PRIORITARIO ({top_factor['nombre']}): {recom_factor.get(top_factor['id'], 'Atención especial.')}")

    return {
        "recomendaciones": base,
        "horizonte_recomendado_revision": "7 días",
        "comite_responsable_sugerido": "Comité de Gestión de Crisis + Asuntos Corporativos",
    }


# =====================================================================
# SECCIONES AVANZADAS (mayo 2026)
# =====================================================================

def _analizar_mineria_ilegal_crimen(arts, crimen_items):
    """Sección 11: minería ilegal, artesanal informal, crimen organizado transnacional."""
    KW_MINERIA_ILEGAL = [
        "minería ilegal", "mineria ilegal", "minero ilegal", "mineros ilegales",
        "dragado ilegal", "extracción ilegal de oro",
        "la pampa", "reinfo", "operativo mercurio",
        "balsa ilegal", "deforestación minería", "interdicción minera",
        "asesinato defensor ambiental", "minería informal",
    ]
    KW_NARCO_MINERIA = [
        "narco-minería", "narcominería", "lavado oro", "lavado de activos minería",
        "ruta del oro", "cártel oro", "tren de aragua oro",
        "frontera bolivia oro", "frontera colombia oro",
        "minería ilegal supera narcotráfico",
        "carteles colombianos oro", "comando vermelho", "pcc oro",
        "sicariato minería", "extorsión minera",
    ]
    items_ilegal = []
    items_narco = []
    for a in arts[:500]:
        texto = _texto(a).lower()
        for kw in KW_MINERIA_ILEGAL:
            if kw in texto:
                items_ilegal.append({
                    "titulo": getattr(a, "title", None) or a.get("title", ""),
                    "url": getattr(a, "url", None) or a.get("url", ""),
                    "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                    "match": kw,
                })
                break
        for kw in KW_NARCO_MINERIA:
            if kw in texto:
                items_narco.append({
                    "titulo": getattr(a, "title", None) or a.get("title", ""),
                    "url": getattr(a, "url", None) or a.get("url", ""),
                    "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                    "match": kw,
                })
                break

    # Items del clasificador crimen_organizado relevantes
    crimen_relevante = []
    for c in crimen_items[:80]:
        raw = c.raw if not isinstance(c, dict) else c
        tipologia = (raw.get("tipologia") if isinstance(raw, dict) else "") or ""
        if tipologia in ("mineria_ilegal", "narcotrafico", "extorsion_sicariato"):
            crimen_relevante.append({
                "tipologia": tipologia,
                "titulo": c.title if not isinstance(c, dict) else c.get("title", ""),
                "url": c.url if not isinstance(c, dict) else c.get("url", ""),
                "region": (c.region if not isinstance(c, dict) else c.get("region", "")) or "—",
            })

    diagnostico = _diagnostico_ilegal_crimen(len(items_ilegal), len(items_narco))
    return {
        "items_mineria_ilegal": items_ilegal[:15],
        "items_narco_mineria": items_narco[:15],
        "crimen_organizado_clasificado": crimen_relevante[:20],
        "n_items_ilegal": len(items_ilegal),
        "n_items_narco_mineria": len(items_narco),
        "diagnostico": diagnostico,
        "departamentos_criticos": ["Madre de Dios (La Pampa)",
                                     "Puno (frontera Bolivia)",
                                     "Loreto (Amazonía)",
                                     "Ucayali", "Amazonas",
                                     "Apurímac (corredor minero)"],
        "actores_relevantes": [
            "FF.AA. (Comando Especial Madre de Dios)",
            "PNP DIROES / DIRANDRO",
            "SUNAT Aduanas",
            "Fiscalía Especializada Crimen Organizado (FECOR)",
            "OEFA / SERFOR",
            "Embajada EEUU (cooperación bilateral)",
        ],
    }


def _diagnostico_ilegal_crimen(n_ilegal, n_narco):
    total = n_ilegal + n_narco
    if total >= 10:
        return ("ALTA EXPOSICIÓN: actividad intensa de minería ilegal y crimen organizado "
                "reportada en la semana. El sector minero formal enfrenta riesgo reputacional "
                "indirecto (todo lo aurífero peruano queda bajo sospecha internacional) y "
                "riesgo competitivo directo (oro ilegal compite con formal en costos).")
    if total >= 5:
        return ("MEDIA-ALTA: actividad relevante de minería ilegal en territorio nacional. "
                "Monitorear de cerca cualquier vinculación de la operación formal con actores "
                "informales (proveedores, contratistas, transportistas).")
    if total >= 1:
        return ("MEDIA-BAJA: actividad esporádica reportada. Mantener due diligence reforzada "
                "en cadena de suministro y compliance KYC.")
    return ("BAJA: sin actividad relevante detectada en la semana. Mantener monitoreo "
            "rutinario, especialmente en departamentos de alta exposición (Madre de Dios, Puno).")


def _analizar_presion_eeuu(arts):
    """Sección 12: presión internacional EEUU, OFAC, FinCEN, Magnitsky, DEA."""
    KW_EEUU = [
        "ofac", "treasury sanctions", "magnitsky", "fincen",
        "lista clinton", "lista negra eeuu", "lista negra estados unidos",
        "dea operativo", "southcom", "us state department",
        "department of treasury", "embajada estados unidos sanciones",
        "designación criminal eeuu", "us sanctions peru",
        "drug certification peru", "trafficking in persons report",
        "rule of law peru", "tier 3", "tier 2 watch list",
    ]
    items = []
    for a in arts[:500]:
        texto = _texto(a).lower()
        for kw in KW_EEUU:
            if kw in texto:
                items.append({
                    "titulo": getattr(a, "title", None) or a.get("title", ""),
                    "url": getattr(a, "url", None) or a.get("url", ""),
                    "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                    "match": kw,
                })
                break

    return {
        "items_presion_eeuu": items[:15],
        "n_items": len(items),
        "diagnostico": _diagnostico_eeuu(len(items)),
        "mecanismos_clave": [
            "OFAC SDN List (Specially Designated Nationals) — bloqueo activos en USD",
            "Magnitsky Act — sanciones individuales por corrupción/DDHH",
            "FinCEN — alertas a sistema financiero global",
            "Drug Certification anual (Casa Blanca) — afecta ayuda bilateral",
            "TIP Report (Trafficking in Persons) — afecta cooperación",
            "INSCR / Money Laundering Report — observa rutas de oro ilegal",
            "DEA — cooperación operativa en VRAEM y fronteras",
        ],
        "impacto_potencial_minero": [
            "Restricción de financiamiento USD para mineras peruanas en bancos correspondientes",
            "Mayor due diligence de compradores internacionales de oro/cobre",
            "Designación a operadores informales reduce reputación país",
            "Acuerdos comerciales (Tratado de Libre Comercio) bajo revisión",
        ],
    }


def _diagnostico_eeuu(n):
    if n >= 5:
        return ("ALTA: actividad significativa de organismos de EEUU sobre Perú esta semana. "
                "Monitorear designaciones potenciales y revisar exposición de la operación "
                "a entidades en listas restrictivas.")
    if n >= 2:
        return ("MEDIA: cobertura de presión bilateral relevante. Mantener línea con asesores "
                "de compliance y verificar contrapartes en SDN/Magnitsky List.")
    if n >= 1:
        return ("BAJA-MEDIA: actividad esporádica. Vigilancia rutinaria.")
    return ("ESTABLE: sin alertas significativas de presión EEUU sobre Perú en la semana.")


def _analizar_capital_markets(arts):
    """Sección 13: sentimiento inversor, riesgo país, capital allocation, calificadoras."""
    KW_MERCADO = [
        "inversores nerviosos", "mining investors jittery",
        "retiro inversión", "ralentización inversión",
        "cae inversión minera",
        "downgrade peru", "fitch peru", "s&p peru", "moody's peru",
        "rebaja calificación", "riesgo país peru sube",
        "fuga de capitales", "capital flight",
        "mining capital allocation", "capex minero",
        "embi peru", "cds peru", "spread soberano",
        "expectativas empresariales", "ipe peru",
        "bcrp encuesta", "indicador económico", "tipo de cambio peru",
        "cae bolsa lima", "bvl cae", "acciones minera caen",
        "buenaventura cae", "southern copper", "freeport",
    ]
    items = []
    sentimiento_neg = 0
    sentimiento_pos = 0
    for a in arts[:500]:
        texto = _texto(a).lower()
        for kw in KW_MERCADO:
            if kw in texto:
                items.append({
                    "titulo": getattr(a, "title", None) or a.get("title", ""),
                    "url": getattr(a, "url", None) or a.get("url", ""),
                    "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                    "match": kw,
                })
                # Heurística sentimiento
                if any(w in texto for w in ["cae", "rebaja", "downgrade", "fuga",
                                              "nerviosos", "retiro", "pierde"]):
                    sentimiento_neg += 1
                elif any(w in texto for w in ["sube", "recupera", "alza", "confianza",
                                                "reactiva", "inversión nueva"]):
                    sentimiento_pos += 1
                break

    if sentimiento_neg > sentimiento_pos * 1.5:
        sentimiento_label = "BAJISTA"
    elif sentimiento_pos > sentimiento_neg * 1.5:
        sentimiento_label = "ALCISTA"
    else:
        sentimiento_label = "NEUTRAL"

    return {
        "items_capital_mercado": items[:15],
        "n_items": len(items),
        "sentimiento_label": sentimiento_label,
        "sentimiento_neg": sentimiento_neg,
        "sentimiento_pos": sentimiento_pos,
        "diagnostico": _diagnostico_capital(len(items), sentimiento_label),
        "indicadores_a_monitorear": [
            "EMBI+ Perú (spread vs Tesoro EEUU)",
            "Calificación soberana (Fitch / S&P / Moody's)",
            "Tipo de cambio sol/dólar",
            "Reservas internacionales BCRP",
            "Índice General BVL (S&P/BVL Peru General)",
            "Acciones mineras: BVN, SCCO, FCX, NEM en NYSE",
            "Curva de proyectos en stand-by (MINEM cartera)",
        ],
    }


def _diagnostico_capital(n, sentimiento):
    base = f"Sentimiento de mercado: {sentimiento}."
    if n >= 8:
        return (f"{base} Cobertura intensa sobre riesgo país y capital allocation. "
                "Si el sentimiento es BAJISTA, considerar comunicación reactiva con "
                "stakeholders financieros internacionales.")
    if n >= 3:
        return f"{base} Actividad moderada del mercado sobre el sector minero peruano."
    return f"{base} Cobertura limitada de capital markets esta semana."


def _analizar_corrupcion_sectorial(arts):
    """Sección 14: corrupción sectorial y captura institucional minera."""
    KW_CORRUPCION = [
        "soborno minería", "coima minería", "captura institucional minería",
        "exministro investigado", "conflicto de interés ministro",
        "puerta giratoria", "transparency peru mining",
        "transparency international perú minería",
        "lava jato minería", "odebrecht minería",
        "operación car wash", "operation car wash",
        "denuncia constitucional ministro",
        "fiscalía investiga minera",
        "lobby ilegal minería",
        "corrupción minera", "integridad pública minería",
    ]
    items = []
    for a in arts[:500]:
        texto = _texto(a).lower()
        for kw in KW_CORRUPCION:
            if kw in texto:
                items.append({
                    "titulo": getattr(a, "title", None) or a.get("title", ""),
                    "url": getattr(a, "url", None) or a.get("url", ""),
                    "fuente": getattr(a, "source_name", None) or a.get("source_name", ""),
                    "match": kw,
                })
                break

    return {
        "items_corrupcion": items[:15],
        "n_items": len(items),
        "diagnostico": _diagnostico_corrupcion(len(items)),
        "indices_referencia": [
            "CPI Transparency International (Perú típicamente 35-40/100)",
            "Resource Governance Index NRGI",
            "GAN Integrity Country Profile Perú",
            "World Bank Doing Business / Governance Indicators",
            "Banco Mundial Peru Mining Sector Diagnostic (2021)",
        ],
        "instituciones_clave": [
            "Contraloría General de la República",
            "Procuraduría Pública Especializada Anticorrupción",
            "Fiscalía Provincial Especializada en Delitos de Corrupción",
            "Comisión de Alto Nivel Anticorrupción",
            "OEFA (fiscalización ambiental)",
            "SUNAT (control tributario sectorial)",
        ],
    }


def _diagnostico_corrupcion(n):
    if n >= 5:
        return ("ALTA: denuncias o investigaciones de corrupción sectorial relevantes esta semana. "
                "Revisar exposure a contrapartes en investigación.")
    if n >= 2:
        return ("MEDIA: actividad anticorrupción reportada. Mantener compliance reforzada.")
    if n >= 1:
        return ("BAJA-MEDIA: hechos puntuales detectados. Monitoreo rutinario.")
    return ("ESTABLE: sin denuncias de corrupción sectorial significativas esta semana.")
