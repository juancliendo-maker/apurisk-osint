"""Recolector de Conflictos Sociales REAL-TIME.

Estrategia:
  1. La Defensoría del Pueblo publica reportes mensuales (PDF) y no expone
     API ni RSS de conflictos. Por eso clasificamos artículos RSS de medios
     en TIEMPO REAL como indicadores de conflictos sociales activos.
  2. Solo cae a demo si no hay artículos RSS clasificables.

Esto garantiza que la pestaña Conflictos del dashboard refleje las
movilizaciones, paros, bloqueos y protestas reportadas EN VIVO por la
prensa, no datos sintéticos viejos.
"""
from __future__ import annotations
from datetime import datetime
from .base import BaseCollector, Article


# -------- Clasificación de conflictos por keywords --------
KEYWORDS_CONFLICTO = [
    # Acciones colectivas
    "paro nacional", "paro regional", "paro indefinido", "paro agrario",
    "huelga", "huelga indefinida", "huelga general",
    "marcha", "movilización", "movilizacion", "movilizaciones",
    "bloqueo", "bloqueado", "bloquean", "bloquearon",
    "toma de carretera", "toma de la carretera", "toma del aeropuerto",
    "protesta", "protestan", "protestaron",
    "manifestación", "manifestacion", "manifestantes",
    "plantón", "planton", "vigilia",
    "enfrentamiento", "enfrentamientos", "se enfrentaron",
    # Conflictos socioambientales
    "comunidades campesinas", "comunidad campesina",
    "comunidad indígena", "comunidades indígenas", "pueblos indígenas",
    "ronda campesina", "ronderos",
    "frente de defensa", "frente regional",
    "cgtp", "construcción civil", "construccion civil",
    "sutep", "sutep convoca",
    "federación", "federacion", "federación departamental",
    # Tensiones sociales
    "conflicto social", "conflictos sociales",
    "demanda social", "demandas sociales",
    "tensión social", "tension social",
    "crisis social",
    # Ubicaciones mineras / corredor minero (alta conflictividad histórica)
    "las bambas", "corredor minero", "antamina", "tintaya",
    "conga", "tía maría", "tia maria", "yanacocha",
    # Acciones específicas con violencia
    "disturbios", "saqueo", "saqueos", "vandalismo",
    "intervención policial", "intervencion policial",
    "represión", "represion",
    "víctimas civiles", "victimas civiles",
    # Sectoriales
    "transportistas paran", "agricultores paran", "comerciantes paran",
    "mineros paran", "pescadores paran",
]

# Departamentos del Perú (para extraer la región del conflicto).
REGIONES_PERU = {
    "amazonas": "Amazonas", "ancash": "Áncash", "áncash": "Áncash",
    "apurimac": "Apurímac", "apurímac": "Apurímac",
    "arequipa": "Arequipa", "ayacucho": "Ayacucho",
    "cajamarca": "Cajamarca", "callao": "Callao",
    "cusco": "Cusco", "huancavelica": "Huancavelica",
    "huánuco": "Huánuco", "huanuco": "Huánuco",
    "ica": "Ica", "junín": "Junín", "junin": "Junín",
    "la libertad": "La Libertad", "lambayeque": "Lambayeque",
    "lima": "Lima", "loreto": "Loreto",
    "madre de dios": "Madre de Dios", "moquegua": "Moquegua",
    "pasco": "Pasco", "piura": "Piura", "puno": "Puno",
    "san martín": "San Martín", "san martin": "San Martín",
    "tacna": "Tacna", "tumbes": "Tumbes", "ucayali": "Ucayali",
}

# Tipos de conflicto (categoría Defensoría).
def _tipo_conflicto(texto: str) -> str:
    t = (texto or "").lower()
    if any(k in t for k in ["las bambas", "corredor minero", "minera", "minero",
                              "antamina", "tia maria", "tía maría", "conga",
                              "contaminación", "contaminacion", "remediación"]):
        return "socioambiental"
    if any(k in t for k in ["sueldo", "salario", "rmv", "remuneración",
                              "cgtp", "huelga", "trabajadores", "sutep", "sunafil"]):
        return "demandas laborales/sectoriales"
    if any(k in t for k in ["comunidad", "ronda", "indígena", "indigena", "pueblo originario"]):
        return "asuntos comunales"
    if any(k in t for k in ["gobierno", "presidente", "ministro", "vacancia",
                              "interpelación", "interpelacion"]):
        return "asuntos de gobierno nacional"
    if any(k in t for k in ["policial", "fuerza", "represión", "represion",
                              "víctimas", "operativo", "militar"]):
        return "uso de fuerza estatal"
    return "conflicto social"


def _severidad(texto: str, criticidad_origen: str) -> str:
    """Severidad estimada (alta | media | baja)."""
    t = (texto or "").lower()
    high_kw = ["muerto", "fallecido", "fallecidos", "víctimas civiles",
               "represión", "represion", "enfrentamiento", "vandalismo",
               "saqueo", "paro indefinido", "bloqueo total",
               "estado de emergencia", "balacera", "balazos"]
    if any(k in t for k in high_kw) or criticidad_origen == "alta":
        return "alta"
    med_kw = ["paro", "huelga", "bloqueo", "marcha", "movilización",
              "movilizacion", "protesta"]
    if any(k in t for k in med_kw):
        return "media"
    return "baja"


def _extraer_region(texto: str) -> str | None:
    """Extrae el departamento del Perú mencionado en el texto.

    Usa patrones contextuales para evitar falsos positivos:
      - "en {region}", "de {region}", "{region}:", "{region},", "{region}."
      - Evita coincidencias dentro de nombres propios como "plaza San Martín"
        o "calle Lima Norte".
    """
    import re
    t = (texto or "").lower()
    if not t.strip():
        return None
    # Descartar falsos positivos comunes
    falsos = ["plaza san martín", "plaza san martin", "av. san martín",
              "calle san martín", "av. lima", "calle lima",
              "lima norte", "lima sur", "lima este", "lima oeste",
              "lima centro", "lima metropolitana"]
    t_limpio = t
    for f in falsos:
        t_limpio = t_limpio.replace(f, "  ")

    # Patrones contextuales: la región debe aparecer como ubicación clara
    for clave, nombre in REGIONES_PERU.items():
        patron_contexto = [
            rf"\ben\s+{re.escape(clave)}\b",
            rf"\bde\s+{re.escape(clave)}\b",
            rf"\b{re.escape(clave)}\s*[,:.]",
            rf"\bdepartamento\s+de\s+{re.escape(clave)}\b",
            rf"\bregión\s+{re.escape(clave)}\b",
            rf"\bregion\s+{re.escape(clave)}\b",
        ]
        for pat in patron_contexto:
            if re.search(pat, t_limpio):
                return nombre
    # Fallback: si solo aparece el nombre del departamento aislado.
    for clave, nombre in REGIONES_PERU.items():
        if re.search(rf"\b{re.escape(clave)}\b", t_limpio):
            return nombre
    return None


def _es_conflicto(texto: str) -> tuple[bool, str | None]:
    t = (texto or "").lower()
    if not t.strip():
        return False, None
    # Descartar deportes
    if any(k in t for k in ["real madrid", "fútbol club", "futbol club", "champions league"]):
        return False, None
    for kw in KEYWORDS_CONFLICTO:
        if kw in t:
            return True, kw
    return False, None


class DefensoriaCollector(BaseCollector):
    source_id = "defensoria_conflictos"
    source_name = "Conflictos Sociales (clasificación OSINT)"
    category = "estado"

    def __init__(self, config: dict, demo: bool = True):
        super().__init__(config, demo=demo)
        self.ventana_dias = config.get("conflictos_ventana_dias", 7)
        self.max_items = config.get("conflictos_max_items", 40)

    def collect(self) -> list[Article]:
        """En modo demo retorna sample. En live retorna lista vacía
        porque main.py llamará a classify_from_media() con los RSS."""
        if self.demo:
            return self._demo_articles()
        return []

    def classify_from_media(self, rss_articles: list[Article]) -> list[Article]:
        """Clasifica artículos RSS como conflictos sociales en TIEMPO REAL.

        Recibe los Article ya recolectados de medios y filtra los que
        reportan protestas, paros, bloqueos, marchas, etc. Cada item
        clasificado se enriquece con tipo, severidad y región (cuando
        son extraíbles del texto).
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
            es_conf, kw = _es_conflicto(texto)
            if not es_conf:
                continue

            region = art.region or _extraer_region(texto) or "Lima"
            tipo = _tipo_conflicto(texto)
            sev = _severidad(texto, art.criticidad or "media")
            raw_meta = dict(art.raw or {})
            raw_meta.update({
                "region": region,
                "tipo": tipo,
                "severidad": sev,
                "estado": "activo",
                "keyword_match": kw,
                "fuente_original": art.source_name,
                "clasificacion": "automática_rss",
            })
            out.append(Article(
                source_id=self.source_id,
                source_name=f"Conflicto · vía {art.source_name}",
                category=self.category,
                title=art.title,
                summary=art.summary,
                region=region,
                url=art.url,
                published=art.published,
                criticidad=sev,
                raw=raw_meta,
            ))

        try:
            out.sort(key=lambda a: a.hours_ago())
        except Exception:
            pass
        return out[: self.max_items]

    def _demo_articles(self) -> list[Article]:
        """Fallback de UI solo si no hay RSS clasificables."""
        from ..data.sample_data import CONFLICTOS_DEMO
        return [
            Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=c["titulo"],
                summary=c["descripcion"],
                region=c.get("region"),
                published=c.get("fecha", datetime.now().isoformat()),
                url=c.get("url", ""),
                criticidad=c.get("severidad", "media"),
                raw={**c, "is_demo": True},
            )
            for c in CONFLICTOS_DEMO
        ]
