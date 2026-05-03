"""Extracción de entidades políticas peruanas (lista curada).

MVP: matching contra diccionario. Para producción usar spaCy es_core_news_lg
o un modelo NER fine-tuned en política peruana.
"""
from __future__ import annotations
import re
from collections import Counter

# Diccionario curado de entidades clave
INSTITUCIONES = [
    "Congreso", "Ejecutivo", "Poder Judicial", "Fiscalía", "Fiscalia",
    "Defensoría del Pueblo", "Defensoria del Pueblo", "JNE", "ONPE", "RENIEC",
    "BCR", "MEF", "MTC", "Mininter", "Minedu", "Minsa", "Sunat",
    "JNJ", "Junta Nacional de Justicia", "Tribunal Constitucional",
    "Contraloría", "Contraloria", "Defensoría", "Defensoria",
    "PCM", "Premier", "Mesa Directiva", "Pleno",
    # Militares / seguridad
    "Fuerzas Armadas", "FFAA", "Ejército del Perú", "Ejercito del Peru",
    "Marina de Guerra", "Fuerza Aérea", "Fuerza Aerea", "PNP",
    "Comando Conjunto", "VRAEM", "DINI", "DIRCOTE",
    # Diplomacia
    "Cancillería", "Cancilleria", "MRE", "Embajada de México",
    "Embajada de Mexico", "Embajada de Chile", "Embajada de Brasil",
    "Embajada de Estados Unidos", "Embajada de Bolivia", "OEA", "ONU",
    "Comunidad Andina",
    # Migratorio
    "Migraciones", "Superintendencia Nacional de Migraciones",
]

# Países (relevantes para tensiones diplomáticas/fronterizas/migratorias)
PAISES = [
    "Chile", "Ecuador", "Bolivia", "Brasil", "Colombia", "Venezuela",
    "México", "Mexico", "Estados Unidos", "EE.UU.", "EEUU",
    "España", "Espana", "Argentina",
]

# Actores políticos clave (líderes regionales, presidentes vecinos)
ACTORES_INTERNACIONALES = [
    "José Antonio Kast", "Jose Antonio Kast", "Kast",
    "Gabriel Boric", "Boric",
    "Claudia Sheinbaum", "Sheinbaum",
    "Daniel Noboa", "Noboa",
    "Luis Arce", "Arce",
    "Gustavo Petro", "Petro",
    "Nicolás Maduro", "Nicolas Maduro", "Maduro",
    "Lula", "Donald Trump", "Trump",
]

PARTIDOS = [
    "Fuerza Popular", "Acción Popular", "Accion Popular", "Alianza para el Progreso",
    "Avanza País", "Avanza Pais", "Renovación Popular", "Renovacion Popular",
    "Perú Libre", "Peru Libre", "Somos Perú", "Somos Peru", "Cambio Democrático",
    "Cambio Democratico", "Juntos por el Perú", "Juntos por el Peru",
    "Frente Amplio", "Podemos Perú", "Podemos Peru",
]

REGIONES = [
    "Amazonas", "Áncash", "Ancash", "Apurímac", "Apurimac", "Arequipa",
    "Ayacucho", "Cajamarca", "Callao", "Cusco", "Huancavelica", "Huánuco",
    "Huanuco", "Ica", "Junín", "Junin", "La Libertad", "Lambayeque", "Lima",
    "Loreto", "Madre de Dios", "Moquegua", "Pasco", "Piura", "Puno",
    "San Martín", "San Martin", "Tacna", "Tumbes", "Ucayali",
]

EMPRESAS_RIESGO = [
    "Las Bambas", "Antamina", "Cerro Verde", "Tía María", "Tia Maria",
    "Conga", "Coroccohuayco", "Camisea", "Petroperú", "Petroperu",
    "Repsol", "MMG", "Glencore",
]


def _find_all(text: str, terms: list[str]) -> list[str]:
    found = []
    for t in terms:
        # match palabra completa, case-insensitive
        if re.search(r"\b" + re.escape(t) + r"\b", text, re.IGNORECASE):
            found.append(t)
    return found


def extraer_entidades(articles: list) -> dict:
    """Devuelve frecuencia de entidades agrupadas por tipo."""
    inst = Counter()
    part = Counter()
    reg = Counter()
    emp = Counter()
    paises = Counter()
    actores_int = Counter()
    for a in articles:
        text = (a.title or "") + " " + (a.summary or "")
        for x in _find_all(text, INSTITUCIONES):
            inst[x] += 1
        for x in _find_all(text, PARTIDOS):
            part[x] += 1
        for x in _find_all(text, REGIONES):
            reg[x] += 1
        for x in _find_all(text, EMPRESAS_RIESGO):
            emp[x] += 1
        for x in _find_all(text, PAISES):
            paises[x] += 1
        for x in _find_all(text, ACTORES_INTERNACIONALES):
            actores_int[x] += 1
    return {
        "instituciones": inst.most_common(15),
        "partidos": part.most_common(15),
        "regiones": reg.most_common(15),
        "empresas_riesgo": emp.most_common(15),
        "paises": paises.most_common(15),
        "actores_internacionales": actores_int.most_common(15),
    }
