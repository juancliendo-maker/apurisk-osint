"""Motor de alertas inmediatas: reglas que disparan flags de criticidad.

Cada alerta incluye:
  - id, nivel (CRÍTICA | ALTA | MEDIA), titular, descripción
  - timestamp, antigüedad (horas)
  - regla disparada
  - URLs / fuentes
  - acción recomendada
"""
from __future__ import annotations
from datetime import datetime


REGLAS = [
    {
        "id": "VACANCIA_ACTIVADA",
        "nivel": "CRÍTICA",
        "patrones": ["moción de vacancia", "mocion de vacancia", "firmas para vacancia", "vacancia presidencial", "destitución", "destitucion", "destituido", "destituida", "censura presidencial"],
        "categoria": "Estabilidad gubernamental",
        "accion": "Activar protocolo de comunicación de crisis. Briefing a stakeholders en 2h.",
    },
    {
        "id": "RENUNCIA_MINISTRO",
        "nivel": "CRÍTICA",
        "patrones": ["renuncia ministro", "renuncia mininter", "renuncia titular", "presentó su renuncia", "presento su renuncia", "renuncia indeclinable"],
        "categoria": "Estabilidad gubernamental",
        "accion": "Mapear sucesor probable. Evaluar continuidad de políticas sectoriales.",
    },
    {
        "id": "BLOQUEO_VIA_NACIONAL",
        "nivel": "CRÍTICA",
        "patrones": ["panamericana", "interoceánica", "interoceanica", "corredor minero", "bloqueo de", "bloquean", "vías bloqueadas", "vias bloqueadas"],
        "categoria": "Conflictos sociales",
        "accion": "Alertar operaciones logísticas y mineras en zona afectada.",
    },
    {
        "id": "CONFLICTO_EXTRACTIVO",
        "nivel": "ALTA",
        "patrones": ["las bambas", "antamina", "tía maría", "tia maria", "coroccohuayco", "espinar", "conga", "comunidades campesinas", "cotabambas"],
        "categoria": "Conflictos sociales",
        "accion": "Notificar empresa minera y autoridad regional. Activar mesa técnica.",
    },
    {
        "id": "PARO_REGIONAL",
        "nivel": "ALTA",
        "patrones": ["paro regional", "paro indefinido", "paro agrario", "paralización", "paralizacion", "frente de defensa", "movilización", "movilizacion"],
        "categoria": "Conflictos sociales",
        "accion": "Monitoreo cada 4h. Activar contacto con autoridades regionales.",
    },
    {
        "id": "RIESGO_PAIS",
        "nivel": "ALTA",
        "patrones": ["riesgo país", "riesgo pais", "embig", "sol se deprecia", "fuga de capitales", "calificadora", "fitch", "moody", "s&p", "soberano"],
        "categoria": "Económico",
        "accion": "Alertar a clientes con exposición FX. Recomendar coberturas.",
    },
    {
        "id": "BCR_ALERTA",
        "nivel": "ALTA",
        "patrones": ["bcr advierte", "bcr alerta", "velarde", "tasa de referencia", "incertidumbre política", "incertidumbre politica"],
        "categoria": "Económico",
        "accion": "Análisis de impacto sobre portafolio. Recomendar reposicionamiento.",
    },
    {
        "id": "INVESTIGACION_FORMAL",
        "nivel": "ALTA",
        "patrones": ["formaliza denuncia", "allanamiento", "imputado", "fiscalía dispone", "fiscalia dispone", "denuncia constitucional", "investigación preparatoria"],
        "categoria": "Corrupción",
        "accion": "Verificar exposición reputacional de stakeholders.",
    },
    {
        "id": "ATAQUE_VIOLENCIA",
        "nivel": "CRÍTICA",
        "patrones": ["ataque a comisaría", "ataque a comisaria", "explosivos", "policías heridos", "policias heridos", "atentado", "asesinato", "homicidio", "extorsión", "extorsion"],
        "categoria": "Seguridad",
        "accion": "Activar protocolo de seguridad para personal en zona.",
    },
    {
        "id": "REFORMA_INSTITUCIONAL",
        "nivel": "ALTA",
        "patrones": ["bicameralidad", "reforma electoral", "valla electoral", "consulta popular", "modifica constitución", "modifica constitucion", "reforma constitucional"],
        "categoria": "Riesgo regulatorio",
        "accion": "Briefing legal-político sobre alcance de la reforma.",
    },
    {
        "id": "PROCESO_ELECTORAL",
        "nivel": "ALTA",
        "patrones": ["actas observadas", "segunda vuelta", "jee", "onpe definirá", "onpe definira", "conteo electoral", "votos en disputa", "fraude electoral"],
        "categoria": "Riesgo regulatorio",
        "accion": "Monitoreo intenso del cierre electoral. Reportes cada 6h hasta resolución JEE.",
    },
    {
        "id": "AUDIOS_FILTRADOS",
        "nivel": "ALTA",
        "patrones": ["audios revelan", "audios filtrados", "audios donde", "panel revela", "filtración", "filtracion", "reuniones secretas"],
        "categoria": "Corrupción",
        "accion": "Mapear menciones y evaluar exposición.",
    },
    {
        "id": "INSEGURIDAD_LIMA",
        "nivel": "ALTA",
        "patrones": ["sjl", "smp", "san juan de lurigancho", "estado de emergencia", "crimen organizado", "marchas vecinales"],
        "categoria": "Seguridad",
        "accion": "Coordinar con seguridad corporativa. Mapear oficinas en zonas afectadas.",
    },
    {
        "id": "MAGNICIDIO",
        "nivel": "CRÍTICA",
        "patrones": ["magnicidio", "atentado contra", "atentado al candidato",
                     "asesinato candidato", "asesinato de candidato", "asesinaron al",
                     "intento de asesinato", "tentativa de homicidio candidato",
                     "balacera mitin", "ataque a mitin", "atentado a mitin",
                     "amenaza de muerte candidato", "ataque al líder", "ataque al lider"],
        "categoria": "Violencia electoral",
        "accion": "ALERTA NACIONAL. Activar protocolo de emergencia. Briefing inmediato a stakeholders. Verificar exposición de personal protegido.",
    },
    {
        "id": "ELECCIONES_CUESTIONADAS",
        "nivel": "CRÍTICA",
        "patrones": ["elecciones nulas", "elecciones inválidas", "elecciones invalidas",
                     "elecciones cuestionadas", "elecciones anuladas", "anulación electoral",
                     "anulacion electoral", "fraude electoral", "votos cuestionados",
                     "impugnación electoral", "impugnacion electoral",
                     "actas falsificadas", "actas adulteradas",
                     "padrón cuestionado", "padron cuestionado",
                     "no reconocer resultados", "rechaza resultados"],
        "categoria": "Estabilidad gubernamental",
        "accion": "Activar protocolo de crisis institucional. Briefing legal-político inmediato. Monitoreo intensivo cada 1h hasta resolución.",
    },
    {
        "id": "CORRUPCION_SISTEMICA",
        "nivel": "CRÍTICA",
        "patrones": ["lava jato", "odebrecht", "729 delitos", "67 congresistas", "lavado de activos",
                     "organización criminal", "organizacion criminal", "denuncia constitucional",
                     "captura del estado", "ministerio público investigado", "ministerio publico investigado"],
        "categoria": "Corrupción",
        "accion": "Briefing legal sobre exposición. Activar comité de crisis reputacional. Mapear stakeholders mencionados.",
    },
    {
        "id": "INTERVENCION_FFAA",
        "nivel": "CRÍTICA",
        "patrones": ["fuerzas armadas en las calles", "militares en las calles", "militares combatan",
                     "decreto supremo militarización", "decreto supremo militarizacion",
                     "comando conjunto despliega", "operación militar conjunta", "operacion militar conjunta",
                     "estado de emergencia ejecutivo", "ffaa intervienen"],
        "categoria": "Militar / Seguridad",
        "accion": "ALERTA INSTITUCIONAL. Evaluar regresión democrática. Briefing geopolítico inmediato.",
    },
    {
        "id": "TENSION_FRONTERIZA_CHILE",
        "nivel": "CRÍTICA",
        "patrones": ["frontera con chile", "muro fronterizo", "escudo fronterizo", "zanja fronteriza",
                     "tacna emergencia", "tacna estado de emergencia", "kast frontera",
                     "agentes en la frontera", "100 agentes frontera", "militares peruanos frontera"],
        "categoria": "Seguridad nacional",
        "accion": "Notificar operaciones logísticas Tacna-Arica. Riesgo de escalamiento bilateral.",
    },
    {
        "id": "TENSION_FRONTERIZA_OTRA",
        "nivel": "ALTA",
        "patrones": ["frontera con ecuador", "frontera con bolivia", "frontera con brasil",
                     "tumbes incidente", "puno bolivia", "incidente fronterizo", "patrullaje binacional"],
        "categoria": "Seguridad nacional",
        "accion": "Monitoreo regional. Verificar canales diplomáticos.",
    },
    {
        "id": "CRISIS_MIGRATORIA",
        "nivel": "ALTA",
        "patrones": ["expulsión migrantes", "expulsion migrantes", "tren de aragua", "ingreso irregular",
                     "deportación masiva", "deportacion masiva", "venezolanos expulsados",
                     "regularización migratoria", "regularizacion migratoria"],
        "categoria": "Social / Seguridad",
        "accion": "Análisis de impacto laboral y social. Coordinar con áreas de RRHH y compliance.",
    },
    {
        "id": "RUPTURA_DIPLOMATICA",
        "nivel": "CRÍTICA",
        "patrones": ["ruptura diplomática", "ruptura diplomatica", "persona non grata",
                     "expulsión embajador", "expulsion embajador", "retira embajador", "rompe relaciones",
                     "congelar relaciones", "asilo betssy", "sheinbaum"],
        "categoria": "Diplomacia / Geopolítica",
        "accion": "Briefing geopolítico crítico. Evaluar exposición comercial bilateral. Activar protocolo país.",
    },
    {
        "id": "TENSION_DIPLOMATICA",
        "nivel": "ALTA",
        "patrones": ["diálogo discreto", "dialogo discreto", "reconciliación bilateral", "reconciliacion bilateral",
                     "tensión bilateral", "tension bilateral", "embajador en consultas",
                     "cancillería convoca", "cancilleria convoca", "embajada resguardada"],
        "categoria": "Diplomacia / Geopolítica",
        "accion": "Monitoreo del canal diplomático. Reportar al equipo de relaciones internacionales.",
    },
]


def _texto(a) -> str:
    return ((a.title or "") + " " + (a.summary or "")).lower()


def detectar_alertas(articulos: list, conflictos: list, ventana_horas: int = 72) -> list[dict]:
    """Recorre artículos+conflictos y emite alertas si aplican reglas, dentro de la ventana.

    ventana_horas controla la antigüedad máxima permitida. Por defecto 72h
    para capturar todo el ciclo informativo reciente.
    """
    out: list[dict] = []
    todos = list(articulos) + list(conflictos)
    for a in todos:
        hours = a.hours_ago()
        if hours == float("inf") or hours > ventana_horas:
            continue
        text = _texto(a)
        for r in REGLAS:
            if any(p in text for p in r["patrones"]):
                # bonificación de severidad si la criticidad del item es alta
                nivel = r["nivel"]
                if a.criticidad == "alta" and nivel == "ALTA":
                    nivel = "CRÍTICA"
                out.append({
                    "alert_id": f"{r['id']}_{abs(hash(a.title)) % 10000}",
                    "regla": r["id"],
                    "nivel": nivel,
                    "categoria": r["categoria"],
                    "titulo": a.title,
                    "resumen": a.summary,
                    "fuente": a.source_name,
                    "url": a.url,
                    "region": a.region,
                    "hours_ago": round(hours, 1),
                    "timestamp": a.published,
                    "accion": r["accion"],
                    "ventana_24h": hours <= 24,
                })
                break  # una sola regla por ítem, la primera que matchee
    # ordenar: críticas primero, luego por antigüedad ascendente
    nivel_rank = {"CRÍTICA": 0, "ALTA": 1, "MEDIA": 2}
    out.sort(key=lambda x: (nivel_rank.get(x["nivel"], 9), x["hours_ago"]))
    return out
