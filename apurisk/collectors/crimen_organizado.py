"""Clasificador OSINT temático: crimen organizado, narcotráfico,
minería ilegal, contrabando y migración irregular.

A diferencia de ACLED (que cubre violencia política y protestas), este
collector procesa los artículos RSS ya recolectados y los clasifica
en categorías criminales/migratorias que ACLED no cubre exhaustivamente
en Perú.

Sub-clasificaciones:
  - narcotráfico (VRAEM, traficantes, droga incautada)
  - minería_ilegal (La Pampa, Madre de Dios, dragado, oro)
  - tala_ilegal (deforestación, madereros ilegales)
  - contrabando (fronteras, mercancía decomisada)
  - migración_irregular (tráfico de personas, frontera, detenciones)
  - extorsión_sicariato (crimen organizado urbano)
  - rutas_fluviales_críticas (Amazonas, Ucayali, Marañón con tráfico ilegal)
"""
from __future__ import annotations
from .base import BaseCollector, Article


# Patrones por tipología. Cada tipo tiene:
#  - fuertes: keywords que casi garantizan el match (peso alto)
#  - contexto: keywords que refuerzan si aparecen con un fuerte
#  - negacion: keywords que descartan el match (falsos positivos comunes)

CATEGORIAS = {
    "narcotrafico": {
        "fuertes": [
            "narcotráfico", "narcotrafico", "narcotraficante", "narcotraficantes",
            "cártel", "cartel de", "cartel del",
            "vraem", "valle de los ríos apurímac",
            "incauta droga", "incautan droga", "incautación de droga",
            "decomiso de droga", "decomisan droga",
            "cocaína", "cocaina", "clorhidrato de cocaína",
            "pasta básica de cocaína", "pbc",
            "hoja de coca ilegal",
            "lavado de activos narco",
            "remanentes terroristas vraem",
            "sendero luminoso narcotráfico", "sl narcotráfico",
            "carteles mexicanos", "carteles colombianos",
        ],
        "contexto": [
            "droga", "estupefacientes", "marihuana", "fiscalía antidroga",
            "dirandro", "dininc", "tráfico ilícito",
            "kilos", "toneladas", "operativo", "captura",
        ],
        "negacion": [
            "famoso narco", "narconovela", "narcoserie",  # entretenimiento
            "guerra contra el narco mexico",  # contexto regional sin foco Perú
        ],
    },
    "mineria_ilegal": {
        "fuertes": [
            "minería ilegal", "mineria ilegal", "minero ilegal", "mineros ilegales",
            "extracción ilegal de oro", "extraccion ilegal de oro",
            "dragado ilegal", "dragas ilegales",
            "la pampa madre de dios", "la pampa tambopata",
            "interdicción minera", "interdiccion minera",
            "reinfo", "registro integral de formalización minera",
            "operativo mercurio",
            "deforestación minería", "deforestacion mineria",
            "minería aurífera ilegal", "mineria aurifera ilegal",
        ],
        "contexto": [
            "minería informal", "mineria informal",
            "madre de dios", "puerto maldonado", "tambopata",
            "balsa de extracción", "balsa de extraccion",
            "mercurio", "contaminación con mercurio",
            "oro", "extracción ilegal",
        ],
        "negacion": [
            "minería formal", "minera grande", "antamina", "las bambas",
            "yanacocha responsable",
        ],
    },
    "tala_ilegal": {
        "fuertes": [
            "tala ilegal", "talador ilegal", "taladores ilegales",
            "extracción ilegal de madera", "extraccion ilegal de madera",
            "madera ilegal", "comercio ilegal de madera",
            "deforestación amazónica", "deforestacion amazonica",
            "deforestación ilegal", "deforestacion ilegal",
            "osinfor", "operativo amazonas verde",
            "incautación de madera", "incautacion de madera",
            "cedro ilegal", "shihuahuaco ilegal", "caoba ilegal",
        ],
        "contexto": [
            "amazonía", "amazonia", "loreto", "ucayali",
            "concesión forestal", "concesion forestal",
            "serfor", "guardabosques", "asesinato dirigente indígena",
        ],
        "negacion": [
            "tala selectiva legal", "manejo forestal sostenible",
        ],
    },
    "contrabando": {
        "fuertes": [
            "contrabando", "contrabandista", "contrabandistas",
            "mercancía contrabandeada", "mercancia contrabandeada",
            "decomiso de contrabando",
            "frontera con bolivia contrabando", "frontera con chile contrabando",
            "santa rosa contrabando", "desaguadero contrabando",
            "kasani contrabando",
            "incautación mercadería ilegal", "incautacion mercaderia ilegal",
            "ropa contrabandeada", "combustible de contrabando",
        ],
        "contexto": [
            "aduanas", "sunat aduanas", "policía fiscal", "policia fiscal",
            "tacna frontera", "tumbes frontera", "puno frontera",
            "decomiso", "mercadería ilegal", "mercaderia ilegal",
            "internado al país", "internado al pais",
        ],
        "negacion": [],
    },
    "migracion_irregular": {
        "fuertes": [
            "migración irregular", "migracion irregular",
            "migración indocumentada", "migracion indocumentada",
            "migrantes irregulares", "migrantes indocumentados",
            "tráfico de personas", "trafico de personas",
            "trata de personas",
            "migraciones detiene", "migraciones interviene",
            "migrantes venezolanos detenidos",
            "frontera ecuador migrantes", "frontera bolivia migrantes",
            "aguas verdes migrantes", "tumbes migrantes",
            "coyote migrante", "coyotes migrantes",
            "expulsión de extranjeros", "expulsion de extranjeros",
        ],
        "contexto": [
            "venezolanos", "haitianos", "extranjeros sin documentos",
            "ptp", "permiso temporal de permanencia",
            "carné de extranjería", "carne de extranjeria",
            "superintendencia nacional de migraciones",
            "ace migraciones",
        ],
        "negacion": [
            "turismo", "migración legal", "migracion legal",
            "residencia legal", "naturalización", "naturalizacion",
        ],
    },
    "extorsion_sicariato": {
        "fuertes": [
            "extorsión", "extorsion", "extorsionado", "extorsionadores",
            "sicariato", "sicario", "sicarios",
            "asesinato por encargo",
            "cobro de cupos", "cobro de cupo",
            "tren de aragua perú", "tren de aragua peru",
            "los pulpos", "los malditos",
            "amenaza con explosivo", "atentado con dinamita",
            "atentado con granada",
            "secuestro al paso", "secuestro extorsivo",
        ],
        "contexto": [
            "trujillo extorsión", "trujillo extorsion",
            "lima norte extorsión", "ate vitarte extorsión",
            "construcción civil amenazada", "construccion civil amenazada",
            "transportistas amenazados",
            "pyme extorsionada",
            "banda criminal", "organización criminal",
        ],
        "negacion": [
            "extorsión política", "extorsion politica",  # otro tema
        ],
    },
}


def _clasificar(texto: str) -> tuple[str | None, str | None]:
    """Retorna (categoria, keyword_match) o (None, None) si no clasifica.

    Algoritmo: una keyword 'fuerte' garantiza match; si no hay fuerte,
    se requieren 2+ keywords de 'contexto'. Las 'negaciones' descartan
    el match incluso si hay fuertes.
    """
    t = (texto or "").lower()
    if not t.strip():
        return None, None

    mejor_categoria = None
    mejor_keyword = None
    mejor_score = 0

    for cat, patrones in CATEGORIAS.items():
        # Si alguna negación está presente, saltar categoría
        if any(neg in t for neg in patrones.get("negacion", [])):
            continue

        # Buscar fuertes
        kw_fuerte = None
        for kw in patrones["fuertes"]:
            if kw in t:
                kw_fuerte = kw
                break

        # Buscar contexto
        n_contexto = sum(1 for kw in patrones["contexto"] if kw in t)

        # Scoring: fuerte vale 3, contexto vale 1
        score = (3 if kw_fuerte else 0) + n_contexto

        # Match si hay fuerte o 2+ contexto
        if kw_fuerte or n_contexto >= 2:
            if score > mejor_score:
                mejor_score = score
                mejor_categoria = cat
                mejor_keyword = kw_fuerte or f"contexto×{n_contexto}"

    return mejor_categoria, mejor_keyword


def _severidad(texto: str, categoria: str) -> str:
    """Severidad estimada según contenido y categoría."""
    t = (texto or "").lower()
    high_indicators = [
        "muerto", "fallecido", "fallecida", "asesinato",
        "atentado", "explosión", "balacera", "tiroteo",
        "secuestro", "desaparecidos",
    ]
    if any(k in t for k in high_indicators):
        return "alta"
    # Por defecto según categoría
    if categoria in ("extorsion_sicariato", "narcotrafico"):
        return "alta"
    return "media"


class CrimenOrganizadoCollector(BaseCollector):
    """Collector temático que clasifica RSS en categorías criminales/migratorias.

    Se invoca en modo 'classify_from_media' similar a defensoria/congreso.
    En modo demo retorna lista vacía; el contenido viene de RSS reales.
    """
    source_id = "crimen_organizado"
    source_name = "Crimen Organizado y Migración (clasificación OSINT)"
    category = "estado"

    def __init__(self, config: dict, demo: bool = True):
        super().__init__(config, demo=demo)
        self.ventana_dias = int(config.get("crimen_ventana_dias", 14))
        self.max_items = int(config.get("crimen_max_items", 100))

    def collect(self) -> list[Article]:
        # En modo demo no genera contenido propio; depende de classify_from_media
        return []

    def classify_from_media(self, rss_articles: list[Article]) -> list[Article]:
        """Clasifica artículos RSS en categorías criminales/migratorias.

        Returns:
            Lista de Article con source_id='crimen_organizado' y raw['tipologia']
            indicando la categoría (narcotrafico, mineria_ilegal, tala_ilegal,
            contrabando, migracion_irregular, extorsion_sicariato).
        """
        out: list[Article] = []
        ventana_horas = self.ventana_dias * 24

        for art in rss_articles:
            try:
                h_ago = art.hours_ago()
                if h_ago == float("inf") or h_ago < 0 or h_ago > ventana_horas:
                    continue
            except Exception:
                continue

            texto = f"{art.title} {art.summary}"
            categoria, keyword = _clasificar(texto)
            if not categoria:
                continue

            severidad = _severidad(texto, categoria)
            raw_meta = dict(art.raw or {})
            raw_meta.update({
                "tipologia": categoria,
                "keyword_match": keyword,
                "severidad": severidad,
                "fuente_original": art.source_name,
                "clasificacion": "automática_rss",
            })

            # Etiqueta legible para UI
            etiquetas_ui = {
                "narcotrafico": "Narcotráfico",
                "mineria_ilegal": "Minería ilegal",
                "tala_ilegal": "Tala ilegal",
                "contrabando": "Contrabando",
                "migracion_irregular": "Migración irregular",
                "extorsion_sicariato": "Extorsión / Sicariato",
            }
            etiqueta = etiquetas_ui.get(categoria, categoria)

            out.append(Article(
                source_id=self.source_id,
                source_name=f"{etiqueta} · vía {art.source_name}",
                category=self.category,
                title=art.title,
                summary=art.summary,
                url=art.url,
                published=art.published,
                region=art.region,
                criticidad=severidad,
                raw=raw_meta,
            ))

        try:
            out.sort(key=lambda a: a.hours_ago())
        except Exception:
            pass
        return out[: self.max_items]
