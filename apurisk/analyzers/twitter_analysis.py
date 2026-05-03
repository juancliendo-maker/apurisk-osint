"""Análisis específico de tweets: hashtags, virales, top voces, sentimiento."""
from __future__ import annotations
from collections import Counter
from .sentiment import analizar_sentimiento


def analizar_twitter(tweets: list) -> dict:
    """Devuelve agregados clave del feed de Twitter."""
    if not tweets:
        return {
            "n": 0, "hashtags": [], "top_users": [], "virales": [],
            "sentimiento_promedio": 0.0, "engagement_total": 0,
            "reach_estimado": 0, "menciones": [],
        }

    hashtags = Counter()
    users = Counter()
    menciones = Counter()
    sents = []
    engagement_total = 0
    reach_estimado = 0
    virales = []

    for t in tweets:
        raw = t.raw or {}
        for h in raw.get("hashtags", []):
            hashtags[h] += 1
        handle = raw.get("handle", "")
        if handle:
            users[handle] += 1
        for m in raw.get("mentions", []):
            menciones[m] += 1
        s = analizar_sentimiento(t.summary or t.title or "")
        sents.append(s["score"])
        metrics = raw.get("metrics", {})
        rt = metrics.get("retweet_count", 0)
        likes = metrics.get("like_count", 0)
        replies = metrics.get("reply_count", 0)
        quotes = metrics.get("quote_count", 0)
        eng = rt + likes + replies + quotes
        engagement_total += eng
        # reach estimado simple: likes + (rt * 100) (asume audiencia promedio)
        reach_estimado += likes + rt * 100
        if eng >= 1500:
            virales.append({
                "tweet_id": raw.get("tweet_id"),
                "handle": handle,
                "name": raw.get("name", handle),
                "verified": raw.get("verified", False),
                "text": t.summary,
                "url": t.url,
                "metrics": metrics,
                "engagement": eng,
                "hours_ago": round(t.hours_ago(), 1),
                "criticidad": t.criticidad,
            })

    virales.sort(key=lambda x: -x["engagement"])

    return {
        "n": len(tweets),
        "hashtags": hashtags.most_common(15),
        "top_users": [{"handle": u, "count": c} for u, c in users.most_common(10)],
        "menciones": menciones.most_common(10),
        "virales": virales[:8],
        "sentimiento_promedio": round(sum(sents) / len(sents), 3) if sents else 0.0,
        "engagement_total": engagement_total,
        "reach_estimado": reach_estimado,
    }
