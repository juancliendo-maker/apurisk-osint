"""Detección de temas/keywords basada en categorías políticas peruanas."""
from __future__ import annotations
from collections import Counter

CATEGORIAS_TEMAS = {
    "estabilidad_gobierno": [
        "vacancia", "moción", "mocion", "incapacidad moral", "renuncia",
        "censura", "interpelación", "interpelacion", "cuestión de confianza",
        "cuestion de confianza", "gabinete", "premier", "ministro",
    ],
    "conflictos_sociales": [
        "protesta", "paro", "bloqueo", "marcha", "movilización", "movilizacion",
        "conflicto social", "frente de defensa", "comuneros", "ronderos",
    ],
    "riesgo_regulatorio": [
        "decreto", "reforma", "proyecto de ley", "regulación", "regulacion",
        "ley orgánica", "ley organica", "modifica ley", "consulta previa",
    ],
    "polarizacion": [
        "polarización", "polarizacion", "extremo", "radical", "antisistema",
        "golpista", "caviar", "fujimorismo", "antifujimorismo",
    ],
    "corrupcion": [
        "soborno", "corrupción", "corrupcion", "lavado", "coima",
        "fiscalía", "fiscalia", "denuncia", "imputado", "investigación",
        "investigacion", "lava jato",
    ],
    "seguridad": [
        "homicidio", "asesinato", "extorsión", "extorsion", "sicariato",
        "narco", "narcotráfico", "narcotrafico", "inseguridad", "delincuencia",
        "estado de emergencia",
    ],
    "electoral": [
        "elecciones", "JNE", "ONPE", "candidato", "padrón", "padron",
        "campaña", "campana", "primarias", "alianza electoral",
    ],
    "economico_inversion": [
        "inversión", "inversion", "minería", "mineria", "riesgo país",
        "riesgo pais", "BCR", "MEF", "PBI", "crecimiento económico",
    ],
}


def detectar_temas(articles: list) -> dict:
    """Cuenta menciones por categoría y devuelve mapa cat -> count, ejemplos."""
    counts = Counter()
    ejemplos: dict[str, list[str]] = {k: [] for k in CATEGORIAS_TEMAS}
    for a in articles:
        text = ((a.title or "") + " " + (a.summary or "")).lower()
        for cat, kws in CATEGORIAS_TEMAS.items():
            for kw in kws:
                if kw.lower() in text:
                    counts[cat] += 1
                    if a.title and len(ejemplos[cat]) < 3:
                        ejemplos[cat].append(a.title)
                    break
    return {
        "conteos": dict(counts),
        "ejemplos": ejemplos,
    }
