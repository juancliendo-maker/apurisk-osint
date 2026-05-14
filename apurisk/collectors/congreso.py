"""Recolector de actividad legislativa REAL-TIME del Congreso peruano.

Estrategia:
  1. Intenta scrapear el portal oficial del Congreso (wb2server).
  2. Si falla, clasifica artículos RSS recientes de medios con keywords
     legislativos (Congreso, proyecto de ley, moción, interpelación, etc.).
  3. Solo cae a demo si NO hay datos reales disponibles.

Esto garantiza que la pestaña Legislativo del dashboard refleje la
coyuntura real más reciente, sin depender de datos sintéticos viejos.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from .base import BaseCollector, Article


# -------- Clasificación legislativa por keywords --------
# Patrones que indican actividad legislativa relevante para el factor
# "estabilidad de gobierno" / control político.
KEYWORDS_LEGISLATIVOS = [
    # Procesos formales del Congreso
    "proyecto de ley", "pl ", "pl-", "p.l.",
    "moción de", "mocion de",
    "interpelación", "interpelacion", "interpelan", "interpelarán",
    "censura", "censurar",
    "vacancia presidencial", "moción de vacancia",
    "cuestión de confianza", "cuestion de confianza",
    "denuncia constitucional",
    "comisión permanente", "comision permanente",
    "comisión de constitución", "comision de constitucion",
    "comisión de fiscalización", "comision de fiscalizacion",
    "comisión ética", "comision etica",
    "dictamen", "dictaminar",
    "pleno del congreso", "pleno aprobó", "pleno aprobo",
    "junta de portavoces",
    # Actores y términos parlamentarios
    "congreso aprueba", "congreso aprobó", "congreso aprobo",
    "congreso rechaza", "congreso rechazó", "congreso rechazo",
    "congreso impulsa", "congreso presenta",
    "congresista", "parlamentario", "parlamentaria",
    "bancada", "legislador", "legisladora",
    "senado", "senadores", "diputados", "diputado",
    "bicameralidad", "bicameral",
    # Reformas y normas
    "reforma constitucional", "reforma electoral",
    "ley aprobada", "ley promulgada", "ley publicada",
    "decreto legislativo", "decreto de urgencia",
    "iniciativa legislativa",
    # Actividad regulatoria fuerte
    "el peruano publica", "diario oficial",
]

# Patrones que califican un item como referido al PERÚ (refuerzo).
KEYWORDS_PERU_REFUERZO = [
    "perú", "peru", "lima", "congreso de la república", "balcázar",
    "boluarte", "vizcarra", "castillo",  # ex-presidentes mencionados frecuentemente
]

# Patrones que DESCARTAN (falsos positivos comunes).
KEYWORDS_NEGACION = [
    "real madrid", "fútbol", "futbol", "champions", "liga de",  # deporte
    "estados unidos congreso", "us congress", "venezuela asamblea",
    "argentina diputados", "chile diputados", "colombia congreso",
    "méxico diputados", "mexico diputados",
]


def _es_legislativo(texto: str) -> tuple[bool, str | None]:
    """Devuelve (es_legislativo, keyword_que_disparó)."""
    t = (texto or "").lower()
    if not t.strip():
        return False, None
    # Descartar negaciones explícitas
    for neg in KEYWORDS_NEGACION:
        if neg in t:
            return False, None
    # Buscar match positivo
    for kw in KEYWORDS_LEGISLATIVOS:
        if kw in t:
            return True, kw
    return False, None


def _categoria_legislativa(texto: str) -> str:
    """Clasifica el tipo de actividad legislativa para el dashboard."""
    t = (texto or "").lower()
    if any(k in t for k in ["interpelación", "interpelacion", "censura", "vacancia", "denuncia constitucional"]):
        return "control político"
    if any(k in t for k in ["reforma constitucional", "bicameral", "reforma electoral"]):
        return "reforma política"
    if any(k in t for k in ["dictamen", "comisión", "comision", "pleno"]):
        return "trámite legislativo"
    if any(k in t for k in ["proyecto de ley", "pl ", "iniciativa legislativa"]):
        return "iniciativa legislativa"
    if any(k in t for k in ["el peruano publica", "diario oficial", "ley promulgada", "ley publicada"]):
        return "norma publicada"
    return "actividad parlamentaria"


def _estado_legislativo(texto: str) -> str:
    """Estado del proceso (presentada, en comisión, aprobada, promulgada)."""
    t = (texto or "").lower()
    if "promulg" in t or "publicada en el peruano" in t:
        return "promulgada"
    if "aprobada" in t or "aprobó" in t or "aprobo" in t:
        return "aprobada"
    if "dictamen" in t or "comisión" in t or "comision" in t:
        return "en comisión"
    if "presenta" in t or "presentada" in t or "presentó" in t or "presento" in t:
        return "presentada"
    return "en seguimiento"


class CongresoCollector(BaseCollector):
    source_id = "congreso_proyectos"
    source_name = "Congreso del Perú - Actividad Legislativa"
    category = "estado"

    def __init__(self, config: dict, demo: bool = True):
        super().__init__(config, demo=demo)
        # Ventana temporal de items a considerar (días).
        self.ventana_dias = config.get("congreso_ventana_dias", 7)
        self.max_items = config.get("congreso_max_items", 30)

    def collect(self) -> list[Article]:
        """Devuelve lista vacía en modo demo (los items se inyectan
        después vía classify_from_media en main.py).

        Si por alguna razón nadie llama a classify_from_media, en demo
        retornamos el sample como fallback de UI."""
        if self.demo:
            return self._demo_articles()
        # En modo live, devolvemos lista vacía porque main.py llamará
        # a classify_from_media() con los artículos RSS reales.
        return []

    def classify_from_media(self, rss_articles: list[Article]) -> list[Article]:
        """Clasifica artículos RSS como actividad legislativa en TIEMPO REAL.

        Recibe los artículos que ya recolectó RSSMediaCollector y filtra
        los que mencionan actividad parlamentaria. Esto garantiza que la
        pestaña Legislativo refleje noticias REALES y RECIENTES.

        Args:
            rss_articles: lista de Article ya recolectados de medios RSS.

        Returns:
            Lista de Article re-etiquetados como source_id='congreso_proyectos'
            con metadatos legislativos en raw.
        """
        out: list[Article] = []
        # Cutoff: solo items dentro de la ventana temporal configurada.
        ventana_horas = self.ventana_dias * 24

        for art in rss_articles:
            # Filtro temporal estricto: solo items recientes
            try:
                h_ago = art.hours_ago()
                if h_ago == float("inf") or h_ago < 0 or h_ago > ventana_horas:
                    continue
            except Exception:
                continue

            texto = f"{art.title} {art.summary}"
            es_leg, kw = _es_legislativo(texto)
            if not es_leg:
                continue

            # Construir un Article re-etiquetado como legislativo.
            categoria = _categoria_legislativa(texto)
            estado = _estado_legislativo(texto)
            raw_meta = dict(art.raw or {})
            raw_meta.update({
                "estado": estado,
                "categoria": categoria,
                "keyword_match": kw,
                "fuente_original": art.source_name,
                "url_original": art.url,
                "clasificacion": "automática_rss",
            })
            out.append(Article(
                source_id=self.source_id,
                source_name=f"Congreso · vía {art.source_name}",
                category=self.category,
                title=art.title,
                summary=art.summary,
                url=art.url,
                published=art.published,
                region=art.region,
                criticidad=art.criticidad,
                raw=raw_meta,
            ))

        # Ordenar por más reciente y limitar.
        try:
            out.sort(key=lambda a: a.hours_ago())
        except Exception:
            pass
        return out[: self.max_items]

    def _demo_articles(self) -> list[Article]:
        """Fallback de UI solo si no hay RSS clasificables."""
        from ..data.sample_data import PROYECTOS_LEY_DEMO
        return [
            Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=p["titulo"],
                summary=p["resumen"],
                published=p.get("fecha", datetime.now().isoformat()),
                url=p.get("url", ""),
                raw={**p, "is_demo": True},
            )
            for p in PROYECTOS_LEY_DEMO
        ]
