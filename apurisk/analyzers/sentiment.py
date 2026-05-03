"""Análisis de sentimiento basado en lexicón en español.

Implementación ligera (sin dependencias externas pesadas) para MVP.
Para producción se recomienda reemplazar con un modelo transformer en
español (p.ej. pysentimiento o BETO) — la interfaz se mantiene.
"""
from __future__ import annotations
import re

POS = {
    "acuerdo", "consenso", "diálogo", "dialogo", "reactivación", "reactivacion",
    "crecimiento", "estabilidad", "transparencia", "inversión", "inversion",
    "desarrollo", "consolidación", "consolidacion", "avance", "logra", "logro",
    "aprobado", "respaldo", "consensuada", "constructivo", "favorable",
    "positivo", "mejora", "fortalece", "modernización", "modernizacion",
}

NEG = {
    "crisis", "vacancia", "incapacidad", "moción", "mocion", "interpelación",
    "interpelacion", "soborno", "corrupción", "corrupcion", "denuncia",
    "investigación", "investigacion", "protesta", "paro", "bloqueo",
    "conflicto", "tensión", "tension", "polarización", "polarizacion",
    "incertidumbre", "retroceso", "irregular", "cuestionada", "cuestionado",
    "estancado", "paraliza", "paralización", "paralizacion", "violencia",
    "muerte", "muertes", "heridos", "fallecidos", "renuncia", "destituye",
    "destituido", "ilegal", "fraude", "lavado", "narco", "extorsión",
    "extorsion", "secuestro", "amenaza", "agresión", "agresion", "abuso",
    "contaminación", "contaminacion", "represión", "represion",
    "inseguridad", "homicidio", "asesinato",
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúñ]+", text.lower())


def analizar_sentimiento(text: str) -> dict:
    """Devuelve dict con score (-1..1), positivos, negativos, etiqueta."""
    if not text:
        return {"score": 0.0, "pos": 0, "neg": 0, "label": "neutral"}
    toks = _tokens(text)
    pos = sum(1 for t in toks if t in POS)
    neg = sum(1 for t in toks if t in NEG)
    total = pos + neg
    if total == 0:
        return {"score": 0.0, "pos": 0, "neg": 0, "label": "neutral"}
    score = (pos - neg) / total
    if score > 0.2:
        label = "positivo"
    elif score < -0.2:
        label = "negativo"
    else:
        label = "neutral"
    return {"score": round(score, 3), "pos": pos, "neg": neg, "label": label}
